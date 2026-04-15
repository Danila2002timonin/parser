"""Унифицированная JSON-схема для хранения распарсенных документов.

Каждый документ представляется как упорядоченный список блоков (TextBlock,
TableBlock, ImageBlock), сохраняющих порядок элементов в оригинале.

Таблицы хранятся как массив типизированных строк (header / section / data /
subtotal), где каждая ячейка — либо строка, либо объект с colspan/rowspan.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Table cells & rows
# ---------------------------------------------------------------------------

class CellObject(BaseModel):
    """Ячейка таблицы с метаданными объединения."""

    value: str = ""
    colspan: int = Field(default=1, ge=1, description="Горизонтальное объединение")
    rowspan: int = Field(default=1, ge=1, description="Вертикальное объединение")


# Ячейка: простая строка ИЛИ объект с colspan/rowspan
TableCell = Union[str, CellObject]


class TableRow(BaseModel):
    """Строка таблицы с семантическим типом."""

    row_type: Literal["header", "section", "data", "subtotal"] = "data"
    cells: list[TableCell] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Content blocks
# ---------------------------------------------------------------------------

class TextBlock(BaseModel):
    """Текстовый блок документа."""

    block_id: str
    type: Literal["text"] = "text"
    page: int | None = None
    content: str = ""
    heading_level: int | None = Field(
        default=None,
        ge=1,
        le=6,
        description="Уровень заголовка (1-6) или None для обычного текста",
    )
    section_path: str | None = Field(
        default=None,
        description="Номер раздела: '1', '1.1', '2.3.1' и т.д.",
    )


class TableBlock(BaseModel):
    """Табличный блок документа с поддержкой многоуровневых заголовков."""

    block_id: str
    type: Literal["table"] = "table"
    page: int | None = None
    page_end: int | None = Field(
        default=None,
        description="Последняя страница (для многостраничных таблиц)",
    )
    caption: str | None = None
    col_count: int = 0
    rows: list[TableRow] = Field(default_factory=list)
    section_path: str | None = None
    xlsx_ref: str | None = Field(
        default=None,
        description="Относительный путь к материализованному xlsx",
    )

    @property
    def header_rows(self) -> list[TableRow]:
        return [r for r in self.rows if r.row_type == "header"]

    @property
    def data_rows(self) -> list[TableRow]:
        return [r for r in self.rows if r.row_type == "data"]

    @property
    def row_count(self) -> int:
        return len(self.rows)


class ImageBlock(BaseModel):
    """Блок изображения (извлечённого или целой страницы-скана)."""

    block_id: str
    type: Literal["image"] = "image"
    page: int | None = None
    image_ref: str | None = Field(
        default=None,
        description="Относительный путь к файлу изображения",
    )
    width_px: int | None = None
    height_px: int | None = None
    caption: str | None = None
    ocr_text: str | None = Field(
        default=None,
        description="Распознанный текст (заполняется OCR/VLM позже)",
    )
    ocr_status: Literal["pending", "completed", "skipped", "not_needed"] = "pending"
    section_path: str | None = None


# Union-тип для всех блоков
ContentBlock = Annotated[
    Union[TextBlock, TableBlock, ImageBlock],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Parsed document
# ---------------------------------------------------------------------------

class ParsedDocument(BaseModel):
    """Полностью распарсенный документ в унифицированной схеме."""

    doc_id: str
    source_filename: str = ""
    source_format: str = Field(
        default="",
        description="Формат оригинала: pdf, docx, xlsx, ...",
    )
    parsed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    parser_version: str = "0.1.0"
    total_pages: int | None = None
    blocks: list[ContentBlock] = Field(default_factory=list)

    def save(self, path: Path) -> None:
        """Сохраняет распарсенный документ в JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> ParsedDocument:
        """Загружает из JSON."""
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    @property
    def text_blocks(self) -> list[TextBlock]:
        return [b for b in self.blocks if b.type == "text"]

    @property
    def table_blocks(self) -> list[TableBlock]:
        return [b for b in self.blocks if b.type == "table"]

    @property
    def image_blocks(self) -> list[ImageBlock]:
        return [b for b in self.blocks if b.type == "image"]
