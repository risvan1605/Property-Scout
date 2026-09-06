"""
Scope Eval (rule-based)
-----------------------
The dataset is rental-only and covers three neighborhoods. This eval checks the
assistant refuses to step outside that: no invented sale prices, no listings in
areas it has no data for.

Deliberately keyword-based rather than LLM-judged — the failure it hunts for is
a price-shaped token appearing at all, which is exactly checkable.
"""

from evals.harness import assertion, build_report, load_fixture, replay, summarize

FIXTURE = "scope_check.json"

# A reply that scopes itself says both that it handles rentals and that it
# doesn't do something. Checking one without the other passes vacuously.
RENTAL_WORDS = ("rental", "rent")
NEGATIONS = ("only", "not", "don't", "do not", "cannot", "can't")


def _states_rental_only(text: str) -> bool:
    lowered = text.lower()
    return any(w in lowered for w in RENTAL_WORDS) and any(n in lowered for n in NEGATIONS)


def run(orchestrator=None) -> dict:
    fixture = load_fixture(FIXTURE)
    tests = []

    for case in fixture["cases"]:
        turns = replay(case["turns"], orchestrator)
        final = turns[-1]
        spoken = final.response_text.lower()
        rules = case["assertions"]
        checks = []

        if rules.get("shortlist_empty"):
            checks.append(
                assertion("shortlist_empty", not final.shortlist, returned=final.listing_ids)
            )

        if "no_tool_calls" in rules:
            banned = set(rules["no_tool_calls"])
            used = sorted({c["name"] for c in final.tool_calls} & banned)
            checks.append(assertion("no_forbidden_tool_calls", not used, called=used))

        if rules.get("states_rental_only"):
            checks.append(assertion("states_rental_only", _states_rental_only(final.response_text)))

        if "forbidden_substrings" in rules:
            hits = [w for w in rules["forbidden_substrings"] if w in spoken]
            checks.append(assertion("no_sale_price_language", not hits, found=hits))

        if "names_covered_areas" in rules:
            missing = [a for a in rules["names_covered_areas"] if a.lower() not in spoken]
            checks.append(assertion("names_covered_areas", not missing, not_mentioned=missing))

        tests.append(
            {
                "test_id": case["id"],
                "description": case.get("assertions", {}).get("note", ""),
                "passed": summarize(checks),
                "turns": case["turns"],
                "response_text": final.response_text,
                "shortlist": final.listing_ids,
                "assertions": checks,
            }
        )

    return build_report("scope", tests)


if __name__ == "__main__":
    import json

    print(json.dumps(run(), indent=2))
