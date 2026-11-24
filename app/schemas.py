from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

Verdict = Literal["allow", "ask_human", "block"]
JevDecision = Literal["allow", "ask_human", "block", "unclear"]
ActionStatus = Literal["allowed", "blocked", "pending", "approved", "rejected"]


class JevResult(BaseModel):
    """Typed answers from one Jev call."""

    is_destructive: float = Field(ge=0, le=1)
    off_task: float = Field(ge=0, le=1)
    decision: JevDecision
    confidence: float = Field(ge=0, le=1)
    source: str = "jev"  # "jev" or "mock"


class CheckRequest(BaseModel):
    goal: str = Field(min_length=1, description="What the user originally asked the agent to do")
    tool: str = Field(min_length=1, description="Name of the tool the agent wants to call")
    arguments: dict[str, Any] = Field(default_factory=dict)
    agent_id: str | None = None


class CheckResponse(BaseModel):
    action_id: int
    verdict: Verdict
    status: ActionStatus
    reason: str
    decided_by: str
    jev: JevResult | None = None


class ApprovalRequest(BaseModel):
    approve: bool
    note: str | None = None


class ActionOut(BaseModel):
    id: int
    created_at: datetime
    agent_id: str | None
    goal: str
    tool: str
    arguments: dict[str, Any]
    verdict: Verdict
    status: ActionStatus
    reason: str
    decided_by: str
    jev_decision: str | None
    jev_confidence: float | None
    is_destructive: float | None
    off_task: float | None
    jev_source: str | None
    reviewer_note: str | None
    resolved_at: datetime | None

    model_config = {"from_attributes": True}
