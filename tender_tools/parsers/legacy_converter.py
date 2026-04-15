"""Конвертация legacy и альтернативных форматов через LibreOffice headless.

Поддерживаемые конвертации:
  .doc  → .docx     .odt → .docx     .rtf → .docx
  .xls  → .xlsx     .ods → .xlsx
  .ppt  → .pptx     .pptx → .pdf (для парсинга через pdf_parser)

Требует установленного LibreOffice.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

FORMAT_MAP = {
    ".doc": "docx",
    ".xls": "xlsx",
    ".odt": "docx",
    ".ods": "xlsx",
    ".odp": "pptx",
    ".rtf": "docx",
    ".ppt": "pptx",
}

_DEFAULT_PATHS = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]


class ConversionError(Exception):
    """Ошибка конвертации legacy-формата."""


def _find_soffice() -> str:
    """Находит путь к soffice.exe."""
    env_path = os.environ.get("LIBREOFFICE_PATH")
    if env_path and Path(env_path).exists():
        return env_path
    for p in _DEFAULT_PATHS:
        if Path(p).exists():
            return p
    found = shutil.which("soffice")
    if found:
        return found
    raise ConversionError(
        "LibreOffice не найден. Установите его или задайте LIBREOFFICE_PATH."
    )


def convert_legacy(src_path: Path, output_dir: Path | None = None) -> Path:
    """Конвертирует .doc → .docx или .xls → .xlsx.

    Args:
        src_path: путь к файлу .doc или .xls.
        output_dir: куда сохранить результат (по умолчанию — рядом с оригиналом).

    Returns:
        Путь к сконвертированному файлу.

    Raises:
        ConversionError: если конвертация не удалась.
    """
    ext = src_path.suffix.lower()
    target_fmt = FORMAT_MAP.get(ext)
    if not target_fmt:
        raise ConversionError(f"Неподдерживаемый формат: {ext}")

    soffice = _find_soffice()
    out_dir = output_dir or src_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    tmp_profile = tempfile.mkdtemp(prefix="lo_profile_")

    try:
        cmd = [
            soffice,
            "--headless",
            "--norestore",
            "--nolockcheck",
            f"-env:UserInstallation=file:///{tmp_profile.replace(os.sep, '/')}",
            "--convert-to", target_fmt,
            "--outdir", str(out_dir),
            str(src_path),
        ]

        logger.debug("LibreOffice: %s", " ".join(cmd))
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120,
        )

        new_path = out_dir / (src_path.stem + "." + target_fmt)

        if result.returncode == 0 and new_path.exists():
            logger.info("Конвертирован: %s → %s", src_path.name, new_path.name)
            return new_path

        raise ConversionError(
            f"Не удалось конвертировать {src_path.name}: "
            f"returncode={result.returncode}, stderr={result.stderr[:200]}"
        )

    except subprocess.TimeoutExpired as exc:
        raise ConversionError(f"Таймаут при конвертации {src_path.name}") from exc
    finally:
        shutil.rmtree(tmp_profile, ignore_errors=True)
