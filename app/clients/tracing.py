"""Опциональный трейсинг LLM-вызовов через Langfuse.

Langfuse включается автоматически, если в окружении заданы
``LANGFUSE_PUBLIC_KEY`` и ``LANGFUSE_SECRET_KEY``. В противном случае
все функции этого модуля работают как no-op.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_langfuse = None
_enabled = False

if os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"):
    try:
        from langfuse import Langfuse

        _langfuse = Langfuse()
        _enabled = True
        logger.info("Langfuse tracing включён")
    except Exception as exc:  # pragma: no cover - зависит от окружения
        logger.debug("Langfuse недоступен, трейсинг отключён: %s", exc)


def is_enabled() -> bool:
    """True, если трейсинг через Langfuse активен."""
    return _enabled and _langfuse is not None


def get_client():
    """Возвращает клиент Langfuse или None, если трейсинг отключён."""
    return _langfuse
