-- ============================================================================
-- Migration 001: Initial schema for Tender Parser Service
-- ============================================================================
-- PostgreSQL 14+
-- Run: psql -U postgres -d tender_parser -f migrations/001_initial.sql
-- ============================================================================

BEGIN;

-- --------------------------------------------------------------------------
-- 1. tenders — реестр тендеров и статусы pipeline
-- --------------------------------------------------------------------------
-- Заменяет: manifest.json + index.json
-- Используется: очередь предобработки, мониторинг прогресса, retry

CREATE TABLE IF NOT EXISTS tenders (
    tender_id           TEXT PRIMARY KEY,
    status              TEXT NOT NULL DEFAULT 'created'
                        CHECK (status IN (
                            'created',
                            'downloaded',
                            'extracted',
                            'parsed',
                            'ocr_done',
                            'passports_done',
                            'indexed',
                            'failed'
                        )),
    source_url          TEXT,

    -- Временные метки pipeline (каждый этап)
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    downloaded_at       TIMESTAMPTZ,
    extracted_at        TIMESTAMPTZ,
    parsed_at           TIMESTAMPTZ,
    ocr_done_at         TIMESTAMPTZ,
    passports_done_at   TIMESTAMPTZ,
    indexed_at          TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Статистика
    document_count      INT DEFAULT 0,
    total_size_bytes    BIGINT DEFAULT 0,

    -- Ошибки и retry
    error_message       TEXT,
    retry_count         INT DEFAULT 0,

    -- S3 ссылки
    archive_s3_path     TEXT
);

CREATE INDEX IF NOT EXISTS idx_tenders_status ON tenders(status);
CREATE INDEX IF NOT EXISTS idx_tenders_created ON tenders(created_at);

-- --------------------------------------------------------------------------
-- 2. documents — реестр документов в тендере
-- --------------------------------------------------------------------------
-- Заменяет: список DocumentRecord в manifest.json
-- Используется: отслеживание парсинга каждого файла, статистика

CREATE TABLE IF NOT EXISTS documents (
    id                  SERIAL PRIMARY KEY,
    tender_id           TEXT NOT NULL REFERENCES tenders(tender_id) ON DELETE CASCADE,
    doc_id              TEXT NOT NULL,

    -- Метаданные оригинального файла
    original_filename   TEXT NOT NULL,
    stored_filename     TEXT NOT NULL,
    extension           TEXT NOT NULL,
    size_bytes          INT NOT NULL,
    source_archive      TEXT,
    archive_path        TEXT,

    -- Статусы обработки
    parse_status        TEXT NOT NULL DEFAULT 'pending'
                        CHECK (parse_status IN ('pending', 'parsed', 'failed', 'unsupported')),
    ocr_status          TEXT NOT NULL DEFAULT 'not_needed'
                        CHECK (ocr_status IN ('not_needed', 'pending', 'completed', 'failed')),

    -- S3 ссылки
    raw_file_s3_path    TEXT,
    parsed_json_s3_path TEXT,

    -- Статистика парсинга
    text_blocks_count   INT,
    tables_count        INT,
    images_count        INT,
    total_pages         INT,
    estimated_tokens    INT,

    -- Временные метки
    extracted_at        TIMESTAMPTZ,
    parsed_at           TIMESTAMPTZ,

    -- Скорость парсинга
    parse_duration_ms   INT,
    conversion_duration_ms INT,           -- время конвертации LibreOffice (если применимо)

    UNIQUE (tender_id, doc_id)
);

CREATE INDEX IF NOT EXISTS idx_documents_tender ON documents(tender_id);
CREATE INDEX IF NOT EXISTS idx_documents_parse_status ON documents(parse_status);
CREATE INDEX IF NOT EXISTS idx_documents_extension ON documents(extension);

-- --------------------------------------------------------------------------
-- 3. passports — паспорта документов (LLM-генерированные)
-- --------------------------------------------------------------------------
-- Заменяет: *_passport.json файлы
-- Используется: роутинг вопросов, построение document_map

CREATE TABLE IF NOT EXISTS passports (
    id                  SERIAL PRIMARY KEY,
    tender_id           TEXT NOT NULL,
    doc_id              TEXT NOT NULL,

    -- Денормализованные поля (для SQL-запросов без распаковки JSONB)
    doc_type            TEXT,
    title               TEXT,
    summary             TEXT,

    -- Полный паспорт как JSONB
    passport_data       JSONB NOT NULL,

    -- Метаданные генерации
    model_used          TEXT,
    prompt_tokens       INT,
    completion_tokens   INT,
    generation_cost_usd DECIMAL(10,6),
    generation_duration_ms INT,
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (tender_id, doc_id),
    FOREIGN KEY (tender_id, doc_id) REFERENCES documents(tender_id, doc_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_passports_tender ON passports(tender_id);
CREATE INDEX IF NOT EXISTS idx_passports_doc_type ON passports(doc_type);
CREATE INDEX IF NOT EXISTS idx_passports_gin ON passports USING GIN (passport_data);

-- --------------------------------------------------------------------------
-- 4. document_maps — агрегированные карты документации тендера
-- --------------------------------------------------------------------------
-- Заменяет: document_map.json
-- Используется: LLM-роутер получает routing_text при каждом вопросе

CREATE TABLE IF NOT EXISTS document_maps (
    tender_id           TEXT PRIMARY KEY REFERENCES tenders(tender_id) ON DELETE CASCADE,
    map_data            JSONB NOT NULL,
    routing_text        TEXT,
    passports_count     INT,
    estimated_tokens    INT,
    generated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- --------------------------------------------------------------------------
-- 5. api_usage — трекинг стоимости, скорости и ошибок всех API-вызовов
-- --------------------------------------------------------------------------
-- Используется: аналитика стоимости, мониторинг rate limits, дебаг ошибок
-- Провайдеры: xAI (Grok), Mistral (OCR), OpenAI (если embedding)

CREATE TABLE IF NOT EXISTS api_usage (
    id                  BIGSERIAL PRIMARY KEY,
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Контекст вызова
    service             TEXT NOT NULL,            -- parser / agent
    action              TEXT NOT NULL,            -- passport / routing / answer / ocr / embedding
    tender_id           TEXT,
    doc_id              TEXT,

    -- API-провайдер
    provider            TEXT NOT NULL,            -- xai / mistral / openai
    model               TEXT NOT NULL,

    -- Потребление токенов (LLM)
    prompt_tokens       INT,
    completion_tokens   INT,
    total_tokens        INT,

    -- Потребление OCR (Mistral OCR — usage_info)
    ocr_pages_count     INT,                      -- количество страниц OCR
    ocr_doc_size_bytes  INT,                      -- размер отправленного документа

    -- Стоимость
    cost_usd            DECIMAL(10,6),

    -- Производительность
    duration_ms         INT,

    -- Результат
    status              TEXT NOT NULL DEFAULT 'success'
                        CHECK (status IN ('success', 'error', 'timeout', 'rate_limited')),
    error_message       TEXT,
    http_status_code    INT
);

CREATE INDEX IF NOT EXISTS idx_api_usage_timestamp ON api_usage(timestamp);
CREATE INDEX IF NOT EXISTS idx_api_usage_service_action ON api_usage(service, action);
CREATE INDEX IF NOT EXISTS idx_api_usage_tender ON api_usage(tender_id);
CREATE INDEX IF NOT EXISTS idx_api_usage_provider_model ON api_usage(provider, model);
CREATE INDEX IF NOT EXISTS idx_api_usage_errors ON api_usage(status) WHERE status != 'success';

-- --------------------------------------------------------------------------
-- 6. pipeline_jobs — очередь задач предобработки
-- --------------------------------------------------------------------------
-- Используется: PostgreSQL-based очередь (без Redis/Celery)
-- Worker loop: SELECT ... WHERE status='queued' ORDER BY priority DESC, queued_at
--              FOR UPDATE SKIP LOCKED LIMIT 1

CREATE TABLE IF NOT EXISTS pipeline_jobs (
    id                  BIGSERIAL PRIMARY KEY,
    tender_id           TEXT NOT NULL REFERENCES tenders(tender_id),

    -- Управление очередью
    priority            INT NOT NULL DEFAULT 0,   -- 0=фоновый, 10=пользователь ждёт
    status              TEXT NOT NULL DEFAULT 'queued'
                        CHECK (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    worker_id           TEXT,

    -- Временные метки
    queued_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,

    -- Результат
    error_message       TEXT,
    retry_count         INT DEFAULT 0,
    max_retries         INT DEFAULT 3
);

CREATE INDEX IF NOT EXISTS idx_jobs_queue
    ON pipeline_jobs(status, priority DESC, queued_at)
    WHERE status = 'queued';
CREATE INDEX IF NOT EXISTS idx_jobs_tender ON pipeline_jobs(tender_id);
CREATE INDEX IF NOT EXISTS idx_jobs_running ON pipeline_jobs(worker_id)
    WHERE status = 'running';

-- --------------------------------------------------------------------------
-- 7. parse_metrics — метрики скорости парсинга (по этапам)
-- --------------------------------------------------------------------------
-- Отдельная таблица для детальной аналитики производительности парсера

CREATE TABLE IF NOT EXISTS parse_metrics (
    id                  BIGSERIAL PRIMARY KEY,
    timestamp           TIMESTAMPTZ NOT NULL DEFAULT now(),
    tender_id           TEXT NOT NULL,
    doc_id              TEXT,

    -- Этап pipeline
    stage               TEXT NOT NULL
                        CHECK (stage IN (
                            'download', 'extract', 'convert',
                            'parse_docx', 'parse_pdf', 'parse_xlsx',
                            'ocr', 'passport', 'index',
                            'full_pipeline'
                        )),

    -- Производительность
    duration_ms         INT NOT NULL,
    input_size_bytes    INT,                      -- размер входного файла
    output_size_bytes   INT,                      -- размер результата (parsed JSON и т.д.)
    items_count         INT,                      -- кол-во обработанных элементов (блоков, страниц, чанков)

    -- Результат
    status              TEXT NOT NULL DEFAULT 'success'
                        CHECK (status IN ('success', 'error')),
    error_message       TEXT
);

CREATE INDEX IF NOT EXISTS idx_parse_metrics_tender ON parse_metrics(tender_id);
CREATE INDEX IF NOT EXISTS idx_parse_metrics_stage ON parse_metrics(stage);
CREATE INDEX IF NOT EXISTS idx_parse_metrics_timestamp ON parse_metrics(timestamp);

-- --------------------------------------------------------------------------
-- Вспомогательные VIEW для аналитики
-- --------------------------------------------------------------------------

-- Стоимость обработки по тендерам
CREATE OR REPLACE VIEW v_tender_costs AS
SELECT
    tender_id,
    COUNT(*) AS api_calls,
    SUM(total_tokens) AS total_tokens,
    SUM(cost_usd) AS total_cost_usd,
    SUM(duration_ms) AS total_duration_ms,
    SUM(CASE WHEN action = 'passport' THEN cost_usd ELSE 0 END) AS passport_cost,
    SUM(CASE WHEN action = 'ocr' THEN cost_usd ELSE 0 END) AS ocr_cost,
    SUM(CASE WHEN action = 'routing' THEN cost_usd ELSE 0 END) AS routing_cost,
    SUM(CASE WHEN action = 'answer' THEN cost_usd ELSE 0 END) AS answer_cost
FROM api_usage
GROUP BY tender_id;

-- Средняя скорость парсинга по форматам
CREATE OR REPLACE VIEW v_parse_speed AS
SELECT
    stage,
    COUNT(*) AS total_ops,
    ROUND(AVG(duration_ms)) AS avg_ms,
    ROUND(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms)) AS p95_ms,
    MAX(duration_ms) AS max_ms,
    SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) AS errors
FROM parse_metrics
GROUP BY stage;

-- Ошибки API за последние 24 часа
CREATE OR REPLACE VIEW v_recent_errors AS
SELECT
    timestamp,
    service,
    action,
    provider,
    model,
    status,
    http_status_code,
    error_message,
    tender_id,
    doc_id
FROM api_usage
WHERE status != 'success'
  AND timestamp > now() - INTERVAL '24 hours'
ORDER BY timestamp DESC;

COMMIT;
