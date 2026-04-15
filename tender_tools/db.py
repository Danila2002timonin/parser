"""Модуль работы с PostgreSQL.

Предоставляет connection pool и CRUD-операции для всех таблиц.
Работает параллельно с файловым хранилищем (дуальный режим).
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal

from psycopg_pool import ConnectionPool
import psycopg

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None


def _get_db_url() -> str:
    from dotenv import load_dotenv
    load_dotenv()
    return os.getenv("DATABASE_URL", "postgresql://postgres:1234@localhost:5432/tender_parser")


def init_db(database_url: str | None = None, min_size: int = 2, max_size: int = 10) -> None:
    """Инициализирует connection pool."""
    global _pool
    if _pool is not None:
        return

    url = database_url or _get_db_url()
    _pool = ConnectionPool(url, min_size=min_size, max_size=max_size)
    logger.info("PostgreSQL pool инициализирован: %s", url.split("@")[-1])


def close_db() -> None:
    """Закрывает connection pool."""
    global _pool
    if _pool:
        _pool.close()
        _pool = None


@contextmanager
def get_conn():
    """Context manager для получения соединения из pool."""
    if _pool is None:
        init_db()
    with _pool.connection() as conn:
        yield conn


# ---------------------------------------------------------------------------
# tenders
# ---------------------------------------------------------------------------

def upsert_tender(
    tender_id: str,
    status: str = "created",
    source_url: str | None = None,
    document_count: int | None = None,
    total_size_bytes: int | None = None,
    archive_s3_path: str | None = None,
    error_message: str | None = None,
    **timestamps,
) -> None:
    """Создаёт или обновляет запись тендера."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO tenders (tender_id, status, source_url, document_count,
                                     total_size_bytes, archive_s3_path, error_message)
                VALUES (%(tender_id)s, %(status)s, %(source_url)s, %(document_count)s,
                        %(total_size_bytes)s, %(archive_s3_path)s, %(error_message)s)
                ON CONFLICT (tender_id) DO UPDATE SET
                    status = COALESCE(EXCLUDED.status, tenders.status),
                    source_url = COALESCE(EXCLUDED.source_url, tenders.source_url),
                    document_count = COALESCE(EXCLUDED.document_count, tenders.document_count),
                    total_size_bytes = COALESCE(EXCLUDED.total_size_bytes, tenders.total_size_bytes),
                    archive_s3_path = COALESCE(EXCLUDED.archive_s3_path, tenders.archive_s3_path),
                    error_message = EXCLUDED.error_message,
                    updated_at = now()
            """, {
                "tender_id": tender_id,
                "status": status,
                "source_url": source_url,
                "document_count": document_count,
                "total_size_bytes": total_size_bytes,
                "archive_s3_path": archive_s3_path,
                "error_message": error_message,
            })


def finalize_tender(tender_id: str, pipeline_duration_ms: int) -> None:
    """Записывает финальную стоимость и длительность pipeline."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE tenders SET
                    total_cost_usd = (
                        SELECT COALESCE(SUM(cost_usd), 0) FROM api_usage WHERE tender_id = %s
                    ),
                    pipeline_duration_ms = %s,
                    updated_at = now()
                WHERE tender_id = %s
            """, (tender_id, pipeline_duration_ms, tender_id))


def update_tender_status(tender_id: str, status: str) -> None:
    """Обновляет статус тендера и соответствующий timestamp."""
    timestamp_col = {
        "downloaded": "downloaded_at",
        "extracted": "extracted_at",
        "parsed": "parsed_at",
        "ocr_done": "ocr_done_at",
        "passports_done": "passports_done_at",
        "indexed": "indexed_at",
    }.get(status)

    with get_conn() as conn:
        with conn.cursor() as cur:
            if timestamp_col:
                cur.execute(f"""
                    UPDATE tenders
                    SET status = %s, {timestamp_col} = now(), updated_at = now()
                    WHERE tender_id = %s
                """, (status, tender_id))
            else:
                cur.execute("""
                    UPDATE tenders SET status = %s, updated_at = now()
                    WHERE tender_id = %s
                """, (status, tender_id))


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------

def upsert_document(
    tender_id: str,
    doc_id: str,
    original_filename: str,
    stored_filename: str,
    extension: str,
    size_bytes: int,
    source_archive: str | None = None,
    archive_path: str | None = None,
) -> None:
    """Создаёт или обновляет запись документа."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO documents (tender_id, doc_id, original_filename, stored_filename,
                                       extension, size_bytes, source_archive, archive_path, extracted_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (tender_id, doc_id) DO UPDATE SET
                    original_filename = EXCLUDED.original_filename,
                    stored_filename = EXCLUDED.stored_filename,
                    extension = EXCLUDED.extension,
                    size_bytes = EXCLUDED.size_bytes,
                    source_archive = EXCLUDED.source_archive,
                    archive_path = EXCLUDED.archive_path,
                    extracted_at = now()
            """, (tender_id, doc_id, original_filename, stored_filename,
                  extension, size_bytes, source_archive, archive_path))


def update_document_parsed(
    tender_id: str,
    doc_id: str,
    text_blocks_count: int,
    tables_count: int,
    images_count: int,
    total_pages: int | None,
    estimated_tokens: int,
    parse_duration_ms: int,
    conversion_duration_ms: int | None = None,
) -> None:
    """Обновляет запись документа после парсинга."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE documents SET
                    parse_status = 'parsed',
                    text_blocks_count = %s,
                    tables_count = %s,
                    images_count = %s,
                    total_pages = %s,
                    estimated_tokens = %s,
                    parse_duration_ms = %s,
                    conversion_duration_ms = %s,
                    parsed_at = now()
                WHERE tender_id = %s AND doc_id = %s
            """, (text_blocks_count, tables_count, images_count, total_pages,
                  estimated_tokens, parse_duration_ms, conversion_duration_ms,
                  tender_id, doc_id))


def update_document_parse_failed(tender_id: str, doc_id: str, status: str = "failed") -> None:
    """Помечает документ как failed/unsupported."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE documents SET parse_status = %s WHERE tender_id = %s AND doc_id = %s
            """, (status, tender_id, doc_id))


# ---------------------------------------------------------------------------
# passports
# ---------------------------------------------------------------------------

def upsert_passport(
    tender_id: str,
    doc_id: str,
    passport_data: dict,
    model_used: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    generation_cost_usd: float | None = None,
    generation_duration_ms: int | None = None,
) -> None:
    """Сохраняет паспорт документа."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO passports (tender_id, doc_id, doc_type, title, summary,
                                       passport_data, model_used, prompt_tokens,
                                       completion_tokens, generation_cost_usd,
                                       generation_duration_ms)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tender_id, doc_id) DO UPDATE SET
                    doc_type = EXCLUDED.doc_type,
                    title = EXCLUDED.title,
                    summary = EXCLUDED.summary,
                    passport_data = EXCLUDED.passport_data,
                    model_used = EXCLUDED.model_used,
                    prompt_tokens = EXCLUDED.prompt_tokens,
                    completion_tokens = EXCLUDED.completion_tokens,
                    generation_cost_usd = EXCLUDED.generation_cost_usd,
                    generation_duration_ms = EXCLUDED.generation_duration_ms,
                    generated_at = now()
            """, (tender_id, doc_id,
                  passport_data.get("doc_type"),
                  passport_data.get("title"),
                  passport_data.get("summary"),
                  json.dumps(passport_data, ensure_ascii=False),
                  model_used, prompt_tokens, completion_tokens,
                  generation_cost_usd, generation_duration_ms))


def upsert_document_map(
    tender_id: str,
    map_data: dict,
    routing_text: str,
    passports_count: int,
    estimated_tokens: int,
) -> None:
    """Сохраняет карту документации тендера."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO document_maps (tender_id, map_data, routing_text,
                                           passports_count, estimated_tokens)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (tender_id) DO UPDATE SET
                    map_data = EXCLUDED.map_data,
                    routing_text = EXCLUDED.routing_text,
                    passports_count = EXCLUDED.passports_count,
                    estimated_tokens = EXCLUDED.estimated_tokens,
                    generated_at = now()
            """, (tender_id,
                  json.dumps(map_data, ensure_ascii=False),
                  routing_text, passports_count, estimated_tokens))


# ---------------------------------------------------------------------------
# api_usage
# ---------------------------------------------------------------------------

def log_api_usage(
    service: str,
    action: str,
    provider: str,
    model: str,
    tender_id: str | None = None,
    doc_id: str | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    ocr_pages_count: int | None = None,
    ocr_doc_size_bytes: int | None = None,
    cost_usd: float | None = None,
    duration_ms: int | None = None,
    status: str = "success",
    error_message: str | None = None,
    http_status_code: int | None = None,
) -> None:
    """Логирует один API-вызов."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO api_usage (service, action, provider, model,
                                           tender_id, doc_id,
                                           prompt_tokens, completion_tokens, total_tokens,
                                           ocr_pages_count, ocr_doc_size_bytes,
                                           cost_usd, duration_ms,
                                           status, error_message, http_status_code)
                    VALUES (%s,%s,%s,%s, %s,%s, %s,%s,%s, %s,%s, %s,%s, %s,%s,%s)
                """, (service, action, provider, model,
                      tender_id, doc_id,
                      prompt_tokens, completion_tokens, total_tokens,
                      ocr_pages_count, ocr_doc_size_bytes,
                      cost_usd, duration_ms,
                      status, error_message, http_status_code))
    except Exception as exc:
        logger.warning("Не удалось записать api_usage: %s", exc)


# ---------------------------------------------------------------------------
# parse_metrics
# ---------------------------------------------------------------------------

def log_parse_metric(
    tender_id: str,
    stage: str,
    duration_ms: int,
    doc_id: str | None = None,
    input_size_bytes: int | None = None,
    output_size_bytes: int | None = None,
    items_count: int | None = None,
    status: str = "success",
    error_message: str | None = None,
) -> None:
    """Логирует метрику скорости парсинга."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO parse_metrics (tender_id, doc_id, stage, duration_ms,
                                               input_size_bytes, output_size_bytes,
                                               items_count, status, error_message)
                    VALUES (%s,%s,%s,%s, %s,%s,%s, %s,%s)
                """, (tender_id, doc_id, stage, duration_ms,
                      input_size_bytes, output_size_bytes,
                      items_count, status, error_message))
    except Exception as exc:
        logger.warning("Не удалось записать parse_metric: %s", exc)
