"""
Voice-First AI Property Scout — FastAPI entry point
---------------------------------------------------
Run locally with:  uvicorn main:app --reload
"""

import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.middleware.cors import add_cors
from api.routes import booking as booking_routes
from api.routes import chat as chat_routes
from api.routes import listings as listings_routes
from api.routes import shortlist_pdf as shortlist_pdf_routes
from api.routes import tts as tts_routes
from config import CHROMA_DB_PATH, GOOGLE_SERVICE_ACCOUNT_JSON, SQLITE_DB_PATH
from core.conversation import session_store
from tools.tts import is_configured as tts_is_configured

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("property-scout")


def _write_service_account_key() -> None:
    """Materialise the Google key from GOOGLE_SERVICE_ACCOUNT_JSON, if provided."""
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        return
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "service-account.json")
    if os.path.exists(path):
        return
    try:
        json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)  # fail loudly on malformed JSON
    except json.JSONDecodeError as exc:
        logger.error("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON: %s", exc)
        return
    with open(path, "w") as handle:
        handle.write(GOOGLE_SERVICE_ACCOUNT_JSON)
    os.chmod(path, 0o600)
    logger.info("Wrote service account key from GOOGLE_SERVICE_ACCOUNT_JSON")


def _check_sqlite() -> int:
    """Confirm the listings database exists and report how many rows it holds."""
    if not os.path.exists(SQLITE_DB_PATH):
        raise RuntimeError(
            f"SQLite database not found at {SQLITE_DB_PATH}. "
            "Run `python data/seed_db.py` first."
        )
    conn = sqlite3.connect(SQLITE_DB_PATH)
    try:
        return conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
    finally:
        conn.close()


def _init_chroma():
    """Open the ChromaDB persistent client, if the store has been seeded."""
    if not os.path.exists(CHROMA_DB_PATH):
        logger.warning(
            "ChromaDB store not found at %s — neighborhood RAG is unavailable "
            "until `python data/seed_db.py` is run.",
            CHROMA_DB_PATH,
        )
        return None
    import chromadb

    return chromadb.PersistentClient(path=CHROMA_DB_PATH)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize shared resources on startup."""
    _write_service_account_key()
    listing_count = _check_sqlite()
    logger.info("SQLite ready: %d listings", listing_count)

    app.state.listing_count = listing_count
    app.state.chroma_client = _init_chroma()
    app.state.session_store = session_store
    logger.info("Session store ready")

    yield

    app.state.chroma_client = None


app = FastAPI(
    title="Voice-First AI Property Scout",
    description="Conversational rental search for Bengaluru.",
    version="0.1.0",
    lifespan=lifespan,
)

add_cors(app)
app.include_router(listings_routes.router)
app.include_router(chat_routes.router)
app.include_router(booking_routes.router)
app.include_router(shortlist_pdf_routes.router)
app.include_router(tts_routes.router)


@app.get("/api/health", tags=["health"])
def health() -> dict:
    """Liveness probe with a summary of what's wired up."""
    return {
        "status": "ok",
        "listings": getattr(app.state, "listing_count", 0),
        "chroma": getattr(app.state, "chroma_client", None) is not None,
        "voice": tts_is_configured(),
    }
