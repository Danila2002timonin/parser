"""Langfuse трейсинг для AI Agent (v4.x).

Записывает input/output каждой ноды и LLM-вызовы с токенами и стоимостью.
Если ключи не настроены — трейсинг отключён, всё работает без Langfuse.
"""

from __future__ import annotations

import functools
import json
import logging
import os
import time
from typing import Callable

logger = logging.getLogger(__name__)

_enabled = False
_langfuse = None


def init_langfuse() -> bool:
    """Инициализирует Langfuse."""
    global _enabled, _langfuse

    from dotenv import load_dotenv
    load_dotenv()

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")

    if not public_key or not secret_key:
        logger.info("Langfuse не настроен (нет ключей), трейсинг отключён")
        _enabled = False
        return False

    try:
        from langfuse import Langfuse
        _langfuse = Langfuse()
        _langfuse.auth_check()
        _enabled = True
        logger.info("Langfuse инициализирован")
        return True
    except Exception as exc:
        logger.warning("Не удалось инициализировать Langfuse: %s", exc)
        _enabled = False
        return False


def create_trace(tender_id: str, session_id: str | None = None):
    """Создаёт trace ID."""
    if not _enabled or not _langfuse:
        return None
    try:
        return _langfuse.create_trace_id()
    except Exception:
        return None


def traced_node(node_name: str):
    """Декоратор для трейсинга ноды LangGraph с input/output."""
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(state, *args, **kwargs):
            t0 = time.time()

            # Компактный input (не весь state — слишком большой)
            input_summary = _summarize_state(state, node_name)

            if _enabled and _langfuse:
                try:
                    with _langfuse.start_as_current_observation(
                        name=node_name,
                        input=input_summary,
                        metadata={
                            "tender_id": getattr(state, "tender_id", ""),
                            "node": node_name,
                        },
                    ) as obs:
                        result = func(state, *args, **kwargs)
                        duration_ms = int((time.time() - t0) * 1000)

                        obs.update(
                            output=_safe_output(result),
                            metadata={
                                "duration_ms": duration_ms,
                                "node": node_name,
                            },
                        )
                        logger.debug("Node %s: %dms (traced)", node_name, duration_ms)
                        return result
                except Exception as exc:
                    logger.debug("Langfuse trace error for %s: %s", node_name, exc)

            result = func(state, *args, **kwargs)
            duration_ms = int((time.time() - t0) * 1000)
            logger.debug("Node %s: %dms", node_name, duration_ms)
            return result

        return wrapper
    return decorator


def log_llm_generation(
    name: str,
    model: str,
    input_messages: list[dict] | None = None,
    output: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    duration_ms: int | None = None,
    cost_input: float | None = None,
    cost_output: float | None = None,
) -> None:
    """Логирует LLM-вызов как Langfuse generation с токенами и стоимостью."""
    if not _enabled or not _langfuse:
        return
    try:
        usage = {}
        if prompt_tokens is not None:
            usage["input"] = prompt_tokens
        if completion_tokens is not None:
            usage["output"] = completion_tokens
        if total_tokens is not None:
            usage["total"] = total_tokens

        cost = None
        if cost_input is not None and cost_output is not None:
            cost = cost_input + cost_output

        _langfuse.start_as_current_observation(
            name=name,
            input=input_messages,
            output=output,
            metadata={
                "model": model,
                "duration_ms": duration_ms,
            },
        ).__enter__()

    except Exception as exc:
        logger.debug("Langfuse generation log error: %s", exc)


def flush():
    """Отправляет все данные в Langfuse."""
    if _enabled and _langfuse:
        try:
            _langfuse.flush()
        except Exception:
            pass


def _summarize_state(state, node_name: str) -> dict:
    """Создаёт компактное представление state для input."""
    try:
        s = state if isinstance(state, dict) else state.__dict__ if hasattr(state, "__dict__") else {}
        summary = {"tender_id": s.get("tender_id", "")}

        if node_name == "fetch_meta":
            criteria = s.get("criteria", [])
            summary["criteria_count"] = len(criteria)
            summary["criteria_names"] = [
                c.get("name", c.name) if hasattr(c, "name") else str(c)
                for c in criteria[:10]
            ]

        elif node_name == "route_criteria":
            summary["criteria_count"] = len(s.get("criteria", []))

        elif node_name == "fetch_and_answer":
            summary["routes_count"] = len(s.get("criteria_routes", []))

        elif node_name == "validate_conditions":
            results = s.get("criteria_results", [])
            summary["results_with_condition"] = sum(
                1 for r in results
                if (r.get("condition") if isinstance(r, dict) else getattr(r, "condition", None))
            )

        elif node_name in ("detect_items", "plan_batches", "generate_batches"):
            summary["items_count"] = s.get("items_count")
            summary["table_columns"] = s.get("table_columns", [])

        elif node_name == "merge_table":
            summary["rows_count"] = len(s.get("table_rows", []))

        elif node_name == "validate_table_conditions":
            summary["rows_count"] = len(s.get("table_rows", []))

        elif node_name == "merge_results":
            summary["criteria_results"] = len(s.get("criteria_results", []))
            summary["table_rows"] = len(s.get("table_rows", []))

        return summary
    except Exception:
        return {"node": node_name}


def _safe_output(result) -> dict | str | None:
    """Безопасно преобразует результат ноды для Langfuse."""
    if result is None:
        return None
    if isinstance(result, dict):
        safe = {}
        for k, v in result.items():
            if isinstance(v, (str, int, float, bool)):
                safe[k] = v
            elif isinstance(v, list):
                safe[k] = f"list[{len(v)} items]"
            elif isinstance(v, dict):
                safe[k] = f"dict[{len(v)} keys]"
            else:
                safe[k] = str(type(v).__name__)
        return safe
    return str(result)[:500]
