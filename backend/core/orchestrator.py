"""
LLM Orchestrator (Agent Core)
-----------------------------
Runs the Gemini function-calling loop: user message in, tool calls dispatched
against the listing DB / OpenStreetMap MCP / neighborhood RAG, grounded answer
plus a structured shortlist out.

The shortlist the UI renders is built from tool results, never parsed out of
the model's prose — so a listing can only reach the screen if the database
actually returned it.

Finding and narrowing are separate. `search_listings` finds listings (a new
search, or more to add). `update_shortlist` narrows what is already on screen
by listing ID, so an edit on any criterion — a field the search has no
parameter for, or data fetched from OpenStreetMap — needs no new filter code.
"""

import asyncio
import logging
import random
from typing import Any, Optional

from google import genai
from google.genai import types

from config import (
    ENRICH_MAX_LISTINGS,
    ENRICH_SHORTLIST_WITH_POIS,
    PREFETCH_POIS_IN_BACKGROUND,
    ENRICH_POI_TYPES,
    ENRICH_RADIUS_METERS,
    ENRICH_TIME_BUDGET_SECONDS,
    PREFETCH_TIME_BUDGET_SECONDS,
    GEMINI_API_KEY,
    GEMINI_MODEL,
    LLM_TEMPERATURE,
    LLM_THINKING_LEVEL,
    MAX_HISTORY_MESSAGES,
    LLM_MAX_ATTEMPTS,
    LLM_RETRY_BASE_SECONDS,
    MAX_TOOL_CALLS_PER_TURN,
    RAG_MIN_SIMILARITY,
)
from core.conversation import BookingSlot, ConversationStage, ConversationState
from core.prompt_templates import (
    FEW_SHOT_GUIDANCE,
    GEMINI_TOOLS,
    SYSTEM_PROMPT,
    build_state_block,
)
from tools import listing_search
from tools import rag_retriever
from tools.mcp_client import osm_mcp

logger = logging.getLogger(__name__)

# Neighborhood guides are static, so snapshot lookups are cached for the
# process lifetime: (neighborhood, aspect) -> retrieval result.
_SNAPSHOT_CACHE: dict[tuple[str, str], dict] = {}

# The snapshot shown on each listing card, mapped to the guide section it is
# taken from verbatim. Fetched by section rather than by similarity: a semantic
# query for "overview" kept returning the Safety chunk.
SNAPSHOT_SECTIONS = {
    "summary": "Overview",
    "safety": "Safety",
    "transit": "Transit & Connectivity",
}

FALLBACK_ERROR_TEXT = (
    "Sorry — I hit a problem reaching my property data just then. "
    "Could you say that again?"
)
QUOTA_ERROR_TEXT = (
    "I've used up my AI quota for now, so I can't think about new requests "
    "for a moment. Your shortlist is still on screen — please try again shortly."
)
BUSY_ERROR_TEXT = (
    "The AI service is busy at the moment and didn't answer in time. "
    "Your shortlist is still on screen — please try that again."
)


def _is_quota_error(exc: Exception) -> bool:
    """Daily/per-minute quota exhaustion, as opposed to a transient blip."""
    text = str(exc)
    return "RESOURCE_EXHAUSTED" in text or "429" in text


def _is_transient_error(exc: Exception) -> bool:
    """Server-side conditions that a later identical request may well survive.

    "This model is currently experiencing high demand" arrives as 503
    UNAVAILABLE and is the common one — it says nothing about the request, so
    retrying is the right answer where retrying a 400 would only waste quota.
    """
    text = str(exc)
    return any(
        marker in text
        for marker in ("UNAVAILABLE", "503", "INTERNAL", "500", "DEADLINE_EXCEEDED", "504")
    )


class Orchestrator:
    """Gemini agent loop with tool dispatch and shortlist assembly."""

    def __init__(self, client: Optional[genai.Client] = None, model: str = GEMINI_MODEL):
        self._client = client
        self.model = model

    # ── LLM plumbing ─────────────────────────────────────────────────────
    @property
    def client(self) -> genai.Client:
        """Create the Gemini client on first use so import never needs a key."""
        if self._client is None:
            if not GEMINI_API_KEY:
                raise RuntimeError("GEMINI_API_KEY is not set in backend/.env")
            self._client = genai.Client(api_key=GEMINI_API_KEY)
        return self._client

    def _config(self, state_block: str, with_tools: bool = True) -> types.GenerateContentConfig:
        return types.GenerateContentConfig(
            system_instruction="\n\n".join([SYSTEM_PROMPT, FEW_SHOT_GUIDANCE, state_block]),
            tools=GEMINI_TOOLS if with_tools else None,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            temperature=LLM_TEMPERATURE,
            thinking_config=types.ThinkingConfig(thinking_level=LLM_THINKING_LEVEL),
        )

    async def _generate(self, contents: list, config: types.GenerateContentConfig):
        """Call Gemini off the event loop, backing off through transient failures.

        A 503 "high demand" is the model being busy, not the request being
        wrong, and one retry 1.5s later is usually still inside the same spike —
        which surfaced to users as a flat failure every other turn. Transient
        errors now get the full attempt budget with exponential backoff and
        jitter, so concurrent turns don't retry in lockstep.
        """
        last_error: Optional[Exception] = None
        for attempt in range(LLM_MAX_ATTEMPTS):
            try:
                return await asyncio.to_thread(
                    self.client.models.generate_content,
                    model=self.model,
                    contents=contents,
                    config=config,
                )
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Gemini call failed (attempt %d/%d): %s",
                    attempt + 1, LLM_MAX_ATTEMPTS, exc,
                )
                # Quota errors carry a retry delay measured in minutes — a retry
                # here just burns another request against the same limit.
                if _is_quota_error(exc):
                    break
                last_attempt = attempt == LLM_MAX_ATTEMPTS - 1
                # An unrecognised error gets the one retry it always had; only a
                # known-transient one is worth waiting out.
                if last_attempt or not (_is_transient_error(exc) or attempt == 0):
                    break
                delay = LLM_RETRY_BASE_SECONDS * (2 ** attempt)
                await asyncio.sleep(delay + random.uniform(0, delay / 2))
        raise last_error  # type: ignore[misc]

    @staticmethod
    def _build_contents(session: ConversationState, text: str) -> list[types.Content]:
        """History (trimmed) plus the new user message, in Gemini's format."""
        history = session.conversation_history[-MAX_HISTORY_MESSAGES:]
        contents = [
            types.Content(
                role="model" if msg.role == "assistant" else "user",
                parts=[types.Part.from_text(text=msg.text)],
            )
            for msg in history
        ]
        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=text)]))
        return contents

    # ── tool dispatch ────────────────────────────────────────────────────
    async def _dispatch(self, name: str, args: dict, turn: dict, session=None) -> dict:
        """Run one tool call and record what it produced for this turn."""
        logger.info("tool call: %s(%s)", name, args)
        turn["tool_calls"].append({"name": name, "args": args})

        if name == "search_listings":
            return self._tool_search_listings(args, turn, session)
        if name == "update_shortlist":
            return self._tool_update_shortlist(args, turn, session)
        if name == "query_openstreetmap":
            return await self._tool_query_osm(args, turn)
        if name == "retrieve_neighborhood_info":
            return self._tool_retrieve_neighborhood(args, turn)
        if name == "book_site_visit":
            return self._tool_book_visit(args, turn)
        if name == "generate_shortlist_pdf":
            return self._tool_generate_pdf(args, turn, session)

        return {"error": f"Unknown tool '{name}'."}

    def _tool_search_listings(self, args: dict, turn: dict, session=None) -> dict:
        listings = listing_search.search_listings(
            max_budget=args.get("max_budget"),
            min_bedrooms=args.get("min_bedrooms"),
            neighborhood=args.get("neighborhood"),
            amenities=args.get("amenities"),
            exclude_ids=args.get("exclude_ids"),
            furnishing=args.get("furnishing"),
        )
        mode = (args.get("mode") or "replace").lower()
        on_screen = [l["id"] for l in session.shortlist] if session else []
        dropped_ids = {d["listing_id"] for d in session.dropped} if session else set()

        if mode == "append":
            # Adding never repeats a card on screen or revives one the user dropped.
            listings = [
                l for l in listings if l["id"] not in on_screen and l["id"] not in dropped_ids
            ]
        limit = args.get("limit")
        if limit:
            listings = listings[: max(int(limit), 0)]

        turn["search_args"] = args
        turn["search_mode"] = mode
        turn["search_results"] = listings
        if mode != "append":
            turn["removed"] = {}  # a fresh result set supersedes earlier removals

        found = [l["id"] for l in listings]
        effect: dict[str, Any] = {
            "added_ids": [i for i in found if i not in on_screen],
            "removed_ids": [] if mode == "append" else [i for i in on_screen if i not in found],
        }
        revived = sorted(dropped_ids & set(found))
        if revived:
            effect["previously_dropped_ids"] = revived
            effect["note"] = (
                "These results bring back listings the user dropped earlier. Unless "
                "they asked to see those again, search again with them in exclude_ids."
            )

        payload: dict[str, Any] = {
            "count": len(listings),
            "listings": listings,
            "filters_applied": {k: v for k, v in args.items() if v not in (None, [], "")},
            "shortlist_effect": effect,
        }
        if not listings:
            # Give the model the facts it needs to suggest a specific relaxation
            # instead of a vague "try something else".
            everything = listing_search.get_all_listings()
            if everything:
                payload["dataset_bounds"] = {
                    "cheapest_rent": min(x["rent"] for x in everything),
                    "priciest_rent": max(x["rent"] for x in everything),
                    "bedroom_options": sorted({x["bedrooms"] for x in everything}),
                    "neighborhoods": sorted({x["neighborhood"] for x in everything}),
                }
        return payload

    def _tool_update_shortlist(self, args: dict, turn: dict, session=None) -> dict:
        """Drop listings from the shortlist by ID, each with the reason it failed.

        The model judges which listings fail the user's criterion; this only
        guarantees the edit is real and contained — IDs must be on screen, every
        removal carries a reason, and nothing else on screen changes.
        """
        on_screen = _pending_shortlist_ids(session, turn)
        accepted, not_on_screen, missing_reason = [], [], []
        for item in args.get("remove") or []:
            listing_id = str((item or {}).get("listing_id") or "").strip()
            reason = str((item or {}).get("reason") or "").strip()
            if listing_id not in on_screen:
                not_on_screen.append(listing_id)
            elif not reason:
                missing_reason.append(listing_id)
            else:
                turn["removed"][listing_id] = reason
                accepted.append(listing_id)

        remaining = [i for i in on_screen if i not in turn["removed"]]
        result: dict[str, Any] = {"removed_ids": accepted, "remaining_ids": remaining}
        if not_on_screen:
            result["not_on_screen"] = not_on_screen
            result["note"] = (
                "Those IDs are not on the user's screen, so nothing was removed for "
                "them. Use IDs from the CURRENT SHORTLIST."
            )
        if missing_reason:
            result["missing_reason"] = missing_reason
            result["error"] = "Every removal needs a reason. Call again with one for these."
        if not remaining:
            result["shortlist_now_empty"] = True
        return result

    async def _tool_query_osm(self, args: dict, turn: dict) -> dict:
        lat, lng = _resolve_lookup_point(args)
        if not _valid_coords(lat, lng):
            return {
                "error": "That listing has no usable coordinates, so I can't check what's nearby.",
                "unavailable": args.get("poi_types") or ENRICH_POI_TYPES,
            }

        result = await osm_mcp.query_nearby(
            lat=float(lat),
            lng=float(lng),
            radius_meters=int(args.get("radius_meters") or ENRICH_RADIUS_METERS),
            poi_types=args.get("poi_types") or ENRICH_POI_TYPES,
        )
        turn["poi_results"].append(result)
        return result

    def _tool_retrieve_neighborhood(self, args: dict, turn: dict) -> dict:
        result = rag_retriever.retrieve_neighborhood_info(
            neighborhood=args.get("neighborhood"),
            query=args.get("query") or "",
            min_similarity=RAG_MIN_SIMILARITY,
        )
        turn["rag_results"].append({"query": args.get("query"), "result": result})

        if not result["has_data"]:
            return {
                "has_data": False,
                "message": (
                    f"No neighborhood data for '{args.get('neighborhood')}'. "
                    f"Covered areas: {', '.join(result['known_neighborhoods'])}. "
                    "Tell the user you don't have this — do not answer from general knowledge."
                ),
                "known_neighborhoods": result["known_neighborhoods"],
            }
        return {
            "has_data": True,
            "neighborhood": result["neighborhood"],
            "chunks": [
                {
                    "text": c["text"],
                    "section": c["section_title"],
                    "source_url": c["source_url"],
                    "similarity": c["similarity_score"],
                }
                for c in result["chunks"]
            ],
        }

    def _tool_book_visit(self, args: dict, turn: dict) -> dict:
        from tools.google_calendar import BookingError, book_visit

        listing = listing_search.get_listing_by_id(args.get("listing_id", ""))
        if listing is None:
            return {"error": f"No listing with ID '{args.get('listing_id')}'."}

        try:
            booking = book_visit(
                listing=listing,
                date=args.get("preferred_date"),
                time_slot=args.get("preferred_time_slot"),
                user_email=args.get("user_email"),
            )
        except BookingError as exc:
            # BookingError messages are written to be spoken to the user.
            return {"error": str(exc)}
        except Exception as exc:
            logger.exception("Booking failed: %s", exc)
            return {"error": "Booking is temporarily unavailable."}

        if not booking.get("attendee_invited"):
            # Service accounts can't invite attendees without Domain-Wide
            # Delegation — email the invite ourselves so the user still gets it.
            from tools.email_sender import EmailError, send_booking_confirmation

            try:
                send_booking_confirmation(args.get("user_email"), listing, booking)
                booking["invite_emailed"] = True
            except EmailError as exc:
                logger.warning("Booked, but the confirmation email failed: %s", exc)
                booking["invite_emailed"] = False

        turn["booking"] = {**booking, "listing_id": listing["id"],
                           "user_email": args.get("user_email")}
        return booking

    def _tool_generate_pdf(self, args: dict, turn: dict, session=None) -> dict:
        from tools.email_sender import EmailError, send_shortlist_email
        from tools.pdf_generator import (
            PDFGenerationError,
            generate_shortlist_pdf,
            render_shortlist_html,
        )

        ids = args.get("shortlist_ids") or []
        # Prefer the enriched cards on screen — they carry POIs, match reasons
        # and neighborhood snapshots that the bare DB rows don't have.
        on_screen = {l["id"]: l for l in (session.shortlist if session else [])}
        listings = [
            on_screen.get(i) or listing_search.get_listing_by_id(i) for i in ids
        ]
        listings = [l for l in listings if l]
        if not listings:
            return {"error": "None of those listing IDs exist, so there's nothing to send."}

        snapshots = dict(turn.get("snapshots") or {})
        for listing in listings:
            snapshot = listing.get("neighborhood_snapshot")
            if snapshot and listing["neighborhood"] not in snapshots:
                snapshots[listing["neighborhood"]] = {
                    **snapshot, "sources": listing.get("sources", []),
                }

        pdf_bytes, html_body = None, None
        try:
            pdf_bytes = generate_shortlist_pdf(listings, snapshots)
        except PDFGenerationError as exc:
            logger.warning("Falling back to an HTML email: %s", exc)
            html_body = render_shortlist_html(listings, snapshots)

        try:
            result = send_shortlist_email(
                args.get("user_email"), pdf_bytes, listings, html_body
            )
        except EmailError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            logger.exception("Sending the shortlist failed: %s", exc)
            return {"error": "I couldn't send that email just now."}

        turn["pdf_sent_to"] = args.get("user_email")
        return {**result, "attachment_included": bool(pdf_bytes)}

    # ── enrichment ───────────────────────────────────────────────────────
    def _neighborhood_snapshot(self, neighborhood: str) -> dict:
        """Cited summary/safety/transit lines for a neighborhood, from RAG only."""
        snapshot: dict[str, Any] = {"neighborhood": neighborhood, "sources": []}
        for aspect, section in SNAPSHOT_SECTIONS.items():
            key = (neighborhood, aspect)
            if key not in _SNAPSHOT_CACHE:
                _SNAPSHOT_CACHE[key] = rag_retriever.get_section(neighborhood, section)
            chunk = _SNAPSHOT_CACHE[key]
            snapshot[aspect] = chunk["text"] if chunk else None
            if chunk:
                snapshot["sources"].append(
                    {
                        "url": chunk["source_url"],
                        "section": chunk["section_title"],
                        "neighborhood": neighborhood,
                        "used_for": f"{aspect} of {neighborhood}",
                    }
                )
        return snapshot

    async def _enrich_shortlist(self, listings: list[dict], turn: dict) -> list[dict]:
        """Attach POIs, neighborhood snapshots and match reasons to the shortlist."""
        if not listings:
            return []

        # Neighborhood snapshots (RAG) for every area represented.
        snapshots = {
            n: self._neighborhood_snapshot(n)
            for n in sorted({l["neighborhood"] for l in listings})
        }
        turn["snapshots"] = snapshots

        # POIs come from a live Overpass round trip per listing per type, which
        # is far too slow to block a spoken reply on. Cached points are folded
        # in for free; the rest are warmed in the background and served by
        # GET /api/listings/{id}/nearby when the user opens a card.
        targets = [
            (i, l) for i, l in enumerate(listings[:ENRICH_MAX_LISTINGS])
            if _valid_coords(l.get("latitude"), l.get("longitude"))
        ]
        points = [(l["latitude"], l["longitude"]) for _, l in targets]
        poi_by_index: dict[int, Optional[dict]] = {}

        if points and ENRICH_SHORTLIST_WITH_POIS:
            results = await osm_mcp.query_nearby_batch(
                points, radius_meters=ENRICH_RADIUS_METERS, poi_types=ENRICH_POI_TYPES,
                timeout_seconds=ENRICH_TIME_BUDGET_SECONDS,
            )
            for (index, _), result in zip(targets, results):
                poi_by_index[index] = result
                if result:
                    turn["poi_results"].append(result)
        elif points:
            for index, listing in targets:
                cached = osm_mcp.cached_result(
                    listing["latitude"], listing["longitude"],
                    ENRICH_RADIUS_METERS, ENRICH_POI_TYPES,
                )
                if cached:
                    poi_by_index[index] = cached
                    turn["poi_results"].append(cached)
            if PREFETCH_POIS_IN_BACKGROUND:
                _prefetch_pois(points)

        preferences = turn.get("search_args") or {}
        enriched = []
        for index, listing in enumerate(listings):
            snapshot = snapshots.get(listing["neighborhood"], {})
            pois = poi_by_index.get(index)
            enriched.append(
                {
                    **listing,
                    "nearby_pois": _poi_summary(pois),
                    "neighborhood_snapshot": {
                        "summary": snapshot.get("summary"),
                        "safety": snapshot.get("safety"),
                        "transit": snapshot.get("transit"),
                    },
                    "match_reasons": _match_reasons(listing, preferences),
                    "sources": snapshot.get("sources", []),
                }
            )
        return enriched

    # ── main loop ────────────────────────────────────────────────────────
    async def process(self, session: ConversationState, text: str) -> dict:
        """Run one conversational turn and return the full API response payload."""
        turn: dict[str, Any] = {
            "tool_calls": [], "search_args": None, "search_mode": "replace",
            "search_results": None, "removed": {},
            "rag_results": [], "poi_results": [], "snapshots": {},
            "booking": None, "pdf_sent_to": None,
        }

        state_block = build_state_block(
            preferences=vars(session.preferences),
            shortlist=session.shortlist,
            dropped=session.dropped,
            clarification_count=session.clarification_count,
            booking=vars(session.booking) if session.booking else None,
        )
        contents = self._build_contents(session, text)
        config = self._config(state_block)

        response_text = ""
        try:
            for _ in range(MAX_TOOL_CALLS_PER_TURN):
                response = await self._generate(contents, config)
                candidate = (response.candidates or [None])[0]
                parts = (candidate.content.parts if candidate and candidate.content else None) or []
                calls = [p.function_call for p in parts if p.function_call]

                if not calls:
                    response_text = (response.text or "").strip()
                    break

                contents.append(candidate.content)
                function_responses = []
                for call in calls:
                    result = await self._dispatch(
                        call.name, dict(call.args or {}), turn, session
                    )
                    function_responses.append(
                        types.Part.from_function_response(name=call.name, response=result)
                    )
                contents.append(types.Content(role="user", parts=function_responses))
            else:
                # Tool budget spent — force a spoken answer with tools switched off.
                logger.warning("Tool-call budget exhausted; forcing a text response")
                final = await self._generate(contents, self._config(state_block, with_tools=False))
                response_text = (final.text or "").strip()
        except Exception as exc:
            logger.exception("Orchestrator turn failed: %s", exc)
            if _is_quota_error(exc):
                message = QUOTA_ERROR_TEXT
            elif _is_transient_error(exc):
                message = BUSY_ERROR_TEXT
            else:
                message = FALLBACK_ERROR_TEXT
            session.add_user_message(text)
            session.add_assistant_message(message)
            return self._response(session, message, error=str(exc))

        if not response_text:
            response_text = FALLBACK_ERROR_TEXT

        # A search replaces the shortlist, unless the user asked to add to it.
        if turn["search_results"] is not None:
            appending = turn.get("search_mode") == "append"
            if appending:
                existing = {l["id"] for l in session.shortlist}
                fresh = [l for l in turn["search_results"] if l["id"] not in existing]
                session.shortlist = session.shortlist + await self._enrich_shortlist(fresh, turn)
            else:
                session.shortlist = await self._enrich_shortlist(turn["search_results"], turn)
                # Appending adds options on new criteria; it does not redefine what
                # the user is looking for, so preferences stand.
                _apply_preferences(session, turn["search_args"] or {})
        elif turn["poi_results"]:
            _attach_pois_to_shortlist(session.shortlist, turn["poi_results"])

        on_screen = {l["id"] for l in session.shortlist}
        # A listing that is back on screen is no longer dropped.
        session.dropped = [d for d in session.dropped if d["listing_id"] not in on_screen]
        if turn["removed"]:
            for listing in session.shortlist:
                if listing["id"] in turn["removed"]:
                    session.dropped.append({
                        "listing_id": listing["id"],
                        "society_name": listing.get("society_name"),
                        "neighborhood": listing.get("neighborhood"),
                        "rent": listing.get("rent"),
                        "bedrooms": listing.get("bedrooms"),
                        "reason": turn["removed"][listing["id"]],
                    })
            session.shortlist = [l for l in session.shortlist if l["id"] not in turn["removed"]]

        if turn["booking"]:
            session.booking = BookingSlot(
                listing_id=turn["booking"].get("listing_id", ""),
                date=turn["booking"].get("date", ""),
                time_slot=turn["booking"].get("time_slot", ""),
                user_email=turn["booking"].get("user_email", ""),
                confirmation_code=turn["booking"].get("confirmation_code"),
                calendar_link=turn["booking"].get("calendar_link"),
            )

        _merge_sources(session, turn)

        # A question that produced no shortlist is a clarifying question.
        if (
            turn["search_results"] is None and not turn["removed"]
            and "?" in response_text and not session.shortlist
        ):
            session.clarification_count += 1

        session.add_user_message(text)
        session.add_assistant_message(response_text)
        session.stage = _next_stage(session)
        return self._response(session, response_text, tool_calls=turn["tool_calls"])

    def _response(self, session: ConversationState, text: str, **extra) -> dict:
        return {
            "session_id": session.session_id,
            "response_text": text,
            "state": session.stage.value,
            "shortlist": session.shortlist,
            "sources": session.sources,
            "dropped": session.dropped,
            "booking": vars(session.booking) if session.booking else None,
            "preferences": vars(session.preferences),
            **extra,
        }


# ─── helpers ─────────────────────────────────────────────────────────────────

def _resolve_lookup_point(args: dict) -> tuple:
    """Resolve a POI lookup to a real listing's coordinates where possible.

    The model often rounds coordinates when echoing them back (12.935 for
    12.9352), which both misses the POI cache and shifts the search centre. A
    listing_id, or a point within ~300m of a known listing, is snapped to that
    listing's exact location.
    """
    listing_id = args.get("listing_id")
    if listing_id:
        listing = listing_search.get_listing_by_id(str(listing_id))
        if listing:
            return listing["latitude"], listing["longitude"]

    lat, lng = args.get("latitude"), args.get("longitude")
    if not _valid_coords(lat, lng):
        return lat, lng

    lat, lng = float(lat), float(lng)
    nearest, best = None, None
    for listing in listing_search.get_all_listings():
        if not _valid_coords(listing.get("latitude"), listing.get("longitude")):
            continue
        # Degrees are fine for a nearest-of-15 comparison at city scale.
        distance = ((listing["latitude"] - lat) ** 2 + (listing["longitude"] - lng) ** 2) ** 0.5
        if best is None or distance < best:
            nearest, best = listing, distance
    if nearest is not None and best is not None and best < 0.003:  # ~300m
        return nearest["latitude"], nearest["longitude"]
    return lat, lng


def _valid_coords(lat, lng) -> bool:
    """0,0 and nulls are placeholders, not locations in Bengaluru."""
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return False
    return not (lat == 0 and lng == 0) and -90 <= lat <= 90 and -180 <= lng <= 180


def _poi_summary(result: Optional[dict]) -> Optional[dict]:
    """Trim an MCP result down to what a listing card shows."""
    if not result:
        return None
    summary: dict[str, Any] = {
        "unavailable": result.get("unavailable", []),
        "error": result.get("error"),
        "radius_meters": result.get("radius_meters"),
        "source": result.get("source"),
    }
    for key in ("metro_stations", "groceries", "hospitals", "restaurants",
                "parks", "bus_stops", "pharmacies", "cafes", "schools", "gyms"):
        if key in result:
            summary[key] = _dedupe_pois(result[key])[:3]
    return summary


def _dedupe_pois(pois: list[dict]) -> list[dict]:
    """OSM often carries the same place as several elements."""
    seen = set()
    unique = []
    for poi in pois or []:
        key = (poi.get("name"), round(poi.get("lat") or 0, 4), round(poi.get("lng") or 0, 4))
        if key in seen:
            continue
        seen.add(key)
        unique.append(poi)
    return unique


def _pending_shortlist_ids(session: Optional[ConversationState], turn: dict) -> list[str]:
    """IDs on screen once this turn's search so far lands, before removals."""
    current = [l["id"] for l in session.shortlist] if session else []
    if turn["search_results"] is None:
        return current
    found = [l["id"] for l in turn["search_results"]]
    if turn["search_mode"] == "append":
        return current + [i for i in found if i not in current]
    return found


def _match_reasons(listing: dict, filters: dict) -> list[str]:
    """Why this listing matched — computed from the data, not from the model."""
    reasons = []
    budget = filters.get("max_budget")
    if budget:
        reasons.append(f"Within ₹{int(budget):,} budget")
    bedrooms = filters.get("min_bedrooms")
    if bedrooms and listing.get("bedrooms", 0) >= bedrooms:
        reasons.append(f"{listing['bedrooms']}BHK match")
    if filters.get("neighborhood"):
        reasons.append(f"In {listing.get('neighborhood')}")
    listing_amenities = {a.lower() for a in listing.get("amenities", [])}
    for amenity in filters.get("amenities") or []:
        if amenity.lower() in listing_amenities:
            reasons.append(f"Has {amenity}")
    if listing.get("furnishing"):
        reasons.append(str(listing["furnishing"]).replace("-", " ").capitalize())
    return reasons


def _apply_preferences(session: ConversationState, filters: dict) -> None:
    """Mirror the model's search filters into the session's preference record."""
    preferences = session.preferences
    if filters.get("max_budget") is not None:
        preferences.budget_max = int(filters["max_budget"])
    if filters.get("min_bedrooms") is not None:
        preferences.bedrooms = int(filters["min_bedrooms"])
    if filters.get("neighborhood"):
        preferences.neighborhood = filters["neighborhood"]
    if filters.get("amenities") is not None:
        preferences.must_have_amenities = list(filters["amenities"])


def _attach_pois_to_shortlist(shortlist: list[dict], poi_results: list[dict]) -> None:
    """Fold on-demand POI lookups into the matching listing card.

    A lookup for one type (just metro stations, say) is merged into what the card
    already shows rather than replacing it, and a failed lookup never overwrites
    data that came back earlier.
    """
    for result in poi_results:
        for listing in shortlist:
            if (
                _valid_coords(listing.get("latitude"), listing.get("longitude"))
                and round(listing["latitude"], 4) == round(result.get("lat", 0), 4)
                and round(listing["longitude"], 4) == round(result.get("lng", 0), 4)
            ):
                existing = listing.get("nearby_pois")
                summary = _poi_summary(result)
                if existing and not existing.get("error"):
                    if summary.get("error"):
                        continue
                    summary = {**existing, **summary}
                listing["nearby_pois"] = summary


def _merge_sources(session: ConversationState, turn: dict) -> None:
    """Collect every citation used this turn, de-duplicated, for the Sources panel."""
    existing = {(s.get("url"), s.get("section")) for s in session.sources}

    def add(url, section, neighborhood, used_for):
        if not url or (url, section) in existing:
            return
        existing.add((url, section))
        session.sources.append({
            "url": url,
            "title": f"{neighborhood} — {section}" if neighborhood else section,
            "section": section,
            "neighborhood": neighborhood,
            "used_for": used_for,
        })

    for entry in turn["rag_results"]:
        result = entry["result"]
        for chunk in result.get("chunks", []):
            add(chunk["source_url"], chunk["section_title"], chunk["neighborhood"],
                f"Answering: {entry['query']}")

    for snapshot in turn["snapshots"].values():
        for source in snapshot.get("sources", []):
            add(source["url"], source["section"], source["neighborhood"], source["used_for"])

    if turn["poi_results"]:
        add("https://www.openstreetmap.org/copyright", "OpenStreetMap via MCP", None,
            "Nearby metro stations, groceries and hospitals")


def _next_stage(session: ConversationState) -> ConversationStage:
    if session.booking:
        return ConversationStage.VISIT_BOOKED
    if session.shortlist:
        return ConversationStage.SHORTLIST_READY
    preferences = session.preferences
    if any([preferences.budget_max, preferences.bedrooms, preferences.neighborhood,
            preferences.must_have_amenities]):
        return ConversationStage.COLLECTING
    if session.conversation_history:
        return ConversationStage.COLLECTING
    return ConversationStage.GREETING


def _prefetch_pois(points: list[tuple[float, float]]) -> None:
    """Warm the POI cache in the background; failures are logged, never raised."""
    async def warm():
        try:
            await osm_mcp.query_nearby_batch(
                points, radius_meters=ENRICH_RADIUS_METERS, poi_types=ENRICH_POI_TYPES,
                timeout_seconds=PREFETCH_TIME_BUDGET_SECONDS,
                # A warm-up that runs out of time must not disable the lookup the
                # user is about to ask for by hand.
                trip_circuit=False, label="prefetch",
            )
        except Exception as exc:
            logger.warning("Background POI prefetch failed: %s", exc)

    try:
        task = asyncio.create_task(warm())
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
    except RuntimeError:
        logger.debug("No running loop for POI prefetch — skipping")


# Strong references so prefetch tasks are not garbage collected mid-flight.
_BACKGROUND_TASKS: set[asyncio.Task] = set()


# Shared instance used by the chat route.
orchestrator = Orchestrator()
