"""
Google Calendar Booking
-----------------------
Creates site-visit events on a shared calendar via a service account and emails
the user an invite.

Setup: enable the Calendar API on a Google Cloud project, create a service
account, download its JSON key to `GOOGLE_SERVICE_ACCOUNT_FILE`, and share the
target calendar with the service account's email ("Make changes to events").
"""

import logging
import os
import re
from datetime import date as date_type
from datetime import datetime, timedelta

from config import GOOGLE_CALENDAR_ID, GOOGLE_SERVICE_ACCOUNT_FILE

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TIMEZONE = "Asia/Kolkata"

# Slot name → (start hour, end hour) in local time.
TIME_SLOTS = {
    "morning": (10, 12),
    "afternoon": (14, 16),
    "evening": (17, 19),
}
SLOT_LABELS = {
    "morning": "10:00 AM - 12:00 PM",
    "afternoon": "2:00 PM - 4:00 PM",
    "evening": "5:00 PM - 7:00 PM",
}

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class BookingError(Exception):
    """A booking could not be made. The message is safe to show the user."""


DEFAULT_KEY_FILENAME = "service-account.json"


def _resolve_service_account_path() -> str:
    """Locate the service account key, relative to the backend directory.

    If the configured filename isn't there but the conventional
    `service-account.json` is, use that — a stale path in .env shouldn't take
    booking offline when the key is sitting right next to it.
    """
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    configured = (
        GOOGLE_SERVICE_ACCOUNT_FILE
        if os.path.isabs(GOOGLE_SERVICE_ACCOUNT_FILE)
        else os.path.join(backend_dir, GOOGLE_SERVICE_ACCOUNT_FILE)
    )
    if os.path.exists(configured):
        return configured

    fallback = os.path.join(backend_dir, DEFAULT_KEY_FILENAME)
    if os.path.exists(fallback):
        logger.warning(
            "Service account key not at %s; using %s instead. Update "
            "GOOGLE_SERVICE_ACCOUNT_FILE in .env to match.",
            configured, fallback,
        )
        return fallback
    return configured


def resolve_time_slot(date: str, slot: str) -> tuple[str, str]:
    """
    Turn a date plus a named slot into ISO start/end datetimes.

    Raises BookingError with a spoken-friendly message for a date that is
    malformed or already past.
    """
    slot_key = (slot or "").strip().lower()
    if slot_key not in TIME_SLOTS:
        raise BookingError(
            f"'{slot}' isn't a slot I offer — please pick morning, afternoon or evening."
        )

    try:
        day = datetime.strptime((date or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        raise BookingError(
            f"'{date}' doesn't look like a valid date. Could you give it as a day, month and year?"
        )

    if day < date_type.today():
        raise BookingError(f"{day.isoformat()} has already passed — could you pick a future date?")

    start_hour, end_hour = TIME_SLOTS[slot_key]
    start = datetime.combine(day, datetime.min.time()).replace(hour=start_hour)
    end = start.replace(hour=end_hour)
    return start.isoformat(), end.isoformat()


def validate_email(user_email: str) -> str:
    """Check the address before spending a Calendar API call on it."""
    address = (user_email or "").strip()
    if not address:
        raise BookingError("I'll need your email address to send the calendar invite.")
    if not EMAIL_PATTERN.match(address):
        raise BookingError(f"'{address}' doesn't look like a valid email address.")
    return address


class GoogleCalendarBooking:
    """Thin wrapper over the Calendar API, built lazily so imports never fail."""

    def __init__(self, service_account_file: str | None = None, calendar_id: str | None = None):
        self.service_account_file = service_account_file or _resolve_service_account_path()
        self.calendar_id = calendar_id or GOOGLE_CALENDAR_ID
        self._service = None

    @property
    def service(self):
        if self._service is not None:
            return self._service

        if not self.calendar_id:
            raise BookingError("Booking is temporarily unavailable.")
        if not os.path.exists(self.service_account_file):
            logger.error("Service account key not found at %s", self.service_account_file)
            raise BookingError("Booking is temporarily unavailable.")

        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build

            credentials = Credentials.from_service_account_file(
                self.service_account_file, scopes=SCOPES
            )
            self._service = build("calendar", "v3", credentials=credentials, cache_discovery=False)
        except Exception as exc:
            logger.exception("Could not build the Calendar client: %s", exc)
            raise BookingError("Booking is temporarily unavailable.")
        return self._service

    def find_existing_visit(self, listing_id: str, start: str, end: str) -> dict | None:
        """Look for a visit already booked for this listing in the same window."""
        try:
            events = self.service.events().list(
                calendarId=self.calendar_id,
                timeMin=f"{start}+05:30",
                timeMax=f"{end}+05:30",
                singleEvents=True,
                maxResults=20,
            ).execute()
        except BookingError:
            raise
        except Exception as exc:
            logger.warning("Could not check for duplicate bookings: %s", exc)
            return None

        for event in events.get("items", []):
            if event.get("extendedProperties", {}).get("private", {}).get("listing_id") == listing_id:
                return event
        return None

    def book_visit(self, listing: dict, date: str, time_slot: str, user_email: str) -> dict:
        """Create the calendar event and invite the user. Returns booking details."""
        address = validate_email(user_email)
        start, end = resolve_time_slot(date, time_slot)
        slot_key = time_slot.strip().lower()

        existing = self.find_existing_visit(listing["id"], start, end)
        if existing:
            return {
                "confirmation_code": existing["id"],
                "calendar_link": existing.get("htmlLink"),
                "date": date,
                "time_slot": SLOT_LABELS[slot_key],
                "status": "already_booked",
                "message": (
                    f"You already have a visit booked for {listing['society_name']} "
                    f"on {date}. Would you like to reschedule instead?"
                ),
            }

        amenities = ", ".join(listing.get("amenities") or []) or "not listed"
        event = {
            "summary": f"Site Visit — {listing['society_name']}",
            "location": f"{listing['neighborhood']}, Bengaluru",
            "description": (
                f"Property: {listing['bedrooms']}BHK, ₹{listing['rent']:,}/month\n"
                f"Size: {listing.get('sqft', 'n/a')} sqft, {listing.get('furnishing', 'n/a')}\n"
                f"Amenities: {amenities}\n"
                f"Listing ID: {listing['id']}\n"
                f"Requested by: {address}"
            ),
            "start": {"dateTime": start, "timeZone": TIMEZONE},
            "end": {"dateTime": end, "timeZone": TIMEZONE},
            "attendees": [{"email": address}],
            "reminders": {"useDefault": True},
            "extendedProperties": {"private": {"listing_id": listing["id"]}},
        }

        result, attendee_invited = self._insert_event(event)

        return {
            "confirmation_code": result["id"],
            "calendar_link": result.get("htmlLink"),
            "date": date,
            "time_slot": SLOT_LABELS[slot_key],
            "start": start,
            "end": end,
            "status": "confirmed",
            # False on consumer Google accounts: a service account may not add
            # attendees without Domain-Wide Delegation, so the caller emails the
            # confirmation (with an .ics) itself.
            "attendee_invited": attendee_invited,
            "message": (
                f"Calendar invite sent to {address}"
                if attendee_invited
                else f"Visit added to the calendar; confirmation emailed to {address}"
            ),
        }

    def _insert_event(self, event: dict) -> tuple[dict, bool]:
        """Insert the event, retrying without attendees where that's disallowed."""
        try:
            result = self.service.events().insert(
                calendarId=self.calendar_id, body=event, sendUpdates="all"
            ).execute()
            return result, True
        except BookingError:
            raise
        except Exception as exc:
            message = str(exc)
            if "Domain-Wide Delegation" in message or "cannot invite attendees" in message:
                logger.info("Service account cannot invite attendees; booking without them")
            else:
                logger.exception("Calendar insert failed: %s", message)
                if "quota" in message.lower() or "rateLimit" in message:
                    raise BookingError(
                        "The booking service is busy right now — try again in a few minutes."
                    )
                raise BookingError("Booking is temporarily unavailable.")

        # Retry without attendees — the event still lands on the shared calendar.
        without_attendees = {k: v for k, v in event.items() if k != "attendees"}
        try:
            result = self.service.events().insert(
                calendarId=self.calendar_id, body=without_attendees, sendUpdates="none"
            ).execute()
        except Exception as exc:
            logger.exception("Calendar insert failed without attendees: %s", exc)
            raise BookingError("Booking is temporarily unavailable.")
        return result, False


# Shared instance; the API client inside is still built on first use.
calendar_booking = GoogleCalendarBooking()


def book_visit(listing: dict, date: str, time_slot: str, user_email: str) -> dict:
    """Module-level entry point used by the orchestrator's tool dispatch."""
    return calendar_booking.book_visit(listing, date, time_slot, user_email)
