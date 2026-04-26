"""Pydantic-модели состояния LangGraph графа."""

from __future__ import annotations

from typing import Annotated, Any
from pydantic import BaseModel, Field
from langgraph.graph.message import add_messages


# ---------------------------------------------------------------------------
# Критерии и результаты
# ---------------------------------------------------------------------------

class Criterion(BaseModel):
    """Один критерий для анализа тендера."""
    name: str
    description: str = ""
    condition: str | None = None
    is_table_column: bool = False


class CriterionRoute(BaseModel):
    """Маршрут для критерия (результат роутинга)."""
    criterion_name: str
    target_docs: list[str] = Field(default_factory=list)
    target_sections: list[str] = Field(default_factory=list)


class CriterionResult(BaseModel):
    """Результат анализа одного критерия."""
    name: str
    answer: str = ""
    source_docs: list[str] = Field(default_factory=list)
    source_sections: list[str] = Field(default_factory=list)
    condition: str | None = None
    match: bool | None = None
    confidence: str | None = None
    explanation: str | None = None


# ---------------------------------------------------------------------------
# Таблица объектов закупки
# ---------------------------------------------------------------------------

class ProcurementItem(BaseModel):
    """Позиция объекта закупки (номер + название)."""
    number: int
    name: str
    source_doc_id: str | None = None
    source_block_ids: list[str] = Field(default_factory=list)


class TableCell(BaseModel):
    """Одна ячейка таблицы ОЗ."""
    column: str
    value: str
    condition: str | None = None
    match: bool | None = None
    confidence: str | None = None
    explanation: str | None = None


class TableRow(BaseModel):
    """Строка таблицы ОЗ."""
    item_number: int
    item_name: str
    cells: list[TableCell] = Field(default_factory=list)
    source_doc_id: str | None = None
    source_block_ids: list[str] = Field(default_factory=list)


class TableBatch(BaseModel):
    """Описание одного батча для генерации таблицы."""
    batch_id: int
    item_numbers: list[int]
    column_names: list[str]
    context_block_ids: dict[int, list[str]] = Field(
        default_factory=dict,
        description="item_number → list[block_id] для точечного контекста",
    )


# ---------------------------------------------------------------------------
# Structured output схемы для LLM
# ---------------------------------------------------------------------------

class CriterionAnswerOut(BaseModel):
    """Structured output: ответ по одному критерию."""
    answer: str
    source_docs: list[str]
    source_sections: list[str]


class ValidationItemOut(BaseModel):
    """Structured output: результат валидации одной пары."""
    criterion_name: str
    match: bool
    confidence: str
    explanation: str


class ValidationBatchOut(BaseModel):
    """Structured output: батч валидаций."""
    results: list[ValidationItemOut]


class TableCellValidationItemOut(BaseModel):
    """Structured output: валидация одной ячейки таблицы."""
    item_number: int
    column: str
    match: bool
    confidence: str
    explanation: str


class TableValidationBatchOut(BaseModel):
    """Structured output: батч валидаций ячеек таблицы."""
    results: list[TableCellValidationItemOut]


class DetectedItemsOut(BaseModel):
    """Structured output: обнаруженные позиции ОЗ."""
    items_count: int
    items: list[DetectedItemOut]


class DetectedItemOut(BaseModel):
    """Structured output: одна обнаруженная позиция ОЗ."""
    number: int
    name: str
    source_doc_id: str = ""
    source_block_ids: list[str] = Field(default_factory=list)


class TableBatchOut(BaseModel):
    """Structured output: результат генерации батча таблицы."""
    rows: list[TableRowOut]


class TableRowOut(BaseModel):
    """Structured output: строка таблицы из LLM."""
    item_number: int
    item_name: str
    cells: list[TableCellOut]
    source_doc_id: str = ""
    source_block_ids: list[str] = Field(default_factory=list)


class TableCellOut(BaseModel):
    """Structured output: ячейка таблицы из LLM."""
    column: str
    value: str


# ---------------------------------------------------------------------------
# Состояние графа
# ---------------------------------------------------------------------------

class AgentState(BaseModel):
    """Полное состояние LangGraph графа."""

    # Входные данные
    tender_id: str = ""
    criteria: list[Criterion] = Field(default_factory=list)

    # Метаданные тендера (из API)
    tender_type: int = 0
    items_count: int | None = None
    items_list: list[ProcurementItem] = Field(default_factory=list)

    # Контекст документации
    document_map_text: str = ""

    # Criteria pipeline
    criteria_routes: list[CriterionRoute] = Field(default_factory=list)
    criteria_results: list[CriterionResult] = Field(default_factory=list)

    # Table pipeline
    table_columns: list[str] = Field(default_factory=list)
    table_column_criteria: dict[str, Criterion] = Field(
        default_factory=dict,
        description="column_name → Criterion (для условий и описаний)",
    )
    table_batches: list[TableBatch] = Field(default_factory=list)
    table_rows: list[TableRow] = Field(default_factory=list)

    # Флаги завершения веток
    criteria_done: bool = False
    table_done: bool = False

    # Ошибки
    errors: list[str] = Field(default_factory=list)
