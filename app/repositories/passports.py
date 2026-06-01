"""Репозиторий таблицы passports."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.db.session import get_session
from app.models import Passport


def upsert_passport(
    tender_id: str,
    doc_id: str,
    passport_data: dict,
    model_used: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    generation_cost_usd: float | None = None,
    generation_duration_ms: int | None = None,
) -> None:
    """Сохраняет паспорт документа."""
    base = insert(Passport).values(
        tender_id=tender_id,
        doc_id=doc_id,
        doc_type=passport_data.get("doc_type"),
        title=passport_data.get("title"),
        summary=passport_data.get("summary"),
        passport_data=passport_data,
        model_used=model_used,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        generation_cost_usd=generation_cost_usd,
        generation_duration_ms=generation_duration_ms,
    )
    stmt = base.on_conflict_do_update(
        index_elements=[Passport.tender_id, Passport.doc_id],
        set_={
            "doc_type": base.excluded.doc_type,
            "title": base.excluded.title,
            "summary": base.excluded.summary,
            "passport_data": base.excluded.passport_data,
            "model_used": base.excluded.model_used,
            "prompt_tokens": base.excluded.prompt_tokens,
            "completion_tokens": base.excluded.completion_tokens,
            "generation_cost_usd": base.excluded.generation_cost_usd,
            "generation_duration_ms": base.excluded.generation_duration_ms,
            "generated_at": func.now(),
        },
    )
    with get_session() as session:
        session.execute(stmt)


def list_passports(tender_id: str) -> list[dict]:
    """Возвращает паспорта тендера."""
    columns = (
        Passport.tender_id, Passport.doc_id, Passport.doc_type, Passport.title,
        Passport.summary, Passport.passport_data, Passport.model_used,
        Passport.prompt_tokens, Passport.completion_tokens,
        Passport.generation_cost_usd, Passport.generation_duration_ms,
        Passport.generated_at,
    )
    with get_session() as session:
        rows = session.execute(
            select(*columns).where(Passport.tender_id == tender_id).order_by(Passport.doc_id)
        ).mappings().all()
        return [dict(row) for row in rows]
