"""Дымовые тесты: проверяют, что основные модули импортируются и собираются."""

from __future__ import annotations


def test_app_imports():
    import app.main  # noqa: F401
    import app.cli  # noqa: F401


def test_models_metadata_has_all_tables():
    from app.models import Base

    expected = {
        "tenders", "documents", "passports", "document_maps",
        "api_usage", "pipeline_jobs", "parse_metrics",
    }
    assert expected.issubset(set(Base.metadata.tables))


def test_fastapi_routes_registered():
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/health" in paths
    assert "/ingest/{tender_id}" in paths
    assert "/status/{tender_id}" in paths


def test_repositories_expose_flat_api():
    from app import repositories as db

    for name in ("upsert_tender", "get_tender", "create_pipeline_job", "log_api_usage"):
        assert hasattr(db, name)
