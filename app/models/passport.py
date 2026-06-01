"""ORM-модель таблицы passports."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Passport(Base):
    """Паспорта документов (сгенерированные LLM)."""

    __tablename__ = "passports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tender_id: Mapped[str] = mapped_column(Text, nullable=False)
    doc_id: Mapped[str] = mapped_column(Text, nullable=False)

    doc_type: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)

    passport_data: Mapped[dict] = mapped_column(JSONB, nullable=False)

    model_used: Mapped[str | None] = mapped_column(Text)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    generation_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    generation_duration_ms: Mapped[int | None] = mapped_column(Integer)
    generated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tender_id", "doc_id", name="passports_tender_id_doc_id_key"),
        ForeignKeyConstraint(
            ["tender_id", "doc_id"],
            ["documents.tender_id", "documents.doc_id"],
            ondelete="CASCADE",
            name="passports_tender_id_doc_id_fkey",
        ),
        Index("idx_passports_tender", "tender_id"),
        Index("idx_passports_doc_type", "doc_type"),
        Index("idx_passports_gin", "passport_data", postgresql_using="gin"),
    )
