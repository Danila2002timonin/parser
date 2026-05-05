"""API schemas for Parser Service."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


class JobResponse(BaseModel):
    tender_id: str
    status: str = "queued"
    job_id: int
    files_count: int | None = None


class JobInfo(BaseModel):
    id: int
    tender_id: str
    priority: int
    status: str
    worker_id: str | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    retry_count: int = 0
    max_retries: int = 3


class TenderStatusResponse(BaseModel):
    tender_id: str
    status: str
    document_count: int = 0
    total_cost_usd: Decimal | None = None
    pipeline_duration_ms: int | None = None
    timestamps: dict[str, datetime | None] = Field(default_factory=dict)
    latest_job: JobInfo | None = None


class DocumentListResponse(BaseModel):
    tender_id: str
    documents: list[dict[str, Any]]


class PassportsResponse(BaseModel):
    tender_id: str
    passports: list[dict[str, Any]]


class DocumentMapResponse(BaseModel):
    tender_id: str
    map_data: dict[str, Any]
    routing_text: str | None = None
    passports_count: int | None = None
    estimated_tokens: int | None = None
    generated_at: datetime | None = None


class SourceBlocksResponse(BaseModel):
    tender_id: str
    doc_id: str
    source_filename: str = ""
    blocks: list[dict[str, Any]]
