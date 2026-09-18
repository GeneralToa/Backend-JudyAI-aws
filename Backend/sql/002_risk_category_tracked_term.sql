-- =============================================================================
-- 002 — add 'tracked_term' to judy_ai.contract_risks.risk_category
--
-- DRAFT FOR REVIEW — not applied. Additive, no data change.
--
-- Why
-- ---
-- The client's flagging criteria (call of 2026-09-16) include "anything with a
-- date or number attached" — expiry dates, renewal windows, notice periods,
-- commission percentages, monetary thresholds. Those are things a reviewer must
-- be shown before signing, but none of them is an unusual term, a missing
-- clause, a date *mismatch* or a compliance gap. With only the SOW's four
-- categories available, the risk agent had nowhere to file them and dropped
-- them: on the validation corpus it reported zero of the three it was built to
-- catch. A category that fits is the fix; prompt wording alone cannot be.
--
-- Constraint name below is the one PostgreSQL auto-assigned to the inline CHECK
-- in 001_ai_schema.sql. Verify with:
--   SELECT conname FROM pg_constraint
--    WHERE conrelid = 'judy_ai.contract_risks'::regclass AND contype = 'c';
-- =============================================================================

ALTER TABLE judy_ai.contract_risks
    DROP CONSTRAINT IF EXISTS contract_risks_risk_category_check;

ALTER TABLE judy_ai.contract_risks
    ADD CONSTRAINT contract_risks_risk_category_check
    CHECK (risk_category IN ('unusual_term', 'missing_clause', 'date_mismatch',
                             'compliance_gap', 'tracked_term'));
