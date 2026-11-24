"""The decision policy. Jev gives probabilities; this module turns them into a verdict.

Design rule: fail closed. Anything uncertain, broken, or contradictory ends in `ask_human`,
never in a silent `allow`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Settings
from .jev import JevClient, JevError
from .reviewer import Reviewer
from .schemas import JevResult, Verdict


@dataclass
class Outcome:
    verdict: Verdict
    reason: str
    decided_by: str  # "jev", "claude", "policy", or "none"
    jev: JevResult | None


def apply_policy(r: JevResult, s: Settings) -> Outcome | None:
    """Return an Outcome, or None if Jev is too uncertain and the slow path should decide."""
    if r.decision == "unclear" or r.confidence < s.low_confidence:
        return None

    if r.decision == "block":
        return Outcome("block", f"Jev blocked this call (confidence {r.confidence:.2f})", "jev", r)

    if r.decision == "allow":
        if r.is_destructive >= s.destructive_threshold:
            return Outcome(
                "ask_human",
                f"Jev said allow, but destructive probability is {r.is_destructive:.2f}",
                "policy",
                r,
            )
        if r.off_task >= s.off_task_threshold:
            return Outcome(
                "ask_human",
                f"Jev said allow, but off-task probability is {r.off_task:.2f}",
                "policy",
                r,
            )
        return Outcome("allow", f"Routine and on-task (confidence {r.confidence:.2f})", "jev", r)

    return Outcome("ask_human", f"Jev asked for human confirmation (confidence {r.confidence:.2f})", "jev", r)


async def evaluate(
    goal: str,
    tool: str,
    arguments: dict[str, Any],
    *,
    jev: JevClient,
    reviewer: Reviewer,
    settings: Settings,
) -> Outcome:
    try:
        result = await jev.assess(goal, tool, arguments)
    except JevError as exc:
        # Jev is down or returned garbage: fail closed, but still give the reviewer a chance.
        review = await reviewer.review(goal, tool, arguments, None)
        reason = f"Jev unavailable ({exc}); {review.reason}"
        verdict = "block" if review.verdict == "block" else "ask_human"
        return Outcome(verdict, reason, review.reviewer, None)

    outcome = apply_policy(result, settings)
    if outcome is not None:
        return outcome

    review = await reviewer.review(goal, tool, arguments, result)
    return Outcome(
        review.verdict,
        f"Jev uncertain ({result.decision} @ {result.confidence:.2f}); {review.reason}",
        review.reviewer,
        result,
    )
