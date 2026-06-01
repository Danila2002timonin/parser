"""Обработка пользовательских дополнительных документов для существующего тендера."""

from __future__ import annotations

import logging
import os
import re
import shutil
import time as _time
from datetime import datetime, timezone
from pathlib import Path

from app import repositories as db
from app.core.config import DATA_DIR
from app.clients.llm_client import LLMClient, LLMError, set_usage_context
from app.schemas.manifest import DocumentRecord, TenderStatus
from .parsers.router import UnsupportedFormatError, parse_document
from .parsers.schema import ParsedDocument
from .passport import DocumentPassport, PassportGenerator, build_document_map
from .storage import TenderStorage

logger = logging.getLogger(__name__)


class AdditionalDocumentError(Exception):
    """Ошибка при добавлении пользовательских документов."""


class AdditionalDocumentProcessor:
    """Добавляет пользовательские файлы в уже существующее пространство тендера."""

    def __init__(
        self,
        data_dir: Path = DATA_DIR,
        llm_base_url: str | None = None,
        passport_model: str | None = None,
    ):
        self.data_dir = data_dir
        self._llm_base_url = llm_base_url
        self._passport_model = passport_model or os.getenv(
            "LLM_PASSPORT_MODEL", "grok-4-fast-non-reasoning"
        )

    def add_files(
        self,
        tender_id: str,
        files: list[tuple[str, Path]],
    ) -> list[DocumentRecord]:
        """Сохраняет, парсит и индексирует дополнительные документы.

        Args:
            tender_id: ID существующего тендера.
            files: пары (оригинальное имя, временный путь к файлу).

        Returns:
            Список добавленных документов.
        """
        if not files:
            return []

        storage = TenderStorage(tender_id, data_dir=self.data_dir)
        if not storage.has_manifest():
            raise AdditionalDocumentError(
                f"Тендер {tender_id} ещё не обработан: manifest.json не найден"
            )

        storage.ensure_dirs()
        storage.parsed_dir.mkdir(parents=True, exist_ok=True)
        storage.passports_dir.mkdir(parents=True, exist_ok=True)

        manifest = storage.load_manifest()
        existing_ids = {doc.doc_id for doc in manifest.documents}
        added_docs: list[DocumentRecord] = []

        for original_filename, temp_path in files:
            doc_id = self._next_user_doc_id(existing_ids)
            existing_ids.add(doc_id)
            ext = Path(original_filename).suffix.lower() or temp_path.suffix.lower()
            stored_filename = f"{doc_id}{ext}"
            target_path = storage.documents_dir / stored_filename
            shutil.copy2(temp_path, target_path)

            record = DocumentRecord(
                doc_id=doc_id,
                original_filename=original_filename,
                stored_filename=stored_filename,
                extension=ext,
                size_bytes=target_path.stat().st_size,
                source_archive="user_upload",
                archive_path=f"additional_documents/{self._safe_name(original_filename)}",
                extracted_at=datetime.now(timezone.utc),
            )
            manifest.documents.append(record)
            added_docs.append(record)

            db.upsert_document(
                tender_id=tender_id,
                doc_id=record.doc_id,
                original_filename=record.original_filename,
                stored_filename=record.stored_filename,
                extension=record.extension,
                size_bytes=record.size_bytes,
                source_archive=record.source_archive,
                archive_path=record.archive_path,
            )

        manifest.status = TenderStatus.PARSED
        storage.save_manifest(manifest)

        self._parse_documents(storage, added_docs)
        self._run_ocr(storage, added_docs)
        self._generate_new_passports(storage, added_docs)
        self._rebuild_document_map(storage)

        total_size = sum(doc.size_bytes for doc in manifest.documents)
        db.upsert_tender(
            tender_id,
            status="indexed",
            document_count=len(manifest.documents),
            total_size_bytes=total_size,
        )
        current_tender = db.get_tender(tender_id) or {}
        db.finalize_tender(
            tender_id,
            pipeline_duration_ms=current_tender.get("pipeline_duration_ms") or 0,
        )

        manifest.status = TenderStatus.INDEXED
        manifest.pipeline_state.indexed = datetime.now(timezone.utc)
        storage.save_manifest(manifest)

        return added_docs

    def _parse_documents(self, storage: TenderStorage, documents: list[DocumentRecord]) -> None:
        for doc_record in documents:
            doc_path = storage.documents_dir / doc_record.stored_filename
            json_path = storage.parsed_dir / f"{doc_record.doc_id}.json"

            try:
                t0 = _time.time()
                parsed_doc = parse_document(
                    file_path=doc_path,
                    doc_id=doc_record.doc_id,
                    output_dir=storage.parsed_dir,
                )
                parsed_doc.save(json_path)
                parse_ms = int((_time.time() - t0) * 1000)
                estimated_tokens = LLMClient.estimate_tokens(
                    " ".join(b.content for b in parsed_doc.text_blocks if hasattr(b, "content"))
                )

                db.update_document_parsed(
                    tender_id=storage.tender_id,
                    doc_id=doc_record.doc_id,
                    text_blocks_count=len(parsed_doc.text_blocks),
                    tables_count=len(parsed_doc.table_blocks),
                    images_count=len(parsed_doc.image_blocks),
                    total_pages=parsed_doc.total_pages,
                    estimated_tokens=estimated_tokens,
                    parse_duration_ms=parse_ms,
                )
                db.log_parse_metric(
                    tender_id=storage.tender_id,
                    doc_id=doc_record.doc_id,
                    stage=f"parse_{doc_record.extension.lstrip('.')}",
                    duration_ms=parse_ms,
                    input_size_bytes=doc_record.size_bytes,
                    items_count=len(parsed_doc.blocks),
                )
            except UnsupportedFormatError:
                logger.warning("Доп. документ %s: формат не поддерживается", doc_record.doc_id)
                db.update_document_parse_failed(storage.tender_id, doc_record.doc_id, "unsupported")
            except Exception as exc:
                logger.error("Доп. документ %s: ошибка парсинга: %s", doc_record.doc_id, exc)
                db.update_document_parse_failed(storage.tender_id, doc_record.doc_id, "failed")

    def _run_ocr(self, storage: TenderStorage, documents: list[DocumentRecord]) -> None:
        try:
            from .ocr_processor import MistralOCRProcessor

            processor = MistralOCRProcessor()
        except Exception as exc:
            logger.warning("OCR недоступен для доп. документов: %s", exc)
            return

        for doc_record in documents:
            parsed_path = storage.parsed_dir / f"{doc_record.doc_id}.json"
            if not parsed_path.exists():
                continue

            parsed_doc = ParsedDocument.load(parsed_path)
            pending = [
                block for block in parsed_doc.blocks
                if hasattr(block, "ocr_status") and block.ocr_status == "pending"
            ]
            if not pending:
                continue

            source_pdf = (
                storage.documents_dir / doc_record.stored_filename
                if doc_record.extension == ".pdf"
                else None
            )
            t0 = _time.time()
            count = processor.process_document(
                parsed_doc,
                storage.parsed_dir,
                source_pdf,
                tender_id=storage.tender_id,
            )
            ocr_ms = int((_time.time() - t0) * 1000)
            if count:
                parsed_doc.save(parsed_path)
                db.log_parse_metric(
                    tender_id=storage.tender_id,
                    doc_id=doc_record.doc_id,
                    stage="ocr",
                    duration_ms=ocr_ms,
                    items_count=count,
                )

    def _generate_new_passports(
        self,
        storage: TenderStorage,
        documents: list[DocumentRecord],
    ) -> None:
        try:
            llm = LLMClient(base_url=self._llm_base_url, model=self._passport_model)
            if not llm.ping():
                logger.warning("LLM недоступен, паспорта доп. документов не сгенерированы")
                llm.close()
                return
        except Exception as exc:
            logger.warning("Не удалось подключиться к LLM: %s", exc)
            return

        generator = PassportGenerator(llm)
        try:
            for doc_record in documents:
                parsed_path = storage.parsed_dir / f"{doc_record.doc_id}.json"
                passport_path = storage.passports_dir / f"{doc_record.doc_id}_passport.json"
                if not parsed_path.exists():
                    continue

                try:
                    set_usage_context("parser", "passport", storage.tender_id, doc_record.doc_id)
                    t0 = _time.time()
                    passport = generator.generate(ParsedDocument.load(parsed_path))
                    passport.save(passport_path)
                    gen_ms = int((_time.time() - t0) * 1000)
                    db.upsert_passport(
                        tender_id=storage.tender_id,
                        doc_id=doc_record.doc_id,
                        passport_data=passport.model_dump(mode="json", exclude={"generated_at"}),
                        model_used=llm.model,
                        generation_duration_ms=gen_ms,
                    )
                    db.log_parse_metric(
                        tender_id=storage.tender_id,
                        doc_id=doc_record.doc_id,
                        stage="passport",
                        duration_ms=gen_ms,
                    )
                except LLMError as exc:
                    logger.error("Доп. документ %s: ошибка LLM: %s", doc_record.doc_id, exc)
                except Exception as exc:
                    logger.error("Доп. документ %s: ошибка паспорта: %s", doc_record.doc_id, exc)
        finally:
            llm.close()

    def _rebuild_document_map(self, storage: TenderStorage) -> None:
        passports: list[DocumentPassport] = []
        for path in sorted(storage.passports_dir.glob("*_passport.json")):
            try:
                passports.append(DocumentPassport.load(path))
            except Exception as exc:
                logger.warning("Не удалось загрузить паспорт %s: %s", path.name, exc)

        if not passports:
            return

        doc_map = build_document_map(passports, storage.tender_id)
        map_path = storage.passports_dir / "document_map.json"
        doc_map.save(map_path)
        routing_text = doc_map.to_routing_text()
        db.upsert_document_map(
            tender_id=storage.tender_id,
            map_data=doc_map.model_dump(mode="json", exclude={"generated_at"}),
            routing_text=routing_text,
            passports_count=len(passports),
            estimated_tokens=LLMClient.estimate_tokens(routing_text),
        )

    @staticmethod
    def _next_user_doc_id(existing_ids: set[str]) -> str:
        idx = 1
        while True:
            doc_id = f"user_doc_{idx:03d}"
            if doc_id not in existing_ids:
                return doc_id
            idx += 1

    @staticmethod
    def _safe_name(filename: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9А-Яа-я._-]+", "_", filename).strip("._")
        return safe or "uploaded_document"
