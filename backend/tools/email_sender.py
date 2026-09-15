"""
Email Sender
------------
Sends the shortlist as a PDF attachment over Gmail SMTP (TLS, app password).

If the PDF could not be rendered, the same document is sent as the HTML body
instead — the user still gets their shortlist.
"""

import base64
import logging
import smtplib
import ssl

import httpx
from email.message import EmailMessage

from config import (
    BREVO_API_KEY,
    EMAIL_FROM,
    EMAIL_FROM_NAME,
    RESEND_API_KEY,
    SMTP_HOST,
    SMTP_PASS,
    SMTP_PORT,
    SMTP_USER,
)

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


def _active_provider() -> str:
    """Which transport to use. An HTTP provider wins when one is configured."""
    if BREVO_API_KEY:
        return "brevo"
    if RESEND_API_KEY:
        return "resend"
    return "smtp"


def _decompose(message) -> dict:
    """Pull an EmailMessage apart into the pieces an HTTP API wants.

    The messages are built once, as MIME, and both transports read from that —
    so the PDF and the .ics travel over HTTP exactly as they do over SMTP, and
    there is only one place where an email's content is decided.
    """
    text, html, attachments = "", "", []
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        disposition = part.get_content_disposition()
        content_type = part.get_content_type()
        if disposition == "attachment":
            payload = part.get_payload(decode=True) or b""
            attachments.append(
                {
                    "name": part.get_filename() or "attachment",
                    "content": base64.b64encode(payload).decode(),
                    "type": content_type,
                }
            )
        elif content_type == "text/plain" and not text:
            text = part.get_content()
        elif content_type == "text/html" and not html:
            html = part.get_content()
    return {
        "subject": message["Subject"],
        "to": message["To"],
        "text": text,
        "html": html,
        "attachments": attachments,
    }


def _deliver_http(message, provider: str) -> None:
    """Send over the provider's HTTPS API.

    Port 443 is never blocked, which is the whole point of this path existing.
    """
    parts = _decompose(message)
    if provider == "brevo":
        url = "https://api.brevo.com/v3/smtp/email"
        headers = {"api-key": BREVO_API_KEY, "content-type": "application/json"}
        payload = {
            "sender": {"email": EMAIL_FROM, "name": EMAIL_FROM_NAME},
            "to": [{"email": parts["to"]}],
            "subject": parts["subject"],
            "textContent": parts["text"] or " ",
        }
        if parts["html"]:
            payload["htmlContent"] = parts["html"]
        if parts["attachments"]:
            payload["attachment"] = [
                {"name": a["name"], "content": a["content"]} for a in parts["attachments"]
            ]
    else:
        url = "https://api.resend.com/emails"
        headers = {
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "from": f"{EMAIL_FROM_NAME} <{EMAIL_FROM}>",
            "to": [parts["to"]],
            "subject": parts["subject"],
            "text": parts["text"] or " ",
        }
        if parts["html"]:
            payload["html"] = parts["html"]
        if parts["attachments"]:
            payload["attachments"] = [
                {"filename": a["name"], "content": a["content"]} for a in parts["attachments"]
            ]

    response = httpx.post(url, json=payload, headers=headers, timeout=30.0)
    if response.status_code in (401, 403):
        logger.error("%s rejected the API key", provider)
        raise smtplib.SMTPAuthenticationError(response.status_code, b"provider rejected the key")
    if response.status_code >= 400:
        raise EmailError(
            f"{provider} refused the message ({response.status_code}): {response.text[:160]}"
        )


def _deliver(message) -> None:
    """Hand one message to the SMTP server.

    Port 465 means implicit TLS (SMTP_SSL); anything else means STARTTLS on a
    plain connection. Hosts that block outbound 587 to deter spam often leave
    465 open, so being able to switch with one variable is the difference
    between email working on a platform and not.

    Raises smtplib.SMTPAuthenticationError for a rejected credential and
    OSError/SMTPException for anything else, so callers can tell a wrong
    password from a blocked port.
    """
    provider = _active_provider()
    if provider != "smtp":
        _deliver_http(message, provider)
        return

    if SMTP_PORT == 465:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30, context=_ssl_context()) as smtp:
            smtp.login(SMTP_USER, SMTP_PASS)
            smtp.send_message(message)
        return
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.starttls(context=_ssl_context())
        smtp.login(SMTP_USER, SMTP_PASS)
        smtp.send_message(message)


def configuration_status() -> tuple[bool, str]:
    """Whether email could be sent, and if not, which credential is missing.

    Checks configuration only — it does not open an SMTP connection, because
    /api/health must stay fast and a network failure is not a misconfiguration.
    A valid-looking credential that Gmail then rejects still fails at send time,
    and that is logged where it happens.
    """
    provider = _active_provider()
    if provider != "smtp":
        if not EMAIL_FROM:
            return False, f"{provider} is configured but EMAIL_FROM (or SMTP_USER) is not set"
        # Brevo issues two credentials on the same settings page: an SMTP key
        # for its mail relay and an `xkeysib-` API key for the HTTPS endpoint
        # this uses. Only the second one works here, and picking the wrong one
        # fails with a 401 that reads like a bad key rather than a wrong kind.
        if provider == "brevo" and not BREVO_API_KEY.startswith("xkeysib-"):
            return False, (
                "BREVO_API_KEY does not start with 'xkeysib-' — this needs the v3 API "
                "key from the API Keys tab, not the SMTP key"
            )
        if provider == "resend" and not RESEND_API_KEY.startswith("re_"):
            return False, "RESEND_API_KEY does not start with 're_' — check the value"
        return True, f"ok (via {provider})"

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
        _deliver(message)
    except smtplib.SMTPAuthenticationError:
        # Never log the password itself.
        logger.error("SMTP authentication failed for user %s", SMTP_USER)
        raise EmailError("The email service is currently unavailable.")
    except (OSError, smtplib.SMTPServerDisconnected) as exc:
        logger.error(
            "Could not reach %s:%s — %s. If this is a timeout, the host is very "
            "likely blocking outbound SMTP; try SMTP_PORT=465.",
            SMTP_HOST, SMTP_PORT, exc,
        )
        raise EmailError("I couldn't send that email just now — please try again shortly.")
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
        _deliver(message)
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP authentication failed for user %s", SMTP_USER)
        raise EmailError("The email service is currently unavailable.")
    except (OSError, smtplib.SMTPServerDisconnected) as exc:
        logger.error(
            "Could not reach %s:%s — %s. If this is a timeout, the host is very "
            "likely blocking outbound SMTP; try SMTP_PORT=465.",
            SMTP_HOST, SMTP_PORT, exc,
        )
        raise EmailError("I couldn't email the confirmation just now.")
    except Exception as exc:
        logger.exception("Sending the booking confirmation failed: %s", exc)
        raise EmailError("I couldn't email the confirmation just now.")

    return {"status": "sent", "recipient": user_email}
