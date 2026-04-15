"""Клиент для embedding и reranking моделей через LM Studio API.

Поддерживает:
- BGE-M3 embeddings (POST /v1/embeddings)
- BGE-reranker-v2-m3 reranking (через embeddings API с парами)
"""

from __future__ import annotations

import logging
import os

import httpx
import numpy as np

logger = logging.getLogger(__name__)


class EmbeddingClient:
    """Клиент для embedding-модели через OpenAI-compatible API."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        reranker_model: str | None = None,
        batch_size: int = 32,
        timeout: float = 120.0,
    ):
        self.base_url = (
            base_url or os.getenv("LLM_BASE_URL", "http://127.0.0.1:1234")
        ).rstrip("/")
        self.model = model or os.getenv(
            "EMBEDDING_MODEL", "text-embedding-bge-m3@fp16"
        )
        self.reranker_model = reranker_model or os.getenv(
            "RERANKER_MODEL", "text-embedding-bge-reranker-v2-m3@fp16"
        )
        self.batch_size = batch_size
        self._client = httpx.Client(timeout=timeout)

    def embed(self, texts: list[str]) -> np.ndarray:
        """Получает embeddings для списка текстов.

        Args:
            texts: список строк для эмбеддинга.

        Returns:
            numpy-матрица [N, dim].
        """
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            logger.debug(
                "Embedding batch %d-%d / %d",
                i, min(i + self.batch_size, len(texts)), len(texts),
            )

            response = self._client.post(
                f"{self.base_url}/v1/embeddings",
                json={"model": self.model, "input": batch},
            )
            response.raise_for_status()
            data = response.json()

            batch_vectors = [item["embedding"] for item in data["data"]]
            all_embeddings.extend(batch_vectors)

        return np.array(all_embeddings, dtype=np.float32)

    def embed_query(self, query: str) -> np.ndarray:
        """Эмбеддинг одного запроса. Возвращает вектор [dim]."""
        result = self.embed([query])
        return result[0]

    def rerank(
        self,
        query: str,
        documents: list[str],
        top_k: int | None = None,
    ) -> list[tuple[int, float]]:
        """Реранкинг документов по релевантности к запросу.

        Использует reranker через endpoint /v1/embeddings:
        подаём пары (query, doc) и используем скоры.

        Args:
            query: вопрос пользователя.
            documents: список текстов-кандидатов.
            top_k: вернуть только top_k результатов.

        Returns:
            Список (original_index, score), отсортированный по score desc.
        """
        if not documents:
            return []

        # Reranker через embeddings: embed query и docs, считаем cosine
        # Это работает с cross-encoder reranker в LM Studio
        query_vec = self._embed_with_model([query], self.reranker_model)[0]
        doc_vecs = self._embed_with_model(documents, self.reranker_model)

        # Cosine similarity
        query_norm = query_vec / (np.linalg.norm(query_vec) + 1e-10)
        doc_norms = doc_vecs / (np.linalg.norm(doc_vecs, axis=1, keepdims=True) + 1e-10)
        scores = doc_norms @ query_norm

        # Сортируем по убыванию score
        ranked = sorted(enumerate(scores.tolist()), key=lambda x: x[1], reverse=True)

        if top_k:
            ranked = ranked[:top_k]

        return ranked

    def _embed_with_model(self, texts: list[str], model: str) -> np.ndarray:
        """Embedding с указанной моделью."""
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            response = self._client.post(
                f"{self.base_url}/v1/embeddings",
                json={"model": model, "input": batch},
            )
            response.raise_for_status()
            data = response.json()
            batch_vectors = [item["embedding"] for item in data["data"]]
            all_embeddings.extend(batch_vectors)

        return np.array(all_embeddings, dtype=np.float32)

    def close(self) -> None:
        self._client.close()
