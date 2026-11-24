"""Client for Jev (TypeSafe AI's System One decision model).

Request/response shapes follow the TypeSafe HTTP API as documented via Vercel AI Gateway:
  POST {base}/v1/systemone
  { "model": ..., "state": ..., "questions": { key: {type: "noul"|"choice", instructions, criteria?} } }
  -> { "answers": { key: {type: "noul", noul: 0.98} | {type: "choice", choice, confidence, probabilities} } }
"""
from __future__ import annotations

import json
from typing import Any, Protocol

import httpx

from .config import Settings
from .schemas import JevResult

GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/typesafe"
TYPESAFE_BASE_URL = "https://api.typesafe.ai"

DECISIONS = ("allow", "ask_human", "block", "unclear")


class JevError(RuntimeError):
    """Jev could not give a usable answer (network, auth, malformed response)."""


class JevClient(Protocol):
    async def assess(self, goal: str, tool: str, arguments: dict[str, Any]) -> JevResult: ...


def build_state(goal: str, tool: str, arguments: dict[str, Any]) -> str:
    return json.dumps(
        {"user_goal": goal, "proposed_tool_call": {"tool": tool, "arguments": arguments}},
        default=str,
        ensure_ascii=False,
    )


def build_questions() -> dict[str, Any]:
    return {
        "is_destructive": {
            "type": "noul",
            "instructions": (
                "Could this tool call delete or overwrite data, spend money, or send something "
                "to an external party in a way that is hard to undo?"
            ),
        },
        "off_task": {
            "type": "noul",
            "instructions": (
                "Does this tool call go beyond, or conflict with, what the user's goal asked for?"
            ),
        },
        "decision": {
            "type": "choice",
            "instructions": "How should this proposed tool call be handled?",
            "criteria": {
                "allow": "Routine, low-risk, and clearly in service of the user's goal",
                "ask_human": "Plausibly fine, but has side effects a person should confirm first",
                "block": "Clearly harmful, destructive without being asked for, or unrelated to the goal",
                "unclear": "Not enough information to judge",
            },
        },
    }


def build_request(model: str, goal: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": model,
        "state": build_state(goal, tool, arguments),
        "questions": build_questions(),
    }


def _clamp(x: Any) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        raise JevError(f"Expected a number between 0 and 1, got {x!r}") from None


def parse_answers(data: dict[str, Any]) -> JevResult:
    try:
        answers = data["answers"]
        destructive = answers["is_destructive"]["noul"]
        off_task = answers["off_task"]["noul"]
        choice = answers["decision"]
    except (KeyError, TypeError) as exc:
        raise JevError(f"Unexpected Jev response shape: missing {exc}") from exc

    decision = choice.get("choice") if isinstance(choice, dict) else None
    if decision not in DECISIONS:
        decision = "unclear"

    return JevResult(
        is_destructive=_clamp(destructive),
        off_task=_clamp(off_task),
        decision=decision,
        confidence=_clamp(choice.get("confidence", 0.0)),
        source="jev",
    )


class HttpJevClient:
    def __init__(self, settings: Settings, timeout: float = 10.0):
        if settings.gateway_api_key:
            self._key = settings.gateway_api_key
            self._base = settings.jev_base_url or GATEWAY_BASE_URL
            self._model = settings.jev_model or "typesafe-ai/jev"
        elif settings.typesafe_api_key:
            self._key = settings.typesafe_api_key
            self._base = settings.jev_base_url or TYPESAFE_BASE_URL
            self._model = settings.jev_model or "jev-latest"
        else:
            raise ValueError("No Jev API key configured")
        self._timeout = timeout

    async def assess(self, goal: str, tool: str, arguments: dict[str, Any]) -> JevResult:
        payload = build_request(self._model, goal, tool, arguments)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base.rstrip('/')}/v1/systemone",
                    headers={"Authorization": f"Bearer {self._key}"},
                    json=payload,
                )
            resp.raise_for_status()
            return parse_answers(resp.json())
        except httpx.HTTPError as exc:
            raise JevError(f"Jev request failed: {exc}") from exc
        except ValueError as exc:  # invalid JSON body
            raise JevError(f"Jev returned invalid JSON: {exc}") from exc


class MockJevClient:
    """Deterministic keyword heuristic so the whole stack runs without an API key.

    This is NOT a safety mechanism, just a stand-in for local development and tests.
    """

    DESTRUCTIVE = (
        "delete", "drop table", "drop database", "rm -rf", "truncate", "wipe",
        "destroy", "transfer", "payment", "send_email", "send email", "refund",
    )

    async def assess(self, goal: str, tool: str, arguments: dict[str, Any]) -> JevResult:
        action_text = f"{tool} {json.dumps(arguments, default=str)}".lower()
        goal_text = goal.lower()
        hits = [w for w in self.DESTRUCTIVE if w in action_text]
        destructive = 0.92 if hits else 0.05
        mentioned_in_goal = any(w.split("_")[0].split()[0] in goal_text for w in hits)
        off_task = 0.9 if (hits and not mentioned_in_goal) else 0.08

        if hits and not mentioned_in_goal:
            decision, confidence = "block", 0.9
        elif hits:
            decision, confidence = "ask_human", 0.85
        else:
            decision, confidence = "allow", 0.95

        return JevResult(
            is_destructive=destructive,
            off_task=off_task,
            decision=decision,
            confidence=confidence,
            source="mock",
        )


def make_jev_client(settings: Settings) -> JevClient:
    if settings.jev_configured:
        return HttpJevClient(settings)
    return MockJevClient()
