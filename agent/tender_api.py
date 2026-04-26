"""Клиент API tenderplan.ru для получения метаданных тендера."""

from __future__ import annotations

import logging
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

from .state import ProcurementItem

logger = logging.getLogger(__name__)

TENDERPLAN_API_BASE = "https://tenderplan.ru/api"


def get_tender_info(tender_id: str) -> dict:
    """Получает метаданные тендера: type и список позиций ОЗ.

    Returns:
        {
            "tender_type": 0 | 1,
            "items_count": int | None,
            "items_list": list[ProcurementItem],
        }
    """
    token = os.getenv("TENDERPLAN_AUTH_KEY", "")
    url = f"{TENDERPLAN_API_BASE}/tenders/get"
    headers = {"Authorization": f"Bearer {token}"}
    params = {"id": tender_id}

    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.get(url, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()

        tender_type = data.get("type", 0)
        items: list[ProcurementItem] = []

        if tender_type == 1:
            products = data.get("products", [])
            for idx, product in enumerate(products, start=1):
                name = product.get("name", f"Позиция {idx}")
                items.append(ProcurementItem(number=idx, name=name))

        logger.info(
            "Tender %s: type=%d, items=%d",
            tender_id, tender_type, len(items),
        )

        return {
            "tender_type": tender_type,
            "items_count": len(items) if items else None,
            "items_list": items,
        }

    except httpx.HTTPStatusError as exc:
        logger.warning("API tenderplan %d: %s", exc.response.status_code, exc)
        return {"tender_type": 0, "items_count": None, "items_list": []}
    except Exception as exc:
        logger.warning("Ошибка API tenderplan: %s", exc)
        return {"tender_type": 0, "items_count": None, "items_list": []}
