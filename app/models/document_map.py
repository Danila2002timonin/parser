"""ORM-модель таблицы document_maps."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentMap(Base):
    """Агрегированная карта документации тендера."""

    __tablename__ = "document_maps"

    tender_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("tenders.tender_id", ondelete="CASCADE"),
        primary_key=True,
    )
    map_data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    routing_text: Mapped[str | None] = mapped_column(Text)
    passports_count: Mapped[int | None] = mapped_column(Integer)
    estimated_tokens: Mapped[int | None] = mapped_column(Integer)
    generated_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
