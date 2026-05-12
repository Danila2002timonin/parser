"""Скачивание архивов тендерной документации через API tenderplan.ru."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import httpx

from .config import TENDERPLAN_API_BASE
from .http_proxy import build_httpx_client
from .models import PipelineState, TenderManifest, TenderStatus
from .storage import TenderStorage

logger = logging.getLogger(__name__)


class DownloadError(Exception):
    """Ошибка при скачивании архива."""


class TenderDownloader:
    """Скачивает ZIP-архив документации тендера по его ID."""

    def __init__(self, api_token: str | None = None, timeout: float = 180.0):
        self.api_token = api_token or os.getenv("TENDERPLAN_AUTH_KEY", "")
        if not self.api_token:
            raise ValueError(
                "API-токен не задан. Установите TENDERPLAN_AUTH_KEY в .env "
                "или передайте api_token в конструктор."
            )
        self.timeout = timeout

    def download(self, tender_id: str, storage: TenderStorage, force: bool = False) -> TenderManifest:
        """Скачивает архив тендера и сохраняет на диск.

        Args:
            tender_id: ID тендера на tenderplan.ru.
            storage: экземпляр TenderStorage для управления путями.
            force: если True — перескачивает, даже если архив уже есть.

        Returns:
            Обновлённый TenderManifest.

        Raises:
            DownloadError: если API вернул ошибку или файл пуст.
        """
        if not force and storage.has_archive():
            logger.info("Архив %s уже скачан, пропускаю (force=False)", tender_id)
            return storage.load_manifest()

        storage.ensure_dirs()

        url = f"{TENDERPLAN_API_BASE}/archive?tenderId={tender_id}"
        headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/zip",
        }

        logger.info("Скачиваю архив тендера %s ...", tender_id)
        dest = storage.archive_path

        try:
            # tenderplan.ru намеренно в bypass-списке прокси: запрос идёт
            # напрямую, мимо корпоративного HTTP-прокси.
            with build_httpx_client(
                target_url=url,
                timeout=self.timeout,
                follow_redirects=True,
            ) as client:
                with client.stream("GET", url, headers=headers) as response:
                    if response.status_code == 401:
                        raise DownloadError("Ошибка авторизации (401). Проверьте API-токен.")
                    if response.status_code == 404:
                        raise DownloadError(f"Тендер {tender_id} не найден (404).")
                    response.raise_for_status()

                    total_bytes = 0
                    with open(dest, "wb") as f:
                        for chunk in response.iter_bytes(chunk_size=65536):
                            f.write(chunk)
                            total_bytes += len(chunk)

        except httpx.HTTPStatusError as exc:
            raise DownloadError(
                f"HTTP-ошибка {exc.response.status_code} при скачивании тендера {tender_id}"
            ) from exc
        except httpx.RequestError as exc:
            raise DownloadError(
                f"Ошибка сети при скачивании тендера {tender_id}: {exc}"
            ) from exc

        if total_bytes == 0:
            dest.unlink(missing_ok=True)
            raise DownloadError(f"Получен пустой архив для тендера {tender_id}")

        logger.info(
            "Архив тендера %s скачан: %.1f MB → %s",
            tender_id,
            total_bytes / (1024 * 1024),
            dest,
        )

        now = datetime.now(timezone.utc)
        manifest = storage.load_manifest()
        manifest.status = TenderStatus.DOWNLOADED
        manifest.source_url = url
        manifest.pipeline_state.downloaded = now
        storage.save_manifest(manifest)

        return manifest
