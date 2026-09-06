"""
Listings Routes
---------------
Read-only endpoints backing the frontend's initial listing load.
"""

from fastapi import APIRouter, HTTPException

from config import ENRICH_POI_TYPES, ENRICH_RADIUS_METERS
from tools.listing_search import get_all_listings, get_listing_by_id
from tools.mcp_client import osm_mcp

router = APIRouter(prefix="/api/listings", tags=["listings"])


@router.get("")
def list_listings() -> dict:
    """Return every available listing."""
    listings = get_all_listings()
    return {"count": len(listings), "listings": listings}


@router.get("/{listing_id}")
def get_listing(listing_id: str) -> dict:
    """Return a single listing by ID."""
    listing = get_listing_by_id(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"Listing '{listing_id}' not found")
    return listing


@router.get("/{listing_id}/nearby")
async def get_nearby(listing_id: str, radius_meters: int = ENRICH_RADIUS_METERS) -> dict:
    """
    POIs around a listing, from OpenStreetMap via MCP.

    Called when the user opens a listing card. The chat turn warms this cache in
    the background, so it usually returns immediately; a cold call takes a few
    seconds while the MCP server is queried.
    """
    listing = get_listing_by_id(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"Listing '{listing_id}' not found")

    lat, lng = listing.get("latitude"), listing.get("longitude")
    if lat is None or lng is None or (lat == 0 and lng == 0):
        return {"listing_id": listing_id, "error": "This listing has no usable coordinates.",
                "unavailable": ENRICH_POI_TYPES}

    result = await osm_mcp.query_nearby(
        lat=lat, lng=lng, radius_meters=radius_meters, poi_types=ENRICH_POI_TYPES
    )
    return {"listing_id": listing_id, **result}
