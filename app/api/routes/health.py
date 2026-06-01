"""Health-check эндпоинт."""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Проверка доступности сервиса."""
    return {"status": "ok"}
