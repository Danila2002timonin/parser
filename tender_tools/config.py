"""Конфигурация путей и настроек проекта."""

from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Корневая директория проекта
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Корневая директория данных (все тендеры хранятся здесь)
DATA_DIR = PROJECT_ROOT / "data"

# API настройки
TENDERPLAN_API_BASE = "https://tenderplan.ru/fileviewer/api/documents"

# Путь к UnRAR для распаковки .rar архивов
UNRAR_TOOL = r"C:\Program Files\UnRAR\UnRAR.exe"

# Расширения, которые считаются архивами (для рекурсивной распаковки)
ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z"}

# Расширения документов, которые мы сохраняем
DOCUMENT_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".odt", ".ods", ".odp", ".rtf", ".txt", ".csv",
    ".ppt", ".pptx", ".xml", ".html", ".htm",
    ".jpg", ".jpeg", ".png", ".tif", ".tiff",
}
