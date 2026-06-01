"""Репозиторий таблицы documents."""

from __future__ import annotations

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from app.db.session import get_session
from app.models import Document


def upsert_document(
    tender_id: str,
    doc_id: str,
    original_filename: str,
    stored_filename: str,
    extension: str,
    size_bytes: int,
    source_archive: str | None = None,
    archive_path: str | None = None,
) -> None:
    """Создаёт или обновляет запись документа."""
    base = insert(Document).values(
        tender_id=tender_id,
        doc_id=doc_id,
        original_filename=original_filename,
        stored_filename=stored_filename,
        extension=extension,
        size_bytes=size_bytes,
        source_archive=source_archive,
        archive_path=archive_path,
        extracted_at=func.now(),
    )
    stmt = base.on_conflict_do_update(
        index_elements=[Document.tender_id, Document.doc_id],
        set_={
            "original_filename": base.excluded.original_filename,
            "stored_filename": base.excluded.stored_filename,
            "extension": base.excluded.extension,
            "size_bytes": base.excluded.size_bytes,
            "source_archive": base.excluded.source_archive,
            "archive_path": base.excluded.archive_path,
            "extracted_at": func.now(),
        },
    )
    with get_session() as session:
        session.execute(stmt)


def update_document_parsed(
    tender_id: str,
    doc_id: str,
    text_blocks_count: int,
    tables_count: int,
    images_count: int,
    total_pages: int | None,
    estimated_tokens: int,
    parse_duration_ms: int,
    conversion_duration_ms: int | None = None,
) -> None:
    """Обновляет запись документа после парсинга."""
    with get_session() as session:
        session.execute(
            update(Document)
            .where(Document.tender_id == tender_id, Document.doc_id == doc_id)
            .values(
                parse_status="parsed",
                text_blocks_count=text_blocks_count,
                tables_count=tables_count,
                images_count=images_count,
                total_pages=total_pages,
                estimated_tokens=estimated_tokens,
                parse_duration_ms=parse_duration_ms,
                conversion_duration_ms=conversion_duration_ms,
                parsed_at=func.now(),
            )
        )


def update_document_parse_failed(
    tender_id: str, doc_id: str, status: str = "failed"
) -> None:
    """Помечает документ как failed/unsupported."""
    with get_session() as session:
        session.execute(
            update(Document)
            .where(Document.tender_id == tender_id, Document.doc_id == doc_id)
            .values(parse_status=status)
        )


def list_documents(tender_id: str) -> list[dict]:
    """Возвращает документы тендера."""
    columns = (
        Document.tender_id, Document.doc_id, Document.original_filename,
        Document.stored_filename, Document.extension, Document.size_bytes,
        Document.source_archive, Document.archive_path, Document.parse_status,
        Document.ocr_status, Document.text_blocks_count, Document.tables_count,
        Document.images_count, Document.total_pages, Document.estimated_tokens,
        Document.extracted_at, Document.parsed_at, Document.parse_duration_ms,
        Document.conversion_duration_ms,
    )
    with get_session() as session:
        rows = session.execute(
            select(*columns).where(Document.tender_id == tender_id).order_by(Document.doc_id)
        ).mappings().all()
        return [dict(row) for row in rows]
