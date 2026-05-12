"""FastAPI entrypoint for Parser Service."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tender_tools import db
from tender_tools.http_proxy import apply_to_environment as apply_proxy_to_environment

from .routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Прокидываем OUTBOUND_PROXY_URL в HTTPS_PROXY/HTTP_PROXY/NO_PROXY,
    # чтобы third-party SDK (Langfuse, OpenTelemetry exporter и т.п.)
    # автоматически ходили через корпоративный прокси.
    apply_proxy_to_environment()
    db.init_db()
    try:
        yield
    finally:
        db.close_db()


app = FastAPI(
    title="Tender Parser Service",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
