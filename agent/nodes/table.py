"""Table Pipeline: определение позиций ОЗ, точечный контекст, батчевая генерация, валидация."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from tender_tools.config import DATA_DIR
from tender_tools.context_assembler import (
    ContextAssembler,
    _format_block,
)
from tender_tools.llm_client import LLMClient, set_usage_context
from tender_tools.passport import DocumentMap
from tender_tools.parsers.schema import ParsedDocument, TableBlock
from tender_tools.question_router import QuestionRouter, QuestionRoute

from ..prompts import (
    TABLE_DETECT_ITEMS_SYSTEM,
    TABLE_DETECT_ITEMS_USER,
    TABLE_GENERATE_SYSTEM,
    TABLE_GENERATE_USER,
    VALIDATION_SYSTEM,
    VALIDATION_USER,
)
from ..state import (
    AgentState,
    DetectedItemOut,
    DetectedItemsOut,
    ProcurementItem,
    TableBatch,
    TableBatchOut,
    TableCell,
    TableCellOut,
    TableCellValidationItemOut,
    TableRow,
    TableValidationBatchOut,
)

logger = logging.getLogger(__name__)

BATCH_SIZE = 10


# ---------------------------------------------------------------------------
# Утилита: загрузка блоков по ID из parsed JSON
# ---------------------------------------------------------------------------

def _load_blocks_by_ids(parsed_dir: Path, doc_id: str, block_ids: list[str]) -> str:
    """Загружает конкретные блоки из parsed JSON и форматирует в текст."""
    path = parsed_dir / f"{doc_id}.json"
    if not path.exists():
        return ""

    parsed = ParsedDocument.load(path)
    target_ids = set(block_ids)
    parts = [f"=== Документ: {parsed.source_filename} ({doc_id}) ===\n"]

    for block in parsed.blocks:
        if hasattr(block, "block_id") and block.block_id in target_ids:
            text = _format_block(block)
            if text.strip():
                parts.append(text)

    return "\n".join(parts)


def _find_oz_documents(tender_dir: Path) -> list[str]:
    """Находит документы с описанием объектов закупки по паспортам."""
    map_path = tender_dir / "passports" / "document_map.json"
    if not map_path.exists():
        return []

    doc_map = DocumentMap.load(map_path)
    oz_docs = []
    keywords = ["technical_specification", "описание объекта", "техническое задание"]

    for p in doc_map.passports:
        if p.doc_type == "technical_specification":
            oz_docs.append(p.doc_id)
        elif any(kw in (p.title or "").lower() for kw in keywords[1:]):
            oz_docs.append(p.doc_id)

    return oz_docs


def _get_table_headers_preview(parsed_dir: Path, doc_ids: list[str]) -> str:
    """Получает превью заголовков таблиц для маппинга позиций."""
    parts = []
    for doc_id in doc_ids:
        path = parsed_dir / f"{doc_id}.json"
        if not path.exists():
            continue
        parsed = ParsedDocument.load(path)
        for block in parsed.blocks:
            if isinstance(block, TableBlock) and block.rows:
                header_cells = []
                for row in block.rows[:2]:
                    for c in row.cells:
                        val = c.value if hasattr(c, "value") else c
                        if val and str(val).strip():
                            header_cells.append(str(val)[:80])
                if header_cells:
                    parts.append(
                        f"[{doc_id}/{block.block_id}] "
                        f"{' | '.join(header_cells[:6])}"
                    )
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Нода: detect_items
# ---------------------------------------------------------------------------

def detect_items(state: AgentState) -> dict:
    """Определяет позиции ОЗ с маппингом к блокам документации."""

    # Type=1: позиции из API (без маппинга к блокам — будет fallback)
    if state.tender_type == 1 and state.items_list:
        logger.info("Позиции ОЗ из API: %d шт.", len(state.items_list))
        return {
            "items_count": len(state.items_list),
            "items_list": state.items_list,
        }

    # Type=0 или нет данных из API: ищем в документации
    tender_dir = DATA_DIR / "tenders" / state.tender_id
    parsed_dir = tender_dir / "parsed"

    llm = LLMClient()
    set_usage_context("agent", "detect_items", state.tender_id)

    # Находим документы с описанием ОЗ
    oz_docs = _find_oz_documents(tender_dir)
    if not oz_docs:
        oz_docs = [f"doc_{i:03d}" for i in range(1, 6)]

    # Собираем контекст из найденных документов
    assembler = ContextAssembler(parsed_dir=parsed_dir, llm=llm)
    route = QuestionRoute(
        question="описание объектов закупки, перечень позиций",
        target_docs=oz_docs,
        target_sections=[],
        scope="multi_doc",
    )
    context = assembler.assemble(route)

    # Также даём превью таблиц для маппинга
    table_preview = _get_table_headers_preview(parsed_dir, oz_docs)

    detect_prompt = TABLE_DETECT_ITEMS_USER.format(context=context.context_text)
    if table_preview:
        detect_prompt += f"\n\nПревью таблиц (doc_id/block_id → заголовки):\n{table_preview}"
        detect_prompt += "\n\nДля каждой позиции укажи source_doc_id и source_block_ids — блоки, содержащие данные этой позиции."

    messages = [
        {"role": "system", "content": TABLE_DETECT_ITEMS_SYSTEM},
        {"role": "user", "content": detect_prompt},
    ]

    try:
        data = llm.chat_structured(messages, schema=DetectedItemsOut, schema_name="detect_items")
        items = [
            ProcurementItem(
                number=item.get("number", 0),
                name=item.get("name", ""),
                source_doc_id=item.get("source_doc_id"),
                source_block_ids=item.get("source_block_ids", []),
            )
            for item in data.get("items", [])
        ]
        count = data.get("items_count", len(items))
        logger.info("Обнаружено %d позиций ОЗ из документации", count)
    except Exception as exc:
        logger.error("Ошибка определения позиций ОЗ: %s", exc)
        items = []
        count = 0

    llm.close()
    return {"items_count": count, "items_list": items}


# ---------------------------------------------------------------------------
# Нода: plan_batches
# ---------------------------------------------------------------------------

def plan_batches(state: AgentState) -> dict:
    """Планирует разбиение таблицы на батчи с маппингом контекста."""

    items_count = state.items_count or 0
    columns = state.table_columns

    if not columns or items_count == 0:
        logger.warning("Нет колонок или позиций ОЗ, пропускаю генерацию таблицы")
        return {"table_batches": [], "table_done": True}

    item_numbers = [item.number for item in state.items_list]
    if not item_numbers:
        item_numbers = list(range(1, items_count + 1))

    # Маппинг item_number → block_ids
    item_blocks = {}
    for item in state.items_list:
        if item.source_block_ids:
            item_blocks[item.number] = item.source_block_ids

    batches: list[TableBatch] = []
    batch_id = 0

    item_chunks = _chunk_list(item_numbers, BATCH_SIZE)
    col_chunks = _chunk_list(columns, BATCH_SIZE)

    for item_chunk in item_chunks:
        for col_chunk in col_chunks:
            batch_id += 1
            context_map = {n: item_blocks.get(n, []) for n in item_chunk}
            batches.append(TableBatch(
                batch_id=batch_id,
                item_numbers=item_chunk,
                column_names=col_chunk,
                context_block_ids=context_map,
            ))

    logger.info(
        "Таблица: %d позиций x %d колонок → %d батчей",
        items_count, len(columns), len(batches),
    )
    return {"table_batches": batches}


# ---------------------------------------------------------------------------
# Нода: generate_batches
# ---------------------------------------------------------------------------

def generate_batches(state: AgentState) -> dict:
    """Параллельная генерация батчей с точечным контекстом."""

    if not state.table_batches:
        return {"table_rows": [], "table_done": True}

    tender_dir = DATA_DIR / "tenders" / state.tender_id
    parsed_dir = tender_dir / "parsed"

    llm = LLMClient()
    assembler = ContextAssembler(parsed_dir=parsed_dir, llm=llm)

    item_name_map = {item.number: item.name for item in state.items_list}
    item_doc_map = {item.number: item.source_doc_id for item in state.items_list}

    # Fallback контекст (если нет маппинга к блокам)
    fallback_context = None

    def _get_batch_context(batch: TableBatch) -> str:
        """Собирает контекст: точечный (по block_ids) или fallback (роутинг)."""
        nonlocal fallback_context

        # Проверяем, есть ли маппинг к блокам для этого батча
        has_block_mapping = any(
            batch.context_block_ids.get(n) for n in batch.item_numbers
        )

        if has_block_mapping:
            # Точечный контекст: загружаем конкретные блоки
            parts = []
            seen_blocks = set()
            for n in batch.item_numbers:
                block_ids = batch.context_block_ids.get(n, [])
                doc_id = item_doc_map.get(n)
                if block_ids and doc_id:
                    key = (doc_id, tuple(block_ids))
                    if key not in seen_blocks:
                        seen_blocks.add(key)
                        text = _load_blocks_by_ids(parsed_dir, doc_id, block_ids)
                        if text.strip():
                            parts.append(text)
            if parts:
                return "\n\n".join(parts)

        # Fallback: один контекст через роутинг (вычисляется один раз)
        if fallback_context is None:
            oz_docs = _find_oz_documents(tender_dir)
            route = QuestionRoute(
                question="описание объектов закупки, характеристики товара",
                target_docs=oz_docs or [],
                scope="multi_doc",
            )
            ctx = assembler.assemble(route)
            fallback_context = ctx.context_text

        return fallback_context

    # Получаем условия из табличных критериев
    col_criteria = state.table_column_criteria

    def _generate_one(batch: TableBatch) -> list[TableRow]:
        set_usage_context("agent", "table_batch", state.tender_id)

        context_text = _get_batch_context(batch)

        items_text = "\n".join(
            f"{n}. {item_name_map.get(n, f'Позиция {n}')}"
            for n in batch.item_numbers
        )
        cols_text = "\n".join(
            f"- {col}" + (f" (описание: {col_criteria[col].description})" if col in col_criteria and col_criteria[col].description else "")
            for col in batch.column_names
        )

        messages = [
            {"role": "system", "content": TABLE_GENERATE_SYSTEM},
            {"role": "user", "content": TABLE_GENERATE_USER.format(
                context=context_text,
                items_list=items_text,
                columns_list=cols_text,
            )},
        ]

        try:
            data = llm.chat_structured(
                messages, schema=TableBatchOut, schema_name="table_batch",
            )
            rows = []
            for row_data in data.get("rows", []):
                item_num = row_data.get("item_number", 0)
                cells = []
                for c in row_data.get("cells", []):
                    cell = TableCell(
                        column=c.get("column", ""),
                        value=c.get("value", ""),
                        condition=col_criteria.get(c.get("column", ""), Criterion(name="")).condition if col_criteria else None,
                    )
                    cells.append(cell)

                rows.append(TableRow(
                    item_number=item_num,
                    item_name=row_data.get("item_name", ""),
                    cells=cells,
                    source_doc_id=item_doc_map.get(item_num) or row_data.get("source_doc_id", ""),
                    source_block_ids=row_data.get("source_block_ids", []),
                ))
            return rows
        except Exception as exc:
            logger.error("Ошибка генерации батча %d: %s", batch.batch_id, exc)
            return []

    all_rows: list[TableRow] = []
    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = {pool.submit(_generate_one, b): b for b in state.table_batches}
        for future in as_completed(futures):
            all_rows.extend(future.result())

    llm.close()
    logger.info("Сгенерировано %d строк таблицы", len(all_rows))
    return {"table_rows": all_rows}


# ---------------------------------------------------------------------------
# Нода: merge_table
# ---------------------------------------------------------------------------

def merge_table(state: AgentState) -> dict:
    """Объединяет батчи, дедуплицирует строки, сортирует."""

    rows_by_item: dict[int, TableRow] = {}

    for row in state.table_rows:
        if row.item_number in rows_by_item:
            existing = rows_by_item[row.item_number]
            existing_cols = {c.column for c in existing.cells}
            for cell in row.cells:
                if cell.column not in existing_cols:
                    existing.cells.append(cell)
            if not existing.source_doc_id and row.source_doc_id:
                existing.source_doc_id = row.source_doc_id
            if row.source_block_ids:
                existing.source_block_ids.extend(row.source_block_ids)
        else:
            rows_by_item[row.item_number] = row

    merged = sorted(rows_by_item.values(), key=lambda r: r.item_number)

    logger.info("Таблица ОЗ: %d строк после merge", len(merged))
    return {"table_rows": merged}


# ---------------------------------------------------------------------------
# Нода: validate_table_conditions
# ---------------------------------------------------------------------------

def validate_table_conditions(state: AgentState) -> dict:
    """Валидация условий для табличных критериев (ячеек с condition)."""

    # Собираем все ячейки с условиями
    pairs = []
    for row in state.table_rows:
        for cell in row.cells:
            criterion = state.table_column_criteria.get(cell.column)
            if criterion and criterion.condition:
                cell.condition = criterion.condition
                pairs.append((row.item_number, row.item_name, cell))

    if not pairs:
        return {"table_done": True}

    llm = LLMClient()
    set_usage_context("agent", "table_validation", state.tender_id)

    batch_size = 15
    for i in range(0, len(pairs), batch_size):
        batch = pairs[i:i + batch_size]

        pairs_text = "\n\n".join(
            f"Позиция #{item_num} ({item_name})\n"
            f"Критерий: {cell.column}\n"
            f"Найденное значение: {cell.value}\n"
            f"Условие: {cell.condition}"
            for item_num, item_name, cell in batch
        )

        messages = [
            {"role": "system", "content": VALIDATION_SYSTEM},
            {"role": "user", "content": VALIDATION_USER.format(pairs=pairs_text)},
        ]

        try:
            data = llm.chat_structured(
                messages,
                schema=TableValidationBatchOut,
                schema_name="table_validation",
            )
            # Маппинг результатов обратно к ячейкам
            result_map = {}
            for v in data.get("results", []):
                key = (v.get("item_number"), v.get("column"))
                result_map[key] = v

            for item_num, _, cell in batch:
                key = (item_num, cell.column)
                if key in result_map:
                    v = result_map[key]
                    cell.match = v.get("match")
                    cell.confidence = v.get("confidence")
                    cell.explanation = v.get("explanation")

        except Exception as exc:
            logger.error("Ошибка валидации таблицы: %s", exc)

    llm.close()

    logger.info("Валидация %d ячеек таблицы завершена", len(pairs))
    return {"table_rows": state.table_rows, "table_done": True}


def _chunk_list(lst: list, chunk_size: int) -> list[list]:
    if len(lst) <= chunk_size:
        return [lst]
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]


# Нужен импорт Criterion для col_criteria
from ..state import Criterion
