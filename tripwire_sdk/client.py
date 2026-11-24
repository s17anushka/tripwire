"""Tripwire SDK: wrap any tool function so it asks Tripwire before it runs.

    tw = Tripwire("http://localhost:8000", default_goal="Summarise the Q3 report")

    @tw.guard
    def delete_file(path): ...

Design rule: fail closed. If Tripwire is unreachable, blocks the call, rejects it, or nobody
approves it in time, the wrapped function does NOT run and an exception is raised.
"""
from __future__ import annotations

import asyncio
import contextvars
import functools
import inspect
import json
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

import httpx

_goal_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("tripwire_goal", default=None)


class TripwireError(Exception):
    """Base class for all Tripwire SDK errors."""


class ActionBlocked(TripwireError):
    def __init__(self, tool: str, reason: str, action_id: int | None = None):
        super().__init__(f"Tripwire blocked '{tool}': {reason}")
        self.tool, self.reason, self.action_id = tool, reason, action_id


class ActionRejected(TripwireError):
    def __init__(self, tool: str, note: str | None, action_id: int):
        suffix = f" ({note})" if note else ""
        super().__init__(f"A human rejected '{tool}'{suffix}")
        self.tool, self.note, self.action_id = tool, note, action_id


class ApprovalTimeout(TripwireError):
    def __init__(self, tool: str, action_id: int, waited: float):
        super().__init__(f"No human decision on '{tool}' within {waited:.0f}s (action {action_id})")
        self.tool, self.action_id, self.waited = tool, action_id, waited


class TripwireUnavailable(TripwireError):
    """Tripwire could not be reached or returned an error. The tool call is not run."""


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=repr))


class Tripwire:
    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        *,
        default_goal: str | None = None,
        agent_id: str | None = None,
        approval_timeout: float = 300.0,
        poll_interval: float = 2.0,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
        async_client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Any] = time.sleep,
        async_sleep: Callable[[float], Any] = asyncio.sleep,
    ):
        self.base_url = base_url.rstrip("/")
        self.default_goal = default_goal
        self.agent_id = agent_id
        self.approval_timeout = approval_timeout
        self.poll_interval = poll_interval
        self._timeout = timeout
        self._client = client
        self._aclient = async_client
        self._sleep = sleep
        self._asleep = async_sleep

    # ----- goal handling -------------------------------------------------

    @contextmanager
    def goal(self, text: str) -> Iterator[None]:
        """Set the user's goal for every guarded call made inside the `with` block."""
        token = _goal_var.set(text)
        try:
            yield
        finally:
            _goal_var.reset(token)

    def _current_goal(self) -> str:
        goal = _goal_var.get() or self.default_goal
        if not goal:
            raise TripwireError(
                "No goal set. Pass default_goal=... or wrap the call in `with tw.goal('...'):`"
            )
        return goal

    # ----- plumbing ------------------------------------------------------

    def _payload(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "goal": self._current_goal(),
            "tool": tool,
            "arguments": arguments,
            "agent_id": self.agent_id,
        }

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    def _ahttp(self) -> httpx.AsyncClient:
        if self._aclient is None:
            self._aclient = httpx.AsyncClient(timeout=self._timeout)
        return self._aclient

    @staticmethod
    def _unavailable(exc: Exception) -> TripwireUnavailable:
        return TripwireUnavailable(f"Tripwire unreachable or errored: {exc}")

    @staticmethod
    def _needs_approval(tool: str, result: dict[str, Any]) -> int | None:
        """Raise if blocked; return the action id if a human must decide; None if allowed."""
        status = result["status"]
        if status == "allowed":
            return None
        if status == "blocked":
            raise ActionBlocked(tool, result.get("reason", ""), result.get("action_id"))
        return result["action_id"]  # pending

    @staticmethod
    def _resolve(tool: str, action: dict[str, Any], waited: float) -> bool:
        """True if approved, raises if rejected, False if still pending."""
        status = action["status"]
        if status == "approved":
            return True
        if status == "rejected":
            raise ActionRejected(tool, action.get("reviewer_note"), action["id"])
        return False

    # ----- sync API ------------------------------------------------------

    def check(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            resp = self._http().post(f"{self.base_url}/check", json=self._payload(tool, arguments or {}))
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise self._unavailable(exc) from exc

    def _wait_for_approval(self, tool: str, action_id: int) -> None:
        start = time.monotonic()
        while True:
            try:
                resp = self._http().get(f"{self.base_url}/actions/{action_id}")
                resp.raise_for_status()
                action = resp.json()
            except httpx.HTTPError as exc:
                raise self._unavailable(exc) from exc
            if self._resolve(tool, action, time.monotonic() - start):
                return
            waited = time.monotonic() - start
            if waited >= self.approval_timeout:
                raise ApprovalTimeout(tool, action_id, waited)
            self._sleep(self.poll_interval)

    def _gate(self, tool: str, arguments: dict[str, Any]) -> None:
        result = self.check(tool, arguments)
        action_id = self._needs_approval(tool, result)
        if action_id is not None:
            self._wait_for_approval(tool, action_id)

    # ----- async API -----------------------------------------------------

    async def acheck(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            resp = await self._ahttp().post(
                f"{self.base_url}/check", json=self._payload(tool, arguments or {})
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as exc:
            raise self._unavailable(exc) from exc

    async def _await_approval(self, tool: str, action_id: int) -> None:
        start = time.monotonic()
        while True:
            try:
                resp = await self._ahttp().get(f"{self.base_url}/actions/{action_id}")
                resp.raise_for_status()
                action = resp.json()
            except httpx.HTTPError as exc:
                raise self._unavailable(exc) from exc
            if self._resolve(tool, action, time.monotonic() - start):
                return
            waited = time.monotonic() - start
            if waited >= self.approval_timeout:
                raise ApprovalTimeout(tool, action_id, waited)
            await self._asleep(self.poll_interval)

    async def _agate(self, tool: str, arguments: dict[str, Any]) -> None:
        result = await self.acheck(tool, arguments)
        action_id = self._needs_approval(tool, result)
        if action_id is not None:
            await self._await_approval(tool, action_id)

    # ----- decorator -----------------------------------------------------

    def guard(self, fn: Callable | None = None, *, tool: str | None = None):
        """Decorator. Works on sync and async functions, with or without arguments:

            @tw.guard
            def delete_file(path): ...

            @tw.guard(tool="db.run_sql")
            async def run_sql(query): ...
        """

        def decorator(func: Callable) -> Callable:
            name = tool or func.__name__
            sig = inspect.signature(func)

            def arguments_of(args: tuple, kwargs: dict) -> dict[str, Any]:
                bound = sig.bind(*args, **kwargs)
                bound.apply_defaults()
                return _jsonable(dict(bound.arguments))

            if inspect.iscoroutinefunction(func):

                @functools.wraps(func)
                async def async_wrapper(*args, **kwargs):
                    await self._agate(name, arguments_of(args, kwargs))
                    return await func(*args, **kwargs)

                return async_wrapper

            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                self._gate(name, arguments_of(args, kwargs))
                return func(*args, **kwargs)

            return wrapper

        return decorator(fn) if fn is not None else decorator