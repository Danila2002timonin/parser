"""FastAPI entrypoint for Parser Service."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tender_tools import db

from .routes import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
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
