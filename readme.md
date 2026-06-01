# Tender Parser Service

FastAPI-сервис для скачивания, распаковки, парсинга и подготовки тендерной документации.

## Структура

```
app/
  api/          # HTTP-роуты (health, ingest, tenders)
  clients/      # внешние клиенты: LLM, OCR-прокси, трейсинг
  core/         # конфигурация и логирование
  db/           # SQLAlchemy engine и сессии
  models/       # ORM-модели
  repositories/ # доступ к данным
  schemas/      # Pydantic-схемы (API + манифест)
  services/     # пайплайн: download -> extract -> parse -> OCR -> passports
    parsers/    # парсеры форматов (pdf, docx, xlsx, legacy)
  cli.py        # CLI-обработка тендеров
  main.py       # FastAPI entrypoint
alembic/        # миграции БД
```

## Установка

```bash
python -m venv venv
venv\Scripts\activate            # Windows
pip install -r requirements.txt
copy .env.example .env           # заполнить ключи и DATABASE_URL
```

## Миграции БД

```bash
alembic upgrade head
```

## Запуск

```bash
uvicorn app.main:app --reload
```

Swagger UI:

```text
http://127.0.0.1:8000/docs
```

## CLI

```bash
python -m app.cli <tender_id> [--force] [-w 10] [-v]
```

## Endpoints

### `GET /health`

Проверка доступности сервиса.

**Ответ:**
```json
{"status": "ok"}
```

---

### `POST /ingest/{tender_id}`

Запускает полный pipeline обработки тендера:

1. скачивание архива документов;
2. распаковка ZIP/RAR/7z;
3. парсинг документов;
4. OCR сканов и изображений;
5. генерация паспортов документов;
6. запись метрик и статусов в PostgreSQL.

**Query params:**

| Параметр | Описание |
|---|---|
| `force` | Пересчитать тендер заново |
| `priority` | Приоритет задачи |
| `workers` | Количество воркеров для генерации паспортов |

---

### `GET /status/{tender_id}`

Возвращает статус обработки тендера, количество документов, стоимость и длительность pipeline.

---

### `GET /tenders/{tender_id}/documents`

Возвращает список документов тендера и метрики парсинга по каждому документу.

---

### `GET /tenders/{tender_id}/passports`

Возвращает паспорта всех документов тендера.

Паспорт содержит:
- тип документа;
- название;
- краткое описание;
- ключевые темы;
- ключевые сущности;
- список разделов.

---

### `GET /tenders/{tender_id}/document-map`

Возвращает агрегированную карту документации (`document_map`) и `routing_text`.

Используется агентом для роутинга вопросов по документам.

---

### `GET /tenders/{tender_id}/parsed/{doc_id}`

Возвращает полный `ParsedDocument` JSON для конкретного документа.

---

### `GET /tenders/{tender_id}/sources/{doc_id}`

Возвращает parsed-блоки документа для отображения источников во фронтенде.

Можно передать `block_ids`, чтобы получить только конкретные фрагменты:

```text
/tenders/{tender_id}/sources/{doc_id}?block_ids=doc_001_b001,doc_001_tbl_002
```

---

### `POST /tenders/{tender_id}/additional-documents`

Загружает дополнительные пользовательские документы к уже обработанному тендеру и запускает их обработку.

Используется, когда пользователь хочет добавить свои файлы в контекст анализа.
```