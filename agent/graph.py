"""LangGraph StateGraph: основной граф агента.

Две параллельные ветки:
- Criteria: route → answer → validate
- Table: detect_items → plan_batches → generate → merge
"""

from __future__ import annotations

from langgraph.graph import END, StateGraph

from .nodes.criteria import fetch_and_answer, route_criteria, validate_conditions
from .nodes.fetch_meta import fetch_meta
from .nodes.merge import merge_results
from .nodes.table import detect_items, generate_batches, merge_table, plan_batches, validate_table_conditions
from .state import AgentState
from .tracing import init_langfuse, traced_node

# Оборачиваем ноды в трейсинг (работает и без Langfuse)
fetch_meta = traced_node("fetch_meta")(fetch_meta)
route_criteria = traced_node("route_criteria")(route_criteria)
fetch_and_answer = traced_node("fetch_and_answer")(fetch_and_answer)
validate_conditions = traced_node("validate_conditions")(validate_conditions)
detect_items = traced_node("detect_items")(detect_items)
plan_batches = traced_node("plan_batches")(plan_batches)
generate_batches = traced_node("generate_batches")(generate_batches)
merge_table = traced_node("merge_table")(merge_table)
validate_table_conditions = traced_node("validate_table_conditions")(validate_table_conditions)
merge_results = traced_node("merge_results")(merge_results)


def _has_conditions(state: AgentState) -> str:
    """Условное ребро: есть ли критерии с condition?"""
    has = any(r.condition for r in state.criteria_results)
    return "validate" if has else "skip_validation"


def _has_table_conditions(state: AgentState) -> str:
    """Условное ребро: есть ли табличные критерии с condition?"""
    has = any(
        c.condition
        for c in state.table_column_criteria.values()
    )
    return "validate_table" if has and state.table_rows else "skip_table_validation"


def _both_done(state: AgentState) -> str:
    """Проверяет, завершились ли обе ветки."""
    if state.criteria_done and state.table_done:
        return "done"
    return "wait"


def build_graph() -> StateGraph:
    """Строит и возвращает скомпилированный граф."""

    graph = StateGraph(AgentState)

    # Ноды
    graph.add_node("fetch_meta", fetch_meta)

    # Criteria branch
    graph.add_node("route_criteria", route_criteria)
    graph.add_node("fetch_and_answer", fetch_and_answer)
    graph.add_node("validate_conditions", validate_conditions)
    graph.add_node("criteria_done_marker", lambda state: {"criteria_done": True})

    # Table branch
    graph.add_node("detect_items", detect_items)
    graph.add_node("plan_batches", plan_batches)
    graph.add_node("generate_batches", generate_batches)
    graph.add_node("merge_table", merge_table)
    graph.add_node("validate_table_conditions", validate_table_conditions)

    # Merge
    graph.add_node("merge_results", merge_results)

    # Entry
    graph.set_entry_point("fetch_meta")

    # После fetch_meta → две параллельные ветки
    # LangGraph поддерживает multiple edges из одной ноды
    graph.add_edge("fetch_meta", "route_criteria")
    graph.add_edge("fetch_meta", "detect_items")

    # Criteria branch flow
    graph.add_edge("route_criteria", "fetch_and_answer")
    graph.add_conditional_edges(
        "fetch_and_answer",
        _has_conditions,
        {
            "validate": "validate_conditions",
            "skip_validation": "criteria_done_marker",
        },
    )
    graph.add_edge("validate_conditions", "criteria_done_marker")
    graph.add_edge("criteria_done_marker", "merge_results")

    # Table branch flow
    graph.add_edge("detect_items", "plan_batches")
    graph.add_edge("plan_batches", "generate_batches")
    graph.add_edge("generate_batches", "merge_table")
    graph.add_conditional_edges(
        "merge_table",
        _has_table_conditions,
        {
            "validate_table": "validate_table_conditions",
            "skip_table_validation": "merge_results",
        },
    )
    graph.add_edge("validate_table_conditions", "merge_results")

    # Merge → END
    graph.add_edge("merge_results", END)

    return graph.compile()


# Singleton для переиспользования
compiled_graph = None


def get_graph():
    """Возвращает скомпилированный граф (lazy singleton)."""
    global compiled_graph
    if compiled_graph is None:
        compiled_graph = build_graph()
    return compiled_graph
