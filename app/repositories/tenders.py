"""Репозиторий таблицы tenders."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.db.session import get_session
from app.models import ApiUsage, Tender

from ._utils import mapping_to_dict

_STATUS_TIMESTAMP = {
    "downloaded": "downloaded_at",
    "extracted": "extracted_at",
    "parsed": "parsed_at",
    "ocr_done": "ocr_done_at",
    "passports_done": "passports_done_at",
    "indexed": "indexed_at",
}


def upsert_tender(
    tender_id: str,
    status: str = "created",
    source_url: str | None = None,
    document_count: int | None = None,
    total_size_bytes: int | None = None,
    archive_s3_path: str | None = None,
    error_message: str | None = None,
    **timestamps,
) -> None:
    """Создаёт или обновляет запись тендера."""
    base = insert(Tender).values(
        tender_id=tender_id,
        status=status,
        source_url=source_url,
        document_count=document_count,
        total_size_bytes=total_size_bytes,
        archive_s3_path=archive_s3_path,
        error_message=error_message,
    )
    stmt = base.on_conflict_do_update(
        index_elements=[Tender.tender_id],
        set_={
            "status": func.coalesce(base.excluded.status, Tender.status),
            "source_url": func.coalesce(base.excluded.source_url, Tender.source_url),
            "document_count": func.coalesce(base.excluded.document_count, Tender.document_count),
            "total_size_bytes": func.coalesce(base.excluded.total_size_bytes, Tender.total_size_bytes),
            "archive_s3_path": func.coalesce(base.excluded.archive_s3_path, Tender.archive_s3_path),
            "error_message": base.excluded.error_message,
            "updated_at": func.now(),
        },
    )
    with get_session() as session:
        session.execute(stmt)


def finalize_tender(tender_id: str, pipeline_duration_ms: int) -> None:
    """Записывает финальную стоимость и длительность pipeline."""
    cost_subq = (
        select(func.coalesce(func.sum(ApiUsage.cost_usd), 0))
        .where(ApiUsage.tender_id == tender_id)
        .scalar_subquery()
    )
    with get_session() as session:
        session.execute(
            update(Tender)
            .where(Tender.tender_id == tender_id)
            .values(
                total_cost_usd=cost_subq,
                pipeline_duration_ms=pipeline_duration_ms,
                updated_at=func.now(),
            )
        )


def update_tender_status(tender_id: str, status: str) -> None:
    """Обновляет статус тендера и соответствующий timestamp."""
    values: dict = {"status": status, "updated_at": func.now()}
    timestamp_col = _STATUS_TIMESTAMP.get(status)
    if timestamp_col:
        values[timestamp_col] = func.now()
    with get_session() as session:
        session.execute(
            update(Tender).where(Tender.tender_id == tender_id).values(**values)
        )


def get_tender(tender_id: str) -> dict | None:
    """Возвращает запись тендера как dict."""
    with get_session() as session:
        row = session.execute(
            select(Tender.__table__).where(Tender.tender_id == tender_id)
        ).mappings().first()
        return mapping_to_dict(row)
