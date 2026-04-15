"""OCR-процессор: распознавание текста из изображений и сканов через Mistral OCR.

Обходит ImageBlock'и с ocr_status="pending" в ParsedDocument,
отправляет изображения в Mistral OCR API, обновляет ocr_text и ocr_status.

Также обрабатывает PDF-сканы: извлекает страницы как изображения
через pypdfium2, затем распознаёт.
"""

from __future__ import annotations

import base64
import logging
import os
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import db
from .llm_client import calculate_ocr_cost
from .parsers.schema import ImageBlock, ParsedDocument

logger = logging.getLogger(__name__)


class OCRError(Exception):
    """Ошибка при OCR-обработке."""


class MistralOCRProcessor:
    """Распознавание текста через Mistral OCR API."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "mistral-ocr-latest",
        max_workers: int = 5,
    ):
        self.api_key = api_key or os.getenv("MISTRAL_API_KEY", "")
        if not self.api_key:
            raise OCRError(
                "Mistral API-ключ не задан. Установите MISTRAL_API_KEY в .env"
            )
        self.model = model
        self.max_workers = max_workers

        from mistralai.client import Mistral
        self._client = Mistral(api_key=self.api_key)

    def process_document(
        self,
        parsed: ParsedDocument,
        parsed_dir: Path,
        source_pdf_path: Path | None = None,
        tender_id: str | None = None,
    ) -> int:
        """Обрабатывает все pending ImageBlock'и в документе.

        Args:
            parsed: распарсенный документ.
            parsed_dir: корневая директория parsed/ (для резолва image_ref).
            source_pdf_path: путь к оригинальному PDF (для извлечения страниц-сканов).

        Returns:
            Количество обработанных изображений.
        """
        pending_blocks = [
            b for b in parsed.blocks
            if isinstance(b, ImageBlock) and b.ocr_status == "pending"
        ]

        if not pending_blocks:
            return 0

        logger.info(
            "OCR: %s — %d изображений для распознавания",
            parsed.doc_id,
            len(pending_blocks),
        )

        # Для PDF-сканов: если все pending — это страницы-сканы одного PDF,
        # отправляем весь PDF за один запрос (эффективнее)
        scan_blocks = [b for b in pending_blocks if b.page and not b.image_ref]
        image_blocks = [b for b in pending_blocks if b.image_ref]

        processed = 0

        _tid = tender_id or parsed.doc_id

        if scan_blocks and source_pdf_path and source_pdf_path.exists():
            processed += self._ocr_pdf_batch(scan_blocks, source_pdf_path, _tid)

        # Отдельные изображения (из DOCX и т.д.) — параллельно
        if image_blocks:
            def _process_one(block: ImageBlock) -> bool:
                try:
                    img_path = parsed_dir / parsed.doc_id / block.image_ref
                    if not img_path.exists():
                        block.ocr_status = "skipped"
                        return False

                    t0 = _time.time()
                    text = self._ocr_image(img_path)
                    duration_ms = int((_time.time() - t0) * 1000)

                    block.ocr_text = text
                    block.ocr_status = "completed"

                    cost = calculate_ocr_cost(self.model, 1)
                    db.log_api_usage(
                        service="parser", action="ocr", provider="mistral",
                        model=self.model, tender_id=_tid, doc_id=block.block_id,
                        ocr_pages_count=1,
                        ocr_doc_size_bytes=img_path.stat().st_size,
                        cost_usd=cost, duration_ms=duration_ms, status="success",
                    )

                    logger.info("  %s: распознано %d символов (%dms, $%.4f)",
                                block.block_id, len(text), duration_ms, cost or 0)
                    return True
                except Exception as exc:
                    logger.error("  %s: ошибка OCR: %s", block.block_id, exc)
                    db.log_api_usage(
                        service="parser", action="ocr", provider="mistral",
                        model=self.model, status="error", error_message=str(exc)[:200],
                    )
                    block.ocr_status = "skipped"
                    return False

            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                futures = {pool.submit(_process_one, b): b for b in image_blocks}
                for future in as_completed(futures):
                    if future.result():
                        processed += 1

        logger.info("OCR завершён: %d/%d обработано", processed, len(pending_blocks))
        return processed

    def _ocr_pdf_batch(self, scan_blocks: list[ImageBlock], pdf_path: Path, doc_id: str = "") -> int:
        """Отправляет весь PDF в Mistral OCR за один запрос.

        Результаты распределяются по ImageBlock'ам на основе номеров страниц.
        """
        try:
            logger.info("  OCR PDF целиком: %s (%d страниц-сканов)", pdf_path.name, len(scan_blocks))
            file_data = base64.b64encode(pdf_path.read_bytes()).decode("utf-8")

            t0 = _time.time()
            response = self._client.ocr.process(
                model=self.model,
                document={
                    "type": "document_url",
                    "document_url": f"data:application/pdf;base64,{file_data}",
                },
                include_image_base64=True,
            )
            duration_ms = int((_time.time() - t0) * 1000)

            pages_count = len(response.pages)
            cost = calculate_ocr_cost(self.model, pages_count)

            # Маппинг: page_num → block
            page_to_block = {b.page: b for b in scan_blocks}
            processed = 0

            for page in response.pages:
                page_num = page.index + 1
                if page_num in page_to_block:
                    block = page_to_block[page_num]
                    block.ocr_text = page.markdown or ""
                    block.ocr_status = "completed"
                    logger.info("  %s (стр. %d): распознано %d символов",
                                block.block_id, page_num, len(block.ocr_text))
                    processed += 1

            for block in scan_blocks:
                if block.ocr_status == "pending":
                    block.ocr_status = "skipped"

            db.log_api_usage(
                service="parser", action="ocr", provider="mistral",
                model=self.model, tender_id=doc_id, doc_id=doc_id,
                ocr_pages_count=pages_count,
                ocr_doc_size_bytes=len(pdf_path.read_bytes()),
                cost_usd=cost, duration_ms=duration_ms, status="success",
            )

            return processed

        except Exception as exc:
            logger.error("  Ошибка OCR PDF: %s", exc)
            db.log_api_usage(
                service="parser", action="ocr", provider="mistral",
                model=self.model, tender_id=doc_id, doc_id=doc_id,
                status="error", error_message=str(exc)[:200],
            )
            for block in scan_blocks:
                block.ocr_status = "skipped"
            return 0

    def _ocr_image(self, image_path: Path) -> str:
        """Отправляет изображение в Mistral OCR и возвращает распознанный текст."""
        file_data = base64.b64encode(image_path.read_bytes()).decode("utf-8")
        suffix = image_path.suffix.lower()

        if suffix == ".pdf":
            doc_type = "document_url"
            url_prefix = "data:application/pdf;base64,"
        else:
            doc_type = "image_url"
            mime_map = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".tiff": "image/tiff",
                ".tif": "image/tiff",
                ".bmp": "image/bmp",
                ".gif": "image/gif",
                ".webp": "image/webp",
            }
            mime_type = mime_map.get(suffix, "image/png")
            url_prefix = f"data:{mime_type};base64,"

        response = self._client.ocr.process(
            model=self.model,
            document={
                "type": doc_type,
                doc_type: f"{url_prefix}{file_data}",
            },
            include_image_base64=True,
        )

        # Собираем текст из всех страниц OCR-результата
        texts: list[str] = []
        for page in response.pages:
            if page.markdown:
                texts.append(page.markdown)
        return "\n\n".join(texts).strip()
