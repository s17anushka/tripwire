from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import Settings, load_settings
from .db import ActionRecord, make_session_factory, utcnow
from .gate import evaluate
from .jev import JevClient, make_jev_client
from .reviewer import Reviewer, make_reviewer
from .schemas import (
    ActionOut,
    ApprovalRequest,
    CheckRequest,
    CheckResponse,
)

_STATUS_FOR_VERDICT = {"allow": "allowed", "block": "blocked", "ask_human": "pending"}
_DASHBOARD = Path(__file__).parent / "static" / "dashboard.html"


def create_app(
    settings: Settings | None = None,
    jev: JevClient | None = None,
    reviewer: Reviewer | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    jev = jev or make_jev_client(settings)
    reviewer = reviewer or make_reviewer(settings)
    SessionLocal = make_session_factory(settings.database_url)

    app = FastAPI(
        title="Tripwire",
        version="0.1.0",
        description="A safety and approval gate between AI agents and their tools, powered by Jev.",
    )

    def get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    @app.get("/", include_in_schema=False)
    def root():
        return RedirectResponse("/dashboard")

    @app.get("/dashboard", include_in_schema=False, response_class=HTMLResponse)
    def dashboard():
        return HTMLResponse(_DASHBOARD.read_text(encoding="utf-8"))

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "jev": "live" if settings.jev_configured else "mock",
            "reviewer": "claude" if settings.anthropic_api_key else "none",
        }

    @app.post("/check", response_model=CheckResponse)
    async def check(req: CheckRequest, db: Session = Depends(get_db)):
        outcome = await evaluate(
            req.goal, req.tool, req.arguments, jev=jev, reviewer=reviewer, settings=settings
        )
        j = outcome.jev
        record = ActionRecord(
            agent_id=req.agent_id,
            goal=req.goal,
            tool=req.tool,
            arguments=req.arguments,
            verdict=outcome.verdict,
            status=_STATUS_FOR_VERDICT[outcome.verdict],
            reason=outcome.reason,
            decided_by=outcome.decided_by,
            jev_decision=j.decision if j else None,
            jev_confidence=j.confidence if j else None,
            is_destructive=j.is_destructive if j else None,
            off_task=j.off_task if j else None,
            jev_source=j.source if j else None,
        )
        db.add(record)
        db.commit()
        return CheckResponse(
            action_id=record.id,
            verdict=outcome.verdict,
            status=record.status,
            reason=outcome.reason,
            decided_by=outcome.decided_by,
            jev=j,
        )

    @app.get("/actions", response_model=list[ActionOut])
    def list_actions(
        status: str | None = Query(None, pattern="^(allowed|blocked|pending|approved|rejected)$"),
        limit: int = Query(50, ge=1, le=500),
        db: Session = Depends(get_db),
    ):
        stmt = select(ActionRecord).order_by(ActionRecord.id.desc()).limit(limit)
        if status:
            stmt = stmt.where(ActionRecord.status == status)
        return db.scalars(stmt).all()

    @app.get("/actions/{action_id}", response_model=ActionOut)
    def get_action(action_id: int, db: Session = Depends(get_db)):
        record = db.get(ActionRecord, action_id)
        if record is None:
            raise HTTPException(404, "Action not found")
        return record

    @app.post("/approvals/{action_id}", response_model=ActionOut)
    def resolve(action_id: int, body: ApprovalRequest, db: Session = Depends(get_db)):
        record = db.get(ActionRecord, action_id)
        if record is None:
            raise HTTPException(404, "Action not found")
        if record.status != "pending":
            raise HTTPException(409, f"Action is already {record.status}")
        record.status = "approved" if body.approve else "rejected"
        record.decided_by = "human"
        record.reviewer_note = body.note
        record.resolved_at = utcnow()
        db.commit()
        return record

    return app


app = create_app()