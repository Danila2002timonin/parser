"""ORM-модель таблицы parse_metrics."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Index, Integer, Text, func
from sqlalchemy.dialects.postgresql import BIGINT, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ParseMetric(Base):
    """Метрики скорости парсинга по этапам pipeline."""

    __tablename__ = "parse_metrics"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    tender_id: Mapped[str] = mapped_column(Text, nullable=False)
    doc_id: Mapped[str | None] = mapped_column(Text)

    stage: Mapped[str] = mapped_column(Text, nullable=False)

    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    input_size_bytes: Mapped[int | None] = mapped_column(Integer)
    output_size_bytes: Mapped[int | None] = mapped_column(Integer)
    items_count: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="success")
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(
            "stage IN ('download', 'extract', 'convert', "
            "'parse_docx', 'parse_pdf', 'parse_xlsx', "
            "'ocr', 'passport', 'index', 'full_pipeline')",
            name="parse_metrics_stage_check",
        ),
        CheckConstraint(
            "status IN ('success', 'error')",
            name="parse_metrics_status_check",
        ),
        Index("idx_parse_metrics_tender", "tender_id"),
        Index("idx_parse_metrics_stage", "stage"),
        Index("idx_parse_metrics_timestamp", "timestamp"),
    )
