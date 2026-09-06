# Project Context — Voice-First AI Property Scout

## Objective

Build a **voice-first AI property scout** that understands a renter/buyer's spoken preferences, shortlists real listings, explains its shortlist decisions, and books a site-visit call — all grounded in real neighborhood data.

---

## Problem Statement

People don't struggle to find listings — they struggle to **judge whether a listing actually fits their life**: is the commute realistic, is the area safe at night, is the extra rent worth the extra room.

The task is to build a **voice-based AI assistant** that:

1. **Collects preferences conversationally** — budget, bedrooms, must-haves, commute point
2. **Shortlists listings** scraped from a real public source that match those preferences
3. **Grounds every neighborhood claim** (safety, amenities, transit) in real public sources — not the model's general knowledge
4. **Allows voice-based shortlist refinement** (e.g. "Drop anything above 40k")
5. **Explains** why each listing was picked or dropped
6. **Books a site-visit slot** via voice once the user finalizes a shortlist
7. **Workflow automation** — an n8n workflow compiles the shortlist into a PDF and emails it to the user

---

## Core Capabilities (Required)

### 1. Voice-Based Preference Collection
- Supports spoken inputs like: *"I'm looking for a 2BHK in Koramangala, budget 35k, need parking, close to a metro station."*
- Ask clarifying questions only when required (max 5)
- Confirm constraints before generating the shortlist

### 2. Voice-Based Shortlist Refinement
- User can modify the shortlist via voice commands:
  - *"Drop anything above 40k."*
  - *"Only show me places within 15 minutes of a metro station."*
  - *"I need something pet-friendly."*
  - *"Add one more option with a balcony."*
- Only the affected part of the shortlist should change

### 3. Explanation & Reasoning
- The assistant must answer questions like:
  - *"Why did you pick this one?"*
  - *"Is the commute from here realistic?"*
  - *"What's this area actually like to live in?"*
- Explanations must be **grounded**, not generic

---

## Companion UI Requirements

A simple UI that must include:

- **Shortlist cards** — rent, bedrooms, area, key amenities
- **Neighborhood snapshot panel** per listing — transit, safety notes, amenities
- **Microphone button + live transcript**
- **"Sources" / "References" section** showing where each neighborhood claim came from
- **Visit-confirmation panel** — booking slot and confirmation code

---

## Data Requirements

### Listings
- Source: [bengaluru.rent](https://bengaluru.rent/)
- Fields: rent, bedrooms, furnishing, amenities, society name, sq. footage, availability status
- **Only currently available listings** — exclude "Not for rent" / transparency-only pins
- **Remove all PII** (owner/agent names, phone numbers) before the data touches the dataset, UI, or logs
- Max **15 listings**, scraped and cleaned manually

### Amenities & Transit
- Source: **OpenStreetMap MCP** — structured, queryable geodata
- Not to be guessed or hallucinated

### Neighborhood Guidance
- Source: Real public sources (e.g. Wikipedia neighborhood/city guide pages)
- Gathered via RAG, not the model's general knowledge
- Max **3 neighborhoods** with guide data

### Rules
- Listings must map back to scraped data and be currently available
- No PII anywhere
- Amenities/transit claims → OpenStreetMap MCP
- Neighborhood character claims → RAG sources with citations
- If data is missing or unreliable → system must say so, not guess

---

## MCP Integration

- Integrate an MCP server in the orchestration layer
- Not required to build MCP from scratch — the skill tested is **wiring a real tool into an agent**
- Shortlist/ranking logic can live in application code and call the MCP tool as needed

### Required MCP Tool

| Tool | Link | Use it for |
|------|------|------------|
| OpenStreetMap MCP | [GitHub](https://github.com/jagan-shanmugam/open-streetmap-mcp) | Nearby amenities, transit points, and POI data around a listing's location |

- Must demonstrate the required MCP call clearly in the demo

---

## RAG Requirements

RAG must be used for:
- Neighborhood practical guidance (safety, amenities, transit character)
- Explanations and justifications for shortlist decisions

### Rules
- All neighborhood claims must have **citations**
- No hallucinated claims about an area
- Voice explanations can be short; citations must appear in the UI

---

## AI Evaluations (Minimum 3)

### 1. Feasibility Eval
- Shortlist respects stated budget and must-haves
- Commute claims are internally consistent with stated commute point

### 2. Edit Correctness Eval
- Voice edits only modify intended parts of the shortlist
- No unintended changes elsewhere

### 3. Grounding & Hallucination Eval
- Listings map to dataset records and are marked as currently available
- Neighborhood claims cite RAG sources
- Uncertainty is explicitly stated when neighborhood data is missing

> Evals can be rule-based or LLM-assisted but must be **runnable**.

---

## Tech & Deployment Requirements

- Build using **LLM APIs**
- **Voice input** (speech-to-text required)
- **Version control** using Git
- **Deployed prototype** with a public URL

---

## Scope Constraints

| Constraint | Limit |
|------------|-------|
| City | 1 (Bengaluru) |
| Listings | Max 15, scraped and cleaned |
| Neighborhoods with guide data | Max 3 |
| Focus | Quality over coverage |

---

## Deliverables

1. **Deployed application link** (public URL)
2. **5-minute demo video** showing:
   - Voice-based preference collection
   - Voice-based shortlist edit
   - Explanation ("why this one?")
   - Sources view
   - At least one eval running
3. **Git repository** with:
   - README (architecture + setup)
   - How MCP was integrated
   - Scraped dataset and collection/cleaning methodology
   - How to run evals
   - Sample test transcripts

---

## Evaluation Rubric

| Criterion | Weight |
|-----------|--------|
| Voice UX & intent handling | 25% |
| MCP usage & system design | 20% |
| AI evals & iteration depth | 20% |
| Grounding & RAG quality | 15% |
| Workflow automation | 10% |
| Deployment & code quality | 10% |

---

## What They're Looking For

- Can you design a **tool-using AI system**?
- Do you understand **LLM limitations**?
- Can you build **evaluations and iterate**?
- Can you **explain your decisions** clearly?
