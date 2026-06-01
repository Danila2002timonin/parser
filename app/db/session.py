"""Управление подключением к PostgreSQL через SQLAlchemy."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def init_engine(database_url: str | None = None) -> Engine:
    """Создаёт engine и фабрику сессий (идемпотентно)."""
    global _engine, _SessionLocal
    if _engine is not None:
        return _engine

    url = database_url or settings.database_url
    _engine = create_engine(
        url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_pre_ping=True,
        future=True,
    )
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    logger.info("SQLAlchemy engine инициализирован: %s", url.split("@")[-1])
    return _engine


def get_engine() -> Engine:
    """Возвращает текущий engine, создавая его при необходимости."""
    if _engine is None:
        init_engine()
    assert _engine is not None
    return _engine


def dispose_engine() -> None:
    """Закрывает engine и освобождает пул соединений."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
        _engine = None
        _SessionLocal = None


@contextmanager
def get_session() -> Iterator[Session]:
    """Контекстный менеджер сессии: commit при успехе, rollback при ошибке."""
    if _SessionLocal is None:
        init_engine()
    assert _SessionLocal is not None

    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
