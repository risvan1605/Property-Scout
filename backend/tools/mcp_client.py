"""
OpenStreetMap MCP Client
------------------------
Wraps the OpenStreetMap MCP server (spawned over stdio via `npx`) so the
orchestrator can ask "what's near this listing?" and get real POI data.

The server exposes `openstreetmap_query_nearby`, which takes exactly one tag
filter per call, so one call is made per requested POI type.
"""

import asyncio
import json
import logging
import os
import time
from collections import OrderedDict
from typing import Optional

from config import (
    MCP_CIRCUIT_BREAK_SECONDS,
    MCP_MAX_POI_TYPES,
    OSM_OVERPASS_ENDPOINTS,
)

logger = logging.getLogger(__name__)

MCP_SERVER_COMMAND = "npx"
MCP_SERVER_ARGS = ["-y", "@cyanheads/openstreetmap-mcp-server"]
NEARBY_TOOL = "openstreetmap_query_nearby"

# POI category → the OSM tag filter the MCP server expects.
POI_TAG_FILTERS: dict[str, dict] = {
    "metro_station": {"tag_key": "railway", "tag_value": "station"},
    "bus_stop": {"tag_key": "highway", "tag_value": "bus_stop"},
    "grocery": {"tag_key": "shop", "tag_value": "supermarket"},
    "hospital": {"amenity": "hospital"},
    "pharmacy": {"amenity": "pharmacy"},
    "restaurant": {"amenity": "restaurant"},
    "cafe": {"amenity": "cafe"},
    "school": {"amenity": "school"},
    "gym": {"tag_key": "leisure", "tag_value": "fitness_centre"},
    "park": {"tag_key": "leisure", "tag_value": "park"},
}

# POI category → the key it appears under in the response.
RESULT_KEYS: dict[str, str] = {
    "metro_station": "metro_stations",
    "bus_stop": "bus_stops",
    "grocery": "groceries",
    "hospital": "hospitals",
    "pharmacy": "pharmacies",
    "restaurant": "restaurants",
    "cafe": "cafes",
    "school": "schools",
    "gym": "gyms",
    "park": "parks",
}

DEFAULT_POI_TYPES = ["metro_station", "grocery", "hospital", "restaurant", "park"]
DEFAULT_RADIUS_METERS = 1500
RESULTS_PER_TYPE = 5
CALL_TIMEOUT_SECONDS = 45
CACHE_MAX_ENTRIES = 128
# Overpass rate-limits bursts (HTTP 429), so pace the per-type calls and retry.
DELAY_BETWEEN_CALLS_SECONDS = 0.5
RETRY_ATTEMPTS = 2
RETRY_BACKOFF_SECONDS = 3


class OpenStreetMapMCP:
    """Client for the OpenStreetMap MCP server, with an LRU response cache."""

    def __init__(self, cache_max_entries: int = CACHE_MAX_ENTRIES):
        self._cache: OrderedDict[tuple, dict] = OrderedDict()
        self._cache_max_entries = cache_max_entries
        # One MCP session at a time. Two concurrent sessions (say a background
        # prefetch and a user asking "what's nearby?") spawn two servers that
        # then rate-limit each other on the shared Overpass endpoint. The lock is
        # created per running loop: this client is a module-level singleton, and
        # a Lock binds to the first loop that uses it, so a second asyncio.run()
        # (the evals) would otherwise fail with "bound to a different event loop".
        self._lock: Optional[asyncio.Lock] = None
        self._lock_loop: Optional[asyncio.AbstractEventLoop] = None
        # Circuit breaker: Unix time until which the server is presumed down.
        self._unavailable_until: float = 0.0

    # ── cache ────────────────────────────────────────────────────────────
    def _cache_key(self, lat: float, lng: float, radius: int, poi_types: list[str]) -> tuple:
        # Round coords to ~11m so near-identical listings share a cache entry.
        return (round(lat, 4), round(lng, 4), radius, tuple(sorted(poi_types)))

    def _cache_get(self, key: tuple) -> Optional[dict]:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def _cache_put(self, key: tuple, value: dict) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        while len(self._cache) > self._cache_max_entries:
            self._cache.popitem(last=False)

    def cached_result(
        self, lat: float, lng: float, radius_meters: int, poi_types: list[str]
    ) -> Optional[dict]:
        """Return an already-cached POI result, or None. Never makes a call."""
        cached = self._cache_get(self._cache_key(lat, lng, radius_meters, poi_types))
        return {**cached, "cached": True} if cached else None

    def cached_for_point(self, lat: float, lng: float) -> Optional[dict]:
        """Most recent cached result for this spot, whatever radius/types it used.

        Lets a failed live lookup fall back on POI data already fetched for the
        same listing instead of telling the user nothing is known.
        """
        point = (round(lat, 4), round(lng, 4))
        for key in reversed(self._cache):
            if key[:2] == point:
                return self._cache[key]
        return None

    def clear_cache(self) -> None:
        self._cache.clear()

    # ── availability ─────────────────────────────────────────────────────
    def _server_env(self) -> dict:
        env = {**os.environ, "MCP_TRANSPORT_TYPE": "stdio", "MCP_LOG_LEVEL": "error"}
        # An empty OSM_OVERPASS_ENDPOINTS is worse than an absent one: the server
        # validates it as a zero-length list and refuses to start. python-dotenv
        # loads blank .env values into os.environ, so strip them here.
        if OSM_OVERPASS_ENDPOINTS.strip():
            env["OSM_OVERPASS_ENDPOINTS"] = OSM_OVERPASS_ENDPOINTS.strip()
        else:
            env.pop("OSM_OVERPASS_ENDPOINTS", None)
        return env

    def _session_lock(self) -> asyncio.Lock:
        """The session lock for the currently running event loop."""
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _circuit_open(self) -> bool:
        """True while the server is presumed down and calls should fail fast."""
        return time.monotonic() < self._unavailable_until

    def _trip_circuit(self) -> None:
        self._unavailable_until = time.monotonic() + MCP_CIRCUIT_BREAK_SECONDS
        logger.warning(
            "OpenStreetMap MCP marked unavailable for %.0fs after repeated failures",
            MCP_CIRCUIT_BREAK_SECONDS,
        )

    def _reset_circuit(self) -> None:
        self._unavailable_until = 0.0

    @staticmethod
    def _limit_poi_types(poi_types: Optional[list[str]]) -> list[str]:
        """Cap the fan-out: each type is its own live Overpass round trip."""
        types = poi_types or list(DEFAULT_POI_TYPES)
        if len(types) > MCP_MAX_POI_TYPES:
            logger.info("Trimming %d requested POI types to %d", len(types), MCP_MAX_POI_TYPES)
        return types[:MCP_MAX_POI_TYPES]

    def _empty_result(self, lat, lng, radius_meters, poi_types, error) -> dict:
        result = {"lat": lat, "lng": lng, "radius_meters": radius_meters,
                  "error": error, "unavailable": list(poi_types),
                  "source": "OpenStreetMap (MCP)", "cached": False}
        for poi_type in poi_types:
            result[RESULT_KEYS.get(poi_type, poi_type)] = []
        return result

    # ── MCP plumbing ─────────────────────────────────────────────────────
    @staticmethod
    def _parse_elements(call_result) -> list[dict]:
        """Pull the OSM elements out of an MCP tool result."""
        payload = getattr(call_result, "structured_content", None)
        if payload is None:
            # Fall back to the text block, which carries the same JSON payload
            # on servers that don't populate structured content.
            for block in getattr(call_result, "content", []) or []:
                text = getattr(block, "text", None)
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                    break
                except json.JSONDecodeError:
                    continue
        if not isinstance(payload, dict):
            return []
        return payload.get("elements") or []

    @staticmethod
    def _to_poi(element: dict) -> dict:
        """Shape one OSM element into the POI record the UI/LLM consumes."""
        tags = element.get("tags") or {}
        distance_m = element.get("distance_meters")
        return {
            "name": element.get("name") or tags.get("name") or "Unnamed",
            "distance_km": round(distance_m / 1000, 2) if distance_m is not None else None,
            "lat": element.get("lat"),
            "lng": element.get("lon"),
            "osm_id": f"{(element.get('osm_type') or '')[:1].upper()}{element.get('osm_id')}",
        }

    async def _query_nearby_async(
        self, lat: float, lng: float, radius_meters: int, poi_types: list[str]
    ) -> dict:
        """Open one MCP session and query every requested POI type through it."""
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=MCP_SERVER_COMMAND,
            args=MCP_SERVER_ARGS,
            env=self._server_env(),
        )

        results: dict = {"lat": lat, "lng": lng, "radius_meters": radius_meters,
                         "error": None, "unavailable": [],
                         "source": "OpenStreetMap (MCP)"}
        for poi_type in poi_types:
            results[RESULT_KEYS.get(poi_type, poi_type)] = []

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for index, poi_type in enumerate(poi_types):
                    tag_filter = POI_TAG_FILTERS.get(poi_type)
                    if tag_filter is None:
                        logger.warning("Unknown POI type '%s' — skipping", poi_type)
                        results["unavailable"].append(poi_type)
                        continue
                    if index:
                        await asyncio.sleep(DELAY_BETWEEN_CALLS_SECONDS)
                    args = {
                        "lat": lat,
                        "lon": lng,
                        "radius_meters": radius_meters,
                        "limit": RESULTS_PER_TYPE,
                        **tag_filter,
                    }
                    elements = await self._call_with_retry(session, poi_type, args)
                    if elements is None:
                        # Lookup failed — leave the list empty but flag it, so
                        # nobody reads the gap as "there is nothing nearby".
                        results["unavailable"].append(poi_type)
                        continue
                    pois = [self._to_poi(e) for e in elements]
                    pois.sort(key=lambda p: (p["distance_km"] is None, p["distance_km"]))
                    results[RESULT_KEYS.get(poi_type, poi_type)] = pois

        return results

    async def _call_with_retry(self, session, poi_type: str, args: dict) -> Optional[list[dict]]:
        """Call the nearby tool, retrying past Overpass rate limits.

        Returns the OSM elements, or None if every attempt failed.
        """
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                call_result = await session.call_tool(NEARBY_TOOL, args)
            except Exception as exc:
                logger.warning("MCP call for %s failed (attempt %d): %s", poi_type, attempt, exc)
            else:
                if not getattr(call_result, "is_error", False):
                    return self._parse_elements(call_result)
                logger.warning("MCP returned an error for %s (attempt %d)", poi_type, attempt)
            if attempt < RETRY_ATTEMPTS:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
        return None

    async def _query_batch_async(
        self,
        points: list[tuple[float, float]],
        radius_meters: int,
        poi_types: list[str],
        sink: Optional[list[dict]] = None,
    ) -> list[dict]:
        """Query several coordinates through a single MCP session.

        Results are appended to `sink` as they complete, so a caller that times
        out mid-batch still keeps the points that already finished.
        """
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(
            command=MCP_SERVER_COMMAND,
            args=MCP_SERVER_ARGS,
            env=self._server_env(),
        )

        results: list[dict] = sink if sink is not None else []
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for lat, lng in points:
                    entry: dict = {"lat": lat, "lng": lng, "radius_meters": radius_meters,
                                   "error": None, "unavailable": [],
                                   "source": "OpenStreetMap (MCP)"}
                    for poi_type in poi_types:
                        entry[RESULT_KEYS.get(poi_type, poi_type)] = []

                    for index, poi_type in enumerate(poi_types):
                        tag_filter = POI_TAG_FILTERS.get(poi_type)
                        if tag_filter is None:
                            entry["unavailable"].append(poi_type)
                            continue
                        if index or results:
                            await asyncio.sleep(DELAY_BETWEEN_CALLS_SECONDS)
                        args = {"lat": lat, "lon": lng, "radius_meters": radius_meters,
                                "limit": RESULTS_PER_TYPE, **tag_filter}
                        elements = await self._call_with_retry(session, poi_type, args)
                        if elements is None:
                            entry["unavailable"].append(poi_type)
                            continue
                        pois = [self._to_poi(e) for e in elements]
                        pois.sort(key=lambda p: (p["distance_km"] is None, p["distance_km"]))
                        entry[RESULT_KEYS.get(poi_type, poi_type)] = pois

                    results.append(entry)
        return results

    async def query_nearby_batch(
        self,
        points: list[tuple[float, float]],
        radius_meters: int = DEFAULT_RADIUS_METERS,
        poi_types: Optional[list[str]] = None,
        timeout_seconds: float = CALL_TIMEOUT_SECONDS,
    ) -> list[Optional[dict]]:
        """
        POIs for several coordinates, sharing one server process.

        Cached points are served without a round trip. Returns one entry per
        input point, aligned by index; an entry is None only if the whole
        batch failed, so callers can tell "no data" from "none nearby".
        """
        poi_types = self._limit_poi_types(poi_types)
        out: list[Optional[dict]] = [None] * len(points)

        pending: list[tuple[int, tuple[float, float]]] = []
        for i, (lat, lng) in enumerate(points):
            cached = self._cache_get(self._cache_key(lat, lng, radius_meters, poi_types))
            if cached is not None:
                out[i] = {**cached, "cached": True}
            else:
                pending.append((i, (lat, lng)))

        if not pending:
            return out

        async with self._session_lock():
            # Re-check: another caller may have filled these while we queued.
            still_pending = []
            for i, (lat, lng) in pending:
                cached = self._cache_get(self._cache_key(lat, lng, radius_meters, poi_types))
                if cached is not None:
                    out[i] = {**cached, "cached": True}
                else:
                    still_pending.append((i, (lat, lng)))
            pending = still_pending
            if not pending:
                return out
            if self._circuit_open():
                logger.info("Skipping POI batch — OpenStreetMap MCP is in cool-off")
                return out

            out = await self._fetch_pending(pending, out, radius_meters, poi_types, timeout_seconds)
        return out

    async def _fetch_pending(
        self,
        pending: list[tuple[int, tuple[float, float]]],
        out: list[Optional[dict]],
        radius_meters: int,
        poi_types: list[str],
        timeout_seconds: float,
    ) -> list[Optional[dict]]:
        """Fetch the uncached points. Caller must hold the session lock."""
        # Collect into a sink so a timeout keeps whatever already finished.
        fetched: list[dict] = []
        task = asyncio.create_task(
            self._query_batch_async(
                [p for _, p in pending], radius_meters, poi_types, sink=fetched
            )
        )
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            task.cancel()
            logger.warning(
                "OpenStreetMap MCP batch timed out after %.0fs with %d/%d points done",
                timeout_seconds, len(fetched), len(pending),
            )
        except Exception as exc:
            task.cancel()
            logger.warning("OpenStreetMap MCP batch failed: %s", exc)

        if not fetched:
            self._trip_circuit()
            return out

        for (i, (lat, lng)), entry in zip(pending, fetched):
            if set(entry.get("unavailable") or []) >= set(poi_types):
                continue  # nothing usable — don't poison the cache
            self._reset_circuit()
            self._cache_put(self._cache_key(lat, lng, radius_meters, poi_types), entry)
            out[i] = {**entry, "cached": False}
        return out

    # ── public API ───────────────────────────────────────────────────────
    async def query_nearby(
        self,
        lat: float,
        lng: float,
        radius_meters: int = DEFAULT_RADIUS_METERS,
        poi_types: Optional[list[str]] = None,
    ) -> dict:
        """
        Find POIs around a coordinate.

        Returns `{metro_stations[], groceries[], ..., error}`. If the MCP
        server is unreachable, every list is empty and `error` is set — the
        caller degrades gracefully instead of crashing.
        """
        poi_types = self._limit_poi_types(poi_types)
        key = self._cache_key(lat, lng, radius_meters, poi_types)

        cached = self._cache_get(key)
        if cached is not None:
            return {**cached, "cached": True}

        if self._circuit_open():
            fallback = self.cached_for_point(lat, lng)
            if fallback:
                return {**fallback, "cached": True, "stale": True}
            return self._empty_result(
                lat, lng, radius_meters, poi_types,
                "OpenStreetMap lookups are unavailable right now.",
            )

        try:
            async with self._session_lock():
                # Whoever held the lock may have just fetched this very point.
                cached = self._cache_get(key)
                if cached is not None:
                    return {**cached, "cached": True}
                result = await asyncio.wait_for(
                    self._query_nearby_async(lat, lng, radius_meters, poi_types),
                    timeout=CALL_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            logger.warning("OpenStreetMap MCP unavailable: %s", exc)
            self._trip_circuit()
            fallback = self.cached_for_point(lat, lng)
            if fallback:
                logger.info("Serving stale cached POIs for (%s, %s)", lat, lng)
                return {**fallback, "cached": True, "stale": True}
            return self._empty_result(
                lat, lng, radius_meters, poi_types,
                f"OpenStreetMap MCP unavailable: {exc}",
            )

        # Every type failing means the upstream API is down, not that the
        # neighbourhood is empty.
        if set(result.get("unavailable") or []) >= set(poi_types):
            self._trip_circuit()
            fallback = self.cached_for_point(lat, lng)
            if fallback:
                return {**fallback, "cached": True, "stale": True}
        else:
            self._reset_circuit()
            self._cache_put(key, result)
        return {**result, "cached": False}

    def query_nearby_sync(
        self,
        lat: float,
        lng: float,
        radius_meters: int = DEFAULT_RADIUS_METERS,
        poi_types: Optional[list[str]] = None,
    ) -> dict:
        """Blocking wrapper, for scripts and eval runs outside the event loop."""
        return asyncio.run(self.query_nearby(lat, lng, radius_meters, poi_types))


# Shared client so the POI cache is process-wide.
osm_mcp = OpenStreetMapMCP()


async def query_nearby(
    lat: float,
    lng: float,
    radius_meters: int = DEFAULT_RADIUS_METERS,
    poi_types: Optional[list[str]] = None,
) -> dict:
    """Module-level entry point used by the orchestrator's tool dispatch."""
    return await osm_mcp.query_nearby(lat, lng, radius_meters, poi_types)
