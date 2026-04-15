"""Диспетчер парсеров: направляет файл в нужный парсер по расширению."""

from __future__ import annotations

import logging
from pathlib import Path

from .schema import ParsedDocument

logger = logging.getLogger(__name__)


class UnsupportedFormatError(Exception):
    """Формат файла не поддерживается парсером."""


def parse_document(
    file_path: Path,
    doc_id: str,
    output_dir: Path | None = None,
) -> ParsedDocument:
    """Парсит документ в ParsedDocument, автоматически выбирая парсер.

    Для legacy-форматов (.doc, .xls) сначала конвертирует через LibreOffice,
    затем парсит результат.

    Args:
        file_path: путь к файлу документа.
        doc_id: идентификатор документа.
        output_dir: директория для артефактов парсинга (images/, tables/).

    Returns:
        ParsedDocument.

    Raises:
        UnsupportedFormatError: если формат не поддерживается.
    """
    ext = file_path.suffix.lower()
    logger.info("Роутинг: %s (формат: %s)", file_path.name, ext)

    if ext == ".docx":
        from .docx_parser import parse_docx
        return parse_docx(file_path, doc_id, output_dir)

    if ext == ".pdf":
        from .pdf_parser import parse_pdf
        return parse_pdf(file_path, doc_id, output_dir)

    if ext == ".xlsx":
        from .xlsx_parser import parse_xlsx
        return parse_xlsx(file_path, doc_id, output_dir)

    if ext in (".doc", ".xls", ".odt", ".ods", ".rtf"):
        from .legacy_converter import convert_legacy
        converted = convert_legacy(file_path, output_dir=file_path.parent)
        return parse_document(converted, doc_id, output_dir)

    if ext in (".ppt", ".pptx", ".odp"):
        from .legacy_converter import convert_legacy, FORMAT_MAP
        if ext in (".ppt", ".odp"):
            converted = convert_legacy(file_path, output_dir=file_path.parent)
            file_path = converted
        # PPTX → PDF через LibreOffice, затем парсим как PDF
        FORMAT_MAP[".pptx"] = "pdf"
        converted_pdf = convert_legacy(file_path, output_dir=file_path.parent)
        del FORMAT_MAP[".pptx"]
        return parse_document(converted_pdf, doc_id, output_dir)

    raise UnsupportedFormatError(
        f"Формат {ext} не поддерживается. "
        f"Поддерживаемые: .pdf, .docx, .xlsx, .doc, .xls, .odt, .ods, .odp, .rtf, .ppt, .pptx"
    )
