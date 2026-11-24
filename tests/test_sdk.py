from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.jev import MockJevClient
from app.main import create_app
from app.reviewer import NullReviewer
from tripwire_sdk import (
    ActionBlocked,
    ActionRejected,
    ApprovalTimeout,
    Tripwire,
    TripwireError,
    TripwireUnavailable,
)


def make_app():
    return create_app(Settings(database_url="sqlite://"), jev=MockJevClient(), reviewer=NullReviewer())


def make_sdk(goal="Summarise the Q3 report", **kw):
    http = TestClient(make_app())
    tw = Tripwire("http://testserver", default_goal=goal, client=http, **kw)
    return http, tw


def test_allowed_call_runs_and_returns_value():
    _, tw = make_sdk()
    ran = []

    @tw.guard
    def read_file(path, encoding="utf-8"):
        ran.append(path)
        return "data"

    assert read_file("q3.txt") == "data"
    assert ran == ["q3.txt"]


def test_arguments_and_defaults_are_sent_to_tripwire():
    http, tw = make_sdk()

    @tw.guard
    def read_file(path, encoding="utf-8"):
        return None

    read_file("q3.txt")
    logged = http.get("/actions/1").json()
    assert logged["tool"] == "read_file"
    assert logged["arguments"] == {"path": "q3.txt", "encoding": "utf-8"}


def test_blocked_call_does_not_run():
    _, tw = make_sdk()
    ran = []

    @tw.guard
    def delete_file(path):
        ran.append(path)

    with pytest.raises(ActionBlocked) as exc:
        delete_file("/data/prod.db")
    assert ran == []
    assert exc.value.tool == "delete_file"


def test_pending_call_runs_after_human_approves():
    holder = {}

    def approve_on_sleep(_):
        http = holder["http"]
        pending = http.get("/actions?status=pending").json()
        http.post(f"/approvals/{pending[0]['id']}", json={"approve": True})

    http, tw = make_sdk("Delete the old temp files", sleep=approve_on_sleep)
    holder["http"] = http
    ran = []

    @tw.guard
    def delete_file(path):
        ran.append(path)
        return "deleted"

    assert delete_file("/tmp/old.log") == "deleted"
    assert ran == ["/tmp/old.log"]


def test_pending_call_does_not_run_if_human_rejects():
    holder = {}

    def reject_on_sleep(_):
        http = holder["http"]
        pending = http.get("/actions?status=pending").json()
        http.post(f"/approvals/{pending[0]['id']}", json={"approve": False, "note": "nope"})

    http, tw = make_sdk("Delete the old temp files", sleep=reject_on_sleep)
    holder["http"] = http
    ran = []

    @tw.guard
    def delete_file(path):
        ran.append(path)

    with pytest.raises(ActionRejected) as exc:
        delete_file("/tmp/old.log")
    assert ran == []
    assert exc.value.note == "nope"


def test_unanswered_approval_times_out_and_does_not_run():
    _, tw = make_sdk("Delete the old temp files", approval_timeout=0)
    ran = []

    @tw.guard
    def delete_file(path):
        ran.append(path)

    with pytest.raises(ApprovalTimeout):
        delete_file("/tmp/old.log")
    assert ran == []


def test_unreachable_tripwire_fails_closed():
    tw = Tripwire("http://127.0.0.1:9", default_goal="anything", timeout=1.0)
    ran = []

    @tw.guard
    def read_file(path):
        ran.append(path)

    with pytest.raises(TripwireUnavailable):
        read_file("a.txt")
    assert ran == []


def test_missing_goal_is_an_error_not_a_silent_allow():
    http = TestClient(make_app())
    tw = Tripwire("http://testserver", client=http)  # no goal

    @tw.guard
    def read_file(path):
        return "x"

    with pytest.raises(TripwireError):
        read_file("a.txt")


def test_goal_context_manager_overrides_default():
    _, tw = make_sdk("Delete the old temp files")  # default goal allows deletes (needs human)

    @tw.guard
    def delete_file(path):
        return "deleted"

    # Under a different goal, the same call is off-task and blocked outright.
    with tw.goal("Summarise the Q3 report"):
        with pytest.raises(ActionBlocked):
            delete_file("/tmp/old.log")


def test_custom_tool_name():
    http, tw = make_sdk()

    @tw.guard(tool="fs.read")
    def read_file(path):
        return "x"

    read_file("a.txt")
    assert http.get("/actions/1").json()["tool"] == "fs.read"


def test_async_functions_are_supported():
    async def run():
        transport = httpx.ASGITransport(app=make_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            tw = Tripwire("http://testserver", default_goal="Summarise the Q3 report", async_client=ac)
            ran = []

            @tw.guard
            async def read_file(path):
                ran.append(path)
                return "ok"

            @tw.guard
            async def delete_file(path):
                ran.append(path)

            assert await read_file("a.txt") == "ok"
            with pytest.raises(ActionBlocked):
                await delete_file("/data/prod.db")
            assert ran == ["a.txt"]

    asyncio.run(run())


def test_dashboard_is_served():
    c = TestClient(make_app())
    r = c.get("/dashboard")
    assert r.status_code == 200 and "Tripwire" in r.text
    assert c.get("/", follow_redirects=False).status_code in (302, 307)