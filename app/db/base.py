"""Декларативная база SQLAlchemy для всех ORM-моделей."""

from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Общий базовый класс для ORM-моделей сервиса."""
