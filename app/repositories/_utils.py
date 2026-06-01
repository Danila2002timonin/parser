"""Вспомогательные функции репозиториев."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def mapping_to_dict(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Преобразует RowMapping в обычный dict (или None)."""
    return dict(row) if row is not None else None
