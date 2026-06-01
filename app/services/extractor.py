"""Рекурсивная распаковка архивов тендерной документации.

Обрабатывает:
- ZIP (с исправлением кириллических имён файлов)
- 7z (через py7zr)
- RAR (через rarfile, требует unrar в PATH)
- Вложенные архивы (архив внутри архива — рекурсивно)
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.core.config import ARCHIVE_EXTENSIONS, DOCUMENT_EXTENSIONS
from app.schemas.manifest import DocumentRecord

logger = logging.getLogger(__name__)

# Короткий базовый путь для временных файлов (избегаем длинных путей Windows)
_SHORT_TEMP_BASE = Path(os.environ.get("TEMP_EXTRACT_DIR", "C:/Temp/_extract"))


class ExtractionError(Exception):
    """Ошибка при извлечении архива."""


class ArchiveExtractor:
    """Рекурсивно извлекает документы из архива в плоскую директорию
    с нормализованными именами файлов."""

    def __init__(self, documents_dir: Path, max_depth: int = 5):
        """
        Args:
            documents_dir: директория для сохранения извлечённых документов.
            max_depth: максимальная глубина рекурсии (защита от бесконечных вложений).
        """
        self.documents_dir = documents_dir
        self.documents_dir.mkdir(parents=True, exist_ok=True)
        self.max_depth = max_depth
        self._doc_counter = 0
        self._documents: list[DocumentRecord] = []

    @property
    def documents(self) -> list[DocumentRecord]:
        return list(self._documents)

    def extract(self, archive_path: Path) -> list[DocumentRecord]:
        """Извлекает все документы из архива (включая вложенные архивы).

        Args:
            archive_path: путь к архиву.

        Returns:
            Список DocumentRecord для каждого извлечённого документа.
        """
        if not archive_path.exists():
            raise ExtractionError(f"Архив не найден: {archive_path}")

        logger.info("Начинаю извлечение из %s", archive_path.name)

        # Используем короткий базовый путь, чтобы избежать ошибок
        # с длиной пути на Windows (> 260 символов)
        _SHORT_TEMP_BASE.mkdir(parents=True, exist_ok=True)
        tmp_dir = tempfile.mkdtemp(prefix="t", dir=str(_SHORT_TEMP_BASE))
        tmp_path = Path(tmp_dir)

        try:
            self._extract_recursive(
                archive_path=archive_path,
                dest_dir=tmp_path,
                source_archive=archive_path.name,
                archive_prefix="",
                depth=0,
            )
        finally:
            shutil.rmtree(tmp_path, ignore_errors=True)

        logger.info(
            "Извлечение завершено: %d документов из %s",
            len(self._documents),
            archive_path.name,
        )
        return self.documents

    def _extract_recursive(
        self,
        archive_path: Path,
        dest_dir: Path,
        source_archive: str,
        archive_prefix: str,
        depth: int,
    ) -> None:
        """Рекурсивно извлекает архив и обрабатывает вложенные."""
        if depth > self.max_depth:
            logger.warning(
                "Превышена максимальная глубина вложенности (%d) для %s",
                self.max_depth,
                archive_path.name,
            )
            return

        try:
            self._extract_single(archive_path, dest_dir)
        except Exception as exc:
            logger.error("Не удалось извлечь %s: %s", archive_path.name, exc)
            return

        nested_archives: list[Path] = []
        document_files: list[Path] = []

        for item in sorted(dest_dir.rglob("*")):
            if not item.is_file():
                continue
            if item.suffix.lower() in ARCHIVE_EXTENSIONS:
                nested_archives.append(item)
            elif item.suffix.lower() in DOCUMENT_EXTENSIONS:
                document_files.append(item)
            else:
                logger.debug("Пропущен файл с неизвестным расширением: %s", item.name)

        for doc_file in document_files:
            relative_in_archive = str(doc_file.relative_to(dest_dir))
            full_archive_path = (
                f"{archive_prefix}/{relative_in_archive}" if archive_prefix
                else relative_in_archive
            )
            self._register_document(doc_file, source_archive, full_archive_path)

        for idx, nested in enumerate(nested_archives):
            nested_prefix = str(nested.relative_to(dest_dir))
            full_prefix = (
                f"{archive_prefix}/{nested_prefix}" if archive_prefix
                else nested_prefix
            )
            # Короткое имя директории: избегаем длинных кириллических путей
            nested_dest = dest_dir / f"_n{depth}_{idx}"
            nested_dest.mkdir(exist_ok=True)

            logger.info(
                "Обнаружен вложенный архив (глубина %d): %s", depth + 1, nested.name
            )
            self._extract_recursive(
                archive_path=nested,
                dest_dir=nested_dest,
                source_archive=source_archive,
                archive_prefix=full_prefix,
                depth=depth + 1,
            )

    def _extract_single(self, archive_path: Path, dest_dir: Path) -> None:
        """Извлекает один архив в директорию."""
        suffix = archive_path.suffix.lower()

        if suffix == ".zip":
            self._extract_zip(archive_path, dest_dir)
        elif suffix == ".7z":
            self._extract_7z(archive_path, dest_dir)
        elif suffix == ".rar":
            self._extract_rar(archive_path, dest_dir)
        else:
            raise ExtractionError(f"Неподдерживаемый формат архива: {suffix}")

    def _extract_zip(self, archive_path: Path, dest_dir: Path) -> None:
        """Извлекает ZIP с исправлением кириллических имён файлов."""
        with zipfile.ZipFile(archive_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue

                filename = self._fix_zip_filename(info.filename)
                target = dest_dir / filename
                target.parent.mkdir(parents=True, exist_ok=True)

                try:
                    with zf.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                except Exception as exc:
                    logger.warning(
                        "Не удалось извлечь файл %s из ZIP: %s", filename, exc
                    )

    def _extract_7z(self, archive_path: Path, dest_dir: Path) -> None:
        """Извлекает 7z-архив."""
        import py7zr

        with py7zr.SevenZipFile(archive_path, mode="r") as sz:
            sz.extractall(path=dest_dir)

    def _extract_rar(self, archive_path: Path, dest_dir: Path) -> None:
        """Извлекает RAR-архив через UnRAR.

        Извлекает во временную директорию с коротким путём, затем перемещает
        файлы в целевую. Это обходит ограничение Windows на длину пути
        (260 символов), которое срабатывает при кириллических именах
        файлов/папок внутри RAR.
        """
        import rarfile

        from app.core.config import UNRAR_TOOL

        rarfile.UNRAR_TOOL = UNRAR_TOOL

        # Извлекаем в короткий temp-путь, чтобы избежать PathTooLong
        _SHORT_TEMP_BASE.mkdir(parents=True, exist_ok=True)
        short_tmp = tempfile.mkdtemp(prefix="r", dir=str(_SHORT_TEMP_BASE))
        short_path = Path(short_tmp)

        try:
            with rarfile.RarFile(archive_path) as rf:
                rf.extractall(path=short_path)

            # Перемещаем всё содержимое в целевую директорию
            for item in short_path.rglob("*"):
                if item.is_file():
                    rel = item.relative_to(short_path)
                    target = dest_dir / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(item), str(target))
        finally:
            shutil.rmtree(short_path, ignore_errors=True)

    @staticmethod
    def _fix_zip_filename(raw_filename: str) -> str:
        """Восстанавливает кириллические имена файлов из ZIP.

        ZIP-архивы, созданные на Windows, часто кодируют имена файлов
        в CP437, тогда как реальная кодировка — CP866 или CP1251.
        """
        try:
            raw_bytes = raw_filename.encode("cp437")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return raw_filename

        for encoding in ("utf-8", "cp1251", "cp866"):
            try:
                decoded = raw_bytes.decode(encoding)
                if any("\u0400" <= c <= "\u04ff" for c in decoded):
                    return decoded
            except UnicodeDecodeError:
                continue

        return raw_filename

    def _register_document(
        self, file_path: Path, source_archive: str, archive_path: str
    ) -> None:
        """Копирует файл в documents/ с нормализованным именем и регистрирует."""
        self._doc_counter += 1
        doc_id = f"doc_{self._doc_counter:03d}"
        stored_name = f"{doc_id}{file_path.suffix.lower()}"
        dest = self.documents_dir / stored_name

        shutil.copy2(file_path, dest)

        record = DocumentRecord(
            doc_id=doc_id,
            original_filename=file_path.name,
            stored_filename=stored_name,
            extension=file_path.suffix.lower(),
            size_bytes=dest.stat().st_size,
            source_archive=source_archive,
            archive_path=archive_path,
            extracted_at=datetime.now(timezone.utc),
        )
        self._documents.append(record)

        logger.debug(
            "  %s: %s → %s (%d bytes)",
            doc_id,
            file_path.name,
            stored_name,
            record.size_bytes,
        )
