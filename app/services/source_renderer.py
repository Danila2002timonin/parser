"""Source block renderer for document fragment UI."""

from __future__ import annotations

from html import escape
from pathlib import Path

from app.core.config import DATA_DIR
from .parsers.schema import CellObject, ImageBlock, ParsedDocument, TableBlock, TextBlock
from .storage import TenderStorage


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
    blocks = []
    html_parts = []

    for block in parsed.blocks:
        if requested and block.block_id not in requested:
            continue

        rendered = _render_block(block, storage.parsed_dir, parsed.doc_id)
        block_data = block.model_dump(mode="json")
        block_data["rendered_html"] = rendered["html"]
        if rendered.get("image_path"):
            block_data["image_path"] = rendered["image_path"]
        blocks.append(block_data)
        html_parts.append(rendered["html"])

    return {
        "tender_id": tender_id,
        "doc_id": doc_id,
        "source_filename": parsed.source_filename,
        "blocks": blocks,
        "html": "\n".join(html_parts),
    }


def _render_block(block, parsed_dir: Path, doc_id: str) -> dict:
    """Render one parsed block to simple HTML for visual source preview."""
    if isinstance(block, TextBlock):
        tag = f"h{block.heading_level}" if block.heading_level else "p"
        meta = _meta(block)
        return {
            "html": f'<section class="block text-block" data-block-id="{escape(block.block_id)}">{meta}<{tag}>{escape(block.content)}</{tag}></section>'
        }

    if isinstance(block, TableBlock):
        rows_html = []
        for row in block.rows:
            cells = []
            cell_tag = "th" if row.row_type == "header" else "td"
            for cell in row.cells:
                if isinstance(cell, CellObject):
                    attrs = ""
                    if cell.colspan > 1:
                        attrs += f' colspan="{cell.colspan}"'
                    if cell.rowspan > 1:
                        attrs += f' rowspan="{cell.rowspan}"'
                    cells.append(f"<{cell_tag}{attrs}>{escape(cell.value)}</{cell_tag}>")
                else:
                    cells.append(f"<{cell_tag}>{escape(str(cell))}</{cell_tag}>")
            rows_html.append(f'<tr class="{escape(row.row_type)}">' + "".join(cells) + "</tr>")

        page_info = _meta(block)
        html = (
            f'<section class="block table-block" data-block-id="{escape(block.block_id)}">'
            f"{page_info}<table>{''.join(rows_html)}</table></section>"
        )
        return {"html": html}

    if isinstance(block, ImageBlock):
        image_path = None
        img_html = ""
        if block.image_ref:
            img_file = parsed_dir / doc_id / block.image_ref
            image_path = str(img_file)
            if img_file.exists():
                img_html = f'<img src="{escape(str(img_file.as_uri()))}" alt="{escape(block.block_id)}" />'

        ocr_html = ""
        if block.ocr_text:
            ocr_html = f'<pre class="ocr-text">{escape(block.ocr_text)}</pre>'

        html = (
            f'<section class="block image-block" data-block-id="{escape(block.block_id)}">'
            f"{_meta(block)}{img_html}{ocr_html}</section>"
        )
        return {"html": html, "image_path": image_path}

    return {"html": f"<pre>{escape(str(block))}</pre>"}


def _meta(block) -> str:
    page = getattr(block, "page", None)
    page_end = getattr(block, "page_end", None)
    section = getattr(block, "section_path", None)
    parts = [f"block: {getattr(block, 'block_id', '')}"]
    if page:
        parts.append(f"page: {page}" + (f"-{page_end}" if page_end and page_end != page else ""))
    if section:
        parts.append(f"section: {section}")
    return '<div class="block-meta">' + " | ".join(escape(p) for p in parts) + "</div>"
