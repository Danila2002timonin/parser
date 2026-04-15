"""Pydantic-модели данных для системы обработки тендерной документации."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class TenderStatus(str, Enum):
    """Статус обработки тендера в pipeline."""

    CREATED = "created"
    DOWNLOADED = "downloaded"
    EXTRACTED = "extracted"
    PARSED = "parsed"
    INDEXED = "indexed"


class DocumentRecord(BaseModel):
    """Запись об одном документе, извлечённом из архива тендера."""

    doc_id: str = Field(description="Уникальный ID документа (doc_001, doc_002, ...)")
    original_filename: str = Field(description="Оригинальное имя файла из архива")
    stored_filename: str = Field(description="Нормализованное имя в documents/")
    extension: str = Field(description="Расширение файла (.pdf, .docx, ...)")
    size_bytes: int = Field(description="Размер файла в байтах")
    source_archive: str = Field(description="Имя архива-источника")
    archive_path: str = Field(
        default="",
        description="Полный путь внутри архива (с учётом вложенных папок)",
    )
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PipelineState(BaseModel):
    """Временные метки каждого этапа обработки."""

    downloaded: datetime | None = None
    extracted: datetime | None = None
    parsed: datetime | None = None
    passports_generated: datetime | None = None
    indexed: datetime | None = None


class TenderManifest(BaseModel):
    """Манифест тендера — центральный реестр с метаданными и списком документов."""

    tender_id: str
    status: TenderStatus = TenderStatus.CREATED
    source_url: str = ""
    pipeline_state: PipelineState = Field(default_factory=PipelineState)
    documents: list[DocumentRecord] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def save(self, path: Path) -> None:
        """Сохраняет манифест в JSON-файл."""
        self.updated_at = datetime.now(timezone.utc)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> TenderManifest:
        """Загружает манифест из JSON-файла."""
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class GlobalIndex(BaseModel):
    """Глобальный индекс всех загруженных тендеров."""

    tenders: dict[str, TenderIndexEntry] = Field(default_factory=dict)

    def save(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> GlobalIndex:
        if not path.exists():
            return cls()
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class TenderIndexEntry(BaseModel):
    """Краткая запись о тендере в глобальном индексе."""

    status: TenderStatus = TenderStatus.CREATED
    downloaded_at: datetime | None = None
    document_count: int = 0


# Нужно обновить forward reference после определения TenderIndexEntry
GlobalIndex.model_rebuild()
