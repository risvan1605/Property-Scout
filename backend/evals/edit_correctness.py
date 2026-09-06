"""
Edit Correctness Eval (rule-based)
----------------------------------
A spoken refinement must do exactly what was asked — and nothing else.

The eval snapshots the shortlist before the edit, applies the edit through the
real agent, then diffs the two sets: what left, what arrived, and whether any
survivor's data changed. Side effects are the failure this is hunting for.
"""

from evals.harness import Turn, assertion, build_report, core, load_fixture, replay, summarize

FIXTURE = "voice_edit.json"


def _amenities(listing: dict) -> set[str]:
    return {a.lower() for a in listing.get("amenities", [])}


def _should_survive(listing: dict, keep_if: dict) -> bool:
    """Would this listing still qualify after the stated edit?"""
    if "max_rent" in keep_if and listing["rent"] > keep_if["max_rent"]:
        return False
    if "has_amenities" in keep_if:
        required = {a.lower() for a in keep_if["has_amenities"]}
        if not required.issubset(_amenities(listing)):
            return False
    return True


def diff(before: list[dict], after: list[dict]) -> dict:
    """What the edit did, in listing IDs plus any mutated survivor."""
    before_by_id = {l["id"]: l for l in before}
    after_by_id = {l["id"]: l for l in after}

    mutated = [
        {"id": lid, "before": core(before_by_id[lid]), "after": core(after_by_id[lid])}
        for lid in set(before_by_id) & set(after_by_id)
        if core(before_by_id[lid]) != core(after_by_id[lid])
    ]
    return {
        "removed": sorted(set(before_by_id) - set(after_by_id)),
        "added": sorted(set(after_by_id) - set(before_by_id)),
        "kept": sorted(set(before_by_id) & set(after_by_id)),
        "mutated": mutated,
    }


def check(spec: dict, before: list[dict], after: list[dict], changes: dict) -> dict:
    kind = spec["type"]

    if kind == "all_within_budget":
        budget = spec["budget"]
        over = [l["id"] for l in after if l["rent"] > budget]
        return assertion(f"all_within_budget<={budget}", not over, over_budget=over)

    if kind == "all_have_amenities":
        required = {a.lower() for a in spec["amenities"]}
        missing = [l["id"] for l in after if not required.issubset(_amenities(l))]
        return assertion(f"all_have_{'+'.join(sorted(required))}", not missing, missing=missing)

    if kind == "all_furnishing":
        wanted = spec["furnishing"].lower()
        wrong = [l["id"] for l in after if (l.get("furnishing") or "").lower() != wanted]
        return assertion(f"all_{wanted}", not wrong, wrong_furnishing=wrong)

    if kind == "no_unintended_removals":
        keep_if = spec.get("keep_if", {})
        should_stay = {l["id"] for l in before if _should_survive(l, keep_if)}
        lost = sorted(should_stay - {l["id"] for l in after})
        return assertion("no_unintended_removals", not lost, wrongly_removed=lost)

    if kind == "no_new_additions":
        return assertion("no_new_additions", not changes["added"], added=changes["added"])

    if kind == "survivors_unchanged":
        return assertion("survivors_unchanged", not changes["mutated"], mutated=changes["mutated"])

    if kind == "previous_listings_preserved":
        lost = changes["removed"]
        return assertion("previous_listings_preserved", not lost, wrongly_removed=lost)

    if kind == "has_new_additions":
        minimum = spec.get("min", 1)
        added = changes["added"]
        return assertion(f"has_new_additions>={minimum}", len(added) >= minimum, added=added)

    if kind == "new_listings_have_amenity":
        amenity = spec["amenity"].lower()
        after_by_id = {l["id"]: l for l in after}
        offenders = [
            lid for lid in changes["added"] if amenity not in _amenities(after_by_id[lid])
        ]
        return assertion(
            f"new_listings_have_{amenity}",
            bool(changes["added"]) and not offenders,
            added=changes["added"],
            without_amenity=offenders,
        )

    if kind == "shortlist_identical":
        identical = not changes["added"] and not changes["removed"] and not changes["mutated"]
        return assertion("shortlist_identical", identical, **changes)

    return assertion(f"unknown_assertion:{kind}", False, note="No checker for this type")


def run(orchestrator=None) -> dict:
    fixture = load_fixture(FIXTURE)
    tests = []

    for case in fixture["cases"]:
        # One continuous conversation: setup turns, then the edit.
        turns: list[Turn] = replay(case["setup_turns"] + [case["edit_turn"]], orchestrator)
        before = turns[-2].shortlist
        after = turns[-1].shortlist
        changes = diff(before, after)

        checks = [check(spec, before, after, changes) for spec in case["assertions"]]
        tests.append(
            {
                "test_id": case["test_id"],
                "description": case["description"],
                "edit_command": case["edit_turn"],
                "passed": summarize(checks),
                "before": [l["id"] for l in before],
                "after": [l["id"] for l in after],
                "diff": changes,
                "response_text": turns[-1].response_text,
                "assertions": checks,
            }
        )

    return build_report("edit_correctness", tests)


if __name__ == "__main__":
    import json

    print(json.dumps(run(), indent=2))
