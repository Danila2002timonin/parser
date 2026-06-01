"""Агрегирующий роутер HTTP API."""

from __future__ import annotations

from fastapi import APIRouter

from .routes import health, ingest, tenders

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(ingest.router)
api_router.include_router(tenders.router)
