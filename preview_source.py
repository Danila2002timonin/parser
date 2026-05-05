"""Простой HTML preview для source fragments.

Использование:
    python preview_source.py <tender_id> <doc_id>
    python preview_source.py <tender_id> <doc_id> --blocks doc_001_b001,doc_001_tbl_002
    python preview_source.py <tender_id> <doc_id> --output preview.html

Открывает/сохраняет HTML, который показывает как frontend может отрендерить
source fragments из parsed JSON: текст, таблицы, изображения и OCR-текст.
"""

from __future__ import annotations

import argparse
import webbrowser
from pathlib import Path

from parser_service.source_renderer import render_source_blocks


HTML_TEMPLATE = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <title>Source Preview: {tender_id}/{doc_id}</title>
  <style>
    body {{
      font-family: Arial, sans-serif;
      margin: 24px;
      background: #f6f7f9;
      color: #20242a;
    }}
    h1 {{ margin-bottom: 6px; }}
    .subtitle {{ color: #667085; margin-bottom: 24px; }}
    .block {{
      background: #fff;
      border: 1px solid #d0d5dd;
      border-radius: 8px;
      padding: 16px;
      margin-bottom: 16px;
      overflow-x: auto;
    }}
    .block-meta {{
      font-size: 12px;
      color: #667085;
      margin-bottom: 10px;
    }}
    table {{
      border-collapse: collapse;
      width: 100%;
      font-size: 13px;
    }}
    th, td {{
      border: 1px solid #d0d5dd;
      padding: 6px 8px;
      vertical-align: top;
    }}
    th, tr.header td {{
      background: #eef4ff;
      font-weight: 600;
    }}
    tr.section td {{
      background: #f2f4f7;
      font-weight: 600;
    }}
    img {{
      max-width: 100%;
      border: 1px solid #d0d5dd;
      border-radius: 4px;
      margin: 8px 0 16px;
    }}
    .ocr-text {{
      white-space: pre-wrap;
      background: #f9fafb;
      border: 1px dashed #d0d5dd;
      padding: 12px;
      border-radius: 6px;
    }}
  </style>
</head>
<body>
  <h1>Source Preview</h1>
  <div class="subtitle">
    tender_id: <b>{tender_id}</b> | doc_id: <b>{doc_id}</b> | file: <b>{source_filename}</b>
  </div>
  {body}
</body>
</html>
"""


def parse_blocks(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Render parsed source blocks as HTML")
    parser.add_argument("tender_id")
    parser.add_argument("doc_id")
    parser.add_argument("--blocks", default=None, help="Comma-separated block IDs")
    parser.add_argument("--output", "-o", default="source_preview.html")
    parser.add_argument("--no-open", action="store_true", help="Do not open browser")
    args = parser.parse_args()

    data = render_source_blocks(args.tender_id, args.doc_id, parse_blocks(args.blocks))
    html = HTML_TEMPLATE.format(
        tender_id=data["tender_id"],
        doc_id=data["doc_id"],
        source_filename=data.get("source_filename", ""),
        body=data.get("html", ""),
    )

    out_path = Path(args.output).resolve()
    out_path.write_text(html, encoding="utf-8")
    print(f"Preview saved: {out_path}")

    if not args.no_open:
        webbrowser.open(out_path.as_uri())


if __name__ == "__main__":
    main()
