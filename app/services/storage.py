"""Управление файловой структурой хранилища тендерной документации.

Структура:
    data/
        index.json                  # Глобальный индекс тендеров
        tenders/
            {tender_id}/
                archives/           # Скачанные архивы (оригиналы)
                documents/          # Извлечённые документы (нормализованные имена)
                parsed/             # [будущее] Структурированные JSON
                passports/          # [будущее] Паспорта документов
                manifest.json       # Реестр: метаданные + список документов
"""

from pathlib import Path

from app.core.config import DATA_DIR
from app.schemas.manifest import GlobalIndex, TenderIndexEntry, TenderManifest, TenderStatus


class TenderStorage:
    """Управление директориями и манифестами для конкретного тендера."""

    def __init__(self, tender_id: str, data_dir: Path = DATA_DIR):
        self.tender_id = tender_id
        self.data_dir = data_dir
        self.tender_dir = data_dir / "tenders" / tender_id

    @property
    def archives_dir(self) -> Path:
        return self.tender_dir / "archives"

    @property
    def documents_dir(self) -> Path:
        return self.tender_dir / "documents"

    @property
    def parsed_dir(self) -> Path:
        return self.tender_dir / "parsed"

    @property
    def passports_dir(self) -> Path:
        return self.tender_dir / "passports"

    @property
    def manifest_path(self) -> Path:
        return self.tender_dir / "manifest.json"

    @property
    def archive_path(self) -> Path:
        """Путь к основному скачанному архиву."""
        return self.archives_dir / "source.zip"

    def ensure_dirs(self) -> None:
        """Создаёт все необходимые директории для тендера."""
        self.archives_dir.mkdir(parents=True, exist_ok=True)
        self.documents_dir.mkdir(parents=True, exist_ok=True)

    def has_manifest(self) -> bool:
        return self.manifest_path.exists()

    def has_archive(self) -> bool:
        return self.archive_path.exists()

    def load_manifest(self) -> TenderManifest:
        """Загружает манифест тендера. Создаёт новый, если не существует."""
        if self.has_manifest():
            return TenderManifest.load(self.manifest_path)
        return TenderManifest(tender_id=self.tender_id)

    def save_manifest(self, manifest: TenderManifest) -> None:
        """Сохраняет манифест тендера."""
        manifest.save(self.manifest_path)


class GlobalStorage:
    """Управление глобальным индексом всех тендеров."""

    def __init__(self, data_dir: Path = DATA_DIR):
        self.data_dir = data_dir
        self.index_path = data_dir / "index.json"

    def load_index(self) -> GlobalIndex:
        return GlobalIndex.load(self.index_path)

    def save_index(self, index: GlobalIndex) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        index.save(self.index_path)

    def update_tender_entry(
        self,
        tender_id: str,
        status: TenderStatus,
        document_count: int = 0,
        downloaded_at=None,
    ) -> None:
        """Обновляет запись тендера в глобальном индексе."""
        index = self.load_index()
        entry = index.tenders.get(tender_id, TenderIndexEntry())
        entry.status = status
        if downloaded_at:
            entry.downloaded_at = downloaded_at
        if document_count:
            entry.document_count = document_count
        index.tenders[tender_id] = entry
        self.save_index(index)
