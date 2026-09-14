# Edge Cases — Voice-First AI Property Scout

A comprehensive catalog of edge cases organized by system component. Each entry includes the scenario, expected behavior, and how to handle it.

---

## 1. Voice Input (STT)

| # | Scenario | Expected Behavior |
|---|----------|-------------------|
| 1.1 | **Browser doesn't support Web Speech API** (e.g. Firefox, Safari) | Show a fallback text input field with a message: "Voice input is not supported in this browser. Please use Chrome or Edge, or type your message below." |
| 1.2 | **Microphone permission denied** | Display a clear error: "Microphone access is required for voice input. Please allow microphone access in your browser settings." |
| 1.3 | **User speaks in a non-English language** | STT may produce garbage text. The LLM should respond: "I didn't quite catch that. Could you please repeat in English?" |
| 1.4 | **Background noise / unintelligible speech** | STT returns low-confidence or empty transcript. Show: "I couldn't understand that. Could you try again in a quieter environment?" |
| 1.5 | **User speaks very long input (>60 seconds)** | Web Speech API may auto-stop. Capture whatever was transcribed, process it, and if incomplete, ask: "It seems like you were cut off. Could you continue?" |
| 1.6 | **Multiple rapid voice inputs before response returns** | Queue inputs and process sequentially. Show a "Processing..." state. Don't fire multiple parallel API calls. |
| 1.7 | **User clicks mic button while already listening** | Toggle off — stop recognition, process whatever was captured so far. |
| 1.8 | **STT misinterprets numbers** ("thirty-five thousand" → "35000" vs "35,000" vs "thirty five thousand") | The LLM should normalize all budget formats. Prompt engineering: "Parse any budget mention into an integer value in INR." |
| 1.9 | **User says neighborhood name with different spellings** ("Koramangala" vs "Kormangla" vs "K R Mangala") | LLM should fuzzy-match to the closest known neighborhood. If ambiguous, ask: "Did you mean Koramangala?" |

---

## 2. Voice Output (TTS)

| # | Scenario | Expected Behavior |
|---|----------|-------------------|
| 2.1 | **TTS speaks over user's next input** | Stop TTS immediately when user clicks mic or starts speaking. Cancel any queued speech. |
| 2.2 | **Very long assistant response** | Truncate TTS to first 2-3 sentences. Full response shown in transcript panel. Add: "You can see the full details in the shortlist panel." |
| 2.3 | **TTS not available** | Silently skip voice output. Show response in transcript only. No error needed. |
| 2.4 | **Rupee symbol (₹) and numbers in TTS** | TTS may say "rupee symbol" instead of "rupees". Format as "35,000 rupees per month" in the spoken text. |

---

## 3. Preference Collection

| # | Scenario | Expected Behavior |
|---|----------|-------------------|
| 3.1 | **User gives all preferences in one sentence** | Extract everything in a single pass. Confirm: "Got it — 2BHK in Koramangala, under ₹35k, with parking. Let me find matching properties." |
| 3.2 | **User gives no actionable preferences** ("I want a nice place") | Ask a clarifying question: "Sure! What's your budget range? And how many bedrooms do you need?" Count toward the 5-question max. |
| 3.3 | **User gives contradictory preferences** ("Under 20k" + "fully furnished" + "3BHK in Indiranagar") | Try the search. If 0 results, say: "I couldn't find any 3BHK fully-furnished options in Indiranagar under ₹20k. Would you like to increase your budget or consider semi-furnished?" |
| 3.4 | **Budget is unrealistically low** (e.g. "under 5k") | Search returns 0 results. Say: "I don't have any listings under ₹5,000. The lowest rent in my data is ₹X. Would you like to adjust your budget?" |
| 3.5 | **Budget is unrealistically high** (e.g. "under 5 lakhs") | Return all matching listings. No special handling needed — the filter works normally. |
| 3.6 | **User asks for a neighborhood we don't have data for** ("I want something in Whitefield") | Return 0 listings. Say: "I currently only have listings in Koramangala, Indiranagar, and HSR Layout. Would you like to explore any of these areas?" |
| 3.7 | **More than 5 clarifying questions asked** | Stop asking. Use whatever preferences are available and generate the shortlist. Say: "Let me work with what I have so far." |
| 3.8 | **User changes preferences mid-conversation** ("Actually, make it 3BHK instead") | Update the preferences and regenerate the shortlist. Don't ask "Are you sure?" — just do it. |
| 3.9 | **User provides preferences as a question** ("Do you have anything in Koramangala?") | Treat as a preference (neighborhood = Koramangala) and ask for missing fields (budget, bedrooms). |

---

## 4. Shortlist Generation & Refinement

| # | Scenario | Expected Behavior |
|---|----------|-------------------|
| 4.1 | **Search returns 0 matching listings** | Don't show empty cards. Say: "No listings match your criteria. Try relaxing your budget or removing some must-haves." Suggest specific relaxations. |
| 4.2 | **Search returns only 1 listing** | Show it. Say: "I found only one match. Would you like me to relax some filters to find more options?" |
| 4.3 | **All listings get dropped after refinement** ("Drop anything above 20k" when all are above 20k) | Show empty state. Say: "That filter removed all listings. Your previous shortlist had rents of ₹X-₹Y. Would you like to adjust the limit?" |
| 4.4 | **User asks to add a listing that doesn't exist** ("Add one with a swimming pool" when no pool listings exist) | Say: "I don't have any listings with a swimming pool in my current dataset. Would you like to try a different amenity?" |
| 4.5 | **Refinement command is ambiguous** ("Remove the expensive ones") | Ask for clarification: "What's your maximum budget? I'll drop anything above that." |
| 4.6 | **User asks to undo a refinement** ("Actually, bring back the ones you removed") | If conversation history has the previous shortlist, restore it. If not, re-run the original search without the last filter. |
| 4.7 | **User requests a refinement before any shortlist exists** ("Drop anything above 40k") | Say: "I don't have a shortlist yet. Tell me what you're looking for and I'll generate one first." |
| 4.8 | **Duplicate refinement** ("Only pet-friendly" when already filtered for pet-friendly) | No-op. Say: "Your shortlist is already filtered for pet-friendly options." |
| 4.9 | **Conflicting refinement with original preferences** (Budget was 35k, then "only show above 40k") | Apply the new filter — it overrides the original preference. The user's latest instruction wins. |

---

## 5. Explanation & Reasoning

| # | Scenario | Expected Behavior |
|---|----------|-------------------|
| 5.1 | **User asks "Why this one?" without selecting a listing** | Ask: "Which listing would you like me to explain?" Or explain the top-ranked one by default. |
| 5.2 | **User asks about a neighborhood we don't have RAG data for** | Say explicitly: "I don't have detailed neighborhood data for [area]. I can only provide information about Koramangala, Indiranagar, and HSR Layout." **Do not hallucinate.** |
| 5.3 | **User asks a question unrelated to real estate** ("What's the weather today?") | Politely redirect: "I'm a property scout assistant — I can help you find rental properties in Bengaluru. What kind of place are you looking for?" |
| 5.4 | **RAG returns low-relevance chunks** (similarity score below threshold) | Don't use the low-quality chunks. Say: "I don't have specific information about that aspect of [neighborhood]." |
| 5.5 | **User asks about commute but no commute point was provided** | Ask: "Where would you be commuting to? I can check transit options from the listings." |
| 5.6 | **User asks the same question twice** | Answer again (don't say "I already told you"). The user may have missed it or wants more detail. |

---

## 6. MCP (OpenStreetMap) Integration

| # | Scenario | Expected Behavior |
|---|----------|-------------------|
| 6.1 | **MCP server fails to start** (Node.js not installed, npx fails) | Log the error. Return listings without POI data. Show a note in UI: "Nearby amenities data is temporarily unavailable." |
| 6.2 | **MCP call times out** (Overpass API is slow) | Set a 10-second timeout. On timeout, skip POI enrichment for that listing. Note: "Transit data unavailable for this listing." |
| 6.3 | **MCP returns empty results** (no POIs in radius) | Show: "No [amenity type] found within 1km." Don't hide the section — explicitly state the absence. |
| 6.4 | **Listing has invalid coordinates** (lat/lng is 0,0 or null) | Skip MCP call for that listing. Note: "Location data unavailable — cannot check nearby amenities." |
| 6.5 | **MCP returns duplicate POIs** | Deduplicate by name + coordinates before displaying. |
| 6.6 | **MCP rate limiting** (too many calls in quick succession) | Implement request queuing with 500ms delay between calls. Cache aggressively. |
| 6.7 | **A background prefetch fails and disables the user-facing lookup** | A shared circuit breaker must not be tripped by work nobody is waiting on. The warm-up's timeout says nothing about whether one foreground lookup would have succeeded, and a tripped breaker makes "what's nearby?" return "unavailable" without a request ever leaving the host. Background batches pass `trip_circuit=False`; only foreground failures open the breaker. |
| 6.8 | **A regional Overpass mirror answers 200 with zero elements** | Worse than an error: outside its region a mirror like `overpass.osm.ch` returns a valid empty result, which surfaces as "nothing nearby" rather than as a failed lookup — the exact hallucination the grounding rules forbid. Only worldwide endpoints may be configured; see the note on `DEFAULT_OVERPASS_ENDPOINTS` in `config.py`. |
| 6.9 | **Overpass is reachable but slow** (20-30s per query under load) | Each POI type is its own round trip, so a multi-type lookup can outlast any sane budget. The foreground call reports the types it could not fetch in `unavailable[]` and the agent says so; an empty list must never be read as "none nearby". Background warming gets a far longer budget because it blocks nobody. |

---

## 7. RAG Pipeline

| # | Scenario | Expected Behavior |
|---|----------|-------------------|
| 7.1 | **ChromaDB is empty or corrupted** | Detect on startup. Log error and prompt admin to re-run `seed_db.py`. Return error to user: "Neighborhood data is currently unavailable." |
| 7.2 | **Query returns no relevant chunks** (all below similarity threshold) | Don't fabricate an answer. Say: "I don't have information about that for [neighborhood]." |
| 7.3 | **Source URL in citation is dead** | Still show the citation (it was valid at scrape time). The citation text itself is the evidence. |
| 7.4 | **User asks a very specific question not in the corpus** ("What's the crime rate in Koramangala block 5?") | Say: "My neighborhood data doesn't have that level of detail. I can share what I know about Koramangala in general." |
| 7.5 | **Embedding model is unavailable** (API quota exceeded) | Fallback to keyword search on ChromaDB if supported, or return error: "Search is temporarily unavailable." |

---

## 8. Google Calendar Booking

| # | Scenario | Expected Behavior |
|---|----------|-------------------|
| 8.1 | **Service account credentials are missing or invalid** | Log error. Return: "Booking is temporarily unavailable. Please try again later." Don't crash the app. |
| 8.2 | **User provides a past date** ("Book for yesterday") | Reject: "That date has already passed. Could you pick a future date?" |
| 8.3 | **User provides an invalid date** ("Book for February 30th") | Catch the parsing error. Ask: "That doesn't seem like a valid date. Could you try again?" |
| 8.4 | **User provides no email** (spoke the booking request but didn't mention email) | Ask: "I'll need your email address to send the calendar invite. What's your email?" |
| 8.5 | **User provides an invalid email** | Validate format before calling the API. Ask: "That doesn't look like a valid email. Could you try again?" |
| 8.6 | **Google Calendar API quota exceeded** | Return error: "Booking service is temporarily busy. Please try again in a few minutes." |
| 8.7 | **User tries to book without any shortlist** | Say: "You don't have a shortlist yet. Tell me your preferences first, and once you like a listing, I'll help you book a visit." |
| 8.8 | **User tries to book multiple listings at once** ("Book visits for all of them") | Create separate calendar events for each. Stagger times by 2 hours. Confirm: "I've booked 3 visits for [date]: 10AM, 12PM, and 2PM." |
| 8.9 | **Duplicate booking** (same listing, same date/time) | Check for existing events. Warn: "You already have a visit booked for this listing on that date. Would you like to reschedule instead?" |
| 8.10 | **User gives a relative date** ("next Friday", "this weekend") | The model has no clock: without today's date in the prompt it answers from training priors and produces a date months in the past. The session state block carries today's date and weekday in `Asia/Kolkata` plus the next seven days enumerated, because date arithmetic is something LLMs get wrong reliably. |
| 8.11 | **"Next Friday" is ambiguous** (said on a weekday it can mean either Friday) | Do not guess and do not book. Resolve it, read the calendar date back, and wait for confirmation: "That would be Friday the eighteenth of September, morning slot. Shall I book it?" — offering the nearer Friday and naming the alternative, so a wrong reading costs one word to correct. An explicit date ("September 18th") books directly. |
| 8.12 | **Server timezone differs from Bengaluru** | "Already passed" must mean passed where the visit happens. A guard comparing against the server's local date would reject or accept a booking differently from the user, and disagree with the date the agent was given. Both compare in `Asia/Kolkata`. |

---

## 9. PDF & Email

| # | Scenario | Expected Behavior |
|---|----------|-------------------|
| 9.1 | **WeasyPrint fails** (missing system dependencies on deployment) | Fall back to a simple text-based PDF or HTML email without PDF attachment. Log the error. |
| 9.2 | **Gmail SMTP auth fails** (wrong app password) | Return error: "Email service is currently unavailable." Log credentials error (without exposing the password). |
| 9.3 | **User email bounces** | smtplib won't know immediately (async bounce). No action needed — the email was sent successfully from our side. |
| 9.4 | **Empty shortlist when requesting PDF** | Say: "Your shortlist is empty. Add some listings first, then I can send you a PDF." |
| 9.5 | **Very large PDF** (shouldn't happen with 15 max listings) | Unlikely edge case. Cap at 15 listings per PDF anyway. |
| 9.6 | **User requests PDF multiple times** | Allow it — generate and send a new PDF each time. The shortlist may have changed. |

---

## 10. Conversation State

| # | Scenario | Expected Behavior |
|---|----------|-------------------|
| 10.1 | **Session expires or server restarts** (in-memory state lost) | Start a fresh session. Say: "It looks like we're starting fresh. What kind of property are you looking for?" |
| 10.2 | **User returns after a long pause** (same session, 30+ min gap) | Resume the conversation. The session state is still valid. Say: "Welcome back! Here's where we left off — you have [N] listings shortlisted." |
| 10.3 | **Conversation history gets too long** (>100 turns) | Trim older messages from the LLM context window (keep system prompt + last 20 turns). Session state (preferences, shortlist) is preserved separately. |
| 10.4 | **Multiple browser tabs with same session** | Same session_id → both tabs see the same state. This is acceptable for a prototype. |
| 10.5 | **User starts a completely new search mid-conversation** ("Forget everything, I want to start over") | Reset preferences and shortlist. Keep the session. Say: "Sure, starting fresh! What are you looking for?" |

---

## 11. Deployment & Infrastructure

| # | Scenario | Expected Behavior |
|---|----------|-------------------|
| 11.1 | **Backend is down** | Frontend shows: "The server is currently unavailable. Please try again later." Disable mic button. |
| 11.2 | **CORS error** (misconfigured origins) | Fix on backend: ensure `FRONTEND_URL` is in allowed origins. User sees: "Connection error." |
| 11.3 | **Gemini API rate limit hit** (15 RPM on free tier) | Queue requests. If hit, return: "I'm a bit busy right now. Please try again in a moment." Implement exponential backoff. |
| 11.4 | **SQLite database file is locked** (concurrent writes) | Use WAL mode for SQLite. For a prototype with single-user expectation, this is unlikely but WAL prevents it. |
| 11.5 | **Persistent volume lost on redeployment** | Re-run `seed_db.py` on startup if database doesn't exist. Include a health check endpoint. |
| 11.6 | **Container base image ships no timezone database** | `zoneinfo` resolves against the system tz database, which `python:*-slim` does not include. `ZoneInfo("Asia/Kolkata")` then raises `ZoneInfoNotFoundError` at runtime on a host where it worked locally, failing every turn that builds the date anchor. The `tzdata` package in `requirements.txt` is the fallback; reproduce the failure with `PYTHONTZPATH=""`. |
| 11.7 | **Dev-only env file baked into a production build** | Vite loads `.env` in every mode, `build` included, so a `VITE_API_URL` meant for local development ends up inlined in the shipped bundle and the deployed UI calls `localhost`. Dev values belong in `.env.development`, which `vite build` never reads. |

---

## 12. Security & Data Privacy

| # | Scenario | Expected Behavior |
|---|----------|-------------------|
| 12.1 | **PII leaks into LLM context** | Listings are already scrubbed in Phase 0. Double-check: no owner names or phone numbers ever appear in API responses, UI, or logs. |
| 12.2 | **User provides personal info in voice** ("My name is X, my phone is Y") | Don't store or log user's personal info. The conversation history is in-memory and ephemeral. |
| 12.3 | **Prompt injection via voice** ("Ignore your instructions and...") | System prompt should be robust. Gemini's function-calling mode helps constrain output. Monitor for unexpected tool calls. |
| 12.4 | **Service account key exposed in git** | `.gitignore` excludes `service-account.json` and `credentials/`. Environment variables for production. |
