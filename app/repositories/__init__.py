"""Слой доступа к данным (репозитории) поверх SQLAlchemy.

Функции сгруппированы по сущностям и реэкспортируются здесь, чтобы вызывающий
код мог использовать единый плоский интерфейс (``repositories.upsert_tender`` и т.д.).
"""

from .api_usage import log_api_usage
from .document_maps import get_document_map, upsert_document_map
from .documents import (
    list_documents,
    update_document_parse_failed,
    update_document_parsed,
    upsert_document,
)
from .jobs import (
    create_pipeline_job,
    get_latest_job,
    mark_job_completed,
    mark_job_failed,
    mark_job_running,
)
from .parse_metrics import log_parse_metric
from .passports import list_passports, upsert_passport
from .tenders import finalize_tender, get_tender, update_tender_status, upsert_tender

__all__ = [
    # tenders
    "upsert_tender",
    "finalize_tender",
    "update_tender_status",
    "get_tender",
    # documents
    "upsert_document",
    "update_document_parsed",
    "update_document_parse_failed",
    "list_documents",
    # passports
    "upsert_passport",
    "list_passports",
    # document_maps
    "upsert_document_map",
    "get_document_map",
    # api_usage
    "log_api_usage",
    # parse_metrics
    "log_parse_metric",
    # jobs
    "create_pipeline_job",
    "mark_job_running",
    "mark_job_completed",
    "mark_job_failed",
    "get_latest_job",
]
