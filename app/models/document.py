"""ORM-модель таблицы documents."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Document(Base):
    """Реестр документов в тендере."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[str] = mapped_column(
        Text, ForeignKey("tenders.tender_id", ondelete="CASCADE"), nullable=False
    )
    doc_id: Mapped[str] = mapped_column(Text, nullable=False)

    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    stored_filename: Mapped[str] = mapped_column(Text, nullable=False)
    extension: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    source_archive: Mapped[str | None] = mapped_column(Text)
    archive_path: Mapped[str | None] = mapped_column(Text)

    parse_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    ocr_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="not_needed")

    raw_file_s3_path: Mapped[str | None] = mapped_column(Text)
    parsed_json_s3_path: Mapped[str | None] = mapped_column(Text)

    text_blocks_count: Mapped[int | None] = mapped_column(Integer)
    tables_count: Mapped[int | None] = mapped_column(Integer)
    images_count: Mapped[int | None] = mapped_column(Integer)
    total_pages: Mapped[int | None] = mapped_column(Integer)
    estimated_tokens: Mapped[int | None] = mapped_column(Integer)

    extracted_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    parsed_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    parse_duration_ms: Mapped[int | None] = mapped_column(Integer)
    conversion_duration_ms: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (
        UniqueConstraint("tender_id", "doc_id", name="documents_tender_id_doc_id_key"),
        CheckConstraint(
            "parse_status IN ('pending', 'parsed', 'failed', 'unsupported')",
            name="documents_parse_status_check",
        ),
        CheckConstraint(
            "ocr_status IN ('not_needed', 'pending', 'completed', 'failed')",
            name="documents_ocr_status_check",
        ),
        Index("idx_documents_tender", "tender_id"),
        Index("idx_documents_parse_status", "parse_status"),
        Index("idx_documents_extension", "extension"),
    )
