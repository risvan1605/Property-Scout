"""
Shortlist PDF Generator
-----------------------
Renders the shortlist as styled HTML with Jinja2, then converts it to PDF with
WeasyPrint.

WeasyPrint needs native libraries (pango, glib, cairo). On macOS those live in
Homebrew's lib directory, which the dynamic loader doesn't search by default —
handled below. If they're missing entirely, `generate_shortlist_pdf` raises and
the caller falls back to an HTML email with no attachment.
"""

import logging
import os
import platform
from datetime import date

from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATES_DIR = os.path.join(BACKEND_DIR, "templates")
TEMPLATE_NAME = "shortlist_email.html"

# Never put more than the whole dataset in one document.
MAX_LISTINGS_PER_PDF = 15

_HOMEBREW_LIB_DIRS = ("/opt/homebrew/lib", "/usr/local/lib")


class PDFGenerationError(RuntimeError):
    """The PDF could not be rendered; send the HTML instead."""


def _ensure_native_library_path() -> None:
    """Put Homebrew's lib directory on the macOS dynamic loader search path."""
    if platform.system() != "Darwin":
        return
    existing = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
    paths = [p for p in existing.split(":") if p]
    for lib_dir in _HOMEBREW_LIB_DIRS:
        if os.path.isdir(lib_dir) and lib_dir not in paths:
            paths.append(lib_dir)
    if paths:
        os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join(paths)


def _jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["rupees"] = lambda value: f"{int(value):,}" if value is not None else "—"
    return env


def render_shortlist_html(listings: list[dict], snapshots: dict | None = None) -> str:
    """Render the shortlist document as standalone HTML."""
    snapshots = snapshots or {}
    listings = listings[:MAX_LISTINGS_PER_PDF]

    # One citation list for the whole document, de-duplicated.
    seen, sources = set(), []
    for snapshot in snapshots.values():
        for source in snapshot.get("sources", []):
            key = (source.get("url"), source.get("section"))
            if source.get("url") and key not in seen:
                seen.add(key)
                sources.append(source)

    return _jinja_env().get_template(TEMPLATE_NAME).render(
        listings=listings,
        snapshots=snapshots,
        sources=sources,
        generated_on=date.today().strftime("%d %B %Y"),
        neighborhoods=sorted({l["neighborhood"] for l in listings}) if listings else [],
    )


def generate_shortlist_pdf(listings: list[dict], snapshots: dict | None = None) -> bytes:
    """
    Render the shortlist to PDF bytes.

    Raises PDFGenerationError if WeasyPrint or its native libraries are missing,
    so the caller can still email the HTML version.
    """
    if not listings:
        raise PDFGenerationError("The shortlist is empty, so there's nothing to put in a PDF.")

    html = render_shortlist_html(listings, snapshots)
    _ensure_native_library_path()

    try:
        from weasyprint import HTML
    except Exception as exc:  # missing native libs surface as OSError, not ImportError
        logger.warning("WeasyPrint unavailable: %s", exc)
        raise PDFGenerationError(f"PDF rendering is unavailable: {exc}")

    try:
        return HTML(string=html, base_url=TEMPLATES_DIR).write_pdf()
    except Exception as exc:
        logger.exception("PDF rendering failed: %s", exc)
        raise PDFGenerationError(f"PDF rendering failed: {exc}")


# Alias matching the orchestrator's tool-dispatch name in the plan.
generate = generate_shortlist_pdf
