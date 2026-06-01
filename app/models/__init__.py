"""SQLAlchemy ORM-модели сервиса парсера.

Импорт всех моделей здесь гарантирует, что они зарегистрированы в
``Base.metadata`` (нужно для Alembic autogenerate).
"""

from app.db.base import Base

from .api_usage import ApiUsage
from .document import Document
from .document_map import DocumentMap
from .parse_metric import ParseMetric
from .passport import Passport
from .pipeline_job import PipelineJob
from .tender import Tender

__all__ = [
    "Base",
    "Tender",
    "Document",
    "Passport",
    "DocumentMap",
    "ApiUsage",
    "PipelineJob",
    "ParseMetric",
]
