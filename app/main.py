"""FastAPI entrypoint для сервиса парсера."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.clients.http_proxy import apply_to_environment as apply_proxy_to_environment
from app.core.logging import setup_logging
from app.db.session import dispose_engine, init_engine

setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Прокидываем OUTBOUND_PROXY_URL в HTTPS_PROXY/HTTP_PROXY/NO_PROXY,
    # чтобы third-party SDK (Langfuse, OpenTelemetry exporter и т.п.)
    # автоматически ходили через корпоративный прокси.
    apply_proxy_to_environment()
    init_engine()
    try:
        yield
    finally:
        dispose_engine()


app = FastAPI(
    title="Tender Parser Service",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(api_router)
