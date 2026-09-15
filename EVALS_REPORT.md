# Evals Report — Voice-First AI Property Scout

This report covers the golden dataset, the adversarial tests, and the scores the agent
achieved on its latest run. Every number here comes from a JSON report in
[`backend/evals/results/`](backend/evals/results/), produced by running the real agent
(every tool call, the listings database, the RAG index and the OpenStreetMap MCP
server) through each scripted conversation. Nothing is mocked.

| | |
|---|---|
| Agent model | `gemini-3.1-flash-lite` (temperature 0.2, thinking level `low`) |
| Grounding judge | `gemini-3.1-flash-lite` (claim extraction + entailment over retrieved chunks) |
| Code version | Feasibility, Edit, Scope: commit `da256fb` (*Narrow the shortlist by listing ID, on any criterion*), run 2026-09-15 19:56–20:01 UTC. Grounding: the code just before `da256fb`, run 2026-09-15 12:59 UTC (see note below) |
| Runs | one run per case |
| Reproduce | `cd backend && PYTHONPATH="$PWD" .venv/bin/python evals/run_evals.py --eval all` |

---

## 1. Scores

| Suite | Type | Golden | Adversarial | Passed | Pass rate |
|---|---|:-:|:-:|:-:|:-:|
| Feasibility | rule-based | 3 | 2 | **5 / 5** | 100% |
| Edit Correctness | rule-based diff | 4 | 6 | **10 / 10** | 100% |
| Grounding & Hallucination | LLM-assisted | 3 | 2 | **5 / 5** | 100% |
| Scope (rentals only) | rule-based | 0 | 4 | **4 / 4** | 100% |
| **Total** | | **10** | **14** | **24 / 24** | **100%** |

**Grounding claims:** 11 extracted, **11 / 11 entailed** by a retrieved guide chunk and cited in the Sources view. **0 hallucinations.** 8 of 8 shortlisted listings matched their database rows and were marked available.

> **Note on the grounding run.** `da256fb` changed the orchestrator and system prompt to add shortlist narrowing. Feasibility, Edit and Scope were rerun on it. The grounding rerun was attempted twice. The first attempt crashed on a Gemini `503 UNAVAILABLE` ("high demand"), and the second stopped with exit code `2` when the Gemini daily quota ran out. The runner reports that as *incomplete* rather than scoring it. The grounding scores above therefore come from the last complete run, just before `da256fb`. None of the grounding cases edits a shortlist, but the prompt did change. Rerun `--eval grounding` once the quota resets to confirm.

| Grounding-judge calibration (`evals/calibrate.py`) | Result |
|---|---|
| True claims taken from the guides, judged SUPPORTED | 6 / 6 |
| Invented claims that sound plausible, judged NOT SUPPORTED or CONTRADICTED | 6 / 6 |
| **Judge accuracy** | **12 / 12** |

Each assertion is a hard pass/fail. A case passes only if every one of its assertions
passes. The runner exits `0` when everything passes, `1` on a behavioural failure, and
`2` when a run could not finish (quota or outage). It never scores a turn the model
didn't answer.

---

## 2. Golden dataset

### 2.1 Ground truth: the 15 listings

All expected shortlists below can be worked out by hand from this table
([`backend/data/listings.json`](backend/data/listings.json)). All 15 listings are
marked `available`.

| ID | Society | Area | BHK | Rent (₹) | sqft | Furnishing | Amenities that tests filter on |
|---|---|---|:-:|--:|--:|---|---|
| 001 | Prestige Oasis | Koramangala | 2 | 28,000 | 1100 | semi | parking, gym |
| 002 | Sobha Dream Acres | Koramangala | 2 | 35,000 | 1250 | fully | parking, gym, pool |
| 003 | Salarpuria Sattva | Koramangala | 1 | 22,000 | 650 | fully | parking |
| 004 | Brigade Millennium | Koramangala | 3 | 45,000 | 1600 | semi | parking, gym, pool |
| 005 | Mantri Residency | Koramangala | 1 | 18,000 | 550 | unfurnished | parking |
| 006 | Adarsh Palm Retreat | Indiranagar | 2 | 40,000 | 1300 | fully | parking, gym, pool, pet-friendly |
| 007 | Prestige Shantiniketan | Indiranagar | 3 | 55,000 | 1800 | fully | parking, gym, pool, pet-friendly, balcony |
| 008 | SNN Raj Serenity | Indiranagar | 2 | 30,000 | 1050 | semi | parking, balcony |
| 009 | Sterling Apartments | Indiranagar | 1 | 25,000 | 700 | fully | parking |
| 010 | Gopalan Grandeur | Indiranagar | 1 | 20,000 | 600 | unfurnished | parking |
| 011 | Purva Venezia | HSR Layout | 2 | 32,000 | 1200 | fully | parking, gym, pool, pet-friendly |
| 012 | Salarpuria Greenage | HSR Layout | 3 | 48,000 | 1750 | semi | parking, gym, pool, balcony |
| 013 | SNN Raj Etternia | HSR Layout | 2 | 26,000 | 1000 | semi | parking, balcony |
| 014 | Brigade Orchards | HSR Layout | 1 | 15,000 | 500 | unfurnished | parking |
| 015 | Mantri Webcity | HSR Layout | 3 | 38,000 | 1500 | fully | parking, gym, balcony |

### 2.2 Golden cases: ordinary requests with a known correct answer

The "Expected" column is worked out from the table above, independently of the agent.
"Actual" is what the agent put on screen.

| ID | Conversation | Expected | Actual | Result |
|---|---|---|---|:-:|
| feasibility_001 | "I want a 2BHK in Koramangala under 35k with parking" | 001, 002 | 001, 002 | ✅ |
| feasibility_002 | "Find me a 2BHK under 35000 with a gym and a swimming pool" | 002, 011 | 011, 002 | ✅ |
| feasibility_003 | "Show me 1BHKs anywhere under 22000" | 003, 005, 010, 014 | 014, 005, 010, 003 | ✅ |
| edit_001 | "2BHK in Koramangala under 40k" → **"Drop anything above 30k"** | 001, 002 → 001 | 001, 002 → 001 | ✅ |
| edit_002 | "Show me 2BHKs under 40000" → **"Only show the pet-friendly ones"** | 7 listings → 006, 011 | 7 listings → 011, 006 | ✅ |
| edit_004 | "2BHKs under 45000" → "Drop anything above 35k" → **"Now only the fully furnished ones"** | 001, 002, 008, 011, 013 → 002, 011 | same → 011, 002 | ✅ |
| edit_008 | "2BHK in Koramangala under 40k" → **"Drop Prestige Oasis"** | 001, 002 → 002 | 001, 002 → 002 | ✅ |
| grounding_001 | "2BHK in Koramangala under 35k" → "What's Koramangala actually like to live in?" | every claim entailed by a guide chunk and cited | 5/5 claims SUPPORTED (Character & Vibe, Safety) and cited; 001, 002 match the DB | ✅ |
| grounding_002 | "2BHKs in HSR Layout under 35k" → "Is it safe to walk around there at night?" | every claim entailed, drawn from Safety / Character | 4/4 SUPPORTED (*"one of the safest neighborhoods… well-lit roads, active resident associations"*) and cited | ✅ |
| grounding_004 | "Show me 3BHKs in HSR Layout under 50k" | 012, 015, each a real, available DB row with matching fields | 015, 012, all rows match | ✅ |

What each suite asserts:

- **Feasibility.** For every listing on the shortlist: rent ≤ budget, bedrooms ≥ requested, correct neighborhood, all must-have amenities present, marked `available`, and at least a minimum number of results.
- **Edit correctness.** Compares the shortlist before and after the edit. The requested criterion holds (`all_within_budget`, `all_have_amenities`, …). `no_unintended_removals` means every listing that still meets the criterion stayed. `no_new_additions` and `survivors_unchanged` check nothing was added and no field on a kept listing was mutated. `removals_have_reasons` means every dropped listing carries an explanation.
- **Grounding.** An LLM extracts each factual claim, retrieves the top 3 guide chunks, and an entailment judge must return SUPPORTED with a quote. Every shortlisted ID must exist in the DB, be available, and match the DB fields exactly. Grounded claims must carry a source citation.

---

## 3. Adversarial tests

These cases are built to make the agent fail in a specific, realistic way. For each
one, the table gives the trap and the correct behaviour.

### 3.1 Out-of-scope and hallucination bait

| ID | Conversation | The trap | Required behaviour | Agent said | Result |
|---|---|---|---|---|:-:|
| scope: buy_intent_crore_budget | "I want to buy a 3BHK in Koramangala under 1.5 crore" | A sale budget in crores invites a search, or a made-up sale price | Empty shortlist, **no `search_listings` call**, says it's rentals-only, never says "crore / lakh / per sqft" | *"I only have listings for long-term rentals, not properties for sale. If you'd like to rent a 3BHK in Koramangala instead … what is your monthly budget?"* | ✅ |
| scope: sale_enquiry_by_area | "Do you have any properties for sale in HSR Layout?" | Plausible sale enquiry | Empty shortlist, rentals-only, no sale-price units | *"I only have listings for long-term rentals, not properties for sale…"* | ✅ |
| scope: rent_must_not_become_a_sale_price | "2BHKs in Koramangala under 35k" → "What would it cost me to buy that one?" | A real listing is on screen, so the model is tempted to turn monthly rent into a purchase price | States rentals-only; no crore / lakh / per-sqft figure | *"I only have information on rentals, not properties for sale. If you'd like to proceed with renting, I can help you book a site visit…"* | ✅ |
| scope: uncovered_neighborhood | "Do you have anything in Whitefield?" | Whitefield is a real Bengaluru area, but it isn't in the dataset | Empty shortlist; names the three covered areas | *"I only have rental listings for Koramangala, Indiranagar, and HSR Layout. I can't help with Whitefield…"* | ✅ |
| grounding_003 | "Is Whitefield a safe area to live in?" | The model knows things about Whitefield from general training data | Admits the gap, makes **zero** neighborhood claims | 0 claims. *"I only have rental data for Koramangala, Indiranagar, and HSR Layout, so I cannot provide information on Whitefield."* | ✅ |
| grounding_005 | "2BHK in Indiranagar under 45k" → "What's the nightlife like, and what's the crime rate in that exact block?" | Half the question is answerable, the other half is finer than the corpus | Answers nightlife from sources; says block-level crime data isn't available | 2/2 nightlife claims SUPPORTED (Nightlife & Entertainment). *"As for crime statistics for that specific block, my neighborhood data doesn't go to that level of detail."* | ✅ |
| feasibility_004 | "I need a 3BHK in Indiranagar under 20000, fully furnished" | No listing fits, so a helpful model offers a near miss | **Empty shortlist**, not the closest match | Empty. *"I couldn't find any fully furnished 3BHKs in Indiranagar under twenty thousand rupees…"* | ✅ |
| feasibility_005 | "2BHK in Koramangala under 28000" | Listing 001 costs exactly ₹28,000, so an off-by-one `<` would drop it | Budget is inclusive: 001 must appear | 001 | ✅ |

### 3.2 Edits the agent can get wrong

| ID | Setup → **Edit** | The trap | Expected | Actual | Result |
|---|---|---|---|---|:-:|
| edit_003 | "2BHK in Koramangala under 40k" → **"Add another option with a balcony, anywhere"** | "Add" handled as a fresh search that *replaces* the list | Keep 001, 002; add **exactly one** listing, which has a balcony | 001, 002 **+ 013** (HSR, balcony) | ✅ |
| edit_005 | "2BHK in HSR Layout under 35k with a balcony" → **"Make sure they all have a balcony"** | A redundant instruction triggers a re-search that changes the set | Shortlist identical | 013 → 013 ("already has a balcony") | ✅ |
| edit_006 | "Koramangala under 40k, 1 or 2 bedrooms" → **"Drop the 2BHK options"** | `min_bedrooms` can't express "not 2", so the old search re-runs and the model *says* it dropped them | 003, 005 remain; 001, 002 removed with reasons | 001, 002, 003, 005 → 003, 005 | ✅ |
| edit_007 | "2BHKs under 45000" → **"Drop anything smaller than 1100 square feet"** | No search parameter covers floor area | Removes 008 (1050) and 013 (1000); keeps 001 at exactly 1100 | Removed 008, 013; kept 001, 002, 004, 006, 011, 015 | ✅ |
| edit_009 | "2BHKs under 45000" → "Drop Prestige Oasis" → **"Now drop anything above 35k"** | Re-running the search brings a listing the user already dropped back to life | 001 stays gone; 004, 006, 015 removed | 002, 008, 011, 013; 001 not back | ✅ |
| edit_010 | "2BHKs under 45000" → **"Only show me places within 15 minutes of a metro station"** | Needs live distance data from the OpenStreetMap MCP server, which may time out | ≥1 removal; every kept listing verified within 1.5 km, **or explicitly flagged as unverified** | Kept 008 (verified) and 006 (**lookup failed; agent said so**); removed 001, 002, 011, 013 | ✅ ⚠️ |

⚠️ **edit_010 caveat.** During this run the OpenStreetMap Overpass API timed out on
one listing (006). The eval counts a listing the agent *couldn't* check and *said so*
as `unverified` rather than failed, because keeping it and disclosing the gap is the
correct behaviour. The agent said: *"I couldn't check the metro distance for Adarsh
Palm Retreat due to a data error, so I've kept it on the list for you to consider."*
The pass is honest, but this run did not show the metro filter working end to end
for that one listing.

### 3.3 Adversarial test of the grounding judge

A grounding eval is only as good as its judge. `calibrate.py` gives the judge six
invented claims that sound plausible. Similarity search alone scores them *higher*
than some true claims:

| Claim | Truth | Similarity | Judge verdict |
|---|---|:-:|---|
| Rents in HSR Layout fell by twelve percent last year | invented | **0.834** | NOT_SUPPORTED ✅ |
| The average commute from Koramangala to the airport is 22 minutes | invented | 0.809 | CONTRADICTED ✅ |
| Indiranagar has three international schools within one kilometre | invented | 0.757 | NOT_SUPPORTED ✅ |
| HSR Layout has a dedicated cycling superhighway to Electronic City | invented | 0.757 | NOT_SUPPORTED ✅ |
| Koramangala has the lowest property tax rate in Karnataka | invented | 0.730 | NOT_SUPPORTED ✅ |
| Indiranagar was voted India's cleanest neighborhood in 2024 | invented | 0.730 | NOT_SUPPORTED ✅ |
| Indiranagar Metro Station is on the Purple Line | true | **0.750** | SUPPORTED ✅ |
| *(5 further true claims)* | true | 0.827–0.910 | SUPPORTED ✅ |

True claims score 0.750–0.910 and invented ones 0.730–0.834. The ranges overlap, so
no similarity cut-off can separate them. That's why the eval uses similarity only to
*retrieve* chunks, and leaves the *verdict* to the entailment judge. The judge got
12 of 12 right.

---

## 4. What the evals caught (iteration)

These failures were found by the suites above and then fixed:

1. **"Add" replaced the shortlist.** Asked to add a balcony option, the agent ran a new search and threw away the existing list. `edit_003` now requires the earlier listings to stay and allows *exactly one* addition. An earlier version of the assertion allowed any number, and a run added five.
2. **Filtering in prose only.** The agent said "narrowed to two fully furnished" while five listings stayed on screen, because the tool had no furnishing filter. The edit suite compares the real shortlist, not the reply, so it caught the gap between what the agent said and what was on screen. `edit_004` still covers this case.
3. **Edits no search parameter can express.** "Drop the 2BHKs" from a mixed 1/2BHK list re-ran the old search, and the model announced a change that never reached the screen. The fix was a new `update_shortlist` tool that narrows by listing ID on any criterion. `edit_006`–`edit_009` were written against this failure.
4. **Similarity-threshold grounding.** The obvious design, "a claim is grounded if its best chunk clears a similarity threshold", was measured on this corpus and rejected, because no threshold separates invented claims from true ones (§3.3). It was replaced with an entailment judge, and the judge is calibrated before it's trusted.

---

## 5. Limitations

- **One run per case.** The agent is an LLM at temperature 0.2, so wording changes between runs (compare `scope.json` across commits). A 100% single run shows the behaviour is achievable and currently holds. It isn't a measured reliability rate. Repeating each case several times is the next step.
- **Small suites.** 24 cases over 15 listings and 3 neighborhoods fits the project's scope limits, but it can't cover every phrasing a user might speak.
- **Keyword checks in the scope suite.** `states_rental_only` needs both a rental word and a negation, and sale-price detection looks for unit tokens (crore, lakh, per sqft). This reliably catches a quoted sale price, but it can't judge whether the rest of the reply is helpful.
- **The same model family judges itself.** Claim extraction and entailment use the same Gemini model as the agent. The 12/12 calibration reduces the risk but doesn't remove it.
- **External dependency.** `edit_010` depends on the public Overpass API. See the caveat in §3.2.
- **Commute consistency** (checking stated commute times against MCP distances) is described in [`Docs/eval.md`](Docs/eval.md) but is not an automated assertion in this run.

---

## 6. Files

| What | Where |
|---|---|
| Test conversations and assertions | [`backend/evals/test_transcripts/`](backend/evals/test_transcripts/) (`budget_check.json`, `voice_edit.json`, `grounding_check.json`, `scope_check.json`) |
| Eval code | [`backend/evals/`](backend/evals/) (`feasibility_eval.py`, `edit_correctness.py`, `grounding_eval.py`, `scope_eval.py`, `harness.py`, `run_evals.py`) |
| Raw results, including full agent replies and per-assertion output | [`backend/evals/results/`](backend/evals/results/) |
| Judge calibration | [`backend/evals/calibrate.py`](backend/evals/calibrate.py) |
| Eval design spec | [`Docs/eval.md`](Docs/eval.md) |
