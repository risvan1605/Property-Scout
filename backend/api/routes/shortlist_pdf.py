"""
Shortlist PDF Route
-------------------
Renders the shortlist as a PDF and emails it to the user.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.conversation import session_store
from tools.email_sender import EmailError, send_shortlist_email
from tools.listing_search import get_listing_by_id
from tools.pdf_generator import PDFGenerationError, generate_shortlist_pdf, render_shortlist_html

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/shortlist", tags=["shortlist"])


class ShortlistPDFRequest(BaseModel):
    session_id: str | None = None
    shortlist_ids: list[str] = Field(min_length=1)
    user_email: str


@router.post("/pdf")
def send_shortlist_pdf(request: ShortlistPDFRequest) -> dict:
    """Generate the shortlist PDF and email it."""
    listings = [l for l in (get_listing_by_id(i) for i in request.shortlist_ids) if l]
    if not listings:
        raise HTTPException(
            status_code=404, detail="None of those listing IDs exist, so there's nothing to send."
        )

    # Prefer the enriched cards held in the session — they carry POIs, match
    # reasons and neighborhood snapshots that the bare DB rows don't have.
    snapshots: dict = {}
    session = session_store.get_session(request.session_id) if request.session_id else None
    if session is not None and session.shortlist:
        by_id = {l["id"]: l for l in session.shortlist}
        listings = [by_id.get(l["id"], l) for l in listings]
        for listing in listings:
            snapshot = listing.get("neighborhood_snapshot")
            if snapshot and listing["neighborhood"] not in snapshots:
                snapshots[listing["neighborhood"]] = {
                    **snapshot,
                    "sources": listing.get("sources", []),
                }

    pdf_bytes, html_body = None, None
    try:
        pdf_bytes = generate_shortlist_pdf(listings, snapshots)
    except PDFGenerationError as exc:
        # Degrade to an HTML email rather than sending nothing at all.
        logger.warning("Falling back to an HTML email: %s", exc)
        html_body = render_shortlist_html(listings, snapshots)

    try:
        result = send_shortlist_email(request.user_email, pdf_bytes, listings, html_body)
    except EmailError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return {
        "status": result["status"],
        "message": f"Shortlist emailed to {request.user_email}"
                   + ("" if pdf_bytes else " (as HTML — PDF rendering was unavailable)"),
        "listing_ids": [l["id"] for l in listings],
        "attachment": result["attachment"],
    }
