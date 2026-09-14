"""
Configuration module
--------------------
Loads environment variables and exposes typed config values.
"""

import os
from dotenv import load_dotenv

# Load .env file from backend directory
_backend_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_backend_dir, ".env"))


# Values copied straight out of .env.example. Treating them as unset means a
# half-filled .env reports "not configured" instead of failing deep inside an
# API client with a confusing error.
_PLACEHOLDERS = {
    "your_gemini_api_key_here",
    "your_calendar_id@group.calendar.google.com",
    "your_email@gmail.com",
    "your_app_password_here",
    "your_elevenlabs_api_key_here",
    "service-account.json.example",
}


def _setting(name: str, default: str = "") -> str:
    """Read an env var, treating a leftover .env.example placeholder as unset."""
    value = os.getenv(name, default).strip()
    return "" if value in _PLACEHOLDERS else value


# ─── Gemini LLM ──────────────────────────────────────────────────────────────
GEMINI_API_KEY = _setting("GEMINI_API_KEY")

# ─── Google Calendar (Service Account) ────────────────────────────────────────
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service-account.json")
# Hosting platforms have no way to ship a key file, so the whole JSON can be
# passed as an env var instead; it is written to disk on startup.
GOOGLE_SERVICE_ACCOUNT_JSON = _setting("GOOGLE_SERVICE_ACCOUNT_JSON")
GOOGLE_CALENDAR_ID = _setting("GOOGLE_CALENDAR_ID")

# ─── Email (Gmail SMTP) ──────────────────────────────────────────────────────
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = _setting("SMTP_USER")
# Google displays App Passwords as "abcd efgh ijkl mnop"; SMTP wants the 16
# characters with no spaces.
SMTP_PASS = _setting("SMTP_PASS").replace(" ", "")

# ─── Text-to-Speech (ElevenLabs) ─────────────────────────────────────────────
# The browser's speechSynthesis voice sounds synthetic; ElevenLabs carries the
# spoken replies instead. Without a key the app falls back to the browser voice,
# so local dev and the deployed demo both keep working.
ELEVENLABS_API_KEY = _setting("ELEVENLABS_API_KEY")
# Free accounts cannot use Voice Library voices over the API (HTTP 402), only
# the premade ones. Sarah is premade and verified working on the free tier;
# Jessica (cgSgspJ2msm6clMCkdW9), Brian (nPczCjzI2devNBz1zQrb) and George
# (JBFqnCBsd6RMkjVDRZzb) also work.
ELEVENLABS_VOICE_ID = _setting("ELEVENLABS_VOICE_ID") or "EXAVITQu4vr4xnSDxMaL"
# flash_v2_5 is the low-latency model; turbo_v2_5 trades ~100ms for a little quality.
ELEVENLABS_MODEL = _setting("ELEVENLABS_MODEL") or "eleven_flash_v2_5"
# The free tier is ~10k characters/month, so replies are capped and cached.
TTS_MAX_CHARS = int(os.getenv("TTS_MAX_CHARS", "600"))
TTS_CACHE_ENTRIES = int(os.getenv("TTS_CACHE_ENTRIES", "128"))

# ─── URLs ─────────────────────────────────────────────────────────────────────
# Comma-separated: the deployed frontend, plus any preview domains.
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173")
ALLOWED_ORIGINS = [o.strip() for o in FRONTEND_URL.split(",") if o.strip()]
# Optional regex for origins that change per deploy, e.g. Vercel previews:
#   ALLOWED_ORIGIN_REGEX=https://property-scout-.*\.vercel\.app
ALLOWED_ORIGIN_REGEX = _setting("ALLOWED_ORIGIN_REGEX")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# ─── Database Paths ──────────────────────────────────────────────────────────
# Overridable so a hosting volume can hold them: mounting a volume over the app
# directory would shadow the code, so point these at the mount instead.
SQLITE_DB_PATH = os.getenv("SQLITE_DB_PATH") or os.path.join(_backend_dir, "listings.db")
CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH") or os.path.join(_backend_dir, "chroma_db")

# ─── Embedding Model ─────────────────────────────────────────────────────────
EMBEDDING_MODEL = "gemini-embedding-001"

# ─── LLM Orchestrator ────────────────────────────────────────────────────────
# Gemini 2.0/2.5 Flash are no longer served to new API keys. Flash-Lite is the
# pick here: same tool-routing accuracy on this workload at ~1.3s per call, and
# a far larger free-tier daily quota than gemini-3.6-flash's 20 requests/day.
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.2"))
# Gemini 3.x thinks by default, which costs ~20s per call — far too slow for a
# spoken reply. "low" keeps tool-routing accuracy at roughly 2s per call.
LLM_THINKING_LEVEL = os.getenv("LLM_THINKING_LEVEL", "low")

# Max tool calls the agent may make in a single turn (loop guard).
MAX_TOOL_CALLS_PER_TURN = int(os.getenv("MAX_TOOL_CALLS_PER_TURN", "5"))
# Max clarifying questions before the agent must show what it has.
MAX_CLARIFYING_QUESTIONS = int(os.getenv("MAX_CLARIFYING_QUESTIONS", "5"))
# Conversation turns kept in the prompt.
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "20"))

# ─── Retrieval & Enrichment ──────────────────────────────────────────────────
# Cosine similarity below this is treated as "no relevant data" rather than a
# weak answer. Calibrated against the seeded corpus: on-topic queries score
# 0.55-0.65, off-topic ones plateau around 0.45.
RAG_MIN_SIMILARITY = float(os.getenv("RAG_MIN_SIMILARITY", "0.50"))

# POI enrichment is a live Overpass round trip per listing, so it is capped.
ENRICH_MAX_LISTINGS = int(os.getenv("ENRICH_MAX_LISTINGS", "3"))
ENRICH_POI_TYPES = ["metro_station", "grocery", "hospital"]
ENRICH_RADIUS_METERS = int(os.getenv("ENRICH_RADIUS_METERS", "1500"))
ENRICH_TIME_BUDGET_SECONDS = float(os.getenv("ENRICH_TIME_BUDGET_SECONDS", "45"))
# Background warming blocks nobody, so it gets a budget that survives a slow
# Overpass. One point costs a spawn plus one round trip per POI type, and
# those round trips run from ~2s to ~30s depending on Overpass load — 45s
# could not finish a single point when the public endpoints were busy.
PREFETCH_TIME_BUDGET_SECONDS = float(os.getenv("PREFETCH_TIME_BUDGET_SECONDS", "240"))
# Blocking POI enrichment adds ~45s to a chat turn (npx spawn + one live
# Overpass query per listing per POI type), which a voice conversation cannot
# absorb. Instead the turn returns immediately and the POI cache is warmed in
# the background, so opening a listing card usually hits a warm cache.
ENRICH_SHORTLIST_WITH_POIS = os.getenv("ENRICH_SHORTLIST_WITH_POIS", "false").lower() == "true"
PREFETCH_POIS_IN_BACKGROUND = os.getenv("PREFETCH_POIS_IN_BACKGROUND", "true").lower() == "true"

# ─── OpenStreetMap MCP ───────────────────────────────────────────────────────
# The MCP server queries one tag per request, so a 10-type ask becomes 10 live
# Overpass round trips. Cap what any single call may fan out to.
MCP_MAX_POI_TYPES = int(os.getenv("MCP_MAX_POI_TYPES", "4"))
# Comma-separated Overpass endpoints, passed through to the MCP server. Set
# this to a mirror if the default endpoint blocks or rate-limits your host.
#
# The MCP server has NO default of its own: it validates the list as
# "at least one item" and refuses every tool call when the variable is absent
# or blank, so a default has to be supplied here. The mirror is listed second
# as a fallback for when the main endpoint rate-limits.
# Both verified to return Bengaluru data. Do NOT add a regional mirror such as
# overpass.osm.ch here: it answers 200 with zero elements outside its region,
# which the agent would report as "nothing nearby" rather than as a failure.
DEFAULT_OVERPASS_ENDPOINTS = (
    "https://overpass-api.de/api/interpreter,"
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter"
)
OSM_OVERPASS_ENDPOINTS = (
    os.getenv("OSM_OVERPASS_ENDPOINTS", "").strip() or DEFAULT_OVERPASS_ENDPOINTS
)
# After the public Overpass API goes dark, stop dialling it for a while — every
# attempt costs ~25s of retries inside the MCP server before it gives up.
MCP_CIRCUIT_BREAK_SECONDS = float(os.getenv("MCP_CIRCUIT_BREAK_SECONDS", "300"))
