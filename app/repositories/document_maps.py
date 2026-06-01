"""Репозиторий таблицы document_maps."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.db.session import get_session
from app.models import DocumentMap

from ._utils import mapping_to_dict


def upsert_document_map(
    tender_id: str,
    map_data: dict,
    routing_text: str,
    passports_count: int,
    estimated_tokens: int,
) -> None:
    """Сохраняет карту документации тендера."""
    base = insert(DocumentMap).values(
        tender_id=tender_id,
        map_data=map_data,
        routing_text=routing_text,
        passports_count=passports_count,
        estimated_tokens=estimated_tokens,
    )
    stmt = base.on_conflict_do_update(
        index_elements=[DocumentMap.tender_id],
        set_={
            "map_data": base.excluded.map_data,
            "routing_text": base.excluded.routing_text,
            "passports_count": base.excluded.passports_count,
            "estimated_tokens": base.excluded.estimated_tokens,
            "generated_at": func.now(),
        },
    )
    with get_session() as session:
        session.execute(stmt)


def get_document_map(tender_id: str) -> dict | None:
    """Возвращает карту документации тендера."""
    columns = (
        DocumentMap.tender_id, DocumentMap.map_data, DocumentMap.routing_text,
        DocumentMap.passports_count, DocumentMap.estimated_tokens,
        DocumentMap.generated_at,
    )
    with get_session() as session:
        row = session.execute(
            select(*columns).where(DocumentMap.tender_id == tender_id)
        ).mappings().first()
        return mapping_to_dict(row)
