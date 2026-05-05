"""Source block renderer for document fragment UI."""

from __future__ import annotations

from pathlib import Path

from tender_tools.config import DATA_DIR
from tender_tools.parsers.schema import ParsedDocument
from tender_tools.storage import TenderStorage


def render_source_blocks(
    tender_id: str,
    doc_id: str,
    block_ids: list[str] | None = None,
    data_dir: Path = DATA_DIR,
) -> dict:
    """Returns structured parsed blocks for UI source previews."""
    storage = TenderStorage(tender_id, data_dir=data_dir)
    parsed_path = storage.parsed_dir / f"{doc_id}.json"
    if not parsed_path.exists():
        raise FileNotFoundError(f"Parsed document not found: {parsed_path}")

    parsed = ParsedDocument.load(parsed_path)
    requested = set(block_ids or [])
    blocks = [
        block.model_dump(mode="json")
        for block in parsed.blocks
        if not requested or block.block_id in requested
    ]

    return {
        "tender_id": tender_id,
        "doc_id": doc_id,
        "source_filename": parsed.source_filename,
        "blocks": blocks,
    }
