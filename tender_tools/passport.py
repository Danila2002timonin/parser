"""Генерация паспортов документов и карты документации через LLM.

Паспорт — компактное описание документа, по которому роутер-модель
может направить вопрос к нужному файлу/разделу без чтения оригинала.

Стратегия для больших документов:
1. Если документ помещается в контекст (~20K токенов) → single-pass
2. Если не помещается → chunk по секциям, суммаризировать каждую,
   затем собрать финальный паспорт из суммарий
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from .llm_client import LLMClient, LLMError
from .parsers.schema import ParsedDocument, TableBlock, TextBlock

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pydantic-схемы паспорта
# ---------------------------------------------------------------------------

class SectionSummary(BaseModel):
    """Описание одного раздела документа."""

    section_id: str = Field(description="Номер раздела или короткий ID")
    title: str = Field(description="Заголовок раздела")
    summary: str = Field(description="Краткое описание содержимого (1-2 предложения)")
    key_entities: list[str] = Field(
        default_factory=list,
        description="Ключевые сущности: организации, даты, суммы, ГОСТы и т.д.",
    )


class DocumentPassport(BaseModel):
    """Паспорт документа — компактное описание для роутинга."""

    doc_id: str
    source_filename: str = ""
    doc_type: str = Field(
        default="",
        description="Тип документа: technical_specification, contract, "
                    "price_justification, application_form, qualification, "
                    "notification, attachment, other",
    )
    title: str = Field(default="", description="Полное название документа")
    summary: str = Field(default="", description="Краткое описание (2-4 предложения)")
    sections: list[SectionSummary] = Field(default_factory=list)
    key_topics: list[str] = Field(
        default_factory=list,
        description="Основные темы документа",
    )
    key_entities: list[str] = Field(
        default_factory=list,
        description="Главные сущности из документа",
    )
    text_blocks_count: int = 0
    tables_count: int = 0
    images_count: int = 0
    estimated_tokens: int = 0
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> DocumentPassport:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


class DocumentMap(BaseModel):
    """Карта документации тендера — агрегат всех паспортов."""

    tender_id: str
    passports: list[DocumentPassport] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> DocumentMap:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def to_routing_text(self) -> str:
        """Генерирует компактное текстовое представление для роутер-модели."""
        lines = [f"# Карта документации тендера {self.tender_id}\n"]
        for p in self.passports:
            lines.append(f"## [{p.doc_id}] {p.title}")
            lines.append(f"Файл: {p.source_filename}")
            lines.append(f"Тип: {p.doc_type}")
            lines.append(f"Описание: {p.summary}")
            if p.key_topics:
                lines.append(f"Темы: {', '.join(p.key_topics)}")
            if p.sections:
                lines.append("Разделы:")
                for s in p.sections:
                    entities_str = f" [{', '.join(s.key_entities)}]" if s.key_entities else ""
                    lines.append(f"  - {s.section_id}. {s.title}: {s.summary}{entities_str}")
            lines.append("")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Structured output schemas (для response_format enforcement)
# ---------------------------------------------------------------------------

class _SectionOut(BaseModel):
    """Structured output: раздел документа."""
    section_id: str
    title: str
    summary: str
    key_entities: list[str]


class _PassportOut(BaseModel):
    """Structured output: полный паспорт документа."""
    doc_type: str
    title: str
    summary: str
    sections: list[_SectionOut]
    key_topics: list[str]
    key_entities: list[str]


class _ChunkSummaryOut(BaseModel):
    """Structured output: суммарий фрагмента."""
    sections: list[_SectionOut]
    key_topics: list[str]
    key_entities: list[str]


# ---------------------------------------------------------------------------
# Промпты (упрощены — формат теперь обеспечивается schema enforcement)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """Ты — аналитик тендерной документации. Создай структурированный паспорт документа.
Паспорт должен быть кратким, но информативным. Он используется для маршрутизации вопросов к нужным документам.
section_id должен быть арабскими цифрами (1, 2, 3.1...), не римскими.
doc_type — одно из: technical_specification, contract, price_justification, application_form, qualification, notification, attachment, other."""

_SINGLE_PASS_PROMPT = """Проанализируй документ из тендерной документации и создай его паспорт.

Документ: "{filename}"

---СОДЕРЖИМОЕ ДОКУМЕНТА---
{content}
---КОНЕЦ ДОКУМЕНТА---"""

_CHUNK_SUMMARY_PROMPT = """Проанализируй фрагмент документа "{filename}" и кратко опиши его содержимое.

---ФРАГМЕНТ---
{content}
---КОНЕЦ ФРАГМЕНТА---"""

_MERGE_PROMPT = """На основе суммарий отдельных фрагментов документа "{filename}" создай общий паспорт документа.

---СУММАРИИ ФРАГМЕНТОВ---
{chunk_summaries}
---КОНЕЦ СУММАРИЙ---"""


# ---------------------------------------------------------------------------
# Генератор паспортов
# ---------------------------------------------------------------------------

def _doc_to_text(parsed: ParsedDocument) -> str:
    """Конвертирует ParsedDocument в плоский текст для LLM."""
    from .parsers.schema import ImageBlock as _ImageBlock

    parts: list[str] = []
    for block in parsed.blocks:
        if isinstance(block, TextBlock):
            if block.heading_level:
                parts.append(f"{'#' * block.heading_level} {block.content}")
            else:
                parts.append(block.content)
        elif isinstance(block, TableBlock):
            parts.append(f"[Таблица: {block.col_count} колонок, {block.row_count} строк]")
            for row in block.rows[:3]:
                cells = []
                for c in row.cells:
                    val = c.value if hasattr(c, "value") else c
                    cells.append(str(val)[:50])
                parts.append(f"  {row.row_type}: {' | '.join(cells)}")
            if block.row_count > 3:
                parts.append(f"  ... ещё {block.row_count - 3} строк")
        elif isinstance(block, _ImageBlock):
            if block.ocr_text:
                page_label = f" (стр. {block.page})" if block.page else ""
                parts.append(f"[OCR{page_label}]\n{block.ocr_text}")
    return "\n".join(parts)


def _split_into_chunks(text: str, max_tokens: int, overlap_chars: int = 200) -> list[str]:
    """Разбивает текст на чанки, влезающие в контекст."""
    max_chars = int(max_tokens * 2.7)
    chunks: list[str] = []
    lines = text.split("\n")
    current: list[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1
        if current_len + line_len > max_chars and current:
            chunks.append("\n".join(current))
            # Overlap: берём последние N символов
            overlap_lines: list[str] = []
            overlap_len = 0
            for prev_line in reversed(current):
                if overlap_len + len(prev_line) > overlap_chars:
                    break
                overlap_lines.insert(0, prev_line)
                overlap_len += len(prev_line)
            current = overlap_lines
            current_len = overlap_len

        current.append(line)
        current_len += line_len

    if current:
        chunks.append("\n".join(current))

    return chunks


class PassportGenerator:
    """Генерирует паспорта документов через LLM."""

    def __init__(self, llm: LLMClient):
        self.llm = llm
        # Резерв токенов: system prompt (~200) + user prompt template (~300) + output (~2000)
        self._prompt_reserve = 2500

    def generate(self, parsed: ParsedDocument) -> DocumentPassport:
        """Генерирует паспорт для распарсенного документа.

        Автоматически выбирает стратегию:
        - skip для пустых документов (без текста, таблиц и OCR)
        - single-pass для маленьких документов
        - chunk + merge для больших
        """
        doc_text = _doc_to_text(parsed)
        estimated_tokens = self.llm.estimate_tokens(doc_text)

        # Пустой документ — не тратим токены на LLM
        if not doc_text.strip():
            logger.warning(
                "Документ %s пуст (0 токенов текста), пропускаю LLM",
                parsed.doc_id,
            )
            return DocumentPassport(
                doc_id=parsed.doc_id,
                source_filename=parsed.source_filename,
                doc_type="empty",
                title=parsed.source_filename,
                summary="Документ не содержит извлекаемого текста (возможно скан без OCR или пустой файл).",
                text_blocks_count=len(parsed.text_blocks),
                tables_count=len(parsed.table_blocks),
                images_count=len(parsed.image_blocks),
                estimated_tokens=0,
            )

        logger.info(
            "Генерация паспорта для %s: ~%d токенов текста",
            parsed.doc_id,
            estimated_tokens,
        )

        available = self.llm.max_input_tokens - self._prompt_reserve

        if estimated_tokens <= available:
            passport_data = self._single_pass(parsed.source_filename, doc_text)
        else:
            passport_data = self._chunked_pass(parsed.source_filename, doc_text, available)

        passport = DocumentPassport(
            doc_id=parsed.doc_id,
            source_filename=parsed.source_filename,
            doc_type=passport_data.get("doc_type", "other"),
            title=passport_data.get("title", parsed.source_filename),
            summary=passport_data.get("summary", ""),
            sections=[
                SectionSummary(**s) for s in passport_data.get("sections", [])
            ],
            key_topics=passport_data.get("key_topics", []),
            key_entities=passport_data.get("key_entities", []),
            text_blocks_count=len(parsed.text_blocks),
            tables_count=len(parsed.table_blocks),
            images_count=len(parsed.image_blocks),
            estimated_tokens=estimated_tokens,
        )

        logger.info(
            "Паспорт %s: тип=%s, %d разделов, %d тем",
            parsed.doc_id,
            passport.doc_type,
            len(passport.sections),
            len(passport.key_topics),
        )
        return passport

    def _single_pass(self, filename: str, doc_text: str) -> dict:
        """Генерация паспорта за один запрос (документ помещается в контекст)."""
        logger.debug("Стратегия: single-pass (structured output)")
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _SINGLE_PASS_PROMPT.format(
                filename=filename,
                content=doc_text,
            )},
        ]
        return self.llm.chat_structured(messages, schema=_PassportOut, schema_name="passport")

    def _chunked_pass(self, filename: str, doc_text: str, available_tokens: int) -> dict:
        """Генерация паспорта в два этапа: суммаризация чанков → слияние."""
        chunk_max_tokens = int(available_tokens * 0.6)
        chunks = _split_into_chunks(doc_text, chunk_max_tokens)

        logger.info(
            "Стратегия: chunked (%d чанков по ~%d токенов, structured output)",
            len(chunks),
            chunk_max_tokens,
        )

        chunk_summaries: list[str] = []
        for i, chunk in enumerate(chunks):
            logger.debug("Обработка чанка %d/%d", i + 1, len(chunks))
            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": _CHUNK_SUMMARY_PROMPT.format(
                    filename=filename,
                    content=chunk,
                )},
            ]
            try:
                result = self.llm.chat_structured(
                    messages, schema=_ChunkSummaryOut,
                    schema_name="chunk_summary", max_tokens=2048,
                )
                chunk_summaries.append(json.dumps(result, ensure_ascii=False, indent=1))
            except LLMError as exc:
                logger.warning("Ошибка при обработке чанка %d: %s", i + 1, exc)
                chunk_summaries.append(f'{{"error": "chunk {i+1} failed"}}')

        merged_text = "\n---\n".join(chunk_summaries)
        logger.debug("Слияние %d суммарий → финальный паспорт", len(chunk_summaries))

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _MERGE_PROMPT.format(
                filename=filename,
                chunk_summaries=merged_text,
            )},
        ]
        return self.llm.chat_structured(messages, schema=_PassportOut, schema_name="passport")


def generate_passport(
    parsed: ParsedDocument,
    llm: LLMClient,
) -> DocumentPassport:
    """Удобная функция: генерирует паспорт документа."""
    generator = PassportGenerator(llm)
    return generator.generate(parsed)


def build_document_map(
    passports: list[DocumentPassport],
    tender_id: str,
) -> DocumentMap:
    """Собирает карту документации из списка паспортов."""
    return DocumentMap(tender_id=tender_id, passports=passports)
