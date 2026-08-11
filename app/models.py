from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    prompt: Mapped[str] = mapped_column(Text)
    rubric: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    submissions: Mapped[list["Submission"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class Submission(Base):
    __tablename__ = "submissions"
    # Один и тот же ключ в пределах задачи — это повтор, а не новая попытка.
    # Ретрай агента по таймауту не должен портить pass rate.
    __table_args__ = (UniqueConstraint("task_id", "idempotency_key", name="uq_submission_idem"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    agent: Mapped[str] = mapped_column(String(120), index=True)
    output: Mapped[str] = mapped_column(Text)
    latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)

    passed: Mapped[bool] = mapped_column(Boolean)
    score: Mapped[float] = mapped_column(Float)
    checks: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    task: Mapped[Task] = relationship(back_populates="submissions")
