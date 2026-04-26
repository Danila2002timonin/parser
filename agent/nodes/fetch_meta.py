"""Нода fetch_meta: загрузка метаданных тендера и document_map."""

from __future__ import annotations

import logging
from pathlib import Path

from tender_tools.config import DATA_DIR
from tender_tools.passport import DocumentMap

from ..state import AgentState
from ..tender_api import get_tender_info

logger = logging.getLogger(__name__)


def fetch_meta(state: AgentState) -> dict:
    """Загружает метаданные тендера из API и document_map с диска.

    Также формирует table_columns из имён критериев.
    """
    tender_id = state.tender_id

    # 1. API: тип тендера и позиции ОЗ
    info = get_tender_info(tender_id)

    # 2. Document map
    tender_dir = DATA_DIR / "tenders" / tender_id
    map_path = tender_dir / "passports" / "document_map.json"
    document_map_text = ""

    if map_path.exists():
        doc_map = DocumentMap.load(map_path)
        document_map_text = doc_map.to_routing_text()
        logger.info("Document map загружен: %d паспортов", len(doc_map.passports))
    else:
        logger.warning("Document map не найден: %s", map_path)

    # 3. Колонки таблицы: только критерии с is_table_column=true
    table_criteria = [c for c in state.criteria if c.is_table_column]
    table_columns = [c.name for c in table_criteria]
    table_column_criteria = {c.name: c for c in table_criteria}

    logger.info(
        "Meta: type=%d, items=%s, text_criteria=%d, table_columns=%d",
        info["tender_type"],
        info["items_count"],
        len([c for c in state.criteria if not c.is_table_column]),
        len(table_columns),
    )

    return {
        "tender_type": info["tender_type"],
        "items_count": info["items_count"],
        "items_list": info["items_list"],
        "document_map_text": document_map_text,
        "table_columns": table_columns,
        "table_column_criteria": table_column_criteria,
    }
