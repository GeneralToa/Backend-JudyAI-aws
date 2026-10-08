-- =============================================================================
-- 006 — add full_name and title columns to app.signer_signatures
--
-- Additive schema change, no data change. Idempotent — IF NOT EXISTS makes
-- each ADD COLUMN a no-op when the column is already present.
--
-- What
-- ----
-- Adds two nullable TEXT columns to app.signer_signatures:
--   full_name  the signer's display name at the time of signing
--   title      the signer's job title at the time of signing
--
-- Note for the Terraform runner: it splits this file on semicolons, so the
-- comments here deliberately contain none.
-- =============================================================================

ALTER TABLE app.signer_signatures 
    ADD COLUMN IF NOT EXISTS full_name TEXT NULL,
    ADD COLUMN IF NOT EXISTS title TEXT NULL;