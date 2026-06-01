"""ORM-модель таблицы api_usage."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Index, Integer, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import BIGINT, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ApiUsage(Base):
    """Трекинг стоимости, скорости и ошибок всех внешних API-вызовов."""

    __tablename__ = "api_usage"

    id: Mapped[int] = mapped_column(BIGINT, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    service: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    tender_id: Mapped[str | None] = mapped_column(Text)
    doc_id: Mapped[str | None] = mapped_column(Text)

    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)

    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)

    ocr_pages_count: Mapped[int | None] = mapped_column(Integer)
    ocr_doc_size_bytes: Mapped[int | None] = mapped_column(Integer)

    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="success")
    error_message: Mapped[str | None] = mapped_column(Text)
    http_status_code: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint(
            "status IN ('success', 'error', 'timeout', 'rate_limited')",
            name="api_usage_status_check",
        ),
        Index("idx_api_usage_timestamp", "timestamp"),
        Index("idx_api_usage_service_action", "service", "action"),
        Index("idx_api_usage_tender", "tender_id"),
        Index("idx_api_usage_provider_model", "provider", "model"),
        Index(
            "idx_api_usage_errors",
            "status",
            postgresql_where=text("status != 'success'"),
        ),
    )
