"""
Eval Runner
-----------
    python evals/run_evals.py --eval all
    python evals/run_evals.py --eval feasibility
    python evals/run_evals.py --eval grounding --quiet

Runs the selected evals against the real agent, writes a JSON report per eval to
`evals/results/`, prints a summary, and exits non-zero if anything failed — so it
can gate a commit or a deploy.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals import edit_correctness, feasibility_eval, grounding_eval, scope_eval  # noqa: E402
from evals.harness import EvalIncomplete, write_report  # noqa: E402

EVALS = {
    "feasibility": ("Feasibility", feasibility_eval),
    "edit": ("Edit Correctness", edit_correctness),
    "grounding": ("Grounding & Hallucination", grounding_eval),
    "scope": ("Scope (rentals only)", scope_eval),
}

WIDTH = 62


def _line(text: str = "") -> str:
    return f"║ {text:<{WIDTH - 4}} ║"


def _rule(left="╠", fill="═", right="╣") -> str:
    return f"{left}{fill * (WIDTH - 2)}{right}"


def render(reports: list[dict]) -> str:
    out = [f"╔{'═' * (WIDTH - 2)}╗", _line("AI Evaluation Results"), _rule()]

    for report in reports:
        label = EVALS[report["key"]][0]
        out.append(_line(f"Eval: {label}"))
        out.append(
            _line(
                f"Tests: {report['total_tests']} | Passed: {report['passed']} "
                f"| Failed: {report['failed']}"
            )
        )
        out.append(_line(f"Pass Rate: {report['pass_rate'] * 100:.0f}%"))

        if report["key"] == "grounding":
            out.append(
                _line(
                    f"Claims: {report['total_claims']} | Grounded: "
                    f"{report['grounded_claims']} | Hallucinations: "
                    f"{len(report['hallucinations'])}"
                )
            )

        for test in report["details"]:
            if test["passed"]:
                continue
            failed = [a["assertion"] for a in test["assertions"] if not a["passed"]]
            out.append(_line(f"  ✗ {test['test_id']}: {', '.join(failed)[:44]}"))
        out.append(_rule())

    total = sum(r["total_tests"] for r in reports)
    passed = sum(r["passed"] for r in reports)
    rate = (passed / total * 100) if total else 0
    out.append(_line(f"OVERALL: {passed}/{total} tests passed ({rate:.0f}%)"))
    out.append(f"╚{'═' * (WIDTH - 2)}╝")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the property scout evals.")
    parser.add_argument(
        "--eval",
        default="all",
        choices=[*EVALS.keys(), "all"],
        help="Which eval to run (default: all)",
    )
    parser.add_argument("--quiet", action="store_true", help="Only print the summary")
    args = parser.parse_args()

    selected = list(EVALS) if args.eval == "all" else [args.eval]
    reports: list[dict] = []
    incomplete: list[tuple[str, str]] = []

    for key in selected:
        label, module = EVALS[key]
        if not args.quiet:
            print(f"Running {label}…", flush=True)
        try:
            report = module.run()
        except EvalIncomplete as exc:
            # Never record an infrastructure failure as a behavioural result.
            print(f"  INCOMPLETE — {exc}\n", flush=True)
            incomplete.append((label, str(exc)))
            continue

        report["key"] = key
        path = write_report(key, report)
        reports.append(report)
        if not args.quiet:
            print(f"  {report['passed']}/{report['total_tests']} passed → {path}\n", flush=True)

    if reports:
        print(render(reports))
    for label, reason in incomplete:
        print(f"\n{label}: DID NOT COMPLETE — {reason}")

    if incomplete:
        return 2
    return 0 if all(r["failed"] == 0 for r in reports) else 1


if __name__ == "__main__":
    sys.exit(main())
