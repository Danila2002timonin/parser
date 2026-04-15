"""Pipeline-оркестратор: скачивание → извлечение → парсинг → паспорта."""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import time as _time

from .config import DATA_DIR
from . import db
from .downloader import TenderDownloader
from .extractor import ArchiveExtractor
from .llm_client import LLMClient, LLMError, set_usage_context
from .models import TenderManifest, TenderStatus
from .parsers.router import UnsupportedFormatError, parse_document
from .parsers.schema import ParsedDocument
from .passport import DocumentPassport, PassportGenerator, build_document_map
from .storage import GlobalStorage, TenderStorage

logger = logging.getLogger(__name__)


class TenderPipeline:
    """Полный цикл загрузки и обработки тендерной документации."""

    def __init__(
        self,
        api_token: str | None = None,
        data_dir: Path = DATA_DIR,
        download_timeout: float = 180.0,
        llm_base_url: str | None = None,
        passport_model: str | None = None,
        passport_workers: int = 10,
    ):
        self.data_dir = data_dir
        self.downloader = TenderDownloader(api_token=api_token, timeout=download_timeout)
        self.global_storage = GlobalStorage(data_dir=data_dir)
        self._llm_base_url = llm_base_url
        self._passport_model = passport_model or os.getenv(
            "LLM_PASSPORT_MODEL", "grok-4-fast-non-reasoning"
        )
        self._passport_workers = passport_workers

    def ingest(self, tender_id: str, force: bool = False) -> TenderManifest:
        """Полный цикл: скачать → извлечь → распарсить → обновить манифест.

        Args:
            tender_id: ID тендера.
            force: если True — пересчитать всё заново.

        Returns:
            TenderManifest с полным реестром документов.
        """
        storage = TenderStorage(tender_id, data_dir=self.data_dir)

        # Проверяем, не обработан ли уже этот тендер
        if not force and storage.has_manifest():
            manifest = storage.load_manifest()
            if manifest.status.value >= TenderStatus.EXTRACTED.value:
                logger.info(
                    "Тендер %s уже обработан (статус: %s), пропускаю",
                    tender_id,
                    manifest.status.value,
                )
                return manifest

        # Шаг 1: Скачивание
        logger.info("=" * 60)
        logger.info("PIPELINE: Тендер %s", tender_id)
        logger.info("=" * 60)

        pipeline_start = _time.time()
        db.upsert_tender(tender_id, status="created")

        manifest = self.downloader.download(tender_id, storage, force=force)
        db.update_tender_status(tender_id, "downloaded")

        # Шаг 2: Извлечение
        logger.info("Этап 2: Извлечение документов...")
        extractor = ArchiveExtractor(documents_dir=storage.documents_dir)
        documents = extractor.extract(storage.archive_path)

        manifest.status = TenderStatus.EXTRACTED
        manifest.documents = documents
        manifest.pipeline_state.extracted = datetime.now(timezone.utc)
        storage.save_manifest(manifest)

        db.update_tender_status(tender_id, "extracted")
        total_size = sum(d.size_bytes for d in documents)
        db.upsert_tender(tender_id, document_count=len(documents), total_size_bytes=total_size)

        for doc in documents:
            db.upsert_document(
                tender_id=tender_id, doc_id=doc.doc_id,
                original_filename=doc.original_filename,
                stored_filename=doc.stored_filename,
                extension=doc.extension, size_bytes=doc.size_bytes,
                source_archive=doc.source_archive, archive_path=doc.archive_path,
            )

        logger.info("Извлечено %d документов", len(documents))
        for doc in documents:
            logger.info(
                "  %s: %s (%s, %.1f KB)",
                doc.doc_id,
                doc.original_filename,
                doc.extension,
                doc.size_bytes / 1024,
            )

        # Шаг 3: Парсинг каждого документа
        logger.info("-" * 60)
        logger.info("Этап 3: Парсинг документов...")
        parsed_dir = storage.parsed_dir
        parsed_dir.mkdir(parents=True, exist_ok=True)

        parsed_count = 0
        skipped_count = 0

        for doc_record in documents:
            doc_path = storage.documents_dir / doc_record.stored_filename
            json_path = parsed_dir / f"{doc_record.doc_id}.json"

            if not force and json_path.exists():
                logger.debug("Уже распарсен: %s", doc_record.doc_id)
                skipped_count += 1
                continue

            try:
                t0 = _time.time()
                parsed_doc = parse_document(
                    file_path=doc_path,
                    doc_id=doc_record.doc_id,
                    output_dir=parsed_dir,
                )
                parsed_doc.save(json_path)
                parse_ms = int((_time.time() - t0) * 1000)
                parsed_count += 1

                est_tokens = LLMClient.estimate_tokens(
                    " ".join(b.content for b in parsed_doc.text_blocks if hasattr(b, "content"))
                )

                db.update_document_parsed(
                    tender_id=tender_id, doc_id=doc_record.doc_id,
                    text_blocks_count=len(parsed_doc.text_blocks),
                    tables_count=len(parsed_doc.table_blocks),
                    images_count=len(parsed_doc.image_blocks),
                    total_pages=parsed_doc.total_pages,
                    estimated_tokens=est_tokens,
                    parse_duration_ms=parse_ms,
                )
                db.log_parse_metric(
                    tender_id=tender_id, doc_id=doc_record.doc_id,
                    stage=f"parse_{doc_record.extension.lstrip('.')}",
                    duration_ms=parse_ms,
                    input_size_bytes=doc_record.size_bytes,
                    items_count=len(parsed_doc.blocks),
                )

                logger.info(
                    "  %s: %d текст, %d таблиц, %d изобр. (%dms)",
                    doc_record.doc_id,
                    len(parsed_doc.text_blocks),
                    len(parsed_doc.table_blocks),
                    len(parsed_doc.image_blocks),
                    parse_ms,
                )

            except UnsupportedFormatError:
                logger.warning(
                    "  %s: формат %s не поддерживается, пропущен",
                    doc_record.doc_id,
                    doc_record.extension,
                )
                db.update_document_parse_failed(tender_id, doc_record.doc_id, "unsupported")
                skipped_count += 1

            except Exception as exc:
                logger.error(
                    "  %s: ошибка парсинга: %s",
                    doc_record.doc_id,
                    exc,
                )
                db.update_document_parse_failed(tender_id, doc_record.doc_id, "failed")
                skipped_count += 1

        manifest.status = TenderStatus.PARSED
        manifest.pipeline_state.parsed = datetime.now(timezone.utc)
        storage.save_manifest(manifest)
        db.update_tender_status(tender_id, "parsed")

        # Шаг 4: OCR для сканов и изображений
        logger.info("-" * 60)
        logger.info("Этап 4: OCR для сканов и изображений...")
        self._run_ocr(storage, documents)

        # Шаг 5: Генерация паспортов через LLM
        logger.info("-" * 60)
        logger.info("Этап 5: Генерация паспортов документов...")

        passports = self._generate_passports(storage, documents, force=force)

        if passports:
            manifest.status = TenderStatus.INDEXED
            manifest.pipeline_state.passports_generated = datetime.now(timezone.utc)
            db.update_tender_status(tender_id, "passports_done")
        else:
            logger.warning("Паспорта не сгенерированы (LLM недоступен?)")

        storage.save_manifest(manifest)

        # Шаг 6: Обновление глобального индекса
        self.global_storage.update_tender_entry(
            tender_id=tender_id,
            status=manifest.status,
            document_count=len(documents),
            downloaded_at=manifest.pipeline_state.downloaded,
        )

        pipeline_ms = int((_time.time() - pipeline_start) * 1000)
        db.log_parse_metric(
            tender_id=tender_id, stage="full_pipeline",
            duration_ms=pipeline_ms, items_count=len(documents),
        )
        db.finalize_tender(tender_id, pipeline_ms)

        logger.info("=" * 60)
        logger.info(
            "PIPELINE ЗАВЕРШЁН: %d документов, %d распарсено, %d паспортов (%ds)",
            len(documents),
            parsed_count,
            len(passports),
            pipeline_ms // 1000,
        )
        logger.info("=" * 60)

        return manifest

    def _run_ocr(self, storage: TenderStorage, documents) -> None:
        """Запускает OCR для документов, содержащих сканы/изображения."""
        try:
            from .ocr_processor import MistralOCRProcessor
            processor = MistralOCRProcessor()
        except Exception as exc:
            logger.warning("OCR недоступен: %s. Пропускаю.", exc)
            return

        total_processed = 0
        tender_id = storage.tender_id

        for doc_record in documents:
            parsed_path = storage.parsed_dir / f"{doc_record.doc_id}.json"
            if not parsed_path.exists():
                continue

            parsed_doc = ParsedDocument.load(parsed_path)

            pending = [
                b for b in parsed_doc.blocks
                if hasattr(b, "ocr_status") and b.ocr_status == "pending"
            ]
            if not pending:
                continue

            source_pdf = None
            if doc_record.extension == ".pdf":
                source_pdf = storage.documents_dir / doc_record.stored_filename

            t0 = _time.time()
            count = processor.process_document(
                parsed_doc, storage.parsed_dir, source_pdf,
                tender_id=tender_id,
            )
            ocr_ms = int((_time.time() - t0) * 1000)

            if count > 0:
                parsed_doc.save(parsed_path)
                total_processed += count
                db.log_parse_metric(
                    tender_id=tender_id, doc_id=doc_record.doc_id,
                    stage="ocr", duration_ms=ocr_ms, items_count=count,
                )
                db.update_tender_status(tender_id, "ocr_done")

        if total_processed > 0:
            logger.info("OCR: всего обработано %d изображений", total_processed)
        else:
            logger.info("OCR: нет изображений для обработки")

    def _generate_passports(
        self,
        storage: TenderStorage,
        documents,
        force: bool = False,
    ) -> list[DocumentPassport]:
        """Генерирует паспорта для всех распарсенных документов (параллельно)."""
        passports_dir = storage.passports_dir
        passports_dir.mkdir(parents=True, exist_ok=True)

        try:
            llm = LLMClient(
                base_url=self._llm_base_url,
                model=self._passport_model,
            )
            if not llm.ping():
                logger.warning("LLM-сервер недоступен, пропускаю генерацию паспортов")
                return []
        except Exception as exc:
            logger.warning("Не удалось подключиться к LLM: %s", exc)
            return []

        logger.info(
            "Генерация паспортов: model=%s, workers=%d",
            self._passport_model,
            self._passport_workers,
        )

        generator = PassportGenerator(llm)
        passports: list[DocumentPassport] = []

        # Собираем задачи: (doc_record, parsed_path, passport_path)
        tasks: list[tuple] = []
        for doc_record in documents:
            passport_path = passports_dir / f"{doc_record.doc_id}_passport.json"
            parsed_path = storage.parsed_dir / f"{doc_record.doc_id}.json"

            if not force and passport_path.exists():
                try:
                    passports.append(DocumentPassport.load(passport_path))
                    logger.debug("Паспорт уже есть: %s", doc_record.doc_id)
                    continue
                except Exception:
                    pass

            if not parsed_path.exists():
                logger.debug("Нет parsed JSON для %s, пропускаю", doc_record.doc_id)
                continue

            tasks.append((doc_record, parsed_path, passport_path))

        if not tasks:
            logger.info("Все паспорта уже сгенерированы")
            self._save_document_map(passports, passports_dir, storage.tender_id)
            llm.close()
            return passports

        # Параллельная генерация
        def _generate_one(args):
            doc_record, parsed_path, passport_path = args
            try:
                set_usage_context("parser", "passport", storage.tender_id, doc_record.doc_id)
                t0 = _time.time()
                parsed_doc = ParsedDocument.load(parsed_path)
                passport = generator.generate(parsed_doc)
                passport.save(passport_path)
                gen_ms = int((_time.time() - t0) * 1000)

                db.upsert_passport(
                    tender_id=storage.tender_id, doc_id=doc_record.doc_id,
                    passport_data=passport.model_dump(mode="json", exclude={"generated_at"}),
                    model_used=self._passport_model,
                    generation_duration_ms=gen_ms,
                )
                db.log_parse_metric(
                    tender_id=storage.tender_id, doc_id=doc_record.doc_id,
                    stage="passport", duration_ms=gen_ms,
                )

                logger.info(
                    "  %s: тип=%s, %d разделов (%dms)",
                    doc_record.doc_id,
                    passport.doc_type,
                    len(passport.sections),
                    gen_ms,
                )
                return passport
            except LLMError as exc:
                logger.error("  %s: ошибка LLM: %s", doc_record.doc_id, exc)
                return None
            except Exception as exc:
                logger.error("  %s: ошибка генерации паспорта: %s", doc_record.doc_id, exc)
                return None

        with ThreadPoolExecutor(max_workers=self._passport_workers) as pool:
            futures = {pool.submit(_generate_one, task): task for task in tasks}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    passports.append(result)

        self._save_document_map(passports, passports_dir, storage.tender_id)
        llm.close()
        return passports

    @staticmethod
    def _save_document_map(passports, passports_dir, tender_id):
        """Сохраняет карту документации из паспортов."""
        if passports:
            doc_map = build_document_map(passports, tender_id)
            map_path = passports_dir / "document_map.json"
            doc_map.save(map_path)

            routing_text = doc_map.to_routing_text()
            db.upsert_document_map(
                tender_id=tender_id,
                map_data=doc_map.model_dump(mode="json", exclude={"generated_at"}),
                routing_text=routing_text,
                passports_count=len(passports),
                estimated_tokens=LLMClient.estimate_tokens(routing_text),
            )

            logger.info(
                "Карта документации сохранена: %d паспортов, %s",
                len(passports),
                map_path,
            )
