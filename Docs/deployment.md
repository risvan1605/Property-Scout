# Deployment Plan — Railway (one service)

Target topology:

```
  Browser ──HTTPS──> Railway (one container)
                        ├── /            → the compiled React bundle
                        └── /api/*       → FastAPI
                              ├── Gemini API       (agent + embeddings)
                              ├── ElevenLabs       (spoken replies)
                              ├── Overpass via MCP (nearby places)
                              ├── Google Calendar  (site visits)
                              └── Gmail SMTP       (shortlist PDF)
```

The root [`Dockerfile`](../Dockerfile) builds the Vite bundle in a Node stage and
copies it into the Python image at `backend/static`, which `main.py` serves. The
UI and the API therefore share an origin — **nothing is cross-origin, so there is
no CORS to configure and no URL to wire back and forth.**

Budget about 20 minutes for a first run, most of it waiting on the build.

---

## 0. Before you start

| Need | Notes |
|---|---|
| GitHub repo | `https://github.com/risvan1605/Property-Scout.git` |
| Railway account | railway.app — Hobby plan is enough |
| Your keys | Everything currently in `backend/.env` — you'll re-enter them in Railway |

Have `backend/.env` open while you work; step 2 copies values out of it.
**`.env`, `service-account.json` and `voice-agent-*.json` are gitignored and
`.dockerignore`d, and must stay that way** — the Google key goes to Railway as an
environment variable instead of a file.

---

## 1. Push the repo

```bash
cd "/Users/ris/Antigravity/Capstone project"
git push origin main
```

**Before pushing, confirm no secrets are staged:**

```bash
git status --short | grep -E "\.env$|service-account|voice-agent-.*\.json" && \
  echo "STOP — secrets staged" || echo "clean"
```

`backend/.env`, `backend/service-account.json`, `backend/chroma_db/`,
`backend/listings.db` and `backend/static/` must **not** appear.

---

## 2. Deploy on Railway

1. **New Project → Deploy from GitHub repo** → pick the repo.
2. **Leave Root Directory empty.** The build context must be the repository root,
   because the image needs both `frontend/` and `backend/`. Setting it to
   `backend` — which earlier versions of this guide told you to do — makes the
   build fail on the missing `frontend/` directory.
3. Railway reads `railway.json` at the root: Docker builder, health check on
   `/api/health` with a 300s timeout (generous, because the first boot seeds the
   vector store).
4. **Variables** — only the secrets. Everything else already has the right
   default in `config.py`, and a second copy is just a second place to get it
   wrong.

| Variable | Value | Notes |
|---|---|---|
| `GEMINI_API_KEY` | *(from .env)* | **Required.** Everything else degrades gracefully |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | *the file's whole contents* | `cat backend/service-account.json \| pbcopy`. Not the filename — the JSON itself |
| `GOOGLE_CALENDAR_ID` | *(from .env)* | Booking; omit → booking reports unavailable |
| `SMTP_USER` | *(from .env)* | Emailing the shortlist PDF |
| `SMTP_PASS` | *(from .env)* | Gmail **app password**, 16 chars |
| `ELEVENLABS_API_KEY` | *(from .env)* | Optional; omit → the browser's own voice |

**Do not set anything else.** `GEMINI_MODEL`, `LLM_THINKING_LEVEL`,
`LLM_TEMPERATURE`, `SMTP_HOST`, `SMTP_PORT`, `ELEVENLABS_VOICE_ID`,
`ELEVENLABS_MODEL`, `GOOGLE_SERVICE_ACCOUNT_FILE`, `RAG_MIN_SIMILARITY`, the
`ENRICH_*`, `PREFETCH_*` and `MCP_*` knobs and `OSM_OVERPASS_ENDPOINTS` all
default to exactly the values a deployment wants. Set one only to *change* it —
the table in `backend/.env.example` documents what each does.

Three that would be actively wrong here:

- `PORT` — Railway injects it and `entrypoint.sh` reads it; setting it breaks routing.
- `FRONTEND_URL` — only meaningful for a split deployment (§7). One container
  serving both halves has no cross-origin request to allow.
- `BACKEND_URL` — dead: defined in `config.py` and referenced nowhere.

5. **Deploy.** Watch the build log for three things: the Node stage running
   `npm ci` and `vite build`, the apt step installing `libpango`/`libcairo`
   (WeasyPrint) and `nodejs` (the MCP server runs via `npx`). First boot then
   seeds SQLite and embeds 27 chunks into ChromaDB — roughly 30–60s, and it costs
   ~27 Gemini embedding calls.
6. **Settings → Networking → Generate Domain.** Note the URL, e.g.
   `https://property-scout.up.railway.app`. That single URL serves both halves.
7. Confirm it's alive:

```bash
curl https://<your-railway-domain>/api/health
# {"status":"ok","listings":15,"chroma":true,"voice":true}
```

All four must be true: `chroma:false` means seeding failed (check `GEMINI_API_KEY`),
`voice:false` means no ElevenLabs key.

### Optional: a volume, to skip re-seeding on every deploy

Railway's filesystem is ephemeral, so by default the vector store is rebuilt on each
deploy. To persist it: **Settings → Volumes → mount at `/data`**, then add

```
SQLITE_DB_PATH=/data/listings.db
CHROMA_DB_PATH=/data/chroma_db
```

Mount at `/data`, never at `/app` — a volume there would shadow the application
code, the compiled frontend included.

---

## 3. Verify the deployment

Run these against the live service — one domain for everything:

```bash
APP=https://<your-railway-domain>

curl -s -o /dev/null -w '%{http_code} %{content_type}\n' $APP/          # 200 text/html
curl -s $APP/api/health                     # all four flags true
curl -s $APP/api/listings | head -c 120     # 15 listings
curl -s -X POST $APP/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"text":"2BHK in Koramangala under 35k"}' | head -c 300
curl -s -o /dev/null -w '%{http_code}\n' -X POST $APP/api/tts \
  -H 'Content-Type: application/json' -d '{"text":"Hello"}'   # 200
curl -s -o /dev/null -w '%{http_code}\n' $APP/api/no-such-route   # 404, not HTML
```

That last one matters: an unmatched `/api/*` path must 404 rather than fall through
to the SPA shell. A `fetch` that receives an index page instead of JSON is far
harder to debug than an honest 404.

Then in **Chrome** on the same URL:

- [ ] The scout greets you out loud on load
- [ ] Mic button → allow microphone → speak "2BHK in Koramangala under 35k"
- [ ] Cards appear; the reply is spoken in the ElevenLabs voice
- [ ] Open a card → neighborhood panel + proximity dial with real POIs
- [ ] Sources panel lists citations with working links
- [ ] "Drop anything above 30k" → shortlist narrows
- [ ] Deep-link a client route (reload on a non-root path) → the app still loads
- [ ] Book a visit for "next Friday" → it reads the date back before booking
- [ ] Confirm → confirmation panel + calendar link; check the invite email
- [ ] "Email me the shortlist at …" → PDF arrives

Voice input needs **Chrome or Edge**; Safari and Firefox fall back to the typed
input. HTTPS is required for microphone access — Railway gives you that.

---

## 4. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Build fails, "Dockerfile not found" | Root Directory set | Railway → Settings → Root Directory must be **empty** |
| Build fails in the Node stage | `package-lock.json` out of sync | Run `npm install` in `frontend/`, commit the lockfile |
| `/` returns 404, API works | Frontend stage didn't copy | Check the build log for `vite build`; `backend/static/index.html` must exist in the image |
| UI loads, every call 404s | Bundle built with a stale base URL | `VITE_API_URL` must be **unset** for this deployment; check `frontend/.env.development` isn't being read at build |
| `health` shows `chroma:false` | Seeding failed | Check `GEMINI_API_KEY`; see deploy logs for the seed step |
| `health` shows `voice:false` | No ElevenLabs key | Add `ELEVENLABS_API_KEY` |
| Replies take ~20s | `LLM_THINKING_LEVEL` overridden to something above `low` | Remove the override — `low` is the default in `config.py` and is what you want |
| "I've used up my AI quota" | Free tier: 15/min, 500/day | Wait, or switch `GEMINI_MODEL` (each model has its own quota) |
| TTS silent, logs show 402 | Voice Library voice on a free plan | Use a premade voice id (Sarah is the default) |
| "Booking is temporarily unavailable" | Key or calendar sharing | Check `GOOGLE_SERVICE_ACCOUNT_JSON` parses; share the calendar with the service-account email |
| Booking works, no Google invite | Service accounts can't add attendees without Domain-Wide Delegation | Expected on consumer Gmail — the app emails an `.ics` instead |
| Booking rejected as "already passed" | Server clock vs Bengaluru | The guard compares in `Asia/Kolkata`; `tzdata` must be installed (it's in `requirements.txt`) |
| "Nearby places unavailable" | Overpass rate-limited or slow | Usually transient. The background prefetch no longer trips the breaker, so a foreground lookup still tries. Set `OSM_OVERPASS_ENDPOINTS` to a **worldwide** mirror if it persists |
| Email fails, logs show auth error | Not an app password | Gmail → 2-Step Verification → App passwords |

---

## 5. Running costs

Everything sits on free tiers, with these ceilings:

- **Gemini**: 15 requests/minute, 500/day. A conversational turn costs ~2.
- **ElevenLabs**: ~10k characters/month (~65 replies). Identical replies are cached;
  past the cap it falls back to the browser voice rather than going silent.
- **Overpass**: no key, but it rate-limits by IP.
- **Railway**: Hobby plan usage-based; this service idles cheaply. One service now
  instead of two, and the image is a little larger for carrying the bundle.

For a live demo, the Gemini daily cap is the real constraint — don't burn the day's
500 on rehearsals.

---

## 6. Redeploying and rolling back

Push to `main` → Railway rebuilds both halves together. Roll back from
**Deployments → ⋯ → Redeploy** on an earlier build; because the frontend ships
inside the same image, a rollback reverts the UI and the API as one unit.

Changing a variable triggers a redeploy. Note that `VITE_*` variables are inlined
at **build** time, so they need a rebuild rather than a restart — this deployment
deliberately uses none.

---

## 7. Splitting the frontend out again

If you later want the UI on a CDN instead:

1. Host `frontend/` as a static Vite build, setting `VITE_API_URL` to the Railway
   domain (no trailing slash).
2. Set `FRONTEND_URL` on Railway to the UI's origin, or `ALLOWED_ORIGIN_REGEX` for
   preview URLs whose hostname changes per push.

The CORS middleware is still wired up for exactly this, and for local development.

---

## 8. After deploying

Update `README.md` with the live URL, and record the demo video against the
deployed site rather than localhost — it's more convincing, and it proves the
deployment works.
