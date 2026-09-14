"""
Email Sender
------------
Sends the shortlist as a PDF attachment over Gmail SMTP (TLS, app password).

If the PDF could not be rendered, the same document is sent as the HTML body
instead — the user still gets their shortlist.
"""

import logging
import smtplib
import ssl
from email.message import EmailMessage

from config import SMTP_HOST, SMTP_PASS, SMTP_PORT, SMTP_USER

logger = logging.getLogger(__name__)


def _ssl_context() -> ssl.SSLContext:
    """TLS context backed by certifi's CA bundle.

    Python installed from python.org on macOS ships without CA roots, so the
    default context fails STARTTLS with CERTIFICATE_VERIFY_FAILED.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()

SUBJECT = "Your Property Shortlist — Bengaluru"
ATTACHMENT_NAME = "property-shortlist.pdf"
BOOKING_SUBJECT = "Site visit confirmed — {society}"
ICS_NAME = "site-visit.ics"


class EmailError(Exception):
    """The email could not be sent. The message is safe to show the user."""


def _log_structure(kind: str, message: EmailMessage) -> None:
    """Record what is actually being transmitted, so a missing attachment is
    diagnosable from the log rather than from someone's inbox."""
    parts = [p.get_content_type() for p in message.walk()]
    attachments = [
        (p.get_filename(), len(p.get_payload(decode=True) or b""))
        for p in message.walk()
        if p.get_filename()
    ]
    logger.info(
        "%s email to %s | subject=%r | parts=%s | attachments=%s",
        kind, message["To"], message["Subject"], parts, attachments or "NONE",
    )


def _plain_text_summary(listings: list[dict]) -> str:
    lines = ["Your property shortlist", ""]
    for listing in listings:
        lines.append(
            f"- {listing['society_name']}, {listing['neighborhood']}: "
            f"Rs {listing['rent']:,}/month, {listing['bedrooms']}BHK"
            + (f", {listing['sqft']} sqft" if listing.get("sqft") else "")
        )
    lines += ["", "The attached PDF has the full details, neighborhood notes and sources."]
    return "\n".join(lines)


def configuration_status() -> tuple[bool, str]:
    """Whether email could be sent, and if not, which credential is missing.

    Checks configuration only — it does not open an SMTP connection, because
    /api/health must stay fast and a network failure is not a misconfiguration.
    A valid-looking credential that Gmail then rejects still fails at send time,
    and that is logged where it happens.
    """
    if not SMTP_USER:
        return False, "SMTP_USER is not set"
    if not SMTP_PASS:
        return False, "SMTP_PASS is not set"
    if len(SMTP_PASS) != 16:
        return False, (
            f"SMTP_PASS is {len(SMTP_PASS)} characters — a Gmail app password is 16. "
            "An account password will not work"
        )
    return True, "ok"


def send_shortlist_email(
    user_email: str,
    pdf_bytes: bytes | None,
    listings: list[dict],
    html_body: str | None = None,
) -> dict:
    """
    Email the shortlist to the user.

    Args:
        user_email: Recipient address.
        pdf_bytes: Rendered PDF, or None to fall back to an HTML-only email.
        listings: Used for the plain-text summary.
        html_body: HTML version of the shortlist, used when there's no PDF.
    """
    if not user_email:
        raise EmailError("I'll need your email address to send the shortlist.")
    if not listings:
        raise EmailError("Your shortlist is empty — add some listings first.")
    if not SMTP_USER or not SMTP_PASS:
        logger.error("SMTP credentials are not configured (SMTP_USER/SMTP_PASS)")
        raise EmailError("The email service is currently unavailable.")

    message = EmailMessage()
    message["Subject"] = SUBJECT
    message["From"] = SMTP_USER
    message["To"] = user_email
    message.set_content(_plain_text_summary(listings))

    if pdf_bytes:
        message.add_attachment(
            pdf_bytes, maintype="application", subtype="pdf", filename=ATTACHMENT_NAME
        )
    elif html_body:
        # No attachment available — send the document itself as the body.
        message.add_alternative(html_body, subtype="html")

    _log_structure("shortlist", message)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.starttls(context=_ssl_context())
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError:
        # Never log the password itself.
        logger.error("SMTP authentication failed for user %s", SMTP_USER)
        raise EmailError("The email service is currently unavailable.")
    except Exception as exc:
        logger.exception("Sending the shortlist email failed: %s", exc)
        raise EmailError("I couldn't send that email just now — please try again shortly.")

    return {
        "status": "sent",
        "recipient": user_email,
        "count": len(listings),
        "attachment": bool(pdf_bytes),
    }


# Alias matching the orchestrator's tool-dispatch name in the plan.
send = send_shortlist_email


def _ics_timestamp(iso_local: str) -> str:
    """Local Asia/Kolkata time (UTC+5:30) as a UTC iCalendar timestamp."""
    from datetime import datetime, timedelta

    return (datetime.fromisoformat(iso_local) - timedelta(hours=5, minutes=30)).strftime(
        "%Y%m%dT%H%M%SZ"
    )


def _build_ics(listing: dict, booking: dict, organizer: str) -> str:
    """A minimal VEVENT the recipient's calendar app can import."""
    from datetime import datetime

    def escape(text: str) -> str:
        return str(text).replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")

    return "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//AI Property Scout//Bengaluru//EN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{booking.get('confirmation_code', 'visit')}@property-scout",
        f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
        f"DTSTART:{_ics_timestamp(booking['start'])}",
        f"DTEND:{_ics_timestamp(booking['end'])}",
        f"SUMMARY:Site Visit — {escape(listing['society_name'])}",
        f"LOCATION:{escape(listing['neighborhood'])}\\, Bengaluru",
        "DESCRIPTION:" + escape(
            f"{listing['bedrooms']}BHK, Rs {listing['rent']:,}/month, "
            f"{listing.get('sqft', 'n/a')} sqft, {listing.get('furnishing', 'n/a')}. "
            f"Listing {listing['id']}."
        ),
        f"ORGANIZER;CN=AI Property Scout:mailto:{organizer}",
        "END:VEVENT",
        "END:VCALENDAR",
    ])


def send_booking_confirmation(user_email: str, listing: dict, booking: dict) -> dict:
    """
    Email the site-visit confirmation with an .ics attachment.

    Used when the Calendar API could not add the user as an attendee — a service
    account may only do that with Domain-Wide Delegation, which consumer Google
    accounts don't have. The event still exists on the shared calendar; this is
    how the user gets it into their own.
    """
    if not user_email:
        raise EmailError("I'll need your email address to send the confirmation.")
    if not SMTP_USER or not SMTP_PASS:
        logger.error("SMTP credentials are not configured (SMTP_USER/SMTP_PASS)")
        raise EmailError("The email service is currently unavailable.")

    message = EmailMessage()
    message["Subject"] = BOOKING_SUBJECT.format(society=listing["society_name"])
    message["From"] = SMTP_USER
    message["To"] = user_email
    message.set_content(
        f"Your site visit is confirmed.\n\n"
        f"Property : {listing['society_name']}, {listing['neighborhood']}\n"
        f"Details  : {listing['bedrooms']}BHK, Rs {listing['rent']:,}/month"
        + (f", {listing['sqft']} sqft" if listing.get("sqft") else "") + "\n"
        f"Date     : {booking['date']}\n"
        f"Time     : {booking['time_slot']} IST\n"
        f"Reference: {booking.get('confirmation_code', 'n/a')}\n\n"
        + (f"Calendar link: {booking['calendar_link']}\n\n" if booking.get("calendar_link") else "")
        + "The attached invite adds this visit to your own calendar."
    )

    try:
        message.add_attachment(
            _build_ics(listing, booking, SMTP_USER).encode("utf-8"),
            maintype="text", subtype="calendar", filename=ICS_NAME,
        )
    except Exception as exc:
        # Losing the .ics is not worth losing the confirmation email.
        logger.warning("Could not build the .ics attachment: %s", exc)

    _log_structure("booking", message)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
            smtp.starttls(context=_ssl_context())
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP authentication failed for user %s", SMTP_USER)
        raise EmailError("The email service is currently unavailable.")
    except Exception as exc:
        logger.exception("Sending the booking confirmation failed: %s", exc)
        raise EmailError("I couldn't email the confirmation just now.")

    return {"status": "sent", "recipient": user_email}
