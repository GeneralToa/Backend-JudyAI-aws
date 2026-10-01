-- =============================================================================
-- Judy AI POC — System Settings Schema
-- Target: Aurora PostgreSQL 17.5, database `ragdb` (rag-app-prod-aurora-postgres)
--
-- Owner: Lloyd (app.* schema)
--
-- Adds one table to app.*:
--   app.system_settings     — system-wide configuration settings
--
-- Re-runnable: every statement is IF NOT EXISTS.
-- =============================================================================


CREATE TABLE IF NOT EXISTS app.system_settings (
     key   TEXT PRIMARY KEY,
     value TEXT NOT NULL,
     updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

GRANT USAGE ON SCHEMA app TO app_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON app.system_settings TO app_writer;