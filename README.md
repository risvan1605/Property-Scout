# Voice-First AI Property Scout — Bengaluru

A voice-first assistant that collects rental preferences by speech, shortlists real
listings, explains its picks against cited neighborhood sources, refines the
shortlist on spoken command, books a site visit on Google Calendar, and emails the
shortlist as a PDF.

Every neighborhood claim is traceable to a source, every listing to a database row.
Where the data runs out, the assistant says so instead of guessing — and there is an
eval that fails the build if it doesn't.

---

## What it does

| Capability | How |
|---|---|
| Voice preference collection | Web Speech API (`en-IN`) → Gemini function-calling agent |
| Shortlist from real listings | 15 scraped, PII-free listings in SQLite |
| Grounded neighborhood answers | RAG over three neighborhood guides in ChromaDB, with citations |
| "What's nearby?" | OpenStreetMap **MCP** server over stdio (live Overpass data) |
| Spoken replies | ElevenLabs, with the browser voice as fallback |
| Voice shortlist edits | `search_listings` with replace/append modes |
| Site-visit booking | Google Calendar API + `.ics` invite by email |
| Shortlist PDF | Jinja2 → WeasyPrint → Gmail SMTP |
| AI evals | 4 suites, 19 tests, run against the real agent |

---

## Architecture

```
Browser (React + Vite)
  speech in  →  Web Speech API
  speech out ←  POST /api/tts  →  ElevenLabs
        │
        ▼  POST /api/chat
FastAPI backend
        │
        ▼
  Orchestrator  ──  Gemini 3.1 Flash-Lite, function calling, max 5 tool calls/turn
        │
        ├─ search_listings ──────────→ SQLite (15 listings)
        ├─ retrieve_neighborhood_info → ChromaDB (27 chunks, gemini-embedding-001)
        ├─ query_openstreetmap ──────→ MCP server (stdio) → Overpass API
        ├─ book_site_visit ──────────→ Google Calendar API
        └─ generate_shortlist_pdf ───→ WeasyPrint → Gmail SMTP
```

The shortlist the UI renders is built from **tool results, never parsed from the
model's prose** — a listing can only reach the screen if the database returned it.
Match reasons are computed from the listing fields, not generated.

Full design notes: [`architecture.md`](Docs/architecture.md) ·
[`implementation_plan.md`](Docs/implementation_plan.md) · [`edgecase.md`](Docs/edgecase.md) ·
[`eval.md`](Docs/eval.md)

---

## Setup

**Prerequisites:** Python 3.13, Node.js 22+ (the MCP server runs via `npx`), and for
PDF rendering the WeasyPrint native libraries — on macOS `brew install pango`, on
Debian/Ubuntu `apt install libpango-1.0-0 libpangoft2-1.0-0 libcairo2 libgdk-pixbuf-2.0-0`.

```bash
# Backend
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # then fill in the keys (see below)
.venv/bin/python data/seed_db.py   # builds listings.db + chroma_db
.venv/bin/uvicorn main:app --port 8000

# Frontend (second terminal)
cd frontend
npm install
npm run dev                   # http://localhost:5173
```

`GET /api/health` reports what came up: `{"status":"ok","listings":15,"chroma":true,"voice":true}`.

### Keys

| Variable | Needed for | Without it |
|---|---|---|
| `GEMINI_API_KEY` | the agent and embeddings | **required** |
| `ELEVENLABS_API_KEY` | natural spoken replies | falls back to the browser voice |
| `GOOGLE_SERVICE_ACCOUNT_FILE` / `GOOGLE_CALENDAR_ID` | booking | booking reports unavailable |
| `SMTP_USER` / `SMTP_PASS` (Gmail **app password**) | emailing the PDF | email reports unavailable |

Every one degrades gracefully — the app runs with only `GEMINI_API_KEY`.

> **Google Calendar:** enable the Calendar API, create a service account, download its
> JSON key to `backend/service-account.json`, and share your calendar with the service
> account's email ("Make changes to events"). Note that service accounts **cannot add
> attendees** without Domain-Wide Delegation (a Workspace feature), so on a consumer
> Google account the app creates the event and emails the invite as an `.ics` itself.

---

## How MCP was integrated

Nearby-places data comes from the **OpenStreetMap MCP server**
(`@cyanheads/openstreetmap-mcp-server`), launched as a subprocess over stdio and
spoken to with the MCP Python SDK — see [`backend/tools/mcp_client.py`](backend/tools/mcp_client.py).

```python
params = StdioServerParameters(
    command="npx",
    args=["-y", "@cyanheads/openstreetmap-mcp-server"],
    env=self._server_env(),
)
async with stdio_client(params) as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        result = await session.call_tool("openstreetmap_query_nearby", {
            "lat": lat, "lon": lng, "radius_meters": radius,
            "limit": 5, **POI_TAG_FILTERS[poi_type],
        })
```

The results are plotted on each listing card as a **proximity dial** — concentric
0.5/1/1.5 km rings with each place at its true distance *and bearing*, computed from
the listing's coordinates.

Overpass is a shared public API and rate-limits hard, so the client is built for it:
one tag filter per request means POI types are capped per call; sessions are
serialised behind a lock (two concurrent servers throttle each other); responses are
LRU-cached by rounded coordinate; and a circuit breaker stops dialling a dead
endpoint, which cut a failed lookup from ~110s to ~4s. An empty list means "none
within the radius" and is reported as such — never confused with "the lookup failed",
which surfaces separately in `unavailable[]`.

---

## Data

15 listings scraped by hand from [bengaluru.rent](https://bengaluru.rent/) across
Koramangala, Indiranagar and HSR Layout — only pins marked currently available, with
all owner names and phone numbers stripped. Fields: rent, bedrooms, sqft, furnishing,
amenities, society name, coordinates, availability, source URL.

Neighborhood guides for the same three areas were compiled from Wikipedia and public
locality guides, with source URLs recorded per file for citation. `data/seed_db.py`
splits them at section headings (250-word chunks, 50-word overlap) so every chunk can
be cited by the section it came from, embeds them with `gemini-embedding-001`, and
stores 27 chunks in ChromaDB.

**Scope: rentals only.** The dataset has one price field — monthly rent. The assistant
refuses to quote sale prices rather than invent them, and an eval enforces it.

---

## Running the evals

```bash
cd backend
PYTHONPATH="$PWD" .venv/bin/python evals/run_evals.py --eval all
```

```
Feasibility     5/5   ·  Edit Correctness  5/5
Grounding       5/5   ·  Scope             4/4
Claims: 11 | Grounded: 11 | Hallucinations: 0
OVERALL: 19/19 tests passed (100%)
```

Reports land in `evals/results/*.json`. Exit code `0` = all passed, `1` = a
behavioural failure, `2` = the run could not complete (quota) — the runner never
scores a turn the model didn't answer.

| Eval | Type | Checks |
|---|---|---|
| `feasibility_eval.py` | rule-based | budget, bedrooms, neighborhood, amenities, availability |
| `edit_correctness.py` | rule-based diff | correct removals, no unintended drops/additions, survivors unmutated |
| `grounding_eval.py` | LLM-assisted | every claim entailed by a retrieved passage; listings match the DB; gaps admitted |
| `scope_eval.py` | rule-based | no invented sale prices, no uncovered neighborhoods |

**On grounding method.** The obvious design — "a claim is grounded if its best
chunk scores above a similarity threshold" — was measured and rejected. On this
corpus, fabricated claims score 0.73–0.83 and true ones 0.75–0.91: the ranges
overlap. *"Rents in HSR Layout fell twelve percent last year"* (invented) scores
**0.834**, higher than the true *"Indiranagar Metro Station is on the Purple Line"*
at 0.750, because embeddings measure topical closeness, not support. The eval
therefore retrieves by similarity but decides with an **LLM entailment judge**,
which scores 12/12 on the calibration set. Reproduce with
`.venv/bin/python evals/calibrate.py`.

These evals earned their keep: they caught the agent replacing the shortlist when
asked to *add* to it, and claiming "narrowed to two fully furnished" while five
listings stayed on screen (the tool had no furnishing filter, so it filtered in
prose). Both are fixed.

---

## Sample transcript

```
Scout  Hi, I'm your property scout. Tell me your budget and how many bedrooms
       you need, and I'll pull up rentals in Koramangala, Indiranagar or HSR Layout.
You    2BHK in Koramangala under 40k with parking
Scout  I found two 2BHKs in Koramangala under forty thousand with parking.
       The cheapest is twenty-eight thousand rupees at Prestige Oasis.
You    Drop anything above 30k
Scout  That leaves one: Prestige Oasis at twenty-eight thousand.
You    Is it safe at night there?
Scout  Koramangala is generally considered safe, with well-lit main roads and
       regular police patrols.                    [cited: Koramangala — Safety]
You    What's near it?
Scout  There's a grocery two hundred metres away and HCG hospital at two hundred
       metres. There's no metro station within 1.5 km.      [OpenStreetMap · MCP]
You    Book a visit on 2026-10-04 in the morning, my email is …
Scout  Booked for October fourth, ten to twelve. The invite is on its way.
```

---

## Deployment

The backend ships as a container (`backend/Dockerfile`) because it needs both
WeasyPrint's native libraries and Node.js for the MCP server. `backend/entrypoint.sh`
seeds the database on first boot if it's missing, then serves on `$PORT`.

**Backend — Railway** (`backend/railway.json`): set the service's Root Directory to
`backend`, add the environment variables, and deploy. Instead of a key file, set
`GOOGLE_SERVICE_ACCOUNT_JSON` to the whole JSON — the app writes it to disk at
startup with mode 600.

**Frontend — Vercel**: root `frontend/`, config in
[`frontend/vercel.json`](frontend/vercel.json). Set `VITE_API_URL` to the backend URL
— Vite inlines it at build time, so changing it needs a redeploy.

**CORS:** set `FRONTEND_URL` on the backend to the deployed frontend origin, or
`ALLOWED_ORIGIN_REGEX` for Vercel preview URLs, or the browser blocks every request.

Step-by-step, in order, with a verification checklist and a troubleshooting table:
[`deployment.md`](Docs/deployment.md).

> Voice input needs **Chrome or Edge** — Web Speech recognition doesn't exist in
> Safari or Firefox, where the UI falls back to a typed input automatically. The page
> must be served over HTTPS (or localhost) for microphone access.

---

## Known limits

- **Free-tier quotas.** Gemini free tier allows 15 requests/minute and 500/day;
  a turn costs about two. ElevenLabs' free tier is ~10k characters/month, so
  identical replies are cached and the browser voice takes over if it runs out.
- **Overpass rate-limits** the shared public endpoint. The client degrades and
  reports it; point `OSM_OVERPASS_ENDPOINTS` at a mirror if your host is blocked.
- **Sessions are in memory** and are lost when the server restarts.
- The dataset is 15 listings in 3 neighborhoods — enough to exercise every path,
  not a market.
