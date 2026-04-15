# Документация: Parser Service для тендерной документации

## 1. Назначение

Parser Service — микросервис предобработки тендерной документации. Получает ID тендера, скачивает архив документов, парсит каждый файл в унифицированную JSON-схему, распознаёт сканы через OCR, генерирует паспорта документов через LLM и записывает метаданные в PostgreSQL.

Результат работы парсера используется Agent Service для ответов на вопросы пользователей по тендерам.

---

## 2. Pipeline: этапы обработки

```
POST /ingest/{tender_id}
         │
    ┌────┴────┐
    │ Этап 1  │  Download — скачивание ZIP-архива через API tenderplan.ru
    └────┬────┘
         │
    ┌────┴────┐
    │ Этап 2  │  Extract — рекурсивная распаковка ZIP/RAR/7z
    └────┬────┘
         │
    ┌────┴────┐
    │ Этап 3  │  Parse — парсинг каждого документа (DOCX/PDF/XLSX/ODP/...)
    └────┬────┘
         │
    ┌────┴────┐
    │ Этап 4  │  OCR — распознавание сканов и изображений (Mistral OCR API)
    └────┬────┘
         │
    ┌────┴────┐
    │ Этап 5  │  Passports — генерация паспортов через LLM (параллельно, 10 workers)
    └────┬────┘
         │
    ┌────┴────┐
    │ Этап 6  │  Finalize — запись итогов в PostgreSQL, обновление document_map
    └─────────┘
```

**Статусы тендера (pipeline_state):** `created → downloaded → extracted → parsed → ocr_done → passports_done → indexed → failed`

**Идемпотентность:** повторный вызов без `--force` пропускает уже обработанные этапы. С `--force` — пересчитывает всё заново.

---

## 3. Кодовая структура

### Ядро парсинга: `tender_tools/parsers/`

| Файл | Назначение |
|---|---|
| `schema.py` | Pydantic-модели: `ParsedDocument`, `TextBlock`, `TableBlock`, `ImageBlock`, `TableRow`, `CellObject` |
| `router.py` | Диспетчер: по расширению файла → нужный парсер. Единая точка входа `parse_document()` |
| `docx_parser.py` | DOCX → ParsedDocument. Обход XML, layout-обёртки, colspan, изображения |
| `pdf_parser.py` | PDF → ParsedDocument. Склейка многостраничных таблиц, детекция сканов |
| `xlsx_parser.py` | XLSX → ParsedDocument. Каждый лист → TableBlock |
| `legacy_converter.py` | Конвертация .doc/.xls/.odt/.ods/.rtf/.ppt/.pptx/.odp через LibreOffice headless |

### Инфраструктура

| Файл | Назначение |
|---|---|
| `config.py` | Пути, URL API, расширения файлов, путь к UnRAR |
| `models.py` | Pydantic: `TenderManifest`, `DocumentRecord`, `PipelineState`, `TenderStatus` |
| `storage.py` | Управление файловой структурой `data/tenders/{id}/` |
| `downloader.py` | Потоковое скачивание архива через API tenderplan.ru с Bearer-токеном |
| `extractor.py` | Рекурсивная распаковка ZIP/RAR/7z. Исправление кириллических имён. Обход лимита длинных путей Windows |
| `ocr_processor.py` | OCR через Mistral OCR API. Два режима: PDF целиком / отдельные изображения |
| `passport.py` | Генерация паспортов и document_map через LLM (structured output) |
| `llm_client.py` | OpenAI-compatible клиент (xAI Grok). chat, chat_structured, трекинг стоимости |
| `db.py` | PostgreSQL: connection pool (psycopg v3), CRUD для всех таблиц |
| `pipeline.py` | Оркестратор всех этапов с записью метрик и стоимости |

### CLI-скрипты (корень проекта)

| Файл | Назначение |
|---|---|
| `ingest.py` | `python ingest.py <tender_id> [--force] [--workers 10]` |
| `ask.py` | `python ask.py <tender_id> [-q "вопрос"] [--mode llm/hybrid]` |

---

## 4. Поддерживаемые форматы документов

### Нативные парсеры (Python-библиотеки)

| Формат | Парсер | Библиотека |
|---|---|---|
| `.docx` | `docx_parser.py` | `python-docx` |
| `.pdf` | `pdf_parser.py` | `pdfplumber` |
| `.xlsx` | `xlsx_parser.py` | `openpyxl` |

### Конвертация через LibreOffice → нативный парсер

| Исходный формат | Конвертируется в | Затем парсится через |
|---|---|---|
| `.doc` (Word 97-2003) | `.docx` | `docx_parser.py` |
| `.xls` (Excel 97-2003) | `.xlsx` | `xlsx_parser.py` |
| `.odt` (OpenDocument Text) | `.docx` | `docx_parser.py` |
| `.ods` (OpenDocument Spreadsheet) | `.xlsx` | `xlsx_parser.py` |
| `.rtf` (Rich Text Format) | `.docx` | `docx_parser.py` |
| `.ppt` (PowerPoint 97-2003) | `.pptx` → `.pdf` | `pdf_parser.py` |
| `.pptx` (PowerPoint) | `.pdf` | `pdf_parser.py` |
| `.odp` (OpenDocument Presentation) | `.pptx` → `.pdf` | `pdf_parser.py` |

### Извлекаемые, но не парсируемые (сохраняются в S3)

`.txt`, `.csv`, `.xml`, `.html`, `.jpg`, `.jpeg`, `.png`, `.tif`, `.tiff`

---

## 5. Унифицированная JSON-схема: `ParsedDocument`

Каждый документ → упорядоченный список блоков, сохраняющий порядок элементов оригинала:

```json
{
  "doc_id": "doc_001",
  "source_filename": "Техническое задание.pdf",
  "source_format": "pdf",
  "total_pages": 45,
  "blocks": [
    {"type": "text", "block_id": "...", "content": "...", "heading_level": 1, "section_path": "1"},
    {"type": "table", "block_id": "...", "col_count": 6, "rows": [...], "page": 2, "page_end": 5},
    {"type": "image", "block_id": "...", "image_ref": "images/...", "ocr_text": "...", "ocr_status": "completed"}
  ]
}
```

### TextBlock
| Поле | Тип | Описание |
|---|---|---|
| `block_id` | string | Уникальный ID блока |
| `content` | string | Текст |
| `heading_level` | int\|null | 1-6 для заголовков, null для обычного текста |
| `section_path` | string\|null | Номер раздела ("1", "1.1", "2.3.1") |
| `page` | int\|null | Номер страницы (PDF) |

### TableBlock
| Поле | Тип | Описание |
|---|---|---|
| `col_count` | int | Количество колонок |
| `rows` | list[TableRow] | Строки таблицы |
| `page` / `page_end` | int\|null | Диапазон страниц (для многостраничных таблиц) |
| `xlsx_ref` | string\|null | Путь к материализованному XLSX |

**TableRow.row_type:** `header` / `section` / `data` / `subtotal`

**Ячейка:** `string` (простая) или `CellObject` с `colspan`/`rowspan` (для сложных таблиц)

### ImageBlock
| Поле | Тип | Описание |
|---|---|---|
| `image_ref` | string\|null | Путь к файлу изображения |
| `ocr_text` | string\|null | Распознанный текст (заполняется OCR) |
| `ocr_status` | enum | `pending` / `completed` / `skipped` / `not_needed` |

---

## 6. Ключевые алгоритмы

### DOCX: Layout-обёртки и вложенные таблицы

Некоторые DOCX используют невидимую внешнюю таблицу (десятки микроколонок, границы `nil`) с вложенными `<w:tbl>` внутри ячеек. Парсер:
1. Детектирует layout-обёртку (`_is_layout_wrapper` — первая ячейка содержит `<w:tbl>`)
2. Рекурсивно извлекает вложенные таблицы (`_extract_nested_tables`)
3. Глубоко извлекает текст через `tc_element.iter()` (`_extract_cell_text_deep`)

### PDF: Склейка многостраничных таблиц

`_merge_tables_across_pages`:
- Буфер `pending_rows` накапливает строки
- Совпадение количества колонок → склейка
- Дубликаты заголовков → пропуск
- Разорванные строки на границе страниц → объединение

### PDF: Детекция сканов

`_is_scan_page`: если `extract_text()` < 50 символов, но изображения покрывают > 50% площади страницы → `ImageBlock(ocr_status="pending")`

### ZIP: Кириллические имена файлов

`_fix_zip_filename`: ZIP из Windows кодирует имена в CP437. Пробуем CP1251 → CP866 → UTF-8, проверяя наличие кириллических символов (U+0400–U+04FF).

### Архивы: Обход лимита длинных путей Windows

Временная директория `C:/Temp/_extract/tXXX`, вложенные папки — короткие имена `_n0_0` вместо полных кириллических.

---

## 7. Внешние API

### tenderplan.ru — скачивание архивов

```
GET https://tenderplan.ru/fileviewer/api/documents/archive?tenderId={tender_id}
Headers: Authorization: Bearer {TENDERPLAN_AUTH_KEY}
Accept: application/zip
```

### xAI (Grok) — генерация паспортов и ответов

```
POST https://api.x.ai/v1/chat/completions
Headers: Authorization: Bearer {XAI_API_KEY}
Model: grok-4-fast-non-reasoning (паспорта), grok-4-fast-reasoning (ответы)
```

Используется `response_format: {"type": "json_schema"}` (structured output) для гарантии формата.

**Стоимость:** $0.20 / 1M input tokens, $0.50 / 1M output tokens

### Mistral OCR — распознавание сканов

```
POST https://api.mistral.ai/v1/ocr
Model: mistral-ocr-latest
```

Два режима: PDF целиком (`document_url`) или изображение (`image_url`).

**Стоимость:** $2.00 / 1000 страниц ($0.002/страница)

---

## 8. Переменные окружения

| Переменная | Обязательная | Описание | Пример |
|---|---|---|---|
| `TENDERPLAN_AUTH_KEY` | Да | API-ключ tenderplan.ru | `c2518644285be...` |
| `XAI_API_KEY` | Да | API-ключ xAI (Grok) | `xai-HeSjXS...` |
| `MISTRAL_API_KEY` | Да | API-ключ Mistral (OCR) | `DwVSOmA1...` |
| `DATABASE_URL` | Да | PostgreSQL connection string | `postgresql://postgres:1234@localhost:5432/postgres` |
| `LLM_BASE_URL` | Нет | URL LLM API | `https://api.x.ai` (по умолчанию) |
| `LLM_MODEL` | Нет | Модель для ответов | `grok-4-fast-reasoning` |
| `LLM_PASSPORT_MODEL` | Нет | Модель для паспортов | `grok-4-fast-non-reasoning` |
| `LIBREOFFICE_PATH` | Нет | Путь к soffice.exe | `C:\Program Files\LibreOffice\program\soffice.exe` |

---

## 9. Зависимости (Python)

```
httpx>=0.27              # HTTP-клиент (API вызовы)
pydantic>=2.0            # Валидация данных, JSON-схемы
python-dotenv>=1.0       # Загрузка .env
python-docx>=1.2         # Парсинг DOCX
pdfplumber>=0.10         # Парсинг PDF
openpyxl>=3.1            # Парсинг/экспорт XLSX
lxml>=5.0                # XML-обработка (python-docx)
py7zr>=0.21              # Распаковка 7z
rarfile>=4.2             # Распаковка RAR (требует UnRAR)
mistralai>=2.0           # Mistral OCR API
psycopg[binary]>=3.3     # PostgreSQL (v3)
psycopg_pool>=3.0        # Connection pool
rank_bm25>=0.2           # BM25 keyword search (для hybrid mode)
numpy>=2.0               # Embedding индексы (для hybrid mode)
```

### Системные зависимости

| Зависимость | Зачем | Docker |
|---|---|---|
| **LibreOffice** | Конвертация .doc/.xls/.odt/.ods/.rtf/.ppt/.pptx/.odp | `apt install libreoffice-core` |
| **UnRAR** | Распаковка .rar архивов | `apt install unrar` или бинарник |
| **PostgreSQL 14+** | Хранение метаданных и аналитики | Отдельный контейнер |

---

## 10. PostgreSQL: схема БД

Миграция: `migrations/001_initial.sql` + `migrations/002_add_total_cost.sql`

### Таблицы

| Таблица | Назначение | Ключевые поля |
|---|---|---|
| `tenders` | Реестр тендеров, статусы pipeline | `tender_id`, `status`, `total_cost_usd`, `pipeline_duration_ms` |
| `documents` | Реестр документов в тендере | `tender_id`, `doc_id`, `extension`, `parse_status`, `parse_duration_ms` |
| `passports` | Паспорта документов (JSONB) | `tender_id`, `doc_id`, `doc_type`, `passport_data` (JSONB) |
| `document_maps` | Карты документации для роутинга | `tender_id`, `routing_text`, `map_data` (JSONB) |
| `api_usage` | Трекинг всех API-вызовов и стоимости | `provider`, `model`, `prompt_tokens`, `completion_tokens`, `cost_usd` |
| `parse_metrics` | Скорость парсинга по этапам | `stage`, `duration_ms`, `items_count` |
| `pipeline_jobs` | Очередь задач (PostgreSQL-based) | `priority`, `status`, `worker_id` |

### VIEW для аналитики

| VIEW | Описание |
|---|---|
| `v_tender_costs` | Стоимость по тендерам (разбивка: passport / ocr / routing / answer) |
| `v_parse_speed` | Средняя/p95/max скорость парсинга по форматам |
| `v_recent_errors` | Ошибки API за последние 24 часа |

---

## 11. LibreOffice в парсере

LibreOffice используется в headless-режиме для конвертации форматов, которые нельзя парсить напрямую.

**Особенности:**

- **Автоматическое обнаружение:** `LIBREOFFICE_PATH` → стандартные пути → `PATH`
- **Уникальный профиль** для каждого вызова (`-env:UserInstallation=file:///tmp_profile`) — параллельные конвертации без конфликтов
- **Таймаут 120 секунд** — защита от зависания на повреждённых файлах
- **PPT/ODP → PDF** — двухшаговая конвертация: `.ppt/.odp` → `.pptx` → `.pdf`

**Docker-требования:**
```dockerfile
RUN apt-get update && apt-get install -y libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress
```

Каждый worker должен иметь свою копию LibreOffice для горизонтального масштабирования.

---

## 12. OCR-процессор

Два режима обработки:

| Режим | Когда | API-вызов | Стоимость |
|---|---|---|---|
| **PDF batch** | PDF-сканы (страницы без текстового слоя) | 1 вызов на весь PDF | $0.002 × pages |
| **Image individual** | Изображения из DOCX | 1 вызов на изображение, параллельно (5 workers) | $0.002/page |

OCR записывает `ocr_text` в `ImageBlock` внутри parsed JSON. Паспорт генерируется после OCR, поэтому распознанный текст попадает в контекст для LLM.

---

## 13. Паспорта документов

Паспорт — компактное описание документа для роутинга вопросов.

**Генерация:** LLM (grok-4-fast-non-reasoning) с structured output. Параллельно, 10 workers.

**Стратегии:**
- **Single-pass** — документ помещается в контекст → 1 LLM-вызов
- **Chunked (map-reduce)** — документ > контекста → разбивка на чанки → суммарии → финальный паспорт
- **Skip** — пустой документ (нет текста, OCR не отработал) → паспорт без LLM-вызова

**DocumentMap** — агрегат всех паспортов тендера. Метод `to_routing_text()` генерирует текст для LLM-роутера. Хранится в PostgreSQL (`document_maps.routing_text`).

---

## 14. Хранение данных: S3

S3 (или MinIO) — хранилище бинарных файлов и больших JSON.

```
s3://tender-data/
  raw/{tender_id}/source.zip                    # Оригинальный архив
  documents/{tender_id}/doc_001.docx, ...       # Извлечённые документы
  parsed/{tender_id}/doc_001.json               # Parsed JSON
  parsed/{tender_id}/doc_001/tables/*.xlsx      # Таблицы
  parsed/{tender_id}/doc_001/images/*.png       # Изображения
  indexes/{tender_id}/vectors.npy, ...          # Embedding индексы
```

**Объёмы:** ~50-100 MB на тендер. 10 000 тендеров ≈ 500 GB - 1 TB.

**Lifecycle rules:**
- `raw/` → Glacier через 30 дней
- `documents/` → Glacier через 90 дней
- `parsed/`, `indexes/` → Standard постоянно (используются при каждом запросе)

**Права:** Parser Service — read/write. Agent Service — read-only на `parsed/`, `indexes/`.

---

## 15. API-контракты Parser Service

### `POST /ingest/{tender_id}`

Запускает полный pipeline обработки тендера.

**Параметры:**
| Параметр | Тип | По умолчанию | Описание |
|---|---|---|---|
| `force` | bool | false | Пересчитать всё заново |
| `priority` | int | 0 | 0=фоновый, 10=пользователь ждёт |
| `workers` | int | 10 | Параллелизм генерации паспортов |

**Ответ (202 Accepted):**
```json
{"tender_id": "69ce8494...", "status": "queued", "job_id": 42}
```

### `GET /status/{tender_id}`

Возвращает текущий статус обработки.

**Ответ:**
```json
{
  "tender_id": "69ce8494...",
  "status": "passports_done",
  "document_count": 11,
  "total_cost_usd": 0.022701,
  "pipeline_duration_ms": 36934,
  "timestamps": {
    "downloaded_at": "2026-04-07T01:38:43Z",
    "extracted_at": "2026-04-07T01:38:43Z",
    "parsed_at": "2026-04-07T01:38:44Z",
    "ocr_done_at": "2026-04-07T01:38:56Z",
    "passports_done_at": "2026-04-07T01:39:10Z"
  }
}
```

### `GET /documents/{tender_id}`

Список документов тендера с метриками парсинга.

### `GET /passport/{tender_id}/{doc_id}`

Паспорт конкретного документа.

---

## 16. Стоимость обработки (benchmark)

Реальные данные на тестовых тендерах:

| Тендер | Документов | Сканов (OCR) | Стоимость LLM | Стоимость OCR | Итого | Время |
|---|---|---|---|---|---|---|
| 5 документов (без сканов) | 5 | 0 | $0.0087 | $0.00 | **$0.0087** | 13 сек |
| 11 документов (17 сканов) | 11 | 17 | $0.0115 | $0.034 | **$0.0455** | 37 сек |

**Средняя стоимость:** ~$0.005-0.05 за тендер (зависит от количества сканов).

При 10 000 тендеров: **$50-500** на предобработку.

---

## 17. Очередь предобработки

```
[API / Scheduler] → [pipeline_jobs в PostgreSQL] → [Parser Workers]
```

Worker loop:
```sql
SELECT * FROM pipeline_jobs
WHERE status = 'queued'
ORDER BY priority DESC, queued_at
FOR UPDATE SKIP LOCKED
LIMIT 1
```

- Приоритизация: `priority=10` (пользователь ждёт) vs `priority=0` (фоновая обработка)
- Worker'ы stateless: берут tender_id → обрабатывают → пишут в S3 + PostgreSQL
- Retry: `max_retries=3`, ошибки записываются в `error_message`
