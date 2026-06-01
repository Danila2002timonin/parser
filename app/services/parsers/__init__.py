"""app.services.parsers — парсинг тендерной документации в унифицированную JSON-схему."""

from .router import UnsupportedFormatError, parse_document
from .schema import (
    CellObject,
    ContentBlock,
    ImageBlock,
    ParsedDocument,
    TableBlock,
    TableCell,
    TableRow,
    TextBlock,
)

__all__ = [
    "parse_document",
    "UnsupportedFormatError",
    "ParsedDocument",
    "TextBlock",
    "TableBlock",
    "ImageBlock",
    "TableRow",
    "TableCell",
    "CellObject",
    "ContentBlock",
]
