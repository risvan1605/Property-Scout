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

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from api.middleware.cors import add_cors
from api.routes import booking as booking_routes
from api.routes import chat as chat_routes
from api.routes import listings as listings_routes
from api.routes import shortlist_pdf as shortlist_pdf_routes
from api.routes import tts as tts_routes
from config import CHROMA_DB_PATH, GOOGLE_SERVICE_ACCOUNT_JSON, SQLITE_DB_PATH
from core.conversation import session_store
from tools.google_calendar import configuration_status as booking_status
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

    # Say once, where only the operator can see it, why booking is or is not
    # ready. /api/health reports the boolean; the reason names variables and
    # file state, which is not something an unauthenticated probe should learn.
    booking_ok, booking_detail = booking_status()
    if booking_ok:
        logger.info("Booking ready")
    else:
        logger.error("Booking NOT available: %s", booking_detail)

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
    booking_ok, _ = booking_status()
    return {
        "status": "ok",
        "listings": getattr(app.state, "listing_count", 0),
        "chroma": getattr(app.state, "chroma_client", None) is not None,
        "voice": tts_is_configured(),
        "booking": booking_ok,
    }


# ─── Frontend ────────────────────────────────────────────────────────────────
# The Docker build drops the compiled Vite bundle here, so one service serves
# both the API and the UI on one origin — which is also why the browser never
# needs CORS in production. In local development the directory is absent and
# these routes simply don't exist: Vite serves the UI on :5173 and the CORS
# middleware above lets it call this API on :8000.
FRONTEND_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
FRONTEND_INDEX = os.path.join(FRONTEND_DIST, "index.html")

if os.path.isfile(FRONTEND_INDEX):
    # Hashed build assets: safe to cache hard, and never a client-side route.
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")),
        name="assets",
    )

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str) -> FileResponse:
        """Serve a real file if there is one, otherwise hand back the SPA shell.

        Registered last so every API router above wins first. An unmatched
        /api/* path must still 404 rather than silently return HTML — a fetch
        that gets an index page instead of JSON is far harder to debug than
        an honest 404.
        """
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = os.path.normpath(os.path.join(FRONTEND_DIST, full_path))
        if (
            full_path
            and candidate.startswith(FRONTEND_DIST + os.sep)
            and os.path.isfile(candidate)
        ):
            return FileResponse(candidate)
        return FileResponse(FRONTEND_INDEX)

    logger.info("Serving frontend from %s", FRONTEND_DIST)
else:
    logger.info("No built frontend at %s — API only", FRONTEND_DIST)
