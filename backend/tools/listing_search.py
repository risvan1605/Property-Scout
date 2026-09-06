"""
Listing Search Tool
-------------------
Search listings in SQLite with dynamic filters for budget, bedrooms,
neighborhood, amenities, and exclusions.
"""

import json
import sqlite3
from typing import Optional

from config import SQLITE_DB_PATH


def get_db_connection() -> sqlite3.Connection:
    """Create a SQLite connection with row factory."""
    conn = sqlite3.connect(SQLITE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def row_to_dict(row: sqlite3.Row) -> dict:
    """Convert a SQLite Row to a dict, parsing the amenities JSON field."""
    d = dict(row)
    # Parse amenities from JSON string to list
    if d.get("amenities"):
        try:
            d["amenities"] = json.loads(d["amenities"])
        except (json.JSONDecodeError, TypeError):
            d["amenities"] = []
    else:
        d["amenities"] = []
    return d


def normalize_furnishing(value: str) -> str:
    """Accept what people say ("fully furnished") as what the data stores."""
    return value.strip().lower().replace(" ", "-")


def search_listings(
    max_budget: Optional[int] = None,
    min_bedrooms: Optional[int] = None,
    neighborhood: Optional[str] = None,
    amenities: Optional[list[str]] = None,
    exclude_ids: Optional[list[str]] = None,
    furnishing: Optional[str] = None,
) -> list[dict]:
    """
    Search listings with dynamic filters.

    Args:
        max_budget: Maximum monthly rent in INR
        min_bedrooms: Minimum number of bedrooms
        neighborhood: Neighborhood name (case-insensitive match)
        amenities: List of required amenities (all must be present)
        exclude_ids: List of listing IDs to exclude
        furnishing: One of fully-furnished, semi-furnished, unfurnished

    Returns:
        List of matching listing dicts
    """
    conn = get_db_connection()
    query = "SELECT * FROM listings WHERE availability = 'available'"
    params: list = []

    if max_budget is not None:
        query += " AND rent <= ?"
        params.append(max_budget)

    if min_bedrooms is not None:
        query += " AND bedrooms >= ?"
        params.append(min_bedrooms)

    if neighborhood is not None:
        query += " AND LOWER(neighborhood) = LOWER(?)"
        params.append(neighborhood)

    if furnishing:
        query += " AND LOWER(furnishing) = ?"
        params.append(normalize_furnishing(furnishing))

    if exclude_ids:
        placeholders = ",".join("?" for _ in exclude_ids)
        query += f" AND id NOT IN ({placeholders})"
        params.extend(exclude_ids)

    query += " ORDER BY rent ASC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    results = [row_to_dict(row) for row in rows]

    # Filter by amenities in Python (since amenities are stored as JSON)
    if amenities:
        required = set(a.lower() for a in amenities)
        results = [
            r for r in results
            if required.issubset(set(a.lower() for a in r.get("amenities", [])))
        ]

    return results


def get_listing_by_id(listing_id: str) -> Optional[dict]:
    """Fetch a single listing by its ID."""
    conn = get_db_connection()
    row = conn.execute(
        "SELECT * FROM listings WHERE id = ?", (listing_id,)
    ).fetchone()
    conn.close()

    if row is None:
        return None
    return row_to_dict(row)


def get_all_listings() -> list[dict]:
    """Fetch all available listings."""
    conn = get_db_connection()
    rows = conn.execute(
        "SELECT * FROM listings WHERE availability = 'available' ORDER BY neighborhood, rent"
    ).fetchall()
    conn.close()
    return [row_to_dict(row) for row in rows]
