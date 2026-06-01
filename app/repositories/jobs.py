"""Репозиторий таблицы pipeline_jobs."""

from __future__ import annotations

from sqlalchemy import func, insert, select, update

from app.db.session import get_session
from app.models import PipelineJob

from ._utils import mapping_to_dict
from .tenders import get_tender, upsert_tender


def create_pipeline_job(tender_id: str, priority: int = 0) -> int:
    """Создаёт job предобработки и возвращает его ID."""
    if get_tender(tender_id) is None:
        upsert_tender(tender_id, status="created")
    with get_session() as session:
        job_id = session.execute(
            insert(PipelineJob)
            .values(tender_id=tender_id, priority=priority, status="queued")
            .returning(PipelineJob.id)
        ).scalar_one()
        return int(job_id)


def mark_job_running(job_id: int, worker_id: str | None = None) -> None:
    """Помечает job как выполняемый."""
    with get_session() as session:
        session.execute(
            update(PipelineJob)
            .where(PipelineJob.id == job_id)
            .values(status="running", worker_id=worker_id, started_at=func.now())
        )


def mark_job_completed(job_id: int) -> None:
    """Помечает job как завершённый."""
    with get_session() as session:
        session.execute(
            update(PipelineJob)
            .where(PipelineJob.id == job_id)
            .values(status="completed", completed_at=func.now())
        )


def mark_job_failed(job_id: int, error_message: str) -> None:
    """Помечает job как упавший."""
    with get_session() as session:
        session.execute(
            update(PipelineJob)
            .where(PipelineJob.id == job_id)
            .values(status="failed", completed_at=func.now(), error_message=error_message[:2000])
        )


def get_latest_job(tender_id: str) -> dict | None:
    """Возвращает последнюю job по тендеру."""
    columns = (
        PipelineJob.id, PipelineJob.tender_id, PipelineJob.priority,
        PipelineJob.status, PipelineJob.worker_id, PipelineJob.queued_at,
        PipelineJob.started_at, PipelineJob.completed_at, PipelineJob.error_message,
        PipelineJob.retry_count, PipelineJob.max_retries,
    )
    with get_session() as session:
        row = session.execute(
            select(*columns)
            .where(PipelineJob.tender_id == tender_id)
            .order_by(PipelineJob.queued_at.desc(), PipelineJob.id.desc())
            .limit(1)
        ).mappings().first()
        return mapping_to_dict(row)
