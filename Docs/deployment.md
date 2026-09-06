# Deployment Plan — Railway (backend) + Vercel (frontend)

Target topology:

```
  Browser ──HTTPS──> Vercel (static React build)
                          │  VITE_API_URL (baked in at build time)
                          ▼
                     Railway (Docker: FastAPI + Node for MCP)
                          ├── Gemini API        (agent + embeddings)
                          ├── ElevenLabs        (spoken replies)
                          ├── Overpass via MCP   (nearby places)
                          ├── Google Calendar    (site visits)
                          └── Gmail SMTP         (shortlist PDF)
```

**Deploy in this order.** Each side needs the other's URL, so the loop is closed in
step 4 rather than up front:

> backend first (get its URL) → frontend with that URL (get its URL) → set the
> frontend URL back on the backend for CORS → redeploy backend.

Budget about 40 minutes for a first run, most of it waiting on builds.

---

## 0. Before you start

| Need | Notes |
|---|---|
| GitHub repo | The project has no commits yet — step 1 covers it |
| Railway account | railway.app — Hobby plan is enough |
| Vercel account | vercel.com — Hobby plan is enough |
| Your keys | Everything currently in `backend/.env` — you'll re-enter them in Railway |

Have `backend/.env` open while you work; step 2 copies values out of it.
**`.env` and `service-account.json` are gitignored and must stay that way** — the
Google key goes to Railway as an environment variable instead of a file.

---

## 1. Push the repo

```bash
cd "/Users/ris/Antigravity/Capstone project"
git add .
git commit -m "Voice-first AI property scout"
git branch -M main
git remote add origin https://github.com/<you>/property-scout.git
git push -u origin main
```

**Before pushing, confirm no secrets are staged:**

```bash
git status --short | grep -E "\.env$|service-account|voice-agent-.*\.json" && \
  echo "STOP — secrets staged" || echo "clean"
```

97 files should be tracked. `backend/.env`, `backend/service-account.json`,
`backend/chroma_db/` and `backend/listings.db` must **not** appear.

---

## 2. Backend on Railway

1. **New Project → Deploy from GitHub repo** → pick the repo.
2. **Settings → Source → Root Directory: `backend`.**
   This is the step people miss. Without it Railway looks for a Dockerfile at the
   repo root and the build fails.
3. Railway reads `backend/railway.json`: Docker builder, health check on
   `/api/health` with a 300s timeout (generous, because the first boot seeds the
   vector store).
4. **Variables** — add these (values from `backend/.env`):

| Variable | Value | Notes |
|---|---|---|
| `GEMINI_API_KEY` | *(from .env)* | Required. Everything else degrades gracefully |
| `GEMINI_MODEL` | `gemini-3.1-flash-lite` | 15 req/min, 500/day on the free tier |
| `LLM_THINKING_LEVEL` | `low` | Without this each reply costs ~20s |
| `ELEVENLABS_API_KEY` | *(from .env)* | Omit → browser voice |
| `ELEVENLABS_VOICE_ID` | `EXAVITQu4vr4xnSDxMaL` | Sarah; free tier can't use Voice Library voices |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | *paste the whole file* | `cat backend/service-account.json \| pbcopy` |
| `GOOGLE_CALENDAR_ID` | *(from .env)* | |
| `SMTP_HOST` | `smtp.gmail.com` | |
| `SMTP_PORT` | `587` | |
| `SMTP_USER` | *(from .env)* | |
| `SMTP_PASS` | *(from .env)* | Gmail **app password**, 16 chars |
| `FRONTEND_URL` | `http://localhost:5173` | Placeholder — corrected in step 4 |

Do **not** set `PORT`; Railway injects it and `entrypoint.sh` reads it.

5. **Deploy.** Watch the build log for two things: the apt step installing
   `libpango`/`libcairo` (WeasyPrint), and `nodejs` (the MCP server runs via `npx`).
   First boot then seeds SQLite and embeds 27 chunks into ChromaDB — roughly 30–60s,
   and it costs ~27 Gemini embedding calls.
6. **Settings → Networking → Generate Domain.** Note the URL, e.g.
   `https://property-scout-api.up.railway.app`.
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

Mount at `/data`, never at `/app` — a volume there would shadow the application code.

---

## 3. Frontend on Vercel

1. **Add New → Project** → same GitHub repo.
2. **Root Directory: `frontend`.** Framework preset auto-detects as Vite.
3. **Environment Variables:**

| Variable | Value |
|---|---|
| `VITE_API_URL` | `https://<your-railway-domain>` — no trailing slash |

   Vite inlines this **at build time**, so changing it later requires a redeploy,
   not just a restart.
4. **Deploy**, then note the URL, e.g. `https://property-scout.vercel.app`.

At this point the app loads but every request fails CORS. That's expected — step 4
fixes it.

---

## 4. Close the CORS loop

Back in **Railway → Variables**, set:

```
FRONTEND_URL=https://property-scout.vercel.app
```

Comma-separate to allow more than one origin. For Vercel preview deployments, whose
URL changes every push, add a regex instead of listing them:

```
ALLOWED_ORIGIN_REGEX=https://property-scout-.*\.vercel\.app
```

Railway redeploys on variable change. Wait for it to go green, then hard-reload the
Vercel site.

---

## 5. Verify the deployment

Run these against the live backend:

```bash
API=https://<your-railway-domain>

curl -s $API/api/health                     # all four flags true
curl -s $API/api/listings | head -c 120     # 15 listings
curl -s -X POST $API/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"text":"2BHK in Koramangala under 35k"}' | head -c 300
curl -s -o /dev/null -w '%{http_code}\n' -X POST $API/api/tts \
  -H 'Content-Type: application/json' -d '{"text":"Hello"}'   # 200
```

Then in **Chrome** on the Vercel URL:

- [ ] The scout greets you out loud on load
- [ ] Mic button → allow microphone → speak "2BHK in Koramangala under 35k"
- [ ] Cards appear; the reply is spoken in the ElevenLabs voice
- [ ] Open a card → neighborhood panel + proximity dial with real POIs
- [ ] Sources panel lists citations with working links
- [ ] "Drop anything above 30k" → shortlist narrows
- [ ] Book a visit → confirmation panel + calendar link; check the invite email
- [ ] "Email me the shortlist at …" → PDF arrives

Voice input needs **Chrome or Edge**; Safari and Firefox fall back to the typed
input. HTTPS is required for microphone access — both platforms give you that.

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Build fails, "Dockerfile not found" | Root Directory not set | Railway → Settings → Root Directory = `backend` |
| Browser console: CORS blocked | `FRONTEND_URL` still the placeholder | Step 4, then wait for the redeploy |
| Frontend calls `localhost:8000` | `VITE_API_URL` missing at build | Set it in Vercel, then **redeploy** (build-time) |
| `health` shows `chroma:false` | Seeding failed | Check `GEMINI_API_KEY`; see deploy logs for the seed step |
| `health` shows `voice:false` | No ElevenLabs key | Add `ELEVENLABS_API_KEY` |
| Replies take ~20s | `LLM_THINKING_LEVEL` unset | Set it to `low` |
| "I've used up my AI quota" | Free tier: 15/min, 500/day | Wait, or switch `GEMINI_MODEL` (each model has its own quota) |
| TTS silent, logs show 402 | Voice Library voice on a free plan | Use a premade voice id (Sarah is the default) |
| "Booking is temporarily unavailable" | Key or calendar sharing | Check `GOOGLE_SERVICE_ACCOUNT_JSON` parses; share the calendar with the service-account email |
| Booking works, no Google invite | Service accounts can't add attendees without Domain-Wide Delegation | Expected on consumer Gmail — the app emails an `.ics` instead |
| "Nearby places unavailable" | Overpass rate-limited the host | Usually transient; the circuit breaker retries after 5 min. Or set `OSM_OVERPASS_ENDPOINTS` to a mirror |
| Email fails, logs show auth error | Not an app password | Gmail → 2-Step Verification → App passwords |

---

## 7. Running costs

Everything sits on free tiers, with these ceilings:

- **Gemini**: 15 requests/minute, 500/day. A conversational turn costs ~2.
- **ElevenLabs**: ~10k characters/month (~65 replies). Identical replies are cached;
  past the cap it falls back to the browser voice rather than going silent.
- **Overpass**: no key, but it rate-limits by IP.
- **Railway**: Hobby plan usage-based; this service idles cheaply.
- **Vercel**: static hosting, comfortably inside the Hobby tier.

For a live demo, the Gemini daily cap is the real constraint — don't burn the day's
500 on rehearsals.

---

## 8. Redeploying and rolling back

- **Backend**: push to `main` → Railway rebuilds. Roll back from
  **Deployments → ⋯ → Redeploy** on an earlier build.
- **Frontend**: push to `main` → Vercel rebuilds. **Instant Rollback** on any prior
  deployment.
- **Changing a variable** on Railway triggers a redeploy; on Vercel, `VITE_*`
  variables need a fresh build to take effect.

## 9. After deploying

Update `README.md` with the two live URLs, and record the demo video against the
deployed site rather than localhost — it's more convincing, and it proves the
deployment works.
