-- Migration 002: Add total_cost_usd to tenders + pipeline_duration_ms
BEGIN;

ALTER TABLE tenders ADD COLUMN IF NOT EXISTS total_cost_usd DECIMAL(10,6);
ALTER TABLE tenders ADD COLUMN IF NOT EXISTS pipeline_duration_ms INT;

COMMIT;
