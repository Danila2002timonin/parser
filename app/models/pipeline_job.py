"""ORM-модель таблицы pipeline_jobs."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import BIGINT, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PipelineJob(Base):
    """Очередь задач предобработки (PostgreSQL-based, без Redis/Celery)."""

    __tablename__ = "pipeline_jobs"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    tender_id: Mapped[str] = mapped_column(
        Text, ForeignKey("tenders.tender_id"), nullable=False
    )

    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="queued")
    worker_id: Mapped[str | None] = mapped_column(Text)

    queued_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int | None] = mapped_column(Integer, server_default="0")
    max_retries: Mapped[int | None] = mapped_column(Integer, server_default="3")

    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="pipeline_jobs_status_check",
        ),
        Index(
            "idx_jobs_queue",
            "status",
            text("priority DESC"),
            "queued_at",
            postgresql_where=text("status = 'queued'"),
        ),
        Index("idx_jobs_tender", "tender_id"),
        Index(
            "idx_jobs_running",
            "worker_id",
            postgresql_where=text("status = 'running'"),
        ),
    )
