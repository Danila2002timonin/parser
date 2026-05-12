"""OpenAI-compatible LLM-клиент.

Поддерживает любой OpenAI-compatible API:
- xAI (Grok)
- OpenAI
- LM Studio (локальный)
- vLLM, Ollama и другие

Конфигурация через переменные окружения:
  LLM_BASE_URL  — URL API (по умолчанию: https://api.x.ai)
  LLM_API_KEY   — API-ключ (или XAI_API_KEY для xAI)
  LLM_MODEL     — имя модели (по умолчанию: grok-4-fast-reasoning)
"""

from __future__ import annotations

import json
import logging
import os
import time

import httpx

from .http_proxy import build_httpx_client

logger = logging.getLogger(__name__)

# Контекст текущего запроса (для трекинга в api_usage)
# Устанавливается вызывающим кодом через set_usage_context()
_usage_context: dict = {}


def set_usage_context(
    service: str = "agent",
    action: str = "chat",
    tender_id: str | None = None,
    doc_id: str | None = None,
) -> None:
    """Устанавливает контекст для трекинга API-вызовов."""
    global _usage_context
    _usage_context = {
        "service": service,
        "action": action,
        "tender_id": tender_id,
        "doc_id": doc_id,
    }

_CHARS_PER_TOKEN_RU = 2.7

# Прайс-лист: (input_per_1M_tokens, output_per_1M_tokens)
_PRICING = {
    "grok-4-fast-non-reasoning":  (0.20, 0.50),
    "grok-4-fast-reasoning":      (0.20, 0.50),
    "grok-4-non-reasoning":       (0.20, 0.50),
}

# Прайс OCR: стоимость за страницу
_OCR_PRICING = {
    "mistral-ocr-latest": 2.0 / 1000,  # $2 per 1000 pages = $0.002/page
}


class LLMError(Exception):
    """Ошибка при обращении к LLM."""


class LLMClient:
    """Клиент для OpenAI-compatible API."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        max_context_tokens: int = 131072,
        max_output_tokens: int = 8192,
        temperature: float = 0.1,
        timeout: float = 300.0,
        max_retries: int = 3,
    ):
        self.base_url = (
            base_url
            or os.getenv("LLM_BASE_URL", "https://api.x.ai")
        ).rstrip("/")

        self.api_key = (
            api_key
            or os.getenv("LLM_API_KEY")
            or os.getenv("XAI_API_KEY", "")
        )

        self.model = model or os.getenv("LLM_MODEL", "grok-4-fast-reasoning")
        self.max_context_tokens = max_context_tokens
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries

        self._client = build_httpx_client(
            target_url=self.base_url, timeout=self.timeout
        )

    @property
    def max_input_tokens(self) -> int:
        """Максимальное количество токенов для входных данных."""
        return self.max_context_tokens - self.max_output_tokens

    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> str:
        """Отправляет запрос к chat completions API.

        Args:
            messages: список сообщений [{"role": "...", "content": "..."}].
            temperature: температура генерации.
            max_tokens: макс. токенов в ответе.
            json_mode: не используется (сохранён для совместимости).

        Returns:
            Текст ответа модели.
        """
        url = f"{self.base_url}/v1/chat/completions"

        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_output_tokens,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            _req_start = time.time()
            try:
                logger.debug(
                    "LLM запрос (попытка %d): model=%s, %d сообщений, ~%d символов",
                    attempt,
                    self.model,
                    len(messages),
                    sum(len(m["content"]) for m in messages),
                )

                response = self._client.post(url, json=payload, headers=headers)
                response.raise_for_status()

                data = response.json()
                content = data["choices"][0]["message"]["content"]

                usage = data.get("usage", {})
                logger.debug(
                    "LLM ответ: %d токенов (prompt=%d, completion=%d)",
                    usage.get("total_tokens", 0),
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                )

                duration_ms = int((time.time() - _req_start) * 1000)
                self._log_usage(
                    usage=usage,
                    duration_ms=duration_ms,
                    status="success",
                )
                self._log_langfuse_generation(
                    "chat", messages, content, usage, duration_ms,
                )

                return content

            except httpx.ConnectError as exc:
                raise LLMError(
                    f"Не удалось подключиться к {self.base_url}. "
                    "Проверьте URL и сетевое подключение."
                ) from exc

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                duration_ms = int((time.time() - _req_start) * 1000)
                if status == 401:
                    self._log_usage(duration_ms=duration_ms, status="error", http_status_code=401)
                    raise LLMError("Ошибка авторизации (401). Проверьте API-ключ.") from exc
                if status == 429:
                    self._log_usage(duration_ms=duration_ms, status="rate_limited", http_status_code=429)
                    wait = min(2 ** attempt * 2, 30)
                    logger.warning("Rate limit (429). Повтор через %ds", wait)
                    time.sleep(wait)
                    last_error = exc
                    continue

                self._log_usage(duration_ms=duration_ms, status="error",
                                error_message=str(exc)[:200], http_status_code=status)
                last_error = exc
                if attempt < self.max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        "HTTP %d (попытка %d/%d): %s. Повтор через %ds",
                        status, attempt, self.max_retries, exc, wait,
                    )
                    time.sleep(wait)

            except (httpx.RequestError, KeyError) as exc:
                duration_ms = int((time.time() - _req_start) * 1000)
                self._log_usage(duration_ms=duration_ms, status="error", error_message=str(exc)[:200])
                last_error = exc
                if attempt < self.max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        "LLM ошибка (попытка %d/%d): %s. Повтор через %ds",
                        attempt, self.max_retries, exc, wait,
                    )
                    time.sleep(wait)

        raise LLMError(f"LLM запрос не удался после {self.max_retries} попыток: {last_error}")

    def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """Отправляет запрос и парсит ответ как JSON (без schema enforcement)."""
        raw = self.chat(messages, temperature=temperature, max_tokens=max_tokens, json_mode=True)

        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            cleaned = "\n".join(lines)

        try:
            return json.loads(cleaned)
        except json.JSONDecodeError as exc:
            logger.error("Невалидный JSON от LLM:\n%s", raw[:500])
            raise LLMError(f"Модель вернула невалидный JSON: {exc}") from exc

    def chat_structured(
        self,
        messages: list[dict[str, str]],
        schema: type,
        schema_name: str = "response",
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """Отправляет запрос с JSON Schema enforcement (structured output).

        API гарантирует, что ответ соответствует схеме.

        Args:
            messages: список сообщений.
            schema: Pydantic-модель для response_format.
            schema_name: имя схемы для API.
            temperature: температура генерации.
            max_tokens: макс. токенов в ответе.

        Returns:
            Распарсенный JSON-объект, гарантированно соответствующий схеме.
        """
        url = f"{self.base_url}/v1/chat/completions"

        json_schema = schema.model_json_schema()
        # xAI требует additionalProperties: false на верхнем уровне
        json_schema["additionalProperties"] = False

        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens or self.max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": json_schema,
                },
            },
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            _req_start = time.time()
            try:
                logger.debug(
                    "LLM structured (попытка %d): model=%s, schema=%s",
                    attempt, self.model, schema_name,
                )

                response = self._client.post(url, json=payload, headers=headers)
                response.raise_for_status()

                data = response.json()
                content = data["choices"][0]["message"]["content"]

                usage = data.get("usage", {})
                duration_ms = int((time.time() - _req_start) * 1000)

                logger.debug(
                    "LLM structured ответ: %d токенов (prompt=%d, completion=%d) за %dms",
                    usage.get("total_tokens", 0),
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                    duration_ms,
                )

                self._log_usage(usage=usage, duration_ms=duration_ms, status="success")
                self._log_langfuse_generation(
                    f"structured:{schema_name}", messages, content, usage, duration_ms,
                )

                return json.loads(content)

            except httpx.ConnectError as exc:
                raise LLMError(
                    f"Не удалось подключиться к {self.base_url}."
                ) from exc

            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code
                duration_ms = int((time.time() - _req_start) * 1000)
                self._log_usage(
                    duration_ms=duration_ms, status="error",
                    error_message=str(exc)[:200], http_status_code=status_code,
                )
                if status_code == 401:
                    raise LLMError("Ошибка авторизации (401).") from exc
                if status_code == 429:
                    self._log_usage(
                        duration_ms=duration_ms, status="rate_limited",
                        http_status_code=429,
                    )
                    wait = min(2 ** attempt * 2, 30)
                    logger.warning("Rate limit (429). Повтор через %ds", wait)
                    time.sleep(wait)
                    last_error = exc
                    continue
                last_error = exc
                if attempt < self.max_retries:
                    wait = 2 ** attempt
                    logger.warning("HTTP %d, повтор через %ds", status_code, wait)
                    time.sleep(wait)

            except (httpx.RequestError, KeyError, json.JSONDecodeError) as exc:
                duration_ms = int((time.time() - _req_start) * 1000)
                self._log_usage(
                    duration_ms=duration_ms, status="error",
                    error_message=str(exc)[:200],
                )
                last_error = exc
                if attempt < self.max_retries:
                    wait = 2 ** attempt
                    logger.warning("Ошибка (попытка %d/%d): %s", attempt, self.max_retries, exc)
                    time.sleep(wait)

        raise LLMError(f"Structured запрос не удался: {last_error}")

    @staticmethod
    def estimate_tokens(text: str) -> int:
        """Приблизительная оценка количества токенов для русского текста."""
        return int(len(text) / _CHARS_PER_TOKEN_RU)

    def fits_in_context(self, text: str, reserve_tokens: int = 0) -> bool:
        """Проверяет, поместится ли текст в контекстное окно."""
        estimated = self.estimate_tokens(text)
        available = self.max_input_tokens - reserve_tokens
        return estimated <= available

    def ping(self) -> bool:
        """Проверяет доступность API."""
        try:
            response = self._client.post(
                f"{self.base_url}/v1/chat/completions",
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 5,
                },
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                },
            )
            return response.status_code == 200
        except httpx.RequestError:
            return False

    def _log_usage(
        self,
        usage: dict | None = None,
        duration_ms: int | None = None,
        status: str = "success",
        error_message: str | None = None,
        http_status_code: int | None = None,
    ) -> None:
        """Логирует API-вызов в PostgreSQL с расчётом стоимости."""
        try:
            from . import db
            provider = "xai"
            if "mistral" in self.base_url:
                provider = "mistral"
            elif "openai" in self.base_url:
                provider = "openai"

            prompt_tokens = (usage or {}).get("prompt_tokens")
            completion_tokens = (usage or {}).get("completion_tokens")

            cost_usd = _calculate_llm_cost(self.model, prompt_tokens, completion_tokens)

            ctx = _usage_context or {}
            db.log_api_usage(
                service=ctx.get("service", "agent"),
                action=ctx.get("action", "chat"),
                provider=provider,
                model=self.model,
                tender_id=ctx.get("tender_id"),
                doc_id=ctx.get("doc_id"),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=(usage or {}).get("total_tokens"),
                cost_usd=cost_usd,
                duration_ms=duration_ms,
                status=status,
                error_message=error_message,
                http_status_code=http_status_code,
            )
        except Exception as exc:
            logger.debug("Не удалось записать api_usage: %s", exc)


    def _log_langfuse_generation(
        self,
        name: str,
        messages: list[dict],
        output: str,
        usage: dict,
        duration_ms: int,
    ) -> None:
        """Логирует LLM-вызов в Langfuse как generation с токенами и стоимостью."""
        try:
            from agent.tracing import _enabled, _langfuse
            if not _enabled or not _langfuse:
                return

            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            pricing = _PRICING.get(self.model)
            input_cost = (prompt_tokens / 1_000_000) * pricing[0] if pricing else None
            output_cost = (completion_tokens / 1_000_000) * pricing[1] if pricing else None

            with _langfuse.start_as_current_observation(
                name=f"llm:{name}",
                input=messages[-1]["content"][:500] if messages else "",
                output=output[:500] if output else "",
                metadata={
                    "model": self.model,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": usage.get("total_tokens", 0),
                    "duration_ms": duration_ms,
                    "cost_input_usd": input_cost,
                    "cost_output_usd": output_cost,
                    "cost_total_usd": round(input_cost + output_cost, 6) if input_cost and output_cost else None,
                },
            ):
                pass
        except Exception as exc:
            logger.debug("Langfuse generation log error: %s", exc)

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _calculate_llm_cost(
    model: str, prompt_tokens: int | None, completion_tokens: int | None
) -> float | None:
    """Рассчитывает стоимость LLM-вызова в USD."""
    pricing = _PRICING.get(model)
    if not pricing or not prompt_tokens:
        return None
    input_cost = (prompt_tokens / 1_000_000) * pricing[0]
    output_cost = ((completion_tokens or 0) / 1_000_000) * pricing[1]
    return round(input_cost + output_cost, 6)


def calculate_ocr_cost(model: str, pages_count: int) -> float | None:
    """Рассчитывает стоимость OCR в USD."""
    price_per_page = _OCR_PRICING.get(model)
    if price_per_page is None:
        return None
    return round(pages_count * price_per_page, 6)
