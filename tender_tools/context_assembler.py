"""Сборка контекста из распарсенных документов по маршрутам роутера.

Context Assembler получает результат роутинга (QuestionRoute) и загружает
из parsed-документов только релевантные блоки, собирая их в текстовый
контекст, помещающийся в окно LLM.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from .llm_client import LLMClient
from .parsers.schema import (
    CellObject,
    ImageBlock,
    ParsedDocument,
    TableBlock,
    TextBlock,
)
from .question_router import QuestionRoute

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Схемы
# ---------------------------------------------------------------------------

class AssembledContext(BaseModel):
    """Собранный контекст для одного вопроса."""

    question: str
    context_text: str = Field(description="Текст контекста для подачи в LLM")
    source_docs: list[str] = Field(
        default_factory=list,
        description="Документы, из которых собран контекст",
    )
    estimated_tokens: int = 0
    strategy: str = Field(
        default="direct",
        description="Использованная стратегия: direct | truncated | section_only",
    )


# ---------------------------------------------------------------------------
# Извлечение блоков по section_path
# ---------------------------------------------------------------------------

def _blocks_match_section(
    parsed: ParsedDocument, target_section: str
) -> list[TextBlock | TableBlock | ImageBlock]:
    """Возвращает блоки, принадлежащие указанному разделу (с учётом дочерних).

    'target_section=3' найдёт блоки с section_path '3', '3.1', '3.2', '3.3.1' и т.д.
    Также захватывает блоки без section_path, идущие сразу после совпавшего.
    """
    matched: list = []
    in_section = False

    for block in parsed.blocks:
        sp = getattr(block, "section_path", None)

        if sp is not None:
            # Точное совпадение или дочерний раздел
            if sp == target_section or sp.startswith(target_section + "."):
                in_section = True
                matched.append(block)
            else:
                # Другой раздел — прекращаем захват
                if in_section:
                    in_section = False
        else:
            # Блок без section_path — включаем, если мы внутри нужного раздела
            if in_section:
                matched.append(block)

    return matched


def _get_all_blocks(parsed: ParsedDocument) -> list:
    """Возвращает все блоки документа."""
    return list(parsed.blocks)


# ---------------------------------------------------------------------------
# Форматирование блоков в текст
# ---------------------------------------------------------------------------

def _format_block(block) -> str:
    """Конвертирует один блок в текстовое представление."""
    if isinstance(block, TextBlock):
        if block.heading_level:
            return f"{'#' * block.heading_level} {block.content}"
        return block.content

    if isinstance(block, TableBlock):
        lines = []
        lines.append(f"[Таблица: {block.col_count} колонок, {block.row_count} строк]")
        for row in block.rows:
            cells = []
            for c in row.cells:
                val = c.value if isinstance(c, CellObject) else c
                cells.append(str(val)[:100])
            lines.append(f"| {' | '.join(cells)} |")
        return "\n".join(lines)

    if isinstance(block, ImageBlock):
        if block.ocr_text:
            return f"[Изображение, OCR: {block.ocr_text}]"
        return "[Изображение: содержимое не распознано]"

    return str(block)


def _format_blocks(blocks: list, doc_id: str, doc_filename: str) -> str:
    """Форматирует список блоков в текст с заголовком документа."""
    if not blocks:
        return ""

    parts = [f"=== Документ: {doc_filename} ({doc_id}) ===\n"]
    for block in blocks:
        text = _format_block(block)
        if text.strip():
            parts.append(text)
    parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Context Assembler
# ---------------------------------------------------------------------------

class ContextAssembler:
    """Собирает контекст из parsed-документов по маршрутам роутера."""

    def __init__(
        self,
        parsed_dir: Path,
        llm: LLMClient,
        max_context_tokens: int | None = None,
    ):
        """
        Args:
            parsed_dir: директория с parsed/*.json файлами.
            llm: LLM-клиент (для оценки токенов).
            max_context_tokens: максимум токенов на контекст.
                По умолчанию — max_input_tokens минус резерв на промпт + ответ.
        """
        self.parsed_dir = parsed_dir
        self.llm = llm
        # Резерв: system prompt (~300) + question (~100) + output (~3000)
        self._reserve = 3400
        self.max_context_tokens = max_context_tokens or (llm.max_input_tokens - self._reserve)
        self._doc_cache: dict[str, ParsedDocument] = {}

    def assemble(self, route: QuestionRoute) -> AssembledContext:
        """Собирает контекст для одного вопроса на основе маршрута.

        Стратегии (автовыбор):
        1. section_only — если секции найдены и дают достаточно контента
        2. direct — загружаем целые документы (fallback или scope=full_scan)
        3. truncated — контекст обрезан до лимита

        Args:
            route: маршрут от QuestionRouter.

        Returns:
            AssembledContext с текстом для подачи в LLM.
        """
        logger.info(
            "Сборка контекста для: '%s' → docs=%s, sections=%s",
            route.question[:50],
            route.target_docs,
            route.target_sections,
        )

        all_blocks_by_doc: dict[str, tuple[list, str]] = {}
        strategy = "direct"

        # Шаг 1: пытаемся собрать по секциям
        if route.target_sections:
            for target in route.target_sections:
                doc_id, section_id = self._parse_target(target)
                if not doc_id:
                    continue
                parsed = self._load_parsed(doc_id)
                if not parsed:
                    continue

                blocks = _blocks_match_section(parsed, section_id)
                if blocks:
                    if doc_id not in all_blocks_by_doc:
                        all_blocks_by_doc[doc_id] = ([], parsed.source_filename)
                    all_blocks_by_doc[doc_id][0].extend(blocks)

            # Проверяем: нашли ли мы достаточно контента по секциям?
            # Если найденные блоки < 10% от документа — section matching ненадёжен
            if all_blocks_by_doc:
                for doc_id in list(all_blocks_by_doc.keys()):
                    parsed = self._load_parsed(doc_id)
                    if not parsed:
                        continue
                    found_blocks = len(all_blocks_by_doc[doc_id][0])
                    total_blocks = len(parsed.blocks)
                    if total_blocks > 10 and found_blocks < max(3, total_blocks * 0.1):
                        logger.info(
                            "  %s: section match слишком мало (%d/%d блоков), загружаю целиком",
                            doc_id, found_blocks, total_blocks,
                        )
                        all_blocks_by_doc[doc_id] = (
                            _get_all_blocks(parsed),
                            parsed.source_filename,
                        )
                        strategy = "direct"
                    else:
                        strategy = "section_only"

        # Шаг 2: загружаем целые документы, если секции не нашлись
        if not all_blocks_by_doc:
            for doc_id in route.target_docs:
                parsed = self._load_parsed(doc_id)
                if parsed:
                    all_blocks_by_doc[doc_id] = (
                        _get_all_blocks(parsed),
                        parsed.source_filename,
                    )

        # Шаг 3: форматируем
        context_parts: list[str] = []
        source_docs: list[str] = []

        for doc_id, (blocks, filename) in all_blocks_by_doc.items():
            formatted = _format_blocks(blocks, doc_id, filename)
            if formatted.strip():
                context_parts.append(formatted)
                source_docs.append(doc_id)

        full_context = "\n".join(context_parts)
        estimated = self.llm.estimate_tokens(full_context)

        # Шаг 4: если не влезает — обрезаем
        if estimated > self.max_context_tokens:
            logger.warning(
                "Контекст %d токенов > лимит %d, обрезаю",
                estimated,
                self.max_context_tokens,
            )
            full_context = self._truncate(full_context, self.max_context_tokens)
            estimated = self.llm.estimate_tokens(full_context)
            strategy = "truncated"

        logger.info(
            "Контекст собран: %d символов, ~%d токенов, стратегия=%s, docs=%s",
            len(full_context),
            estimated,
            strategy,
            source_docs,
        )

        return AssembledContext(
            question=route.question,
            context_text=full_context,
            source_docs=source_docs,
            estimated_tokens=estimated,
            strategy=strategy,
        )

    def assemble_batch(self, routes: list[QuestionRoute]) -> list[AssembledContext]:
        """Собирает контекст для батча вопросов."""
        return [self.assemble(route) for route in routes]

    def _load_parsed(self, doc_id: str) -> ParsedDocument | None:
        """Загружает parsed-документ (с кэшированием)."""
        if doc_id in self._doc_cache:
            return self._doc_cache[doc_id]

        path = self.parsed_dir / f"{doc_id}.json"
        if not path.exists():
            logger.warning("Parsed JSON не найден: %s", path)
            return None

        parsed = ParsedDocument.load(path)
        self._doc_cache[doc_id] = parsed
        return parsed

    @staticmethod
    def _parse_target(target: str) -> tuple[str | None, str]:
        """Разбирает 'doc_001:3.1' → ('doc_001', '3.1')."""
        if ":" not in target:
            return None, ""
        parts = target.split(":", 1)
        return parts[0].strip(), parts[1].strip()

    def _truncate(self, text: str, max_tokens: int) -> str:
        """Обрезает текст до max_tokens, сохраняя целостность строк."""
        max_chars = int(max_tokens * 2.7)
        if len(text) <= max_chars:
            return text

        truncated = text[:max_chars]
        last_newline = truncated.rfind("\n")
        if last_newline > max_chars * 0.8:
            truncated = truncated[:last_newline]

        return truncated + "\n\n[... контекст обрезан из-за ограничения размера ...]"
