"""Обёртка для исходящего HTTP-трафика через прокси с авторизацией.

Все внешние запросы (LLM, OCR, embeddings, Langfuse) должны идти через
HTTP-прокси, кроме хостов из bypass-списка (по умолчанию tenderplan.ru).

URL прокси задаст backend-разработчик через переменную окружения, до этого
обёртка работает прозрачно — без прокси.

Конфигурация через env:

- ``OUTBOUND_PROXY_URL`` — URL прокси с авторизацией, например
  ``http://user:pass@proxy.example.com:3128``. Если не задано, прокси
  не используется.
- ``OUTBOUND_PROXY_BYPASS`` — список хостов через запятую, для которых
  прокси не применяется. По умолчанию ``tenderplan.ru``.

Использование в коде:

    from tender_tools.http_proxy import build_httpx_client

    client = build_httpx_client(target_url="https://api.x.ai", timeout=300)

Для third-party SDK, которые сами создают HTTP-клиент (Langfuse, OpenTelemetry,
любая библиотека, читающая ``HTTPS_PROXY``), вызывайте один раз при старте
сервиса :func:`apply_to_environment`.
"""

from __future__ import annotations

import logging
import os
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

OUTBOUND_PROXY_ENV = "OUTBOUND_PROXY_URL"
OUTBOUND_PROXY_BYPASS_ENV = "OUTBOUND_PROXY_BYPASS"

DEFAULT_BYPASS_HOSTS: tuple[str, ...] = ("tenderplan.ru",)


def get_proxy_url() -> str | None:
    """Возвращает URL прокси из env или None, если он не задан."""
    value = os.getenv(OUTBOUND_PROXY_ENV, "").strip()
    return value or None


def get_bypass_hosts() -> tuple[str, ...]:
    """Возвращает кортеж хостов, для которых прокси не используется."""
    raw = os.getenv(OUTBOUND_PROXY_BYPASS_ENV, "").strip()
    if not raw:
        return DEFAULT_BYPASS_HOSTS
    extra = tuple(host.strip().lower() for host in raw.split(",") if host.strip())
    # Гарантируем, что tenderplan всегда в bypass — это требование задачи.
    return tuple(dict.fromkeys(DEFAULT_BYPASS_HOSTS + extra))


def should_bypass(target_url: str | None) -> bool:
    """Проверяет, относится ли URL к bypass-хостам."""
    if not target_url:
        return False
    try:
        host = (urlsplit(target_url).hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    for bypass in get_bypass_hosts():
        bypass = bypass.lower()
        if host == bypass or host.endswith("." + bypass):
            return True
    return False


def _resolve_proxy_for(target_url: str | None) -> str | None:
    proxy_url = get_proxy_url()
    if proxy_url and should_bypass(target_url):
        return None
    return proxy_url


def build_httpx_client(
    *,
    target_url: str | None = None,
    **kwargs,
) -> httpx.Client:
    """Создаёт ``httpx.Client`` с прокси, если он задан и URL не в bypass.

    ``target_url`` — основной URL, к которому будет ходить клиент. Используется
    только для проверки bypass: если URL принадлежит tenderplan, прокси не
    применяется даже если ``OUTBOUND_PROXY_URL`` задан.

    Все остальные kwargs пробрасываются в ``httpx.Client``.
    """
    proxy = _resolve_proxy_for(target_url)
    if proxy:
        kwargs.setdefault("proxy", proxy)
    return httpx.Client(**kwargs)


def build_httpx_async_client(
    *,
    target_url: str | None = None,
    **kwargs,
) -> httpx.AsyncClient:
    """Аналог :func:`build_httpx_client` для асинхронных вызовов."""
    proxy = _resolve_proxy_for(target_url)
    if proxy:
        kwargs.setdefault("proxy", proxy)
    return httpx.AsyncClient(**kwargs)


def build_proxies_dict(target_url: str | None = None) -> dict[str, str] | None:
    """Возвращает proxies в формате ``requests``/``urllib``.

    Удобно, если в коде используется ``requests`` или внешняя библиотека,
    принимающая словарь ``{"http": ..., "https": ...}``.
    """
    proxy = _resolve_proxy_for(target_url)
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def apply_to_environment(force: bool = False) -> None:
    """Прописывает ``HTTPS_PROXY``/``HTTP_PROXY``/``NO_PROXY`` в окружение.

    Нужно для third-party SDK (Langfuse, OpenTelemetry exporter, любые
    библиотеки на базе ``urllib``/``requests``), которые сами создают
    HTTP-клиента и читают стандартные env-переменные.

    Если соответствующая переменная уже задана вручную, она не перезаписывается
    (если только ``force=True``). Если ``OUTBOUND_PROXY_URL`` пуст, функция
    ничего не делает.
    """
    proxy = get_proxy_url()
    if not proxy:
        return

    bypass = ",".join(get_bypass_hosts())

    pairs = {
        "HTTPS_PROXY": proxy,
        "HTTP_PROXY": proxy,
        "https_proxy": proxy,
        "http_proxy": proxy,
        "NO_PROXY": bypass,
        "no_proxy": bypass,
    }

    for key, value in pairs.items():
        if force or not os.environ.get(key):
            os.environ[key] = value

    logger.info(
        "Outbound proxy applied: %s (bypass: %s)",
        _redact(proxy),
        bypass,
    )


def _redact(proxy_url: str) -> str:
    """Скрывает учётные данные в строке прокси для безопасного логирования."""
    try:
        parts = urlsplit(proxy_url)
    except ValueError:
        return "***"
    if parts.username:
        host = parts.hostname or ""
        port = f":{parts.port}" if parts.port else ""
        return f"{parts.scheme}://***:***@{host}{port}"
    return proxy_url
