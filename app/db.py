from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.pool import StaticPool


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class ActionRecord(Base):
    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    goal: Mapped[str] = mapped_column(Text)
    tool: Mapped[str] = mapped_column(String(256), index=True)
    arguments: Mapped[dict] = mapped_column(JSON, default=dict)

    verdict: Mapped[str] = mapped_column(String(16))   # allow | ask_human | block
    status: Mapped[str] = mapped_column(String(16), index=True)  # allowed | blocked | pending | approved | rejected
    reason: Mapped[str] = mapped_column(Text)
    decided_by: Mapped[str] = mapped_column(String(16))  # jev | claude | policy | none | human

    jev_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    jev_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_destructive: Mapped[float | None] = mapped_column(Float, nullable=True)
    off_task: Mapped[float | None] = mapped_column(Float, nullable=True)
    jev_source: Mapped[str | None] = mapped_column(String(16), nullable=True)

    reviewer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def make_session_factory(database_url: str) -> sessionmaker:
    kwargs: dict = {}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url or database_url in ("sqlite://", "sqlite:///"):
            kwargs["poolclass"] = StaticPool
    engine = create_engine(database_url, **kwargs)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
