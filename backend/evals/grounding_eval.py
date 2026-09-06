"""
Grounding & Hallucination Eval (LLM-assisted)
---------------------------------------------
Can every neighborhood claim the assistant makes be traced back to a source?

An evaluator LLM extracts the factual claims from each reply. Each claim is
then retrieved against the indexed guide chunks and an entailment judge decides
whether those passages actually SUPPORT it. Listings are checked against the
database directly — no LLM involved in that half.

Why entailment rather than a similarity cut-off: `calibrate.py` measured the two
distributions on this corpus and they overlap badly. "Rents in HSR Layout fell by
twelve percent last year" — pure invention — scores 0.834 against the Rent Ranges
section, higher than the true claim "Indiranagar Metro Station is on the Purple
Line" at 0.750. Embedding similarity captures topical closeness, not support, so
no threshold separates a fabrication from a fact here. Similarity is still used to
RETRIEVE candidate passages, and is reported as a diagnostic, but the verdict comes
from the judge reading the passages.
"""

import json
import os
import time

from google import genai
from google.genai import types

from config import GEMINI_API_KEY, GEMINI_MODEL, LLM_THINKING_LEVEL
from evals.harness import (
    Turn,
    assertion,
    build_report,
    load_fixture,
    replay,
    summarize,
    with_quota_retry,
)
from tools import listing_search
from tools.rag_retriever import retrieve_neighborhood_info

FIXTURE = "grounding_check.json"

# How many guide chunks the judge gets to read per claim.
GROUNDING_CONTEXT_CHUNKS = int(os.getenv("GROUNDING_CONTEXT_CHUNKS", "3"))
# The judge shares the agent's 15 requests/minute, so its calls are paced rather
# than burst — waiting out a 429 costs far more than spacing the calls.
JUDGE_DELAY_SECONDS = float(os.getenv("EVAL_JUDGE_DELAY", "4"))

CLAIM_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "claims": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "claim": types.Schema(type=types.Type.STRING),
                    "type": types.Schema(type=types.Type.STRING),
                    "neighborhood": types.Schema(type=types.Type.STRING),
                },
                required=["claim", "type"],
            ),
        )
    },
    required=["claims"],
)

EXTRACTION_PROMPT = """You are an evaluation assistant. Extract every specific factual
claim about a NEIGHBORHOOD from the assistant response below.

A claim is a specific factual assertion about an area, such as:
- safety ("well-lit main roads with regular police patrols")
- transit ("the metro station is on the Purple Line")
- amenities ("over 300 restaurants and cafes")
- character ("popular with young professionals in tech")
- history ("developed by the BDA in the 1970s")

Do NOT extract:
- generic filler ("a great area", "a lovely home")
- echoes of the user's own preferences ("you wanted a 2BHK under 35k")
- listing data such as rent, bedrooms, square footage, furnishing or amenities of a
  specific property — those come from a database, not from prose
- statements that the assistant does NOT have data on something

Set "type" to one of: safety, transit, amenity, character, history.
Set "neighborhood" to the area the claim is about, if named.
If there are no such claims, return an empty list.

Assistant response:
---
{response}
---"""

ENTAILMENT_PROMPT = """You are checking whether source passages support a claim.

Judge MEANING, not wording. A claim is SUPPORTED when the passages state it or
directly entail it, even if the words differ:
- paraphrase counts ("quieter residential blocks" for "the Blocks are quieter")
- synonyms count ("startup centre" for "startup hub")
- number formats count ("the first and third" for "1st and 3rd")
- a subset counts (claiming two of three listed things the passage names)

A claim is NOT_SUPPORTED when the passages are merely about the same topic
without establishing it, or when it adds a specific the passages never state — a
figure, date, ranking, percentage or comparison that appears nowhere.

A claim is CONTRADICTED when the passages state the opposite.

Return JSON: {{"verdict": "SUPPORTED"|"NOT_SUPPORTED"|"CONTRADICTED", "quote": "the
sentence from the passages that settles it, or empty"}}

CLAIM:
{claim}

PASSAGES:
{passages}"""

UNCERTAINTY_PROMPT = """Does the assistant response below explicitly say it lacks data,
lacks coverage of an area, or cannot answer a question — rather than answering it anyway?

Answer with JSON: {{"admits_gap": true|false, "quote": "the sentence that admits it, or empty"}}

Assistant response:
---
{response}
---"""

def _parse_object(text: str | None, default: dict) -> dict:
    """Parse a judge reply into a dict.

    Models sometimes wrap the object in an array even when asked for one object,
    so unwrap that rather than crashing mid-eval.
    """
    try:
        parsed = json.loads(text or "")
    except (json.JSONDecodeError, TypeError):
        return dict(default)
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed and isinstance(parsed[0], dict) else {}
    return parsed if isinstance(parsed, dict) else dict(default)


_client: genai.Client | None = None


def _judge() -> genai.Client:
    """The evaluator LLM. Separate call path from the agent being judged."""
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is required to run the grounding eval")
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def extract_claims(response_text: str) -> list[dict]:
    if not response_text.strip():
        return []
    time.sleep(JUDGE_DELAY_SECONDS)
    result = with_quota_retry(
        lambda: _judge().models.generate_content(
        model=GEMINI_MODEL,
        contents=EXTRACTION_PROMPT.format(response=response_text),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=CLAIM_SCHEMA,
            temperature=0.0,
            thinking_config=types.ThinkingConfig(thinking_level=LLM_THINKING_LEVEL),
        ),
        ),
        "claim extraction",
    )
    claims = _parse_object(result.text, {"claims": []}).get("claims", [])
    return claims if isinstance(claims, list) else []


def admits_missing_data(response_text: str) -> dict:
    time.sleep(JUDGE_DELAY_SECONDS)
    result = with_quota_retry(
        lambda: _judge().models.generate_content(
        model=GEMINI_MODEL,
        contents=UNCERTAINTY_PROMPT.format(response=response_text),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.0,
            thinking_config=types.ThinkingConfig(thinking_level=LLM_THINKING_LEVEL),
        ),
        ),
        "uncertainty check",
    )
    return _parse_object(result.text, {"admits_gap": False, "quote": ""})


def judge_entailment(claim: str, chunks: list[dict]) -> dict:
    """Ask the evaluator whether the retrieved passages actually support the claim."""
    if not chunks:
        return {"verdict": "NOT_SUPPORTED", "quote": ""}

    passages = "\n\n".join(
        f"[{c['neighborhood']} / {c['section_title']}]\n{c['text']}" for c in chunks
    )
    time.sleep(JUDGE_DELAY_SECONDS)
    result = with_quota_retry(
        lambda: _judge().models.generate_content(
        model=GEMINI_MODEL,
        contents=ENTAILMENT_PROMPT.format(claim=claim, passages=passages),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.0,
            thinking_config=types.ThinkingConfig(thinking_level=LLM_THINKING_LEVEL),
        ),
        ),
        "entailment judgment",
    )
    return _parse_object(result.text, {"verdict": "NOT_SUPPORTED", "quote": ""})


def verify_claim(claim: dict) -> dict:
    """Retrieve candidate passages, then judge whether they support the claim."""
    labelled = claim.get("neighborhood") or None
    result = retrieve_neighborhood_info(
        labelled, claim["claim"], n_results=GROUNDING_CONTEXT_CHUNKS
    )
    chunks = result.get("chunks") or []
    scope = labelled or "all areas"

    # The extractor often labels a claim with a sub-area ("Koramangala 5th and
    # 6th blocks"), which matches no indexed neighborhood and retrieves nothing.
    # Judging that as unsupported would be an artefact of the label, not of the
    # claim, so widen the search to the whole corpus.
    if not chunks and labelled:
        result = retrieve_neighborhood_info(
            None, claim["claim"], n_results=GROUNDING_CONTEXT_CHUNKS
        )
        chunks = result.get("chunks") or []
        scope = f"all areas (fell back from {labelled!r})"
    best = chunks[0] if chunks else None
    verdict = judge_entailment(claim["claim"], chunks)

    return {
        "claim": claim["claim"],
        "type": claim.get("type"),
        "neighborhood": claim.get("neighborhood"),
        "grounded": verdict.get("verdict") == "SUPPORTED",
        "searched": scope,
        "verdict": verdict.get("verdict"),
        "supporting_quote": verdict.get("quote", ""),
        # Retrieval diagnostics — not the basis of the verdict.
        "top_similarity": best["similarity_score"] if best else 0.0,
        "source_url": best["source_url"] if best else None,
        "section": best["section_title"] if best else None,
    }


def check_listings(shortlist: list[dict]) -> list[dict]:
    """Every card on screen must be a real, available row in the database."""
    checks = []
    for listing in shortlist:
        row = listing_search.get_listing_by_id(listing["id"])
        matches = bool(row) and all(
            row.get(field) == listing.get(field) for field in ("rent", "bedrooms", "neighborhood")
        )
        checks.append(
            {
                "listing_id": listing["id"],
                "exists_in_db": row is not None,
                "is_available": bool(row) and row.get("availability") == "available",
                "data_matches": matches,
            }
        )
    return checks


def run(orchestrator=None) -> dict:
    fixture = load_fixture(FIXTURE)
    tests = []
    total_claims = grounded_claims = 0
    hallucinations: list[dict] = []

    for case in fixture["cases"]:
        turns: list[Turn] = replay(case["turns"], orchestrator)
        final = turns[-1]

        claims = extract_claims(final.response_text)
        verdicts = [verify_claim(c) for c in claims]
        listing_checks = check_listings(final.shortlist)

        total_claims += len(verdicts)
        grounded_claims += sum(1 for v in verdicts if v["grounded"])
        ungrounded = [v for v in verdicts if not v["grounded"]]
        hallucinations.extend(ungrounded)

        checks = [
            assertion(
                "all_claims_grounded",
                not ungrounded,
                total_claims=len(verdicts),
                ungrounded=[v["claim"] for v in ungrounded],
            ),
            assertion(
                "listings_exist_and_available",
                all(c["exists_in_db"] and c["is_available"] for c in listing_checks),
                problems=[c for c in listing_checks if not (c["exists_in_db"] and c["is_available"])],
            ),
            assertion(
                "listing_data_matches_db",
                all(c["data_matches"] for c in listing_checks),
                mismatched=[c["listing_id"] for c in listing_checks if not c["data_matches"]],
            ),
        ]

        if verdicts:
            cited_urls = {s.get("url") for s in final.sources}
            missing_citation = [
                v["claim"] for v in verdicts if v["grounded"] and v["source_url"] not in cited_urls
            ]
            checks.append(
                assertion(
                    "grounded_claims_are_cited",
                    not missing_citation,
                    uncited=missing_citation,
                )
            )

        if case.get("expects_uncertainty"):
            verdict = admits_missing_data(final.response_text)
            checks.append(
                assertion(
                    "admits_missing_data",
                    bool(verdict.get("admits_gap")),
                    quote=verdict.get("quote", ""),
                )
            )

        tests.append(
            {
                "test_id": case["test_id"],
                "description": case["description"],
                "passed": summarize(checks),
                "response_text": final.response_text,
                "shortlist": final.listing_ids,
                "claims": verdicts,
                "listing_checks": listing_checks,
                "assertions": checks,
            }
        )

    return build_report(
        "grounding",
        tests,
        {
            "verification": "llm_entailment_over_retrieved_chunks",
            "context_chunks_per_claim": GROUNDING_CONTEXT_CHUNKS,
            "total_claims": total_claims,
            "grounded_claims": grounded_claims,
            "grounding_rate": round(grounded_claims / total_claims, 4) if total_claims else 1.0,
            "hallucinations": [
                {"claim": h["claim"], "verdict": h["verdict"], "top_similarity": h["top_similarity"]}
                for h in hallucinations
            ],
        },
    )


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
