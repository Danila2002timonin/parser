"""Нода merge_results: объединение criteria_results и table_rows в финальный JSON."""

from __future__ import annotations

import logging

from ..state import AgentState

logger = logging.getLogger(__name__)


def merge_results(state: AgentState) -> dict:
    """Формирует финальный результат. Ждёт завершения обеих веток."""
    logger.info(
        "Merge: %d критериев, %d строк таблицы, %d ошибок",
        len(state.criteria_results),
        len(state.table_rows),
        len(state.errors),
    )
    return {}


def format_summary(state: AgentState) -> dict:
    """Форматирует финальный JSON-результат саммари."""

    # Criteria results
    criteria_section = [
        {
            "name": r.name,
            "answer": r.answer,
            "source_docs": r.source_docs,
            "source_sections": r.source_sections,
            "condition": r.condition,
            "match": r.match,
            "confidence": r.confidence,
            "explanation": r.explanation,
        }
        for r in state.criteria_results
    ]

    # Table with source info and validation
    table_rows = []
    for row in state.table_rows:
        row_data = {
            "item_number": row.item_number,
            "item_name": row.item_name,
            "source_doc_id": row.source_doc_id,
            "source_block_ids": row.source_block_ids,
            "cells": {},
        }
        for cell in row.cells:
            cell_data: dict = {"value": cell.value}
            if cell.condition:
                cell_data["condition"] = cell.condition
                cell_data["match"] = cell.match
                cell_data["confidence"] = cell.confidence
                cell_data["explanation"] = cell.explanation
            row_data["cells"][cell.column] = cell_data
        table_rows.append(row_data)

    summary = {
        "tender_id": state.tender_id,
        "criteria_results": criteria_section,
        "procurement_table": {
            "columns": ["Номер", "Название"] + state.table_columns,
            "columns_with_conditions": [
                col for col in state.table_columns
                if state.table_column_criteria.get(col) and state.table_column_criteria[col].condition
            ],
            "rows": table_rows,
        },
        "errors": state.errors,
    }
    return summary
