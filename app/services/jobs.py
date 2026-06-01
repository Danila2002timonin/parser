"""Background job runners for Parser Service."""

from __future__ import annotations

import logging
import shutil
import socket
from pathlib import Path

from app import repositories as db
from .additional_documents import AdditionalDocumentProcessor
from .pipeline import TenderPipeline

logger = logging.getLogger(__name__)


def run_ingest_job(
    job_id: int,
    tender_id: str,
    force: bool = False,
    workers: int = 10,
) -> None:
    """Runs the full tender ingest pipeline and updates pipeline_jobs."""
    worker_id = f"api:{socket.gethostname()}"
    db.mark_job_running(job_id, worker_id=worker_id)
    try:
        pipeline = TenderPipeline(passport_workers=workers)
        pipeline.ingest(tender_id, force=force)
        db.update_tender_status(tender_id, "indexed")
        db.mark_job_completed(job_id)
    except Exception as exc:
        logger.exception("Ingest job %s failed for tender %s", job_id, tender_id)
        db.upsert_tender(tender_id, status="failed", error_message=str(exc)[:2000])
        db.mark_job_failed(job_id, str(exc))


def run_additional_documents_job(
    job_id: int,
    tender_id: str,
    uploaded_files: list[tuple[str, Path]],
) -> None:
    """Adds uploaded user documents to an existing tender."""
    worker_id = f"api:{socket.gethostname()}"
    db.mark_job_running(job_id, worker_id=worker_id)
    temp_dirs = {path.parent for _, path in uploaded_files}
    try:
        processor = AdditionalDocumentProcessor()
        processor.add_files(tender_id, uploaded_files)
        db.mark_job_completed(job_id)
    except Exception as exc:
        logger.exception("Additional documents job %s failed for tender %s", job_id, tender_id)
        db.upsert_tender(tender_id, status="failed", error_message=str(exc)[:2000])
        db.mark_job_failed(job_id, str(exc))
    finally:
        for temp_dir in temp_dirs:
            shutil.rmtree(temp_dir, ignore_errors=True)
