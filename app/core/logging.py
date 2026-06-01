"""Настройка логирования сервиса."""

from __future__ import annotations

import logging

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO, *, datefmt: str | None = None) -> None:
    """Конфигурирует корневой логгер сервиса."""
    logging.basicConfig(level=level, format=_LOG_FORMAT, datefmt=datefmt)
