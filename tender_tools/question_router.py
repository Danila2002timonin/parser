"""Маршрутизация вопросов к нужным документам/разделам по карте документации.

Router получает батч вопросов + DocumentMap и определяет, в каких документах
и разделах содержатся ответы. Это позволяет загружать в контекст LLM
только релевантные фрагменты, а не всю документацию.
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field

from .llm_client import LLMClient
from .passport import DocumentMap

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Схемы
# ---------------------------------------------------------------------------

class QuestionRoute(BaseModel):
    """Маршрут для одного вопроса: куда идти за ответом."""

    question: str = Field(description="Исходный вопрос")
    target_docs: list[str] = Field(
        default_factory=list,
        description="Список doc_id документов, где содержится ответ",
    )
    target_sections: list[str] = Field(
        default_factory=list,
        description="Конкретные разделы: 'doc_001:s3', 'doc_020:s5.1' и т.д.",
    )
    reasoning: str = Field(
        default="",
        description="Краткое обоснование выбора (для отладки)",
    )
    scope: str = Field(
        default="single_doc",
        description="Оценка объёма: single_fact | single_doc | multi_doc | full_scan",
    )


class RoutingResult(BaseModel):
    """Результат маршрутизации батча вопросов."""

    routes: list[QuestionRoute] = Field(default_factory=list)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> RoutingResult:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Промпт
# ---------------------------------------------------------------------------

# Structured output schema для роутинга
class _RouteOut(BaseModel):
    question: str
    target_docs: list[str]
    target_sections: list[str]
    reasoning: str
    scope: str


class _RoutingOut(BaseModel):
    routes: list[_RouteOut]


_SYSTEM_PROMPT = """Ты — маршрутизатор вопросов по тендерной документации.
Тебе дана карта документации тендера и список вопросов.
Для КАЖДОГО вопроса определи:
1. target_docs — в каких документах (doc_id) содержится ответ
2. target_sections — конкретные разделы (формат: "doc_id:section_id", арабские цифры)
3. scope — оценка объёма: single_fact | single_doc | multi_doc | full_scan
4. reasoning — кратко, почему эти документы"""

_ROUTING_PROMPT = """Карта документации:

{document_map}

---

Вопросы:
{questions_text}"""


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

class QuestionRouter:
    """Маршрутизирует вопросы к документам/разделам через LLM."""

    def __init__(self, llm: LLMClient, document_map: DocumentMap):
        self.llm = llm
        self.document_map = document_map
        self._routing_text = document_map.to_routing_text()

    def route(self, questions: list[str]) -> RoutingResult:
        """Маршрутизирует батч вопросов.

        Args:
            questions: список вопросов пользователя.

        Returns:
            RoutingResult с маршрутом для каждого вопроса.
        """
        if not questions:
            return RoutingResult()

        logger.info("Роутинг %d вопросов по карте из %d документов",
                     len(questions), len(self.document_map.passports))

        questions_text = "\n".join(
            f"{i+1}. {q}" for i, q in enumerate(questions)
        )

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _ROUTING_PROMPT.format(
                document_map=self._routing_text,
                questions_text=questions_text,
            )},
        ]

        # Проверяем, помещается ли запрос в контекст
        total_text = _SYSTEM_PROMPT + self._routing_text + questions_text
        estimated = self.llm.estimate_tokens(total_text)
        logger.info("Оценка токенов: ~%d (лимит: %d)", estimated, self.llm.max_input_tokens)

        if estimated > self.llm.max_input_tokens:
            logger.warning(
                "Карта документации + вопросы не помещаются в контекст (%d > %d). "
                "Используем сокращённую карту.",
                estimated,
                self.llm.max_input_tokens,
            )
            compact_map = self._make_compact_map()
            messages[1]["content"] = _ROUTING_PROMPT.format(
                document_map=compact_map,
                questions_text=questions_text,
            )

        data = self.llm.chat_structured(
            messages, schema=_RoutingOut,
            schema_name="routing", max_tokens=4096,
        )

        routes = []
        for route_data in data.get("routes", []):
            try:
                routes.append(QuestionRoute(**route_data))
            except Exception as exc:
                logger.warning("Ошибка парсинга маршрута: %s", exc)

        # Проверяем, что все вопросы покрыты
        if len(routes) < len(questions):
            logger.warning(
                "LLM вернул маршруты для %d из %d вопросов",
                len(routes),
                len(questions),
            )
            # Добавляем дефолтные маршруты для пропущенных вопросов
            routed_questions = {r.question for r in routes}
            for q in questions:
                if q not in routed_questions:
                    routes.append(QuestionRoute(
                        question=q,
                        target_docs=[p.doc_id for p in self.document_map.passports[:3]],
                        reasoning="Автоматический fallback — LLM не вернул маршрут",
                        scope="full_scan",
                    ))

        result = RoutingResult(routes=routes)

        for route in routes:
            logger.info(
                "  Q: %s → docs=%s, scope=%s",
                route.question[:60],
                route.target_docs,
                route.scope,
            )

        return result

    def _make_compact_map(self) -> str:
        """Сокращённая карта (без разделов) для случаев, когда полная не влезает."""
        lines = [f"# Карта документации тендера {self.document_map.tender_id}\n"]
        for p in self.document_map.passports:
            lines.append(f"[{p.doc_id}] {p.title} | Тип: {p.doc_type} | {p.summary}")
        return "\n".join(lines)
