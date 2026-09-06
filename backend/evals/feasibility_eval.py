"""
Feasibility Eval (rule-based)
-----------------------------
Does the shortlist actually satisfy what the user asked for?

Purely deterministic: every assertion is checked against the listing rows the
agent put on screen, with no LLM in the judging path. A failure names the
listings responsible.
"""

from evals.harness import Turn, assertion, build_report, load_fixture, replay, summarize

FIXTURE = "budget_check.json"


def _amenities(listing: dict) -> set[str]:
    return {a.lower() for a in listing.get("amenities", [])}


def check(spec: dict, shortlist: list[dict]) -> dict:
    """Evaluate one assertion against the final shortlist."""
    kind = spec["type"]

    if kind == "all_within_budget":
        budget = spec["budget"]
        over = [l["id"] for l in shortlist if l["rent"] > budget]
        return assertion(f"all_within_budget<={budget}", not over, over_budget=over)

    if kind == "all_min_bedrooms":
        wanted = spec["bedrooms"]
        short = [l["id"] for l in shortlist if l.get("bedrooms", 0) < wanted]
        return assertion(f"all_min_bedrooms>={wanted}", not short, too_few_bedrooms=short)

    if kind == "all_in_neighborhood":
        area = spec["neighborhood"].lower()
        wrong = [l["id"] for l in shortlist if (l.get("neighborhood") or "").lower() != area]
        return assertion(f"all_in_{spec['neighborhood']}", not wrong, wrong_neighborhood=wrong)

    if kind == "all_have_amenities":
        required = {a.lower() for a in spec["amenities"]}
        missing = [l["id"] for l in shortlist if not required.issubset(_amenities(l))]
        return assertion(
            f"all_have_{'+'.join(sorted(required))}", not missing, missing_amenity=missing
        )

    if kind == "all_available":
        unavailable = [l["id"] for l in shortlist if l.get("availability") != "available"]
        return assertion("all_available", not unavailable, unavailable=unavailable)

    if kind == "empty_shortlist":
        return assertion(
            "empty_shortlist", not shortlist, unexpected=[l["id"] for l in shortlist]
        )

    if kind == "min_results":
        count = spec["count"]
        return assertion(f"min_results>={count}", len(shortlist) >= count, returned=len(shortlist))

    if kind == "contains_listings":
        expected = set(spec["listing_ids"])
        present = {l["id"] for l in shortlist}
        missing = sorted(expected - present)
        return assertion("contains_listings", not missing, missing=missing)

    return assertion(f"unknown_assertion:{kind}", False, note="No checker for this type")


def run(orchestrator=None) -> dict:
    fixture = load_fixture(FIXTURE)
    tests = []

    for case in fixture["cases"]:
        turns: list[Turn] = replay(case["turns"], orchestrator)
        final = turns[-1]
        checks = [check(spec, final.shortlist) for spec in case["assertions"]]

        tests.append(
            {
                "test_id": case["test_id"],
                "description": case["description"],
                "passed": summarize(checks),
                "shortlist": final.listing_ids,
                "response_text": final.response_text,
                "assertions": checks,
            }
        )

    return build_report("feasibility", tests)


if __name__ == "__main__":
    import json

    print(json.dumps(run(), indent=2))
