"""Репозиторий таблицы api_usage."""

from __future__ import annotations

import logging

from sqlalchemy import insert

from app.db.session import get_session
from app.models import ApiUsage

logger = logging.getLogger(__name__)


def log_api_usage(
    service: str,
    action: str,
    provider: str,
    model: str,
    tender_id: str | None = None,
    doc_id: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    ocr_pages_count: int | None = None,
    ocr_doc_size_bytes: int | None = None,
    cost_usd: float | None = None,
    duration_ms: int | None = None,
    status: str = "success",
    error_message: str | None = None,
    http_status_code: int | None = None,
) -> None:
    """Логирует один внешний API-вызов (ошибки записи не пробрасываются)."""
    try:
        with get_session() as session:
            session.execute(
                insert(ApiUsage).values(
                    service=service,
                    action=action,
                    provider=provider,
                    model=model,
                    tender_id=tender_id,
                    doc_id=doc_id,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    ocr_pages_count=ocr_pages_count,
                    ocr_doc_size_bytes=ocr_doc_size_bytes,
                    cost_usd=cost_usd,
                    duration_ms=duration_ms,
                    status=status,
                    error_message=error_message,
                    http_status_code=http_status_code,
                )
            )
    except Exception as exc:
        logger.warning("Не удалось записать api_usage: %s", exc)
