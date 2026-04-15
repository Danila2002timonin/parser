"""Hybrid Search: BM25 + Dense Embeddings + RRF + Reranker.

Трёхступенчатый поиск по индексу тендерной документации:
1. Dense search (BGE-M3 cosine similarity) → top-K кандидатов
2. BM25 keyword search → top-K кандидатов
3. RRF (Reciprocal Rank Fusion) → объединение результатов
4. Reranker (BGE-reranker-v2-m3, опционально) → финальный top-N
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi

from .embedding_client import EmbeddingClient
from .question_router import QuestionRoute

logger = logging.getLogger(__name__)

# Константа для RRF: k=60 — стандартное значение из литературы
_RRF_K = 60


# ---------------------------------------------------------------------------
# Загрузка индекса
# ---------------------------------------------------------------------------

class SearchIndex:
    """Загруженный индекс для поиска."""

    def __init__(self, index_dir: Path):
        self.index_dir = index_dir

        logger.info("Загрузка индекса из %s", index_dir)

        self.vectors = np.load(str(index_dir / "vectors.npy"))
        self.mapping: list[dict] = json.loads(
            (index_dir / "mapping.json").read_text(encoding="utf-8")
        )
        bm25_corpus: list[list[str]] = json.loads(
            (index_dir / "bm25_corpus.json").read_text(encoding="utf-8")
        )
        self.bm25 = BM25Okapi(bm25_corpus)
        self.config: dict = json.loads(
            (index_dir / "config.json").read_text(encoding="utf-8")
        )

        # Нормализуем векторы для cosine similarity
        norms = np.linalg.norm(self.vectors, axis=1, keepdims=True)
        self.vectors_normed = self.vectors / (norms + 1e-10)

        logger.info(
            "Индекс загружен: %d чанков, dim=%d",
            len(self.mapping),
            self.vectors.shape[1],
        )


# ---------------------------------------------------------------------------
# Tokenizer (тот же что в indexer)
# ---------------------------------------------------------------------------

import re
_TOKEN_RE = re.compile(r"[а-яА-ЯёЁa-zA-Z0-9]+")


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


# ---------------------------------------------------------------------------
# Hybrid Search
# ---------------------------------------------------------------------------

class HybridSearcher:
    """Hybrid search по индексу тендерной документации."""

    def __init__(
        self,
        index: SearchIndex,
        embedding_client: EmbeddingClient,
        use_reranker: bool = True,
        dense_top_k: int = 30,
        bm25_top_k: int = 30,
        final_top_k: int = 5,
    ):
        self.index = index
        self.embedding_client = embedding_client
        self.use_reranker = use_reranker
        self.dense_top_k = dense_top_k
        self.bm25_top_k = bm25_top_k
        self.final_top_k = final_top_k

    def search(self, query: str) -> list[dict]:
        """Выполняет hybrid search по запросу.

        Returns:
            Список результатов, каждый: {doc_id, section_path, text_preview, score, ...}
        """
        logger.info("Hybrid search: '%s'", query[:60])

        # 1. Dense search
        dense_results = self._dense_search(query, self.dense_top_k)

        # 2. BM25 search
        bm25_results = self._bm25_search(query, self.bm25_top_k)

        # 3. RRF fusion
        fused = self._rrf_fuse(dense_results, bm25_results)

        logger.debug(
            "Dense: %d, BM25: %d, RRF fused: %d candidates",
            len(dense_results),
            len(bm25_results),
            len(fused),
        )

        # 4. Reranker (опционально)
        if self.use_reranker and fused:
            top_candidates = fused[: self.final_top_k * 3]
            results = self._rerank(query, top_candidates)
        else:
            results = fused[: self.final_top_k]

        for r in results[:5]:
            logger.info(
                "  [%.3f] %s:%s — %s",
                r["score"],
                r["doc_id"],
                r["section_path"],
                r["text_preview"][:60],
            )

        return results

    def search_to_route(self, question: str) -> QuestionRoute:
        """Выполняет search и конвертирует результат в QuestionRoute."""
        results = self.search(question)

        target_docs = list(dict.fromkeys(r["doc_id"] for r in results))
        target_sections = list(dict.fromkeys(
            f"{r['doc_id']}:{r['section_path']}" for r in results
            if r["section_path"] and r["section_path"] != "__header__"
        ))

        if len(target_docs) > 3:
            scope = "multi_doc"
        elif len(target_sections) <= 1:
            scope = "single_fact"
        else:
            scope = "single_doc"

        return QuestionRoute(
            question=question,
            target_docs=target_docs,
            target_sections=target_sections,
            reasoning=f"hybrid search: {len(results)} results",
            scope=scope,
        )

    # --- Dense search ---

    def _dense_search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        """Cosine similarity search."""
        query_vec = self.embedding_client.embed_query(query)
        query_normed = query_vec / (np.linalg.norm(query_vec) + 1e-10)

        scores = self.index.vectors_normed @ query_normed
        top_indices = np.argsort(scores)[::-1][:top_k]

        return [(int(idx), float(scores[idx])) for idx in top_indices]

    # --- BM25 search ---

    def _bm25_search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        """BM25 keyword search."""
        tokens = _tokenize(query)
        if not tokens:
            return []

        scores = self.index.bm25.get_scores(tokens)
        top_indices = np.argsort(scores)[::-1][:top_k]

        return [
            (int(idx), float(scores[idx]))
            for idx in top_indices
            if scores[idx] > 0
        ]

    # --- RRF Fusion ---

    def _rrf_fuse(
        self,
        dense_results: list[tuple[int, float]],
        bm25_results: list[tuple[int, float]],
    ) -> list[dict]:
        """Reciprocal Rank Fusion — объединяет два списка по рангам."""
        rrf_scores: dict[int, float] = {}

        for rank, (idx, _score) in enumerate(dense_results):
            rrf_scores[idx] = rrf_scores.get(idx, 0) + 1.0 / (_RRF_K + rank + 1)

        for rank, (idx, _score) in enumerate(bm25_results):
            rrf_scores[idx] = rrf_scores.get(idx, 0) + 1.0 / (_RRF_K + rank + 1)

        sorted_indices = sorted(rrf_scores.keys(), key=lambda i: rrf_scores[i], reverse=True)

        results = []
        for idx in sorted_indices:
            entry = self.index.mapping[idx]
            results.append({
                "index": idx,
                "doc_id": entry["doc_id"],
                "source_filename": entry["source_filename"],
                "section_path": entry["section_path"],
                "text_preview": entry["text_preview"],
                "text_length": entry["text_length"],
                "score": rrf_scores[idx],
            })

        return results

    # --- Reranker ---

    def _rerank(self, query: str, candidates: list[dict]) -> list[dict]:
        """Реранкинг через BGE-reranker-v2-m3."""
        texts = [c["text_preview"] for c in candidates]

        try:
            ranked = self.embedding_client.rerank(query, texts, top_k=self.final_top_k)
        except Exception as exc:
            logger.warning("Reranker недоступен: %s. Возвращаю без реранкинга.", exc)
            return candidates[: self.final_top_k]

        results = []
        for orig_idx, score in ranked:
            entry = candidates[orig_idx].copy()
            entry["score"] = score
            results.append(entry)

        return results
