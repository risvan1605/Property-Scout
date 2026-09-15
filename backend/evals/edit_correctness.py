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
    if "not_bedrooms" in keep_if and listing["bedrooms"] in keep_if["not_bedrooms"]:
        return False
    if "min_sqft" in keep_if and (listing.get("sqft") or 0) < keep_if["min_sqft"]:
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


def check(
    spec: dict, before: list[dict], after: list[dict], changes: dict, dropped: list[dict]
) -> dict:
    kind = spec["type"]

    if kind == "all_within_budget":
        budget = spec["budget"]
        over = [l["id"] for l in after if l["rent"] > budget]
        return assertion(f"all_within_budget<={budget}", not over, over_budget=over)

    if kind == "all_have_amenities":
        required = {a.lower() for a in spec["amenities"]}
        missing = [l["id"] for l in after if not required.issubset(_amenities(l))]
        return assertion(f"all_have_{'+'.join(sorted(required))}", not missing, missing=missing)

    if kind == "none_with_bedrooms":
        sizes = set(spec["bedrooms"])
        left = [l["id"] for l in after if l.get("bedrooms") in sizes]
        label = "+".join(str(b) for b in sorted(sizes))
        return assertion(f"none_with_{label}BHK", not left, still_listed=left)

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
        minimum, maximum = spec.get("min", 1), spec.get("max")
        added = changes["added"]
        in_range = len(added) >= minimum and (maximum is None or len(added) <= maximum)
        label = f"{minimum}-{maximum}" if maximum is not None else f">={minimum}"
        return assertion(f"has_new_additions:{label}", in_range, added=added)

    if kind == "has_removals":
        minimum = spec.get("min", 1)
        removed = changes["removed"]
        return assertion(f"has_removals>={minimum}", len(removed) >= minimum, removed=removed)

    if kind == "all_min_sqft":
        floor = spec["sqft"]
        small = [l["id"] for l in after if (l.get("sqft") or 0) < floor]
        return assertion(f"all_min_sqft>={floor}", not small, too_small=small)

    if kind == "removed_exactly":
        expected = sorted(spec["ids"])
        return assertion("removed_exactly", changes["removed"] == expected,
                         expected=expected, removed=changes["removed"])

    if kind == "not_listed":
        back = [lid for lid in spec["ids"] if lid in {l["id"] for l in after}]
        return assertion("dropped_listings_stay_dropped", not back, reappeared=back)

    if kind == "removals_have_reasons":
        reasons = {d["listing_id"]: (d.get("reason") or "").strip() for d in dropped}
        unexplained = [lid for lid in changes["removed"] if not reasons.get(lid)]
        return assertion("removals_have_reasons", not unexplained, unexplained=unexplained)

    if kind == "kept_near_metro":
        # Only listings whose metro lookup actually came back can be judged; one
        # the agent couldn't check is rightly kept and said so, not failed here.
        limit = spec["max_km"]
        far, unverified = [], []
        for listing in after:
            pois = listing.get("nearby_pois") or {}
            if pois.get("error") or "metro_stations" not in pois:
                unverified.append(listing["id"])
                continue
            distances = [p.get("distance_km") for p in pois["metro_stations"]
                         if p.get("distance_km") is not None]
            if not distances or min(distances) > limit:
                far.append(listing["id"])
        return assertion(f"kept_within_{limit}km_of_metro", not far,
                         too_far=far, unverified=unverified)

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

        dropped = turns[-1].dropped
        checks = [check(spec, before, after, changes, dropped) for spec in case["assertions"]]
        tests.append(
            {
                "test_id": case["test_id"],
                "description": case["description"],
                "edit_command": case["edit_turn"],
                "passed": summarize(checks),
                "before": [l["id"] for l in before],
                "after": [l["id"] for l in after],
                "diff": changes,
                "dropped": dropped,
                "response_text": turns[-1].response_text,
                "assertions": checks,
            }
        )

    return build_report("edit_correctness", tests)


if __name__ == "__main__":
    import json

    print(json.dumps(run(), indent=2))
