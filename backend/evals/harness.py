"""
Eval Harness
------------
Shared plumbing for the three evals: replaying conversations through the real
orchestrator, loading fixtures, and writing reports.

Every eval drives the actual agent — same prompts, same tools, same database —
so a passing run says something about the system, not about a mock.
"""

import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Allow `python evals/run_evals.py` from the backend directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.conversation import ConversationState, SessionStore  # noqa: E402
from core.orchestrator import Orchestrator  # noqa: E402

# The free tier allows 15 requests/minute and a turn costs about two, so turns
# are paced. Without this the evals rate-limit themselves and score the agent on
# replies it never got to make.
TURN_DELAY_SECONDS = float(os.getenv("EVAL_TURN_DELAY", "5"))
QUOTA_RETRIES = int(os.getenv("EVAL_QUOTA_RETRIES", "3"))
DEFAULT_QUOTA_WAIT = float(os.getenv("EVAL_QUOTA_WAIT", "25"))

EVALS_DIR = os.path.dirname(os.path.abspath(__file__))
TRANSCRIPTS_DIR = os.path.join(EVALS_DIR, "test_transcripts")
RESULTS_DIR = os.path.join(EVALS_DIR, "results")

# Fields that come straight from the database. POI data and neighborhood
# snapshots are enriched per turn and can legitimately differ between calls
# (cache warmth, a live Overpass outage), so only these are compared when
# checking that a listing survived an edit untouched.
CORE_FIELDS = (
    "id", "society_name", "neighborhood", "rent", "bedrooms",
    "sqft", "furnishing", "amenities", "availability",
)


class EvalIncomplete(RuntimeError):
    """The agent never produced a real answer, so nothing can be scored.

    Raised instead of recording a failure — a quota error is not a behavioural
    result, and reporting it as one would be a lie about the system.
    """


def _is_quota_error(message: str) -> bool:
    return "RESOURCE_EXHAUSTED" in message or "429" in message


def _retry_delay(message: str) -> float:
    """Honour the server's own retry hint when it gives one."""
    match = re.search(r"retryDelay['\"]?:\s*['\"]?(\d+(?:\.\d+)?)s", message)
    return float(match.group(1)) + 2 if match else DEFAULT_QUOTA_WAIT


def with_quota_retry(call, label: str = "judge call"):
    """Run a synchronous LLM call, waiting out per-minute quota limits.

    The evaluator LLM shares the agent's quota, so its calls need the same
    protection — an eval that dies mid-run tells you nothing about the system.
    """
    for attempt in range(1, QUOTA_RETRIES + 1):
        try:
            return call()
        except Exception as exc:
            message = str(exc)
            if not _is_quota_error(message):
                raise
            if attempt == QUOTA_RETRIES:
                raise EvalIncomplete(
                    f"Gemini quota exhausted after {QUOTA_RETRIES} attempts on {label}."
                )
            wait = _retry_delay(message)
            print(f"    quota hit on {label}; waiting {wait:.0f}s", flush=True)
            time.sleep(wait)
    raise EvalIncomplete("unreachable")


@dataclass
class Turn:
    """One replayed exchange."""
    text: str
    response_text: str
    shortlist: list[dict]
    sources: list[dict]
    state: str
    booking: dict | None
    tool_calls: list[dict] = field(default_factory=list)
    dropped: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def listing_ids(self) -> list[str]:
        return [listing["id"] for listing in self.shortlist]


def core(listing: dict) -> dict:
    """The database-sourced half of a shortlist card."""
    return {k: listing.get(k) for k in CORE_FIELDS}


async def _one_turn(session, orchestrator: Orchestrator, text: str) -> dict:
    """Run a turn, waiting out per-minute quota limits rather than scoring them."""
    for attempt in range(1, QUOTA_RETRIES + 1):
        payload = await orchestrator.process(session, text)
        error = payload.get("error") or ""
        if not error or not _is_quota_error(error):
            return payload

        if attempt == QUOTA_RETRIES:
            raise EvalIncomplete(
                f"Gemini quota exhausted after {QUOTA_RETRIES} attempts on turn: {text!r}. "
                "Re-run when quota resets, or raise EVAL_TURN_DELAY."
            )
        wait = _retry_delay(error)
        print(f"    quota hit; waiting {wait:.0f}s before retrying turn", flush=True)
        await asyncio.sleep(wait)
        # The failed turn was still appended to history; drop both halves so the
        # retry sees the same conversation the first attempt saw.
        del session.conversation_history[-2:]
    raise EvalIncomplete("unreachable")


async def _replay_async(turns: list[str], orchestrator: Orchestrator) -> list[Turn]:
    store = SessionStore()
    session: ConversationState = store.create_session()
    results: list[Turn] = []

    for index, text in enumerate(turns):
        if index:
            await asyncio.sleep(TURN_DELAY_SECONDS)
        payload = await _one_turn(session, orchestrator, text)
        results.append(
            Turn(
                text=text,
                response_text=payload.get("response_text", ""),
                shortlist=payload.get("shortlist", []),
                sources=payload.get("sources", []),
                state=payload.get("state", ""),
                booking=payload.get("booking"),
                tool_calls=payload.get("tool_calls", []),
                dropped=payload.get("dropped", []),
                error=payload.get("error"),
            )
        )
    return results


def replay(turns: list[str], orchestrator: Orchestrator | None = None) -> list[Turn]:
    """Run a conversation through the real agent and return every turn."""
    return asyncio.run(_replay_async(turns, orchestrator or Orchestrator()))


def load_fixture(filename: str) -> dict:
    path = os.path.join(TRANSCRIPTS_DIR, filename)
    with open(path) as handle:
        return json.load(handle)


def write_report(eval_name: str, payload: dict) -> str:
    """Persist a report and return its path."""
    os.makedirs(RESULTS_DIR, exist_ok=True)
    path = os.path.join(RESULTS_DIR, f"{eval_name}.json")
    with open(path, "w") as handle:
        json.dump(payload, handle, indent=2)
    return path


def build_report(eval_name: str, tests: list[dict], extra: dict | None = None) -> dict:
    passed = sum(1 for t in tests if t["passed"])
    return {
        "eval_name": eval_name,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "total_tests": len(tests),
        "passed": passed,
        "failed": len(tests) - passed,
        "pass_rate": round(passed / len(tests), 4) if tests else 0.0,
        **(extra or {}),
        "details": tests,
    }


def assertion(name: str, passed: bool, **detail: Any) -> dict:
    """One checked condition, with whatever evidence explains a failure."""
    return {"assertion": name, "passed": bool(passed), **detail}


def summarize(assertions: list[dict]) -> bool:
    return all(a["passed"] for a in assertions)
