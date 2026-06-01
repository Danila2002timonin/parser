"""Конфигурация сервиса парсера.

Единая точка настройки: пути, подключение к БД, внешние API и прокси.
Значения читаются из переменных окружения / файла .env через pydantic-settings.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Загружаем .env в окружение, чтобы клиенты, читающие os.getenv напрямую
# (LLM/OCR/tenderplan ключи), тоже видели переменные.
load_dotenv()

# Корень репозитория (app/core/config.py -> app -> <root>)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Расширения, которые считаются архивами (для рекурсивной распаковки)
ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z"}

# Расширения документов, которые мы сохраняем
DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".odt", ".ods", ".odp", ".rtf", ".txt", ".csv",
    ".ppt", ".pptx", ".xml", ".html", ".htm",
    ".jpg", ".jpeg", ".png", ".tif", ".tiff",
}


class Settings(BaseSettings):
    """Настройки сервиса парсера."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # PostgreSQL
    database_url: str = "postgresql+psycopg://postgres:1234@localhost:5432/tender_parser"
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_pool_timeout: float = 30.0

    # Данные / хранилище
    data_dir: Path = PROJECT_ROOT / "data"

    # Внешние сервисы
    tenderplan_api_base: str = "https://tenderplan.ru/fileviewer/api/documents"

    # Путь к UnRAR для распаковки .rar архивов
    unrar_tool: str = r"C:\Program Files\UnRAR\UnRAR.exe"


settings = Settings()

# Удобные модульные константы для сервисного слоя.
DATABASE_URL = settings.database_url
DATA_DIR = settings.data_dir
TENDERPLAN_API_BASE = settings.tenderplan_api_base
UNRAR_TOOL = settings.unrar_tool
