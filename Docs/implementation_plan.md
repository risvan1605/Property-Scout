# Implementation Plan — Voice-First AI Property Scout

A phased, step-by-step plan to build the complete system. Each phase builds on the previous one and is independently testable.

---

## Phase 0: Project Setup & Data Collection
**Goal:** Initialize the project, install dependencies, and prepare the real data.

### 0.1 Project Scaffolding

#### [NEW] Project root structure
- Initialize Git repo
- Create `frontend/` via `npx -y create-vite@latest ./ -- --template react`
- Create `backend/` Python project with virtual environment
- Create `.gitignore` (node_modules, __pycache__, .env, *.db, service-account.json)

#### [NEW] [requirements.txt](file:///Users/ris/Antigravity/Capstone%20project/backend/requirements.txt)
```
fastapi
uvicorn[standard]
google-generativeai
chromadb
mcp
google-api-python-client
google-auth
weasyprint
jinja2
python-dotenv
```

#### [NEW] [package.json](file:///Users/ris/Antigravity/Capstone%20project/frontend/package.json)
- React + Vite (from create-vite template)
- No additional dependencies needed initially

#### [NEW] [.env](file:///Users/ris/Antigravity/Capstone%20project/backend/.env)
- `GEMINI_API_KEY`, `GOOGLE_SERVICE_ACCOUNT_FILE`, `GOOGLE_CALENDAR_ID`
- `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`
- `FRONTEND_URL`, `BACKEND_URL`

### 0.2 Data Scraping & Cleaning

#### [NEW] [data/listings.json](file:///Users/ris/Antigravity/Capstone%20project/backend/data/listings.json)
- Manually scrape **15 available listings** from [bengaluru.rent](https://bengaluru.rent/)
- For each listing, capture: rent, bedrooms, furnishing, amenities, society name, sqft, lat/lng, availability
- **Only include pins marked as currently available** — skip "Not for rent" pins
- **Strip all PII** (owner names, phone numbers) from every record
- Pick listings from **3 neighborhoods**: Koramangala, Indiranagar, HSR Layout
- Validate: all 15 records have required fields, no PII, all marked available

#### [NEW] [data/neighborhoods/koramangala.txt](file:///Users/ris/Antigravity/Capstone%20project/backend/data/neighborhoods/koramangala.txt)
#### [NEW] [data/neighborhoods/indiranagar.txt](file:///Users/ris/Antigravity/Capstone%20project/backend/data/neighborhoods/indiranagar.txt)
#### [NEW] [data/neighborhoods/hsr_layout.txt](file:///Users/ris/Antigravity/Capstone%20project/backend/data/neighborhoods/hsr_layout.txt)
- Scrape Wikipedia pages + any public city guide sources for each neighborhood
- Focus on: history, character, safety, nightlife, transit connectivity, food scene, who lives there
- Store raw text with source URLs at the top of each file for citation tracking

### 0.3 Database Seeding

#### [NEW] [data/seed_db.py](file:///Users/ris/Antigravity/Capstone%20project/backend/data/seed_db.py)
- Load `listings.json` → create SQLite `listings.db` with the schema from [architecture.md](file:///Users/ris/Antigravity/Capstone%20project/architecture.md)
- Load neighborhood `.txt` files → chunk (500 tokens, 50-token overlap, paragraph-boundary splits) → embed with `text-embedding-004` → store in ChromaDB
- Each ChromaDB document stores metadata: `{source_url, source_file, section_title, chunk_index}`
- **Verify:** Run the script, confirm 15 rows in SQLite, confirm chunks in ChromaDB with `collection.count()`

> [!IMPORTANT]
> Phase 0 is **manual work** — scraping listings and neighborhood data by hand. This must be done carefully to ensure data quality. The rest of the project depends on this data being correct.

---

## Phase 1: Backend Foundation
**Goal:** FastAPI server with listing search and conversation state, no LLM yet.

### 1.1 FastAPI App

#### [NEW] [main.py](file:///Users/ris/Antigravity/Capstone%20project/backend/main.py)
- FastAPI app with CORS middleware (allow frontend origin)
- Mount routes from `api/routes/`
- Startup event: initialize SQLite connection, ChromaDB client, session store

#### [NEW] [config.py](file:///Users/ris/Antigravity/Capstone%20project/backend/config.py)
- Load all env vars via `python-dotenv`
- Expose typed config: `GEMINI_API_KEY`, `SMTP_*`, `GOOGLE_*`, etc.

### 1.2 Listing Search Tool

#### [NEW] [tools/listing_search.py](file:///Users/ris/Antigravity/Capstone%20project/backend/tools/listing_search.py)
- `search_listings(max_budget, min_bedrooms, neighborhood, amenities, exclude_ids)` → `list[Listing]`
- Builds parameterized SQLite queries dynamically
- Amenities filter: checks JSON array field contains all required amenities
- Returns structured listing dicts (no PII fields ever exposed)

#### [NEW] [api/routes/listings.py](file:///Users/ris/Antigravity/Capstone%20project/backend/api/routes/listings.py)
- `GET /api/listings` — returns all listings (for frontend initial load)
- `GET /api/listings/{id}` — returns single listing by ID

### 1.3 Conversation State Manager

#### [NEW] [core/conversation.py](file:///Users/ris/Antigravity/Capstone%20project/backend/core/conversation.py)
- `ConversationState` dataclass: session_id, state (enum), preferences, shortlist, history, clarification_count, booking
- `UserPreferences` dataclass: budget_max, bedrooms, neighborhood, must_have_amenities, commute_point, nice_to_haves
- `SessionStore` class: in-memory dict, create/get/update session
- State transitions: `greeting → collecting → confirming → shortlist_ready → booking → visit_booked`

**Verify:** `uvicorn main:app --reload`, hit `/api/listings`, confirm JSON response with 15 listings.

---

## Phase 2: RAG Pipeline & MCP Integration
**Goal:** Wire up the two external data sources — neighborhood RAG and OpenStreetMap MCP.

### 2.1 RAG Retriever

#### [NEW] [tools/rag_retriever.py](file:///Users/ris/Antigravity/Capstone%20project/backend/tools/rag_retriever.py)
- `retrieve_neighborhood_info(neighborhood, query)` → `{answer, chunks[]}`
- Connect to ChromaDB persistent store
- Embed query using same model as ingestion (`text-embedding-004`)
- Retrieve top-5 chunks by cosine similarity
- Each returned chunk includes: `text`, `source_url`, `section_title`, `similarity_score`
- Does **not** generate an answer — returns raw chunks to the orchestrator

**Verify:** Call `retrieve_neighborhood_info("Koramangala", "safety at night")`, confirm relevant chunks returned with citations.

### 2.2 OpenStreetMap MCP Client

#### [NEW] [tools/mcp_client.py](file:///Users/ris/Antigravity/Capstone%20project/backend/tools/mcp_client.py)
- `OpenStreetMapMCP` class wrapping the MCP Python SDK
- `query_nearby(lat, lng, radius_meters, poi_types)` → structured POI data
- Uses stdio transport: spawns `npx -y @cyanheads/openstreetmap-mcp-server` as subprocess
  (the `open-streetmap-mcp` package named earlier does not exist on npm)
- Parses MCP tool response into: `{metro_stations[], groceries[], hospitals[], restaurants[], parks[]}`
- Each POI includes: name, distance_km, lat, lng
- **Caching:** LRU cache keyed by `(lat, lng, radius, poi_types)` to avoid redundant calls
- Error handling: if MCP server fails, return empty results with error flag (don't crash)
- The server takes one OSM tag filter per call, so one call is made per POI type;
  Overpass rate-limits bursts (HTTP 429), so calls are paced and retried, and any
  type that still fails is listed in `unavailable[]` — an empty list therefore
  means "none nearby", never "the lookup failed"

**Verify:** Call `query_nearby(12.9352, 77.6245, 1000, ["metro_station", "grocery"])` for a Koramangala coordinate, confirm real POI data returned.

> [!NOTE]
> The OpenStreetMap MCP server needs Node.js installed. Ensure `npx` is available on the deployment platform.

---

## Phase 3: LLM Orchestrator (Agent Core)
**Goal:** The brain of the system — Gemini function-calling agent that routes intent and calls tools.

### 3.1 Prompt Templates

#### [NEW] [core/prompt_templates.py](file:///Users/ris/Antigravity/Capstone%20project/backend/core/prompt_templates.py)
- **System prompt:** Define the assistant's role, personality, grounding rules
  - "You are a property scout for Bengaluru. You help users find rental properties..."
  - "NEVER make claims about a neighborhood without citing RAG sources"
  - "If you don't have data, say so explicitly"
  - "Keep voice responses concise (2-3 sentences). Put detailed citations in the structured response"
  - "Maximum 5 clarifying questions before showing a shortlist"
- **Tool descriptions:** The 5 function-calling tool schemas (search_listings, query_openstreetmap, retrieve_neighborhood_info, book_site_visit, generate_shortlist_pdf)
- Few-shot examples for tricky cases (partial preferences, refinement commands)

### 3.2 LLM Orchestrator

> [!NOTE]
> **Model choice (revised during Phase 3):** Gemini 2.0 and 2.5 Flash are no
> longer served to new API keys, and `gemini-3.6-flash` allows only **20 free-tier
> requests per day** — exhausted in a single test run. The orchestrator defaults to
> **`gemini-3.1-flash-lite`** (override with `GEMINI_MODEL`), which routes tools
> identically on this workload at ~1.3s per call. Gemini 3.x also thinks by default,
> costing ~20s per call; `LLM_THINKING_LEVEL=low` brings that down to ~2s.

#### [NEW] [core/orchestrator.py](file:///Users/ris/Antigravity/Capstone%20project/backend/core/orchestrator.py)
- `Orchestrator` class — the agentic loop:
  1. Receive user message + conversation state
  2. Build messages array: system prompt + conversation history + new user message
  3. Call Gemini API with function declarations
  4. If LLM returns a function call → dispatch to the appropriate tool → feed result back to LLM
  5. If LLM returns text → parse structured response (response_text + any shortlist/booking updates)
  6. Loop until LLM produces a final text response (max 5 tool calls per turn to prevent infinite loops)
  7. Update conversation state with new shortlist/preferences/booking
  8. Return: `{response_text, shortlist, sources, booking, state}`

- **Tool dispatch map:**
  - `search_listings` → `listing_search.search_listings(**args)`
  - `query_openstreetmap` → `mcp_client.query_nearby(**args)`
  - `retrieve_neighborhood_info` → `rag_retriever.retrieve(**args)`
  - `book_site_visit` → `google_calendar.book_visit(**args)`
  - `generate_shortlist_pdf` → `pdf_generator.generate(**args)` + `email_sender.send(**args)`

- **Shortlist enrichment flow** (triggered after initial search):
  1. Get matching listings from `search_listings`
  2. Attach a neighborhood snapshot per area — summary/safety/transit pulled
     **by section** from ChromaDB (exact lookup, not similarity: a semantic query
     for "overview" kept returning the Safety chunk)
  3. Compute `match_reasons` from the listing fields and active filters, so what
     the card claims is never model-generated
  4. POIs are **not** fetched inline: one live Overpass round trip per listing per
     POI type added ~45s to a spoken turn. The turn returns immediately, the POI
     cache is warmed in the background, and `GET /api/listings/{id}/nearby` serves
     a card when the user opens it. Set `ENRICH_SHORTLIST_WITH_POIS=true` to go
     back to blocking enrichment.
  5. Feed all data back to LLM → generate grounded explanation with citations

- **MCP resilience:** POI type fan-out is capped (`MCP_MAX_POI_TYPES`), MCP
  sessions are serialised behind a lock (two concurrent servers rate-limit each
  other on the shared Overpass endpoint), and a circuit breaker stops dialling a
  dead endpoint for `MCP_CIRCUIT_BREAK_SECONDS` — without it a failed lookup took
  ~110s to report "unavailable", with it ~4s. A failed lookup falls back to any
  cached POIs already held for that listing. `OSM_OVERPASS_ENDPOINTS` points the
  MCP server at a mirror when the default endpoint blocks the host.

### 3.3 Chat API Route

#### [NEW] [api/routes/chat.py](file:///Users/ris/Antigravity/Capstone%20project/backend/api/routes/chat.py)
- `POST /api/chat` — accepts `{session_id, text}`
- `GET /api/chat/{session_id}` — current session state, for page reload
- Creates or retrieves session from `SessionStore`
- Passes to `Orchestrator.process(session, text)`
- Returns: `{session_id, response_text, state, shortlist[], sources[], booking}`

**Verify:**
- Send text: "I want a 2BHK in Koramangala under 35k" → confirm shortlist returned
- Send follow-up: "Drop anything above 30k" → confirm shortlist refined
- Send: "Why did you pick this one?" → confirm grounded explanation with citations
- Send: "What's near this property?" → confirm MCP data in response

---

## Phase 4: Google Calendar Booking & PDF/Email
**Goal:** Wire up the booking and workflow automation tools.

### 4.1 Google Calendar Integration

#### [NEW] [tools/google_calendar.py](file:///Users/ris/Antigravity/Capstone%20project/backend/tools/google_calendar.py)
- `GoogleCalendarBooking` class (as per architecture sketch)
- `book_visit(listing, date, time_slot, user_email)` → creates calendar event + sends invite
- `_resolve_time_slot(date, slot)` → converts "morning"/"afternoon"/"evening" to ISO datetime ranges
  - morning: 10:00 AM – 12:00 PM
  - afternoon: 2:00 PM – 4:00 PM
  - evening: 5:00 PM – 7:00 PM
- Event includes: listing details in description, neighborhood as location, user as attendee
- Returns: `{confirmation_code, calendar_link, date, time_slot}`

#### [NEW] [api/routes/booking.py](file:///Users/ris/Antigravity/Capstone%20project/backend/api/routes/booking.py)
- `POST /api/booking` — accepts `{session_id, listing_id, preferred_date, preferred_time_slot, user_email}`
- Fetches listing from DB, calls `GoogleCalendarBooking.book_visit()`
- Returns confirmation with calendar link

> [!NOTE]
> **Phase 4 notes from implementation:**
> - Validation (email format, date parseable, date not in the past, known time
>   slot) runs *before* any Calendar API call, so bad input never costs a request.
> - Duplicate detection uses `extendedProperties.private.listing_id` on the event,
>   so re-booking the same listing in the same window returns the existing event
>   instead of creating a second one.
> - WeasyPrint needs pango/glib/cairo. On macOS (`brew install pango`) those land
>   in Homebrew's lib directory, which the dynamic loader doesn't search — the PDF
>   generator adds it to `DYLD_FALLBACK_LIBRARY_PATH` at import time. A deployment
>   container needs the equivalent apt packages.
> - `config.py` treats leftover `.env.example` placeholder values as unset, so a
>   half-filled `.env` reports "not configured" instead of failing inside an API
>   client.
> - **Service accounts cannot add attendees** to an event without Domain-Wide
>   Delegation, which requires Google Workspace — a consumer Gmail calendar gets
>   `403 "Service accounts cannot invite attendees"`. The booking flow therefore
>   inserts the event with attendees, and on that specific 403 retries without
>   them and emails the user the confirmation with an `.ics` attachment instead.
>   A Workspace deployment still gets native Google invites; nothing to change.
> - Gmail App Passwords are displayed as `abcd efgh ijkl mnop`; the spaces are
>   stripped in config. Python installed from python.org on macOS has no CA roots,
>   so STARTTLS needs certifi's bundle (added to requirements.txt).

> [!IMPORTANT]
> **Google Calendar setup required:**
> 1. Create a Google Cloud project
> 2. Enable Calendar API
> 3. Create a Service Account, download JSON key
> 4. Share your calendar with the service account email

### 4.2 PDF Generation

#### [NEW] [tools/pdf_generator.py](file:///Users/ris/Antigravity/Capstone%20project/backend/tools/pdf_generator.py)
- `generate_shortlist_pdf(listings, neighborhood_data)` → bytes (PDF content)
- Jinja2 HTML template with styled cards for each listing (rent, BHK, amenities, neighborhood snapshot)
- WeasyPrint converts HTML → PDF
- Returns PDF as bytes for email attachment

#### [NEW] [templates/shortlist_email.html](file:///Users/ris/Antigravity/Capstone%20project/backend/templates/shortlist_email.html)
- Jinja2 template: clean, styled HTML layout for the shortlist PDF
- Cards with property details, neighborhood snapshot, amenity icons

### 4.3 Email Sender

#### [NEW] [tools/email_sender.py](file:///Users/ris/Antigravity/Capstone%20project/backend/tools/email_sender.py)
- `send_shortlist_email(user_email, pdf_bytes, shortlist_summary)` → success/failure
- Uses `smtplib` + `email.mime` to compose email with PDF attachment
- Gmail SMTP: smtp.gmail.com:587, TLS, app password auth
- Subject: "Your Property Shortlist — Bengaluru"

#### [NEW] [api/routes/shortlist_pdf.py](file:///Users/ris/Antigravity/Capstone%20project/backend/api/routes/shortlist_pdf.py)
- `POST /api/shortlist/pdf` — accepts `{session_id, shortlist_ids, user_email}`
- Fetches listings, generates PDF, sends email
- Returns `{status, message}`

**Verify:**
- Call `/api/booking` with a real listing + email → confirm Google Calendar event created + invite received
- Call `/api/shortlist/pdf` with listing IDs + email → confirm PDF received in inbox

---

## Phase 5: Frontend — Companion UI
**Goal:** Build the React UI with voice input, shortlist display, and all required panels.

### 5.1 App Shell & State Management

#### [MODIFY] [App.jsx](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/App.jsx)
- Main layout: sidebar (transcript) + main content (shortlist cards) + side panel (neighborhood/sources)
- Dark theme, modern glassmorphism aesthetic
- Responsive layout

#### [NEW] [context/AppContext.jsx](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/context/AppContext.jsx)
- React Context + `useReducer` for global state
- State shape: `{ session_id, preferences, shortlist[], conversationHistory[], sources[], booking, isListening, isProcessing }`
- Actions: `SET_SHORTLIST`, `ADD_MESSAGE`, `SET_BOOKING`, `SET_LISTENING`, `UPDATE_SOURCES`

#### [NEW] [services/api.js](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/services/api.js)
- `sendMessage(session_id, text)` → POST `/api/chat`
- `getListings()` → GET `/api/listings`
- `bookVisit(session_id, listing_id, date, timeSlot, email)` → POST `/api/booking`
- `sendShortlistPDF(session_id, shortlistIds, email)` → POST `/api/shortlist/pdf`

### 5.2 Voice Input Component

#### [NEW] [components/VoiceInput.jsx](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/components/VoiceInput.jsx)
#### [NEW] [components/VoiceInput.css](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/components/VoiceInput.css)
- **Spoken greeting on arrival**: a fixed line is written to the transcript and
  spoken through the same ElevenLabs path as any reply. It costs no Gemini quota
  (no `/api/chat` call) and, being fixed text, is served from the TTS cache after
  the first synthesis. Browsers block audio before any interaction, so if playback
  is refused the greeting is retried once on the visitor's first click or keypress —
  and it is always readable in the transcript regardless. It is suppressed when a
  session is restored, so a returning visitor isn't greeted over their own conversation.
- Floating mic button (bottom-center) with pulse animation when listening
- Web Speech API `SpeechRecognition` for STT
  - `continuous: true`, `interimResults: true` for live transcript
  - `lang: 'en-IN'` for Indian English
- On final result → send to backend via `api.sendMessage()`
- TTS: `speechSynthesis.speak()` for assistant responses
- States: idle (mic icon) → listening (animated pulse) → processing (spinner)

> [!NOTE]
> **Speech output uses ElevenLabs, not the browser voice.** `speechSynthesis`
> sounded robotic, so replies are synthesized by `POST /api/tts`
> (`tools/tts.py` → ElevenLabs `eleven_flash_v2_5`) and played as MP3.
> Measured ~0.6–0.9s live, ~1ms from cache. `speechSynthesis` remains the
> fallback whenever the key is missing, the quota is spent, or playback fails.
> - Free accounts may only use **premade** voices over the API; Voice Library
>   voices (including Rachel, `21m00Tcm4TlvDq8ikWAM`) return HTTP 402. Verified
>   working: Sarah `EXAVITQu4vr4xnSDxMaL` (default), Jessica, Brian, George.
> - The free tier is ~10k characters/month, so identical replies are cached in
>   process and replies over 600 characters are refused rather than truncated.

#### [NEW] [utils/speechUtils.js](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/utils/speechUtils.js)
- `startRecognition(onInterim, onFinal)` — wraps Web Speech API
- `speakText(text)` — wraps SpeechSynthesis with Indian English voice preference
- `isSupported()` — check browser support, show fallback text input if not

### 5.3 Transcript Panel

#### [NEW] [components/TranscriptPanel.jsx](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/components/TranscriptPanel.jsx)
#### [NEW] [components/TranscriptPanel.css](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/components/TranscriptPanel.css)
- Scrolling chat-style transcript (user bubbles + assistant bubbles)
- Live interim transcript shown in grey while user is speaking
- Auto-scroll to bottom on new messages
- Timestamps on each message

### 5.4 Shortlist Panel & Listing Cards

#### [NEW] [components/ShortlistPanel.jsx](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/components/ShortlistPanel.jsx)
#### [NEW] [components/ShortlistPanel.css](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/components/ShortlistPanel.css)
- Grid of listing cards (or scrollable list)
- "No listings yet — speak your preferences to get started" empty state
- Smooth transitions when cards are added/removed (CSS transitions)

#### [NEW] [components/ListingCard.jsx](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/components/ListingCard.jsx)
#### [NEW] [components/ListingCard.css](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/components/ListingCard.css)
- Card displays: rent (₹), bedrooms (BHK), sqft, furnishing, amenities (icon chips)
- Match reasons shown as small tags ("Within budget", "Has parking")
- Click to expand → shows neighborhood snapshot
- Hover animation (subtle lift + shadow)

### 5.5 Neighborhood Panel

#### [NEW] [components/NeighborhoodPanel.jsx](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/components/NeighborhoodPanel.jsx)
#### [NEW] [components/NeighborhoodPanel.css](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/components/NeighborhoodPanel.css)
- Slide-out panel when a listing card is selected
- Sections: Transit (metro stations, bus stops from MCP), Safety notes (from RAG), Nearby amenities (groceries, hospitals from MCP)
- Each section shows data source (MCP or RAG)

### 5.6 Sources Panel

#### [NEW] [components/SourcesPanel.jsx](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/components/SourcesPanel.jsx)
#### [NEW] [components/SourcesPanel.css](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/components/SourcesPanel.css)
- Collapsible panel listing all citations used in the current shortlist
- Each source: title, URL (clickable), what it was used for
- Grouped by neighborhood

### 5.7 Booking Panel

#### [NEW] [components/BookingPanel.jsx](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/components/BookingPanel.jsx)
#### [NEW] [components/BookingPanel.css](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/components/BookingPanel.css)
- Shows after user books a visit via voice
- Displays: listing name, date, time slot, confirmation code, Google Calendar link (clickable)
- "Open in Google Calendar" button
- Success animation on booking confirmation

> [!NOTE]
> **Phase 5 notes from implementation:**
> - **Design direction:** dark map-at-night ground with marigold as the only
>   signal colour, teal reserved *exclusively* for sourced facts, and vermilion
>   for missing data — so the palette itself tells you what is grounded.
>   Bricolage Grotesque / Instrument Sans / JetBrains Mono, with mono on every
>   number (rents, distances, coordinates) so data reads as instrument output.
> - **Signature element — the proximity dial** (`components/ProximityDial.jsx`):
>   concentric 0.5/1/1.5 km rings with real OpenStreetMap places plotted at their
>   true distance *and bearing*, computed from the listing's coordinates. It makes
>   the MCP integration visible on the card rather than buried in a list.
> - POIs load when a card is opened (`GET /api/listings/{id}/nearby`), matching
>   the backend's on-demand enrichment; the card distinguishes "none within
>   1.5 km" from "lookup unavailable".
> - Guide text is lightly marked up (`**bold**`, `- ` bullets); the neighborhood
>   panel renders that as real structure instead of leaking asterisks.
> - Mobile stacks into a single scrolling page. A viewport-height grid clipped the
>   shortlist and let it paint over the panel below, so at ≤900px the workspace
>   is a plain block and the page itself scrolls.
> - `VITE_API_URL` selects the backend; `.env.local` overrides it for local runs.

### 5.8 Main Stylesheet

#### [NEW] [App.css](file:///Users/ris/Antigravity/Capstone%20project/frontend/src/App.css)
- CSS custom properties for design tokens (colors, spacing, radii, shadows)
- Dark theme with vibrant accent colors
- Glassmorphism effects (backdrop-filter, semi-transparent backgrounds)
- Responsive breakpoints (mobile, tablet, desktop)
- Smooth micro-animations (transitions, keyframes)
- Google Fonts: Inter or Outfit

**Verify:**
- Run `npm run dev`, confirm UI renders with all panels
- Click mic → speak → see live transcript → see shortlist cards appear
- Click a listing → see neighborhood panel
- See sources panel with citations
- Book a visit → see confirmation panel with calendar link

---

## Phase 6: AI Evaluations
**Goal:** Implement the 3 required evals with test transcripts and a CLI runner.

### 6.1 Test Transcripts

#### [NEW] [evals/test_transcripts/budget_check.json](file:///Users/ris/Antigravity/Capstone%20project/backend/evals/test_transcripts/budget_check.json)
- 3-5 test cases for budget and must-have compliance
- Each case: user turns → expected shortlist behavior → assertions

#### [NEW] [evals/test_transcripts/voice_edit.json](file:///Users/ris/Antigravity/Capstone%20project/backend/evals/test_transcripts/voice_edit.json)
- 3-5 test cases for shortlist refinement commands
- Test: "drop above X", "only show pet-friendly", "add one with balcony"
- Assert: only intended changes, no side effects

#### [NEW] [evals/test_transcripts/grounding_check.json](file:///Users/ris/Antigravity/Capstone%20project/backend/evals/test_transcripts/grounding_check.json)
- 3-5 test cases for grounding and hallucination
- Test: neighborhood claims have citations, missing data is flagged

### 6.2 Eval Implementations

#### [NEW] [evals/feasibility_eval.py](file:///Users/ris/Antigravity/Capstone%20project/backend/evals/feasibility_eval.py)
- **Rule-based**: For each test case, replay the conversation through the orchestrator
- Check: every shortlisted listing's rent ≤ stated budget
- Check: every shortlisted listing has all required amenities
- Check: if commute point stated, commute claims are consistent
- Output: pass/fail per assertion, overall pass rate

#### [NEW] [evals/edit_correctness.py](file:///Users/ris/Antigravity/Capstone%20project/backend/evals/edit_correctness.py)
- **Rule-based + LLM-assisted**: Replay conversations with refinement commands
- Check: after "drop above 40k", no listing > 40k remains
- Check: listings that should be unchanged are byte-identical before and after
- Check: no new listings appeared that shouldn't have
- Output: pass/fail per edit command, diff of changes

#### [NEW] [evals/grounding_eval.py](file:///Users/ris/Antigravity/Capstone%20project/backend/evals/grounding_eval.py)
- **LLM-assisted**: Extract all neighborhood claims from assistant responses
- For each claim, check if a matching RAG chunk exists (semantic similarity > threshold)
- Flag any claim that cannot be traced to a source
- Check: when neighborhood data is missing, assistant says "I don't have data on that"
- Output: cited/uncited claims count, hallucination score

### 6.3 Eval Runner

#### [NEW] [evals/run_evals.py](file:///Users/ris/Antigravity/Capstone%20project/backend/evals/run_evals.py)
- CLI: `python evals/run_evals.py [--eval feasibility|edit|grounding|all]`
- Loads test transcripts, runs selected evals, prints results table
- Outputs JSON report to `evals/results/`
- Exit code: 0 if all pass, 1 if any fail

> [!NOTE]
> **Phase 6 notes from implementation:**
> - **Similarity thresholds do not detect hallucination on this corpus.** The spec's
>   "similarity > 0.75" rule was measured and fails: invented claims score 0.73-0.83
>   and true ones 0.75-0.91, so the ranges overlap. "Rents in HSR Layout fell twelve
>   percent last year" (pure invention) scores 0.834 — higher than the true claim
>   "Indiranagar Metro Station is on the Purple Line" at 0.750, because embeddings
>   measure topical closeness, not support. The eval therefore RETRIEVES by
>   similarity but DECIDES with an LLM entailment judge, which scores 12/12 on the
>   calibration set. Re-check with `python evals/calibrate.py`.
> - A 4th eval, **scope**, asserts the rental-only boundary (no invented sale prices).
> - **The runner never scores a turn the model did not answer.** Free-tier quota is
>   15 requests/minute and 500/day; turns are paced (`EVAL_TURN_DELAY`, default 5s)
>   and quota errors are waited out. If the quota is genuinely spent the eval reports
>   `INCOMPLETE` and exits 2 — distinct from a behavioural failure, which exits 1.
>
> **Defects these evals found and fixed:**
> | Found by | Defect | Fix |
> |---|---|---|
> | edit_003 | "Add another option with a balcony" *replaced* the shortlist — a shortlist was always the last search's results | `mode: "append"` on `search_listings`; the orchestrator unions with what's on screen |
> | edit_004 | Agent said "narrowed to two fully furnished" while five listings, three semi-furnished, stayed on screen — it filtered in prose because the tool had no such filter | added a real `furnishing` filter to the tool and the SQL |
> | edit_002 | Asked which neighborhood when budget + bedrooms were already given | prompt: search all three once budget and bedrooms are known, then offer to narrow |
> | grounding_005 | Answered the nightlife half of a two-part question and silently dropped "crime rate in that block" | prompt: answer what you can AND name the part you cannot |
> | (eval bugs) | Sub-area labels ("Koramangala 5th and 6th blocks") retrieved nothing, so true claims read as hallucinations; forbidden-phrase lists matched correct refusals ("cannot provide a purchase price") | fall back to a corpus-wide search; forbid only sale-price units |

**Verify:** `python evals/run_evals.py --eval all` — runs all 4 evals and writes reports to `evals/results/`.

---

## Phase 7: Integration, Polish & Testing
**Goal:** Wire everything together, fix edge cases, and polish the UX.

### 7.1 End-to-End Integration Testing
- Full voice flow: speak preferences → get shortlist → refine → explain → book → PDF email
- Test all conversation state transitions
- Test error cases: empty results, MCP timeout, invalid date for booking
- Test browser compatibility (Chrome, Edge)

### 7.2 UX Polish
- Loading states for all async operations (shortlist generation, booking, PDF)
- Error toasts for failed operations
- Fallback text input for browsers without Web Speech API
- Empty states for all panels
- Smooth transitions when shortlist changes (cards animate in/out)

> [!NOTE]
> **Phase 7 notes from implementation:**
> - **Session survives a refresh.** `sessionStorage` holds the session id and the
>   app restores state from `GET /api/chat/{id}` on load — an accidental reload
>   no longer discards the shortlist mid-demo. Restored messages carry no
>   timestamp (the server doesn't record one) and render `·` rather than a fake time.
> - **A stale key path took booking offline.** `GOOGLE_SERVICE_ACCOUNT_FILE` in
>   `.env` had reverted to the Downloads filename, so bookings failed with
>   "temporarily unavailable" while the key sat next to it. The path is corrected,
>   and `_resolve_service_account_path()` now falls back to `service-account.json`
>   with a warning rather than taking the feature down.
>
> **Verified end to end** (one session): collecting → shortlist → refine → explain →
> RAG-grounded safety answer → booking (`visit_booked`, real calendar event) → PDF
> emailed. Edge cases confirmed live: booking with no shortlist guides back; an
> impossible search reports the dataset's actual floor; February 30th is rejected;
> backend down shows an error bar and *keeps* the shortlist and transcript on screen.
> Browser support: Chromium verified directly; the no-`SpeechRecognition` path
> (Firefox, Safari) verified by running `speechUtils` against a window without it —
> the component starts in typed mode and `startRecognition` returns null safely.

### 7.3 Edge Cases
- User speaks preferences that match 0 listings → assistant says so, suggests relaxing constraints
- User asks about a neighborhood we don't have data for → assistant says "I don't have data on that area"
- MCP server is unreachable → graceful degradation, show listings without POI data
- User tries to book without a shortlist → assistant guides them back
- Multiple rapid voice inputs → debounce/queue

---

## Phase 8: Deployment & Deliverables
**Goal:** Deploy to public URLs and prepare all submission materials.

### 8.1 Backend Deployment (Railway/Render)
- Dockerfile or `Procfile` for FastAPI
- Environment variables configured on platform
- Persistent volume for SQLite + ChromaDB files
- Ensure MCP server (Node.js) is available in container
- Test all endpoints from deployed URL

### 8.2 Frontend Deployment (Vercel)
- Connect Git repo to Vercel
- Set `VITE_API_URL` env var pointing to deployed backend
- Confirm CORS works between Vercel frontend ↔ Railway backend
- Test voice input on deployed site

### 8.3 README

#### [NEW] [README.md](file:///Users/ris/Antigravity/Capstone%20project/README.md)
- Architecture overview (with diagram)
- Tech stack summary
- Setup instructions (local dev)
- How MCP was integrated (with code snippets)
- Data: how listings were scraped and cleaned
- How to run evals: `python evals/run_evals.py --eval all`
- Sample test transcripts
- Deployment instructions
- Links to deployed app

> [!NOTE]
> **Phase 8 notes from implementation:**
> - The backend ships as a **container** rather than a plain Python buildpack: it
>   needs WeasyPrint's native libraries *and* Node.js on PATH, because the
>   OpenStreetMap MCP server is an npx-launched Node process. A buildpack gives
>   you neither.
> - `entrypoint.sh` seeds SQLite + ChromaDB on first boot when they're absent, so
>   an ephemeral filesystem works without a volume (the `render.yaml` disk is
>   optional and only avoids re-embedding on each deploy).
> - A key file can't be committed, so `GOOGLE_SERVICE_ACCOUNT_JSON` accepts the
>   whole JSON as an env var and `main.py` writes it to disk (mode 600) at startup.
> - `FRONTEND_URL` is now comma-separated, so preview domains can be allowed
>   alongside production.
>
> **Verified locally:** service-account-from-env writes the key and a real booking
> succeeded with it; CORS allows the deployed origin and omits the header for an
> unlisted one; the frontend production build bakes in `VITE_API_URL`.
> **Not verified:** the Docker image has never been built — Docker isn't installed
> on this machine — and nothing has been deployed. Treat the Dockerfile as
> unproven until its first build.

### 8.4 Demo Video (5 minutes)
Record a demo showing:
1. Voice-based preference collection (~1 min)
2. Voice-based shortlist edit (~1 min)
3. Explanation — "why this one?" with citations (~1 min)
4. Sources view in UI (~30 sec)
5. Booking a visit → Google Calendar event (~30 sec)
6. At least one eval running (~1 min)

---

## Scope: rentals only

> [!IMPORTANT]
> The objective sentence says "a renter **or buyer's** spoken preferences", but every
> requirement under it is rental-specific: the mandated source (bengaluru.rent) is a
> rental-transparency site, the required fields are "**rent**, bedrooms, furnishing,
> amenities, society name, square footage, availability status", the sample commands
> are monthly ("budget 35k", "drop anything above 40k"), and the rubric grades
> "shortlist respects stated budget". The scraped dataset has exactly one price field,
> `rent` (₹15,000–55,000/month) — there is no sale price in it.
>
> **Decision: rentals only, stated explicitly rather than left implicit.** Adding a buy
> mode on this data would mean inventing sale prices, which breaks the grounding
> requirement the evals are built around. Instead:
> - the system prompt scopes the assistant to long-term rentals and forbids quoting any
>   sale price, per-sqft rate or valuation;
> - buy intent gets an honest redirect ("I only have rentals — tell me your monthly
>   budget and I'll find 3BHKs in Koramangala");
> - the UI says "Rentals only, in Koramangala, Indiranagar and HSR Layout" in the
>   masthead, transcript and empty state;
> - `evals/test_transcripts/scope_check.json` asserts no invented sale prices, covering
>   buy intent, sale enquiries, a rent-restated-as-price trap, and an uncovered area.
>
> Revisit only if real for-sale listings are sourced for the same neighborhoods; that
> needs a `listing_type` column, a `price` field, and a second scrape.

## Open Questions

> [!IMPORTANT]
> **1. Google Calendar auth approach:**
> Service Account (simpler, no user consent needed, but requires sharing calendar) vs. OAuth2 (requires user consent flow in UI). **Recommendation: Service Account** — simpler for a prototype.

> [!IMPORTANT]
> **2. Neighborhoods selection:**
> The plan uses **Koramangala, Indiranagar, HSR Layout**. Are these the 3 neighborhoods you want, or do you prefer different ones?

> [!IMPORTANT]
> **3. LLM choice:** ~~Gemini 2.0 Flash~~ — **resolved in Phase 3.** 2.0/2.5 Flash
> are no longer available to new API keys. Now on `gemini-3.1-flash-lite` with
> `thinking_level=low`; `gemini-3.6-flash` works too but its free tier is 20
> requests/day.

> [!IMPORTANT]
> **4. Deployment platforms:**
> **Vercel** (frontend) + **Railway** (backend). Are you set up on these platforms, or do you prefer alternatives (e.g., Render, Fly.io)?

---

## Estimated Effort by Phase

| Phase | Description | Estimated Time |
|-------|-------------|---------------|
| 0 | Project setup + data collection | 1-2 days |
| 1 | Backend foundation (FastAPI, listings, state) | 0.5 day |
| 2 | RAG pipeline + MCP integration | 1 day |
| 3 | LLM orchestrator (agent core) | 1-2 days |
| 4 | Google Calendar + PDF/Email | 0.5 day |
| 5 | Frontend companion UI | 2-3 days |
| 6 | AI evaluations | 1 day |
| 7 | Integration, polish, testing | 1-2 days |
| 8 | Deployment + deliverables | 0.5-1 day |
| **Total** | | **~8-12 days** |

---

## Verification Plan

### Automated Tests
- `python evals/run_evals.py --eval all` — runs all 3 AI evals
- Manual curl tests for each API endpoint during development

### Manual Verification
- Full voice conversation flow in Chrome (preference → shortlist → refine → explain → book)
- Google Calendar invite received in email
- PDF shortlist received in email
- Deployed site accessible at public URL
- MCP call visible in demo (OpenStreetMap data in UI)
- Citations visible in Sources panel for every neighborhood claim
