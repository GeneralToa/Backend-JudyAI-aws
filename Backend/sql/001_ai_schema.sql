-- =============================================================================
-- Judy AI POC — data model for the AI workstream
-- Target: Aurora PostgreSQL 17.5, database `ragdb` (rag-app-prod-aurora-postgres)
--
-- DRAFT FOR REVIEW — Andres (cluster owner) and Lloyd (app owner).
-- Nothing here has been applied to the cluster.
--
-- Covers the SOW "AI and Data Management" line item:
--   "Database design for storing contract metadata, AI analysis results,
--    signature tracking, and obligation records"
--
-- Ownership is enforced structurally by splitting into two schemas:
--
--   app.*       owned by the application (Lloyd). App writes, AI only reads.
--               Minimal shape here — expand as the signature workflow needs.
--   judy_ai.*   owned by the AI workstream (Zuhair). AI writes, app only reads.
--
-- Untouched: bedrock_integration.* (the Knowledge Base vector store). This
-- schema does not read from or write to it.
--
-- Re-runnable: every statement is IF NOT EXISTS.
-- gen_random_uuid() is core in PG13+, so no pgcrypto extension is needed.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS judy_ai;


-- =============================================================================
-- app schema — application-owned (Lloyd)
-- =============================================================================

-- One row per uploaded contract document.
-- The AI side never writes here; it reads s3_key to locate the document and
-- joins everything else to contracts.id.
CREATE TABLE IF NOT EXISTS app.contracts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    s3_bucket           TEXT        NOT NULL,
    s3_key              TEXT        NOT NULL,
    original_filename   TEXT        NOT NULL,
    mime_type           TEXT,
    file_size_bytes     BIGINT,
    -- Optional, set by the user at upload. The template-detection agent records
    -- what it *detected* separately, in judy_ai.template_detections.
    template_type       TEXT        CHECK (template_type IN ('sow', 'purchase_agreement', 'msa')),
    uploaded_by         TEXT,       -- Cognito sub
    uploaded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Application workflow state. AI analysis state lives in judy_ai.agent_runs.
    status              TEXT        NOT NULL DEFAULT 'uploaded'
                                    CHECK (status IN ('uploaded', 'analyzing', 'ready_for_review',
                                                      'routed_for_signature', 'signed', 'failed')),
    signed_at           TIMESTAMPTZ,
    CONSTRAINT contracts_s3_object_unique UNIQUE (s3_bucket, s3_key)
);

-- Signature tracking. Deliberately minimal: this is Lloyd's domain and the
-- approval-chain model belongs to him. It appears here only because the
-- obligation-tracking agent triggers off event_type = 'completed'.
CREATE TABLE IF NOT EXISTS app.signature_events (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id   UUID        NOT NULL REFERENCES app.contracts(id) ON DELETE CASCADE,
    event_type    TEXT        NOT NULL
                              CHECK (event_type IN ('sent_for_signature', 'signer_confirmed',
                                                    'completed', 'declined')),
    signer_email  TEXT,
    occurred_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS signature_events_contract_idx
    ON app.signature_events (contract_id, occurred_at DESC);


-- =============================================================================
-- judy_ai schema — AI-owned (Zuhair)
-- =============================================================================

-- Bedrock Data Automation output. Every agent consumes extracted_text, so this
-- runs once per contract and all four agents read the same extraction.
--
-- Split storage on purpose: the full BDA artifact (blocks, tables, geometry) is
-- bulky and effectively never queried, so it stays in S3. The normalized plain
-- text is what the agents actually prompt against, so it is stored inline —
-- at the SOW's ceiling of 20 pages / 10 MB that is well under 100 KB per row.
CREATE TABLE IF NOT EXISTS judy_ai.document_extractions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id         UUID        NOT NULL REFERENCES app.contracts(id) ON DELETE CASCADE,
    bda_invocation_arn  TEXT,
    status              TEXT        NOT NULL DEFAULT 'pending'
                                    CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    raw_output_s3_key   TEXT,       -- full BDA JSON artifact
    extracted_text      TEXT,       -- normalized text the agents prompt against
    page_count          INTEGER,
    char_count          INTEGER,
    error_message       TEXT,       -- parsing failures also go to the SQS DLQ per the SOW
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS document_extractions_contract_idx
    ON judy_ai.document_extractions (contract_id, started_at DESC);

-- One row per agent invocation. This is what the "trigger analysis" API creates
-- and what the "retrieve results" API polls.
--
-- raw_response keeps the exact model output. The SOW requires AI behaviour to be
-- "validated manually against sample contracts" — that review is only practical
-- if the unedited output is retained alongside the parsed rows.
--
-- model_id / prompt_version exist so that when output quality changes during
-- tuning, it is possible to tell which prompt and model produced which result.
--
-- The token and latency columns feed SOW acceptance criterion #11 (performance
-- baseline) without needing separate instrumentation.
CREATE TABLE IF NOT EXISTS judy_ai.agent_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id     UUID        NOT NULL REFERENCES app.contracts(id) ON DELETE CASCADE,
    extraction_id   UUID        NOT NULL REFERENCES judy_ai.document_extractions(id) ON DELETE CASCADE,
    agent_type      TEXT        NOT NULL
                                CHECK (agent_type IN ('risk_clause',
                                                      'template_prepopulation',
                                                      'summary',
                                                      'obligation_tracking')),
    status          TEXT        NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    model_id        TEXT,
    prompt_version  TEXT,
    raw_response    JSONB,
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    latency_ms      INTEGER,
    error_message   TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS agent_runs_lookup_idx
    ON judy_ai.agent_runs (contract_id, agent_type, started_at DESC);

CREATE INDEX IF NOT EXISTS agent_runs_status_idx
    ON judy_ai.agent_runs (status) WHERE status IN ('pending', 'running');


-- -----------------------------------------------------------------------------
-- Agent 1 — risk & clause analysis (pre-signing)
-- -----------------------------------------------------------------------------
-- risk_category uses the SOW's own four finding types verbatim. The specific
-- rule that fired lives in playbook_section (NULL when the finding came from
-- general "common patterns" rather than a playbook rule).
--
-- suggested_language is the SOW's advisory clause alternative. It is stored
-- beside the risk and never applied to the source document.
--
-- source_quote / source_page make manual validation possible — a reviewer can
-- check a flag against the actual sentence that triggered it.
CREATE TABLE IF NOT EXISTS judy_ai.contract_risks (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id             UUID        NOT NULL REFERENCES judy_ai.agent_runs(id) ON DELETE CASCADE,
    -- Denormalized from agent_runs on purpose: every dashboard query filters by
    -- contract, and this avoids a join through agent_runs on the read path.
    contract_id        UUID        NOT NULL REFERENCES app.contracts(id) ON DELETE CASCADE,
    risk_category      TEXT        NOT NULL
                                   CHECK (risk_category IN ('unusual_term', 'missing_clause',
                                                            'date_mismatch', 'compliance_gap')),
    severity           TEXT        NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
    title              TEXT        NOT NULL,   -- short label for the dashboard row
    detail             TEXT        NOT NULL,   -- what is wrong and why
    playbook_section   TEXT,                   -- e.g. 'access_to_personal_information'
    suggested_language TEXT,                   -- advisory alternative clause
    source_quote       TEXT,
    source_page        INTEGER,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS contract_risks_contract_idx
    ON judy_ai.contract_risks (contract_id, severity);

CREATE INDEX IF NOT EXISTS contract_risks_run_idx
    ON judy_ai.contract_risks (run_id);


-- -----------------------------------------------------------------------------
-- Agent 2 — template pre-population (pre-signing)
-- -----------------------------------------------------------------------------
-- Document-level output: which of the (up to 3) predefined template types this
-- document matches. Per-field output goes in contract_fields below.
CREATE TABLE IF NOT EXISTS judy_ai.template_detections (
    id                     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                 UUID        NOT NULL UNIQUE REFERENCES judy_ai.agent_runs(id) ON DELETE CASCADE,
    contract_id            UUID        NOT NULL REFERENCES app.contracts(id) ON DELETE CASCADE,
    detected_template_type TEXT        NOT NULL
                                       CHECK (detected_template_type IN ('sow', 'purchase_agreement',
                                                                         'msa', 'unknown')),
    rationale              TEXT,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS template_detections_contract_idx
    ON judy_ai.template_detections (contract_id);

-- One row per detected signature block or form field.
-- position holds the BDA bounding box as {"x":..,"y":..,"width":..,"height":..}
-- in normalized page coordinates, so the app can place an overlay without
-- re-parsing the document.
CREATE TABLE IF NOT EXISTS judy_ai.contract_fields (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id       UUID        NOT NULL REFERENCES judy_ai.agent_runs(id) ON DELETE CASCADE,
    contract_id  UUID        NOT NULL REFERENCES app.contracts(id) ON DELETE CASCADE,
    field_type   TEXT        NOT NULL
                             CHECK (field_type IN ('signature', 'initial', 'date', 'full_name',
                                                   'title', 'company', 'text')),
    label        TEXT,                    -- what the document calls it
    signer_role  TEXT,                    -- e.g. 'supplier', 'company', 'witness'
    page         INTEGER,
    position     JSONB,
    is_required  BOOLEAN     NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS contract_fields_contract_idx
    ON judy_ai.contract_fields (contract_id);

CREATE INDEX IF NOT EXISTS contract_fields_run_idx
    ON judy_ai.contract_fields (run_id);


-- -----------------------------------------------------------------------------
-- Agent 3 — plain-language summary (pre-signing)
-- -----------------------------------------------------------------------------
-- key_points is a JSONB array of strings. It is display-only and never
-- filtered on, so it does not justify its own table.
CREATE TABLE IF NOT EXISTS judy_ai.contract_summaries (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id       UUID        NOT NULL UNIQUE REFERENCES judy_ai.agent_runs(id) ON DELETE CASCADE,
    contract_id  UUID        NOT NULL REFERENCES app.contracts(id) ON DELETE CASCADE,
    summary_text TEXT        NOT NULL,
    key_points   JSONB,
    word_count   INTEGER,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS contract_summaries_contract_idx
    ON judy_ai.contract_summaries (contract_id);


-- -----------------------------------------------------------------------------
-- Agent 4 — obligation tracking (post-signing)
-- -----------------------------------------------------------------------------
-- The SOW scopes this to obligations "where clearly stated in document text".
-- due_date and raw_date_text are kept as a pair on purpose: when the document
-- says something like "within 30 days of the Effective Date", the parsed date
-- may be absent or uncertain, but the original wording must not be lost.
--
-- recurrence covers the client's stated case of commission percentages that
-- change annually.
CREATE TABLE IF NOT EXISTS judy_ai.contract_obligations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID        NOT NULL REFERENCES judy_ai.agent_runs(id) ON DELETE CASCADE,
    contract_id     UUID        NOT NULL REFERENCES app.contracts(id) ON DELETE CASCADE,
    obligation_type TEXT        NOT NULL
                                CHECK (obligation_type IN ('commitment', 'renewal',
                                                           'milestone', 'other')),
    description     TEXT        NOT NULL,
    owner_party     TEXT,                  -- who owes it, e.g. 'supplier' / 'company'
    due_date        DATE,                  -- NULL when not clearly stated
    raw_date_text   TEXT,                  -- what the document literally said
    recurrence      TEXT        CHECK (recurrence IN ('one_time', 'monthly', 'quarterly', 'annual')),
    source_quote    TEXT,
    source_page     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS contract_obligations_contract_idx
    ON judy_ai.contract_obligations (contract_id);

-- Drives the renewal / milestone alert view on the dashboard.
CREATE INDEX IF NOT EXISTS contract_obligations_due_idx
    ON judy_ai.contract_obligations (due_date) WHERE due_date IS NOT NULL;


-- =============================================================================
-- Read helper
-- =============================================================================
-- A contract can be analyzed more than once (re-upload after legal review).
-- Results are tied to the run that produced them, so "current results" means
-- the newest succeeded run per contract per agent. This view keeps that logic
-- in one place instead of repeating it in every API query.
CREATE OR REPLACE VIEW judy_ai.v_latest_agent_runs AS
SELECT DISTINCT ON (contract_id, agent_type)
       id AS run_id,
       contract_id,
       agent_type,
       model_id,
       prompt_version,
       completed_at
FROM   judy_ai.agent_runs
WHERE  status = 'succeeded'
ORDER  BY contract_id, agent_type, started_at DESC;


-- =============================================================================
-- Grants — REVIEW WITH ANDRES, he owns cluster roles
-- =============================================================================
-- Intent: the AI Lambdas write only to judy_ai and read only from app; the
-- application does the reverse. Role creation and credential handling are
-- Andres's call, so the role names below are a proposal, not a decision.
--
-- CREATE ROLE judy_ai_writer LOGIN;
-- CREATE ROLE app_writer     LOGIN;
--
-- GRANT USAGE ON SCHEMA judy_ai TO judy_ai_writer, app_writer;
-- GRANT USAGE ON SCHEMA app     TO judy_ai_writer, app_writer;
--
-- GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA judy_ai TO judy_ai_writer;
-- GRANT SELECT                  ON ALL TABLES IN SCHEMA app     TO judy_ai_writer;
--
-- GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA app     TO app_writer;
-- GRANT SELECT                  ON ALL TABLES IN SCHEMA judy_ai TO app_writer;
