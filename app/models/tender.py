"""ORM-модель таблицы tenders."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import BIGINT, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_TENDER_STATUSES = (
    "created", "downloaded", "extracted", "parsed",
    "ocr_done", "passports_done", "indexed", "failed",
)


class Tender(Base):
    """Реестр тендеров и статусы pipeline."""

    __tablename__ = "tenders"

    tender_id: Mapped[str] = mapped_column(Text, primary_key=True)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="created")
    source_url: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
    downloaded_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    extracted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    parsed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    ocr_done_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    passports_done_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    indexed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    document_count: Mapped[int | None] = mapped_column(Integer, server_default="0")
    total_size_bytes: Mapped[int | None] = mapped_column(BIGINT, server_default="0")

    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int | None] = mapped_column(Integer, server_default="0")

    archive_s3_path: Mapped[str | None] = mapped_column(Text)

    # Добавлено миграцией 002
    total_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    pipeline_duration_ms: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'downloaded', 'extracted', 'parsed', "
            "'ocr_done', 'passports_done', 'indexed', 'failed')",
            name="tenders_status_check",
        ),
        Index("idx_tenders_status", "status"),
        Index("idx_tenders_created", "created_at"),
    )
