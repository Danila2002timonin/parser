"""Генерация ответов на вопросы по тендерной документации.

Финальное звено цепочки: Route → Assemble Context → **Answer**.
Получает собранный контекст и вопрос, генерирует ответ через LLM.
"""

from __future__ import annotations

import logging

from .context_assembler import AssembledContext
from .llm_client import LLMClient

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = """Ты — эксперт-аналитик тендерной документации. Отвечай на вопросы точно и полно, основываясь ТОЛЬКО на предоставленном контексте из документации.

Правила:
- Отвечай на русском языке
- Используй конкретные факты, даты, суммы, названия из документов
- Если в контексте нет информации для ответа — прямо скажи об этом
- Ссылайся на документы и разделы, откуда взята информация
- Будь лаконичен, но не упускай важных деталей"""

_USER_PROMPT = """Контекст из тендерной документации:

{context}

---

Вопрос: {question}

Ответь на вопрос, основываясь на контексте выше."""


class AnswerGenerator:
    """Генерирует ответы на вопросы по собранному контексту."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def answer(self, assembled: AssembledContext) -> str:
        """Генерирует ответ на вопрос.

        Args:
            assembled: собранный контекст от ContextAssembler.

        Returns:
            Текст ответа.
        """
        if not assembled.context_text.strip():
            return "Не удалось найти релевантную информацию в документации для ответа на этот вопрос."

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _USER_PROMPT.format(
                context=assembled.context_text,
                question=assembled.question,
            )},
        ]

        logger.info(
            "Генерация ответа: вопрос='%s', контекст ~%d токенов, источники=%s",
            assembled.question[:50],
            assembled.estimated_tokens,
            assembled.source_docs,
        )

        response = self.llm.chat(messages, max_tokens=2048)

        logger.info("Ответ сгенерирован: %d символов", len(response))
        return response
