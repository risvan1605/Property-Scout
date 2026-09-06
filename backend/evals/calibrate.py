"""
Grounding Verifier Calibration
------------------------------
Checks that the grounding eval can actually tell a fact from a fabrication, using
six claims drawn from the corpus and six invented ones that sound plausible.

This is how the verifier was chosen. A similarity cut-off — the obvious design —
fails here: fabrications score 0.73-0.83 and true claims 0.75-0.91, so the ranges
overlap and no threshold separates them. Embedding similarity measures topical
closeness, not support. The eval therefore retrieves with similarity but decides
with an entailment judge, which this script verifies end to end.

    python evals/calibrate.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.rag_retriever import retrieve_neighborhood_info  # noqa: E402

# Claims a correct assistant could make — each is supported by the guides.
GROUNDED = [
    ("Koramangala", "Koramangala has well-lit main roads with regular police patrols"),
    ("Koramangala", "Koramangala is known as the startup hub of Bengaluru"),
    ("Indiranagar", "Indiranagar Metro Station is on the Purple Line"),
    ("Indiranagar", "Indiranagar has a dense concentration of pubs and bars"),
    ("HSR Layout", "HSR Layout is a planned BDA layout organised into sectors"),
    ("HSR Layout", "HSR Layout is considered one of the safest neighborhoods in Bengaluru"),
]

# Plausible-sounding claims the corpus does NOT support — these must score low.
FABRICATED = [
    ("Koramangala", "Koramangala has the lowest property tax rate in Karnataka"),
    ("Koramangala", "The average commute from Koramangala to the airport is 22 minutes"),
    ("Indiranagar", "Indiranagar was voted India's cleanest neighborhood in 2024"),
    ("Indiranagar", "Indiranagar has three international schools within one kilometre"),
    ("HSR Layout", "HSR Layout has a dedicated cycling superhighway to Electronic City"),
    ("HSR Layout", "Rents in HSR Layout fell by twelve percent last year"),
]


def evaluate(area: str, claim: str) -> tuple[float, str]:
    """Return (top similarity, entailment verdict) for one claim."""
    from evals.grounding_eval import GROUNDING_CONTEXT_CHUNKS, judge_entailment

    result = retrieve_neighborhood_info(area, claim, n_results=GROUNDING_CONTEXT_CHUNKS)
    chunks = result.get("chunks") or []
    similarity = chunks[0]["similarity_score"] if chunks else 0.0
    return similarity, judge_entailment(claim, chunks).get("verdict", "NOT_SUPPORTED")


def main() -> None:
    rows, correct = [], 0

    print(f"{'expected':>10}  {'verdict':>14}  {'sim':>5}  claim")
    for label, cases in (("supported", GROUNDED), ("fabricated", FABRICATED)):
        for area, claim in cases:
            similarity, verdict = evaluate(area, claim)
            is_right = (verdict == "SUPPORTED") == (label == "supported")
            correct += is_right
            rows.append((label, verdict, similarity))
            mark = "ok " if is_right else "MISS"
            print(f"{label:>10}  {verdict:>14}  {similarity:.3f}  {mark} {claim[:52]}")

    sims_true = [s for lbl, _, s in rows if lbl == "supported"]
    sims_false = [s for lbl, _, s in rows if lbl == "fabricated"]
    total = len(rows)

    print(f"\nentailment judge: {correct}/{total} correct")
    print(f"similarity ranges — grounded {min(sims_true):.3f}-{max(sims_true):.3f}, "
          f"fabricated {min(sims_false):.3f}-{max(sims_false):.3f}")
    if min(sims_true) <= max(sims_false):
        print("similarity alone CANNOT separate these (ranges overlap), which is why "
              "the verdict comes from the judge rather than a threshold.")


if __name__ == "__main__":
    main()
