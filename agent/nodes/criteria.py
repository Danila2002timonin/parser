"""Criteria Pipeline: роутинг, поиск, ответ и валидация по критериям."""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tender_tools.config import DATA_DIR
from tender_tools.context_assembler import ContextAssembler
from tender_tools.llm_client import LLMClient, set_usage_context
from tender_tools.passport import DocumentMap
from tender_tools.question_router import QuestionRouter, QuestionRoute

from ..prompts import (
    CRITERIA_ANSWER_SYSTEM,
    CRITERIA_ANSWER_USER,
    VALIDATION_SYSTEM,
    VALIDATION_USER,
)
from ..state import (
    AgentState,
    CriterionAnswerOut,
    CriterionResult,
    CriterionRoute,
    ValidationBatchOut,
)

logger = logging.getLogger(__name__)


def route_criteria(state: AgentState) -> dict:
    """Роутинг обычных (не табличных) критериев батчем через document_map."""
    # Фильтруем: только критерии для текстовых ответов
    text_criteria = [c for c in state.criteria if not c.is_table_column]
    if not text_criteria:
        return {"criteria_routes": [], "criteria_done": True}

    llm = LLMClient()
    doc_map = DocumentMap.model_validate_json(
        '{"tender_id": "' + state.tender_id + '", "passports": []}'
    )
    tender_dir = DATA_DIR / "tenders" / state.tender_id
    map_path = tender_dir / "passports" / "document_map.json"
    if map_path.exists():
        doc_map = DocumentMap.load(map_path)

    router = QuestionRouter(llm, doc_map)

    criterion_names = [c.name for c in text_criteria]
    routing_result = router.route(criterion_names)

    routes = []
    for route in routing_result.routes:
        routes.append(CriterionRoute(
            criterion_name=route.question,
            target_docs=route.target_docs,
            target_sections=route.target_sections,
        ))

    llm.close()

    logger.info("Роутинг %d критериев завершён", len(routes))
    return {"criteria_routes": routes}


def fetch_and_answer(state: AgentState) -> dict:
    """Для каждого критерия: сборка контекста + LLM-ответ (параллельно)."""
    tender_dir = DATA_DIR / "tenders" / state.tender_id
    parsed_dir = tender_dir / "parsed"

    llm = LLMClient()
    assembler = ContextAssembler(parsed_dir=parsed_dir, llm=llm)

    text_criteria = [c for c in state.criteria if not c.is_table_column]
    route_map = {r.criterion_name: r for r in state.criteria_routes}

    def _process_one(criterion):
        set_usage_context("agent", "criteria_answer", state.tender_id)

        route_data = route_map.get(criterion.name)
        if not route_data:
            return CriterionResult(
                name=criterion.name,
                answer="Не удалось определить, где искать информацию.",
                condition=criterion.condition,
            )

        qr = QuestionRoute(
            question=criterion.name,
            target_docs=route_data.target_docs,
            target_sections=route_data.target_sections,
        )

        context = assembler.assemble(qr)

        messages = [
            {"role": "system", "content": CRITERIA_ANSWER_SYSTEM},
            {"role": "user", "content": CRITERIA_ANSWER_USER.format(
                context=context.context_text,
                criterion_name=criterion.name,
                criterion_description=criterion.description,
            )},
        ]

        try:
            data = llm.chat_structured(
                messages,
                schema=CriterionAnswerOut,
                schema_name="criterion_answer",
            )
            return CriterionResult(
                name=criterion.name,
                answer=data.get("answer", ""),
                source_docs=data.get("source_docs", []),
                source_sections=data.get("source_sections", []),
                condition=criterion.condition,
            )
        except Exception as exc:
            logger.error("Ошибка ответа по критерию '%s': %s", criterion.name, exc)
            return CriterionResult(
                name=criterion.name,
                answer=f"Ошибка: {exc}",
                condition=criterion.condition,
            )

    results = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_process_one, c): c for c in text_criteria}
        for future in as_completed(futures):
            results.append(future.result())

    llm.close()

    # Сортируем по исходному порядку критериев
    name_order = {c.name: i for i, c in enumerate(text_criteria)}
    results.sort(key=lambda r: name_order.get(r.name, 999))

    logger.info("Ответы по %d критериям получены", len(results))
    return {"criteria_results": results}


def validate_conditions(state: AgentState) -> dict:
    """Валидация условий для критериев, у которых есть condition."""
    pairs = [
        r for r in state.criteria_results
        if r.condition
    ]

    if not pairs:
        return {"criteria_done": True}

    llm = LLMClient()
    set_usage_context("agent", "validation", state.tender_id)

    # Группируем по 10 для батчевой валидации
    batch_size = 10
    updated_results = list(state.criteria_results)
    result_map = {r.name: r for r in updated_results}

    for i in range(0, len(pairs), batch_size):
        batch = pairs[i:i + batch_size]

        pairs_text = "\n\n".join(
            f"Критерий: {p.name}\n"
            f"Найденная информация: {p.answer}\n"
            f"Условие: {p.condition}"
            for p in batch
        )

        messages = [
            {"role": "system", "content": VALIDATION_SYSTEM},
            {"role": "user", "content": VALIDATION_USER.format(pairs=pairs_text)},
        ]

        try:
            data = llm.chat_structured(
                messages,
                schema=ValidationBatchOut,
                schema_name="validation_batch",
            )

            for v in data.get("results", []):
                if v.get("criterion_name") in result_map:
                    r = result_map[v["criterion_name"]]
                    r.match = v.get("match")
                    r.confidence = v.get("confidence")
                    r.explanation = v.get("explanation")

        except Exception as exc:
            logger.error("Ошибка валидации батча: %s", exc)
            for p in batch:
                if p.name in result_map:
                    result_map[p.name].explanation = f"Ошибка валидации: {exc}"

    llm.close()

    logger.info("Валидация %d условий завершена", len(pairs))
    return {"criteria_results": updated_results, "criteria_done": True}
