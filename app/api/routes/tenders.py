"""Эндпоинты чтения статуса, документов, паспортов и источников тендера."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app import repositories as db
from app.core.config import DATA_DIR
from app.schemas.api import (
    DocumentListResponse,
    DocumentMapResponse,
    PassportsResponse,
    SourceBlocksResponse,
    TenderStatusResponse,
)
from app.services.parsers.schema import ParsedDocument
from app.services.passport import DocumentMap, DocumentPassport
from app.services.source_renderer import render_source_blocks
from app.services.storage import TenderStorage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["tenders"])


@router.get("/status/{tender_id}", response_model=TenderStatusResponse)
def get_status(tender_id: str) -> TenderStatusResponse:
    """Returns current tender status and latest parser job."""
    tender = _try_db(db.get_tender, tender_id)
    latest_job = _try_db(db.get_latest_job, tender_id)

    if tender is None:
        storage = TenderStorage(tender_id, data_dir=DATA_DIR)
        if not storage.has_manifest():
            raise HTTPException(status_code=404, detail="Tender not found")
        manifest = storage.load_manifest()
        return TenderStatusResponse(
            tender_id=tender_id,
            status=manifest.status.value,
            document_count=len(manifest.documents),
            timestamps={
                "downloaded_at": manifest.pipeline_state.downloaded,
                "extracted_at": manifest.pipeline_state.extracted,
                "parsed_at": manifest.pipeline_state.parsed,
                "passports_done_at": manifest.pipeline_state.passports_generated,
                "indexed_at": manifest.pipeline_state.indexed,
            },
            latest_job=latest_job,
        )

    return TenderStatusResponse(
        tender_id=tender_id,
        status=tender["status"],
        document_count=tender.get("document_count") or 0,
        total_cost_usd=tender.get("total_cost_usd"),
        pipeline_duration_ms=tender.get("pipeline_duration_ms"),
        timestamps={
            "downloaded_at": tender.get("downloaded_at"),
            "extracted_at": tender.get("extracted_at"),
            "parsed_at": tender.get("parsed_at"),
            "ocr_done_at": tender.get("ocr_done_at"),
            "passports_done_at": tender.get("passports_done_at"),
            "indexed_at": tender.get("indexed_at"),
        },
        latest_job=latest_job,
    )


@router.get("/tenders/{tender_id}/documents", response_model=DocumentListResponse)
def list_documents(tender_id: str) -> DocumentListResponse:
    """Returns tender documents with parser metrics."""
    documents = _try_db(db.list_documents, tender_id, default=[])
    if not documents:
        storage = TenderStorage(tender_id, data_dir=DATA_DIR)
        if not storage.has_manifest():
            raise HTTPException(status_code=404, detail="Tender not found")
        documents = [
            doc.model_dump(mode="json")
            for doc in storage.load_manifest().documents
        ]
    return DocumentListResponse(tender_id=tender_id, documents=documents)


@router.get("/tenders/{tender_id}/passports", response_model=PassportsResponse)
def list_passports(tender_id: str) -> PassportsResponse:
    """Returns all document passports for a tender."""
    passports = _try_db(db.list_passports, tender_id, default=[])
    if not passports:
        storage = TenderStorage(tender_id, data_dir=DATA_DIR)
        passports = _load_passports_from_files(storage)
        if not passports:
            raise HTTPException(status_code=404, detail="Passports not found")
    return PassportsResponse(tender_id=tender_id, passports=passports)


@router.get("/tenders/{tender_id}/document-map", response_model=DocumentMapResponse)
def get_document_map(tender_id: str) -> DocumentMapResponse:
    """Returns aggregated document map and routing text."""
    row = _try_db(db.get_document_map, tender_id)
    if row:
        return DocumentMapResponse(
            tender_id=tender_id,
            map_data=row["map_data"],
            routing_text=row.get("routing_text"),
            passports_count=row.get("passports_count"),
            estimated_tokens=row.get("estimated_tokens"),
            generated_at=row.get("generated_at"),
        )

    storage = TenderStorage(tender_id, data_dir=DATA_DIR)
    map_path = storage.passports_dir / "document_map.json"
    if not map_path.exists():
        raise HTTPException(status_code=404, detail="Document map not found")

    doc_map = DocumentMap.load(map_path)
    return DocumentMapResponse(
        tender_id=tender_id,
        map_data=doc_map.model_dump(mode="json", exclude={"generated_at"}),
        routing_text=doc_map.to_routing_text(),
        passports_count=len(doc_map.passports),
        generated_at=doc_map.generated_at,
    )


@router.get("/tenders/{tender_id}/parsed/{doc_id}")
def get_parsed_document(tender_id: str, doc_id: str) -> dict[str, Any]:
    """Returns the full ParsedDocument JSON."""
    storage = TenderStorage(tender_id, data_dir=DATA_DIR)
    parsed_path = storage.parsed_dir / f"{doc_id}.json"
    if not parsed_path.exists():
        raise HTTPException(status_code=404, detail="Parsed document not found")
    return ParsedDocument.load(parsed_path).model_dump(mode="json")


@router.get("/tenders/{tender_id}/sources/{doc_id}", response_model=SourceBlocksResponse)
def get_source_blocks(
    tender_id: str,
    doc_id: str,
    block_ids: list[str] | None = Query(None),
) -> SourceBlocksResponse:
    """Returns selected parsed blocks for source preview UI."""
    parsed_block_ids = _parse_block_ids(block_ids)
    try:
        data = render_source_blocks(tender_id, doc_id, parsed_block_ids)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return SourceBlocksResponse(**data)


def _load_passports_from_files(storage: TenderStorage) -> list[dict[str, Any]]:
    passports: list[dict[str, Any]] = []
    for path in sorted(storage.passports_dir.glob("*_passport.json")):
        passports.append(DocumentPassport.load(path).model_dump(mode="json"))
    return passports


def _parse_block_ids(block_ids: list[str] | None) -> list[str]:
    if not block_ids:
        return []

    parsed: list[str] = []
    for item in block_ids:
        parsed.extend(part.strip() for part in item.split(",") if part.strip())
    return parsed


def _try_db(func, *args, default=None):
    try:
        return func(*args)
    except Exception as exc:
        logger.warning("DB fallback for %s: %s", getattr(func, "__name__", "query"), exc)
        return default
