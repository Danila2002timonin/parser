"""Эндпоинты запуска обработки тендера и загрузки доп. документов."""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, UploadFile, status

from app import repositories as db
from app.core.config import DATA_DIR
from app.schemas.api import JobResponse
from app.services.jobs import run_additional_documents_job, run_ingest_job
from app.services.storage import TenderStorage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["ingest"])


@router.post(
    "/ingest/{tender_id}",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def ingest_tender(
    tender_id: str,
    background_tasks: BackgroundTasks,
    force: bool = Query(False),
    priority: int = Query(0),
    workers: int = Query(10, ge=1, le=50),
) -> JobResponse:
    """Queues full tender preprocessing."""
    job_id = db.create_pipeline_job(tender_id, priority=priority)
    background_tasks.add_task(run_ingest_job, job_id, tender_id, force, workers)
    return JobResponse(tender_id=tender_id, status="queued", job_id=job_id)


@router.post(
    "/tenders/{tender_id}/additional-documents",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def upload_additional_documents(
    tender_id: str,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    priority: int = Query(10),
) -> JobResponse:
    """Queues processing of user-uploaded additional documents."""
    storage = TenderStorage(tender_id, data_dir=DATA_DIR)
    if not storage.has_manifest():
        raise HTTPException(
            status_code=404,
            detail="Tender must be ingested before adding user documents",
        )
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")

    job_id = db.create_pipeline_job(tender_id, priority=priority)
    upload_dir = storage.tender_dir / "uploads" / f"job_{job_id}"
    upload_dir.mkdir(parents=True, exist_ok=True)

    saved_files: list[tuple[str, Path]] = []
    try:
        for idx, upload in enumerate(files, start=1):
            original_filename = upload.filename or "uploaded_document"
            safe_name = _safe_upload_name(original_filename)
            target_path = upload_dir / f"{idx:03d}_{safe_name}"
            with target_path.open("wb") as dst:
                shutil.copyfileobj(upload.file, dst)
            saved_files.append((original_filename, target_path))
    except Exception as exc:
        shutil.rmtree(upload_dir, ignore_errors=True)
        db.mark_job_failed(job_id, str(exc))
        raise HTTPException(status_code=400, detail=f"Upload failed: {exc}") from exc

    background_tasks.add_task(run_additional_documents_job, job_id, tender_id, saved_files)
    return JobResponse(
        tender_id=tender_id,
        status="queued",
        job_id=job_id,
        files_count=len(saved_files),
    )


def _safe_upload_name(filename: str) -> str:
    path = Path(filename)
    stem = re.sub(r"[^A-Za-z0-9А-Яа-я._-]+", "_", path.stem).strip("._")
    suffix = re.sub(r"[^A-Za-z0-9.]+", "", path.suffix.lower())
    safe_stem = stem or "uploaded_document"
    return f"{safe_stem}{suffix}"
