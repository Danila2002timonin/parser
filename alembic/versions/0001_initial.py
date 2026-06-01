"""Initial schema for Tender Parser Service

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-29

Соответствует прежним SQL-миграциям 001_initial.sql и 002_add_total_cost.sql.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----------------------------------------------------------------- tenders
    op.create_table(
        "tenders",
        sa.Column("tender_id", sa.Text(), primary_key=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="created"),
        sa.Column("source_url", sa.Text()),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("downloaded_at", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("extracted_at", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("parsed_at", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("ocr_done_at", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("passports_done_at", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("indexed_at", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("document_count", sa.Integer(), server_default="0"),
        sa.Column("total_size_bytes", postgresql.BIGINT(), server_default="0"),
        sa.Column("error_message", sa.Text()),
        sa.Column("retry_count", sa.Integer(), server_default="0"),
        sa.Column("archive_s3_path", sa.Text()),
        sa.Column("total_cost_usd", sa.Numeric(10, 6)),
        sa.Column("pipeline_duration_ms", sa.Integer()),
        sa.CheckConstraint(
            "status IN ('created', 'downloaded', 'extracted', 'parsed', "
            "'ocr_done', 'passports_done', 'indexed', 'failed')",
            name="tenders_status_check",
        ),
    )
    op.create_index("idx_tenders_status", "tenders", ["status"])
    op.create_index("idx_tenders_created", "tenders", ["created_at"])

    # --------------------------------------------------------------- documents
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tender_id", sa.Text(), nullable=False),
        sa.Column("doc_id", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("stored_filename", sa.Text(), nullable=False),
        sa.Column("extension", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("source_archive", sa.Text()),
        sa.Column("archive_path", sa.Text()),
        sa.Column("parse_status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("ocr_status", sa.Text(), nullable=False, server_default="not_needed"),
        sa.Column("raw_file_s3_path", sa.Text()),
        sa.Column("parsed_json_s3_path", sa.Text()),
        sa.Column("text_blocks_count", sa.Integer()),
        sa.Column("tables_count", sa.Integer()),
        sa.Column("images_count", sa.Integer()),
        sa.Column("total_pages", sa.Integer()),
        sa.Column("estimated_tokens", sa.Integer()),
        sa.Column("extracted_at", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("parsed_at", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("parse_duration_ms", sa.Integer()),
        sa.Column("conversion_duration_ms", sa.Integer()),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.tender_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("tender_id", "doc_id", name="documents_tender_id_doc_id_key"),
        sa.CheckConstraint(
            "parse_status IN ('pending', 'parsed', 'failed', 'unsupported')",
            name="documents_parse_status_check",
        ),
        sa.CheckConstraint(
            "ocr_status IN ('not_needed', 'pending', 'completed', 'failed')",
            name="documents_ocr_status_check",
        ),
    )
    op.create_index("idx_documents_tender", "documents", ["tender_id"])
    op.create_index("idx_documents_parse_status", "documents", ["parse_status"])
    op.create_index("idx_documents_extension", "documents", ["extension"])

    # --------------------------------------------------------------- passports
    op.create_table(
        "passports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tender_id", sa.Text(), nullable=False),
        sa.Column("doc_id", sa.Text(), nullable=False),
        sa.Column("doc_type", sa.Text()),
        sa.Column("title", sa.Text()),
        sa.Column("summary", sa.Text()),
        sa.Column("passport_data", postgresql.JSONB(), nullable=False),
        sa.Column("model_used", sa.Text()),
        sa.Column("prompt_tokens", sa.Integer()),
        sa.Column("completion_tokens", sa.Integer()),
        sa.Column("generation_cost_usd", sa.Numeric(10, 6)),
        sa.Column("generation_duration_ms", sa.Integer()),
        sa.Column("generated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tender_id", "doc_id", name="passports_tender_id_doc_id_key"),
        sa.ForeignKeyConstraint(
            ["tender_id", "doc_id"],
            ["documents.tender_id", "documents.doc_id"],
            ondelete="CASCADE",
            name="passports_tender_id_doc_id_fkey",
        ),
    )
    op.create_index("idx_passports_tender", "passports", ["tender_id"])
    op.create_index("idx_passports_doc_type", "passports", ["doc_type"])
    op.create_index("idx_passports_gin", "passports", ["passport_data"], postgresql_using="gin")

    # ------------------------------------------------------------ document_maps
    op.create_table(
        "document_maps",
        sa.Column("tender_id", sa.Text(), primary_key=True),
        sa.Column("map_data", postgresql.JSONB(), nullable=False),
        sa.Column("routing_text", sa.Text()),
        sa.Column("passports_count", sa.Integer()),
        sa.Column("estimated_tokens", sa.Integer()),
        sa.Column("generated_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.tender_id"], ondelete="CASCADE"),
    )

    # --------------------------------------------------------------- api_usage
    op.create_table(
        "api_usage",
        sa.Column("id", postgresql.BIGINT(), primary_key=True),
        sa.Column("timestamp", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("tender_id", sa.Text()),
        sa.Column("doc_id", sa.Text()),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer()),
        sa.Column("completion_tokens", sa.Integer()),
        sa.Column("total_tokens", sa.Integer()),
        sa.Column("ocr_pages_count", sa.Integer()),
        sa.Column("ocr_doc_size_bytes", sa.Integer()),
        sa.Column("cost_usd", sa.Numeric(10, 6)),
        sa.Column("duration_ms", sa.Integer()),
        sa.Column("status", sa.Text(), nullable=False, server_default="success"),
        sa.Column("error_message", sa.Text()),
        sa.Column("http_status_code", sa.Integer()),
        sa.CheckConstraint(
            "status IN ('success', 'error', 'timeout', 'rate_limited')",
            name="api_usage_status_check",
        ),
    )
    op.create_index("idx_api_usage_timestamp", "api_usage", ["timestamp"])
    op.create_index("idx_api_usage_service_action", "api_usage", ["service", "action"])
    op.create_index("idx_api_usage_tender", "api_usage", ["tender_id"])
    op.create_index("idx_api_usage_provider_model", "api_usage", ["provider", "model"])
    op.create_index(
        "idx_api_usage_errors", "api_usage", ["status"],
        postgresql_where=sa.text("status != 'success'"),
    )

    # ----------------------------------------------------------- pipeline_jobs
    op.create_table(
        "pipeline_jobs",
        sa.Column("id", postgresql.BIGINT(), primary_key=True),
        sa.Column("tender_id", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.Text(), nullable=False, server_default="queued"),
        sa.Column("worker_id", sa.Text()),
        sa.Column("queued_at", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("error_message", sa.Text()),
        sa.Column("retry_count", sa.Integer(), server_default="0"),
        sa.Column("max_retries", sa.Integer(), server_default="3"),
        sa.ForeignKeyConstraint(["tender_id"], ["tenders.tender_id"]),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'completed', 'failed', 'cancelled')",
            name="pipeline_jobs_status_check",
        ),
    )
    op.create_index(
        "idx_jobs_queue", "pipeline_jobs",
        ["status", sa.text("priority DESC"), "queued_at"],
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index("idx_jobs_tender", "pipeline_jobs", ["tender_id"])
    op.create_index(
        "idx_jobs_running", "pipeline_jobs", ["worker_id"],
        postgresql_where=sa.text("status = 'running'"),
    )

    # ----------------------------------------------------------- parse_metrics
    op.create_table(
        "parse_metrics",
        sa.Column("id", postgresql.BIGINT(), primary_key=True),
        sa.Column("timestamp", postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("tender_id", sa.Text(), nullable=False),
        sa.Column("doc_id", sa.Text()),
        sa.Column("stage", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("input_size_bytes", sa.Integer()),
        sa.Column("output_size_bytes", sa.Integer()),
        sa.Column("items_count", sa.Integer()),
        sa.Column("status", sa.Text(), nullable=False, server_default="success"),
        sa.Column("error_message", sa.Text()),
        sa.CheckConstraint(
            "stage IN ('download', 'extract', 'convert', "
            "'parse_docx', 'parse_pdf', 'parse_xlsx', "
            "'ocr', 'passport', 'index', 'full_pipeline')",
            name="parse_metrics_stage_check",
        ),
        sa.CheckConstraint(
            "status IN ('success', 'error')",
            name="parse_metrics_status_check",
        ),
    )
    op.create_index("idx_parse_metrics_tender", "parse_metrics", ["tender_id"])
    op.create_index("idx_parse_metrics_stage", "parse_metrics", ["stage"])
    op.create_index("idx_parse_metrics_timestamp", "parse_metrics", ["timestamp"])

    # ------------------------------------------------------ аналитические VIEW
    op.execute(
        """
        CREATE OR REPLACE VIEW v_tender_costs AS
        SELECT
            tender_id,
            COUNT(*) AS api_calls,
            SUM(total_tokens) AS total_tokens,
            SUM(cost_usd) AS total_cost_usd,
            SUM(duration_ms) AS total_duration_ms,
            SUM(CASE WHEN action = 'passport' THEN cost_usd ELSE 0 END) AS passport_cost,
            SUM(CASE WHEN action = 'ocr' THEN cost_usd ELSE 0 END) AS ocr_cost,
            SUM(CASE WHEN action = 'routing' THEN cost_usd ELSE 0 END) AS routing_cost,
            SUM(CASE WHEN action = 'answer' THEN cost_usd ELSE 0 END) AS answer_cost
        FROM api_usage
        GROUP BY tender_id;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW v_parse_speed AS
        SELECT
            stage,
            COUNT(*) AS total_ops,
            ROUND(AVG(duration_ms)) AS avg_ms,
            ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms)) AS p95_ms,
            MAX(duration_ms) AS max_ms,
            SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors
        FROM parse_metrics
        GROUP BY stage;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE VIEW v_recent_errors AS
        SELECT
            timestamp, service, action, provider, model, status,
            http_status_code, error_message, tender_id, doc_id
        FROM api_usage
        WHERE status != 'success'
          AND timestamp > now() - INTERVAL '24 hours'
        ORDER BY timestamp DESC;
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS v_recent_errors")
    op.execute("DROP VIEW IF EXISTS v_parse_speed")
    op.execute("DROP VIEW IF EXISTS v_tender_costs")
    op.drop_table("parse_metrics")
    op.drop_table("pipeline_jobs")
    op.drop_table("api_usage")
    op.drop_table("document_maps")
    op.drop_table("passports")
    op.drop_table("documents")
    op.drop_table("tenders")
