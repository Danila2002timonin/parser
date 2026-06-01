"""Репозиторий таблицы parse_metrics."""

from __future__ import annotations

import logging

from sqlalchemy import insert

from app.db.session import get_session
from app.models import ParseMetric

logger = logging.getLogger(__name__)


def log_parse_metric(
    tender_id: str,
    stage: str,
    duration_ms: int,
    doc_id: str | None = None,
    input_size_bytes: int | None = None,
    output_size_bytes: int | None = None,
    items_count: int | None = None,
    status: str = "success",
    error_message: str | None = None,
) -> None:
    """Логирует метрику скорости парсинга (ошибки записи не пробрасываются)."""
    try:
        with get_session() as session:
            session.execute(
                insert(ParseMetric).values(
                    tender_id=tender_id,
                    doc_id=doc_id,
                    stage=stage,
                    duration_ms=duration_ms,
                    input_size_bytes=input_size_bytes,
                    output_size_bytes=output_size_bytes,
                    items_count=items_count,
                    status=status,
                    error_message=error_message,
                )
            )
    except Exception as exc:
        logger.warning("Не удалось записать parse_metric: %s", exc)
