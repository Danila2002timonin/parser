"""CLI для запуска AI Agent.

Использование:
    python -m agent.run <tender_id> --criteria criteria.json
    python -m agent.run <tender_id> --criteria criteria.json --output result.json
    python -m agent.run <tender_id> --criteria criteria.json -v
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .graph import get_graph
from .nodes.merge import format_summary
from .state import AgentState, Criterion
from .tracing import init_langfuse, create_trace, flush


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def load_criteria(path: str) -> list[Criterion]:
    """Загружает критерии из JSON-файла."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Criterion(**c) for c in data]


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Agent: анализ тендера по критериям")
    parser.add_argument("tender_id", help="ID тендера")
    parser.add_argument(
        "--criteria", "-c", required=True,
        help="Путь к JSON-файлу с критериями",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Путь для сохранения результата (по умолчанию — stdout)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Подробный вывод",
    )
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)
    logger = logging.getLogger(__name__)

    # Langfuse (опционально)
    init_langfuse()

    # Загрузка критериев
    criteria = load_criteria(args.criteria)
    logger.info("Загружено %d критериев из %s", len(criteria), args.criteria)

    # Начальное состояние
    initial_state = AgentState(
        tender_id=args.tender_id,
        criteria=criteria,
    )

    # Запуск графа
    logger.info("=" * 60)
    logger.info("AGENT: Тендер %s, %d критериев", args.tender_id, len(criteria))
    logger.info("=" * 60)

    t0 = time.time()
    trace = create_trace(args.tender_id)
    graph = get_graph()
    final_state_data = graph.invoke(initial_state.model_dump())
    elapsed = time.time() - t0

    # Формируем финальный state для вывода
    final_state = AgentState(**final_state_data)
    summary = format_summary(final_state)

    logger.info("=" * 60)
    logger.info(
        "AGENT ЗАВЕРШЁН: %d критериев, %d строк таблицы (%.1fs)",
        len(summary["criteria_results"]),
        len(summary["procurement_table"]["rows"]),
        elapsed,
    )
    logger.info("=" * 60)

    # Вывод
    result_json = json.dumps(summary, ensure_ascii=False, indent=2)

    if args.output:
        Path(args.output).write_text(result_json, encoding="utf-8")
        logger.info("Результат сохранён: %s", args.output)
    else:
        print(result_json)

    flush()


if __name__ == "__main__":
    main()
