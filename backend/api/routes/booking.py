"""
Booking Route
-------------
Direct site-visit booking, used by the UI's booking panel. The voice flow books
through the orchestrator's `book_site_visit` tool, which shares this code.
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.conversation import BookingSlot, session_store
from tools.email_sender import EmailError, send_booking_confirmation
from tools.google_calendar import BookingError, book_visit
from tools.listing_search import get_listing_by_id

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/booking", tags=["booking"])


class BookingRequest(BaseModel):
    session_id: str | None = None
    listing_id: str = Field(min_length=1)
    preferred_date: str = Field(description="ISO date, e.g. 2026-09-12")
    preferred_time_slot: str = Field(description="morning, afternoon or evening")
    user_email: str


@router.post("")
def create_booking(request: BookingRequest) -> dict:
    """Book a site visit and return the confirmation with a calendar link."""
    listing = get_listing_by_id(request.listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"Listing '{request.listing_id}' not found")

    try:
        booking = book_visit(
            listing=listing,
            date=request.preferred_date,
            time_slot=request.preferred_time_slot,
            user_email=request.user_email,
        )
    except BookingError as exc:
        # These messages are written to be shown to the user as-is.
        raise HTTPException(status_code=400, detail=str(exc))

    # A service account can't add attendees without Domain-Wide Delegation, so
    # on consumer Google accounts we send the invite ourselves.
    booking["invite_emailed"] = False
    if not booking.get("attendee_invited"):
        try:
            send_booking_confirmation(request.user_email, listing, booking)
            booking["invite_emailed"] = True
        except EmailError as exc:
            logger.warning("Booked, but the confirmation email failed: %s", exc)

    session = session_store.get_session(request.session_id) if request.session_id else None
    if session is not None:
        session.booking = BookingSlot(
            listing_id=listing["id"],
            date=booking["date"],
            time_slot=booking["time_slot"],
            user_email=request.user_email,
            confirmation_code=booking.get("confirmation_code"),
            calendar_link=booking.get("calendar_link"),
        )
        session_store.update_session(session)

    return {"listing_id": listing["id"], **booking}
