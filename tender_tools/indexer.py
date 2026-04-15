"""Индексация parsed-документов: embedding + BM25 по секциям.

Разбивает документы на секции (chunks), создаёт:
- Dense index (numpy embeddings через BGE-M3)
- BM25 index (keyword search)
- Mapping: vector_idx → (doc_id, section_id, text_preview)

Всё хранится в файлах: vectors.npy, mapping.json, bm25_corpus.json
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import numpy as np

from .embedding_client import EmbeddingClient
from .parsers.schema import (
    CellObject,
    ImageBlock,
    ParsedDocument,
    TableBlock,
    TextBlock,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Chunk: единица индексации (секция или группа блоков)
# ---------------------------------------------------------------------------

class ChunkRecord:
    """Один чанк для индексации."""

    def __init__(
        self,
        doc_id: str,
        source_filename: str,
        section_path: str,
        text: str,
        block_ids: list[str],
    ):
        self.doc_id = doc_id
        self.source_filename = source_filename
        self.section_path = section_path
        self.text = text
        self.block_ids = block_ids

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "source_filename": self.source_filename,
            "section_path": self.section_path,
            "text_preview": self.text[:200],
            "text_length": len(self.text),
            "block_ids": self.block_ids,
        }


# ---------------------------------------------------------------------------
# Разбиение документа на чанки по секциям
# ---------------------------------------------------------------------------

def _block_to_text(block) -> str:
    """Конвертирует блок в текст для индексации."""
    if isinstance(block, TextBlock):
        return block.content

    if isinstance(block, TableBlock):
        lines = []
        for row in block.rows:
            cells = []
            for c in row.cells:
                val = c.value if isinstance(c, CellObject) else c
                cells.append(str(val))
            lines.append(" | ".join(cells))
        return "\n".join(lines)

    if isinstance(block, ImageBlock):
        return block.ocr_text or ""

    return ""


def extract_chunks(
    parsed: ParsedDocument,
    max_chunk_chars: int = 2000,
    min_chunk_chars: int = 50,
) -> list[ChunkRecord]:
    """Разбивает ParsedDocument на чанки по секциям.

    Логика:
    - Группируем блоки по section_path
    - Если секция слишком большая — разбиваем
    - Если секция слишком маленькая — объединяем с соседней
    """
    # Группируем блоки по section_path
    sections: list[tuple[str, list]] = []
    current_section = "__header__"
    current_blocks: list = []

    for block in parsed.blocks:
        sp = getattr(block, "section_path", None) or ""

        # Новая секция, если section_path изменился на непустой
        if sp and sp != current_section:
            if current_blocks:
                sections.append((current_section, current_blocks))
            current_section = sp
            current_blocks = [block]
        else:
            current_blocks.append(block)

    if current_blocks:
        sections.append((current_section, current_blocks))

    # Создаём чанки
    chunks: list[ChunkRecord] = []

    for section_path, blocks in sections:
        text_parts: list[str] = []
        block_ids: list[str] = []

        for block in blocks:
            bt = _block_to_text(block)
            if bt.strip():
                text_parts.append(bt)
                block_ids.append(getattr(block, "block_id", ""))

        full_text = "\n".join(text_parts)

        if len(full_text) < min_chunk_chars:
            continue

        # Если текст слишком длинный — разбиваем
        if len(full_text) > max_chunk_chars:
            sub_chunks = _split_text(full_text, max_chunk_chars)
            for i, sub_text in enumerate(sub_chunks):
                chunks.append(ChunkRecord(
                    doc_id=parsed.doc_id,
                    source_filename=parsed.source_filename,
                    section_path=f"{section_path}#{i}" if i > 0 else section_path,
                    text=sub_text,
                    block_ids=block_ids,
                ))
        else:
            chunks.append(ChunkRecord(
                doc_id=parsed.doc_id,
                source_filename=parsed.source_filename,
                section_path=section_path,
                text=full_text,
                block_ids=block_ids,
            ))

    return chunks


def _split_text(text: str, max_chars: int) -> list[str]:
    """Разбивает текст на части по max_chars, сохраняя целостность строк."""
    parts: list[str] = []
    lines = text.split("\n")
    current: list[str] = []
    current_len = 0

    for line in lines:
        if current_len + len(line) > max_chars and current:
            parts.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line) + 1

    if current:
        parts.append("\n".join(current))

    return parts


# ---------------------------------------------------------------------------
# Tokenizer для BM25
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[а-яА-ЯёЁa-zA-Z0-9]+")


def _tokenize(text: str) -> list[str]:
    """Простая токенизация для BM25: слова + цифры, lowercase."""
    return [t.lower() for t in _TOKEN_RE.findall(text)]


# ---------------------------------------------------------------------------
# Indexer
# ---------------------------------------------------------------------------

class TenderIndexer:
    """Создаёт embedding + BM25 индекс для тендера."""

    def __init__(self, embedding_client: EmbeddingClient):
        self.embedding_client = embedding_client

    def build_index(
        self,
        parsed_dir: Path,
        index_dir: Path,
        doc_ids: list[str] | None = None,
    ) -> int:
        """Строит индекс из parsed-документов.

        Args:
            parsed_dir: директория с parsed/*.json.
            index_dir: директория для сохранения индекса.
            doc_ids: список doc_id для индексации (None = все).

        Returns:
            Количество проиндексированных чанков.
        """
        index_dir.mkdir(parents=True, exist_ok=True)

        # Собираем все чанки
        all_chunks: list[ChunkRecord] = []
        parsed_files = sorted(parsed_dir.glob("*.json"))

        for path in parsed_files:
            parsed = ParsedDocument.load(path)
            if doc_ids and parsed.doc_id not in doc_ids:
                continue

            chunks = extract_chunks(parsed)
            all_chunks.extend(chunks)
            logger.info(
                "  %s (%s): %d чанков",
                parsed.doc_id,
                parsed.source_filename,
                len(chunks),
            )

        if not all_chunks:
            logger.warning("Нет чанков для индексации")
            return 0

        logger.info("Всего чанков: %d", len(all_chunks))

        # 1. Embedding index
        logger.info("Создание embedding индекса (BGE-M3)...")
        texts = [chunk.text for chunk in all_chunks]
        vectors = self.embedding_client.embed(texts)

        np.save(str(index_dir / "vectors.npy"), vectors)
        logger.info("Vectors: shape=%s, saved", vectors.shape)

        # 2. Mapping
        mapping = [chunk.to_dict() for chunk in all_chunks]
        (index_dir / "mapping.json").write_text(
            json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        # 3. BM25 corpus
        bm25_corpus = [_tokenize(chunk.text) for chunk in all_chunks]
        (index_dir / "bm25_corpus.json").write_text(
            json.dumps(bm25_corpus, ensure_ascii=False), encoding="utf-8"
        )

        # 4. Config
        config = {
            "model": self.embedding_client.model,
            "dim": int(vectors.shape[1]),
            "chunks_count": len(all_chunks),
            "docs_indexed": list({c.doc_id for c in all_chunks}),
        }
        (index_dir / "config.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        logger.info(
            "Индекс создан: %d чанков, dim=%d, сохранён в %s",
            len(all_chunks),
            vectors.shape[1],
            index_dir,
        )
        return len(all_chunks)
