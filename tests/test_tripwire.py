from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.jev import JevError, MockJevClient, build_request, parse_answers
from app.main import create_app
from app.reviewer import ReviewResult
from app.schemas import JevResult
from app.gate import apply_policy

SETTINGS = Settings(database_url="sqlite://")


class FixedJev:
    def __init__(self, result: JevResult):
        self.result = result

    async def assess(self, goal, tool, arguments):
        return self.result


class BrokenJev:
    async def assess(self, goal, tool, arguments):
        raise JevError("boom")


class StubReviewer:
    def __init__(self, verdict="block"):
        self.verdict = verdict
        self.calls = 0

    async def review(self, goal, tool, arguments, jev):
        self.calls += 1
        return ReviewResult(verdict=self.verdict, reason="stub review", reviewer="claude")


def client(jev=None, reviewer=None) -> TestClient:
    app = create_app(SETTINGS, jev=jev or MockJevClient(), reviewer=reviewer or StubReviewer())
    return TestClient(app)


def check(c, goal, tool, args=None):
    return c.post("/check", json={"goal": goal, "tool": tool, "arguments": args or {}}).json()


def test_safe_action_is_allowed():
    body = check(client(), "Summarise the Q3 report", "read_file", {"path": "q3.txt"})
    assert body["verdict"] == "allow"
    assert body["status"] == "allowed"


def test_destructive_off_task_action_is_blocked():
    body = check(client(), "Summarise the Q3 report", "delete_file", {"path": "/data/prod.db"})
    assert body["verdict"] == "block"
    assert body["status"] == "blocked"


def test_destructive_but_requested_action_needs_human_then_approval_flow():
    c = client()
    body = check(c, "Delete the old temp files", "delete_file", {"path": "/tmp/old.log"})
    assert body["verdict"] == "ask_human"
    assert body["status"] == "pending"

    action_id = body["action_id"]
    assert c.get(f"/actions?status=pending").json()[0]["id"] == action_id

    resolved = c.post(f"/approvals/{action_id}", json={"approve": True, "note": "ok"}).json()
    assert resolved["status"] == "approved"
    assert resolved["decided_by"] == "human"

    # Cannot resolve twice
    again = c.post(f"/approvals/{action_id}", json={"approve": False})
    assert again.status_code == 409


def test_unknown_action_404():
    c = client()
    assert c.get("/actions/999").status_code == 404
    assert c.post("/approvals/999", json={"approve": True}).status_code == 404


def test_low_confidence_goes_to_reviewer():
    jev = FixedJev(JevResult(is_destructive=0.3, off_task=0.3, decision="allow", confidence=0.4))
    reviewer = StubReviewer("block")
    body = check(client(jev, reviewer), "do a thing", "mystery_tool")
    assert reviewer.calls == 1
    assert body["verdict"] == "block"
    assert body["decided_by"] == "claude"


def test_unclear_goes_to_reviewer():
    jev = FixedJev(JevResult(is_destructive=0.1, off_task=0.1, decision="unclear", confidence=0.95))
    reviewer = StubReviewer("ask_human")
    body = check(client(jev, reviewer), "do a thing", "mystery_tool")
    assert reviewer.calls == 1
    assert body["verdict"] == "ask_human"


def test_confident_decision_skips_reviewer():
    jev = FixedJev(JevResult(is_destructive=0.0, off_task=0.0, decision="allow", confidence=0.99))
    reviewer = StubReviewer("block")
    body = check(client(jev, reviewer), "read stuff", "read_file")
    assert reviewer.calls == 0
    assert body["verdict"] == "allow"


def test_jev_failure_fails_closed():
    reviewer = StubReviewer("allow")  # even a permissive reviewer must not yield "allow"
    body = check(client(BrokenJev(), reviewer), "anything", "any_tool")
    assert body["verdict"] == "ask_human"
    assert "Jev unavailable" in body["reason"]


def test_allow_with_high_destructive_probability_is_overridden():
    r = JevResult(is_destructive=0.95, off_task=0.1, decision="allow", confidence=0.9)
    out = apply_policy(r, SETTINGS)
    assert out is not None and out.verdict == "ask_human" and out.decided_by == "policy"


def test_allow_with_high_off_task_probability_is_overridden():
    r = JevResult(is_destructive=0.1, off_task=0.95, decision="allow", confidence=0.9)
    out = apply_policy(r, SETTINGS)
    assert out is not None and out.verdict == "ask_human"


def test_request_shape_matches_typesafe_api():
    req = build_request("typesafe-ai/jev", "goal", "tool", {"a": 1})
    assert req["model"] == "typesafe-ai/jev"
    qs = req["questions"]
    assert qs["is_destructive"]["type"] == "noul"
    assert qs["decision"]["type"] == "choice"
    assert set(qs["decision"]["criteria"]) == {"allow", "ask_human", "block", "unclear"}


def test_parse_answers_happy_path_and_bad_choice():
    data = {
        "answers": {
            "is_destructive": {"type": "noul", "noul": 0.2},
            "off_task": {"type": "noul", "noul": 0.1},
            "decision": {"type": "choice", "choice": "allow", "confidence": 0.93, "probabilities": {}},
        }
    }
    r = parse_answers(data)
    assert (r.decision, r.confidence, r.source) == ("allow", 0.93, "jev")

    data["answers"]["decision"]["choice"] = "something_else"
    assert parse_answers(data).decision == "unclear"


def test_parse_answers_malformed_raises():
    with pytest.raises(JevError):
        parse_answers({"answers": {}})


def test_health_reports_mock_mode():
    body = client().get("/health").json()
    assert body["jev"] == "mock"
