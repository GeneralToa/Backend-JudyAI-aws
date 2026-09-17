-- =============================================================================
-- Judy AI POC — data model for the AI workstream
-- Target: Aurora PostgreSQL 17.5, database `ragdb` (rag-app-prod-aurora-postgres)
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
--
-- Role creation and grants are managed by Terraform (sql.tf).
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS app;
CREATE SCHEMA IF NOT EXISTS judy_ai;


-- =============================================================================
-- app schema — application-owned (Lloyd)
-- =============================================================================

CREATE TABLE IF NOT EXISTS app.contracts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    s3_bucket           TEXT        NOT NULL,
    s3_key              TEXT        NOT NULL,
    original_filename   TEXT        NOT NULL,
    mime_type           TEXT,
    file_size_bytes     BIGINT,
    template_type       TEXT        CHECK (template_type IN ('sow', 'purchase_agreement', 'msa')),
    uploaded_by         TEXT,
    uploaded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    status              TEXT        NOT NULL DEFAULT 'uploaded'
                                    CHECK (status IN ('uploaded', 'analyzing', 'ready_for_review',
                                                      'routed_for_signature', 'signed', 'failed')),
    signed_at           TIMESTAMPTZ,
    CONSTRAINT contracts_s3_object_unique UNIQUE (s3_bucket, s3_key)
);

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

CREATE TABLE IF NOT EXISTS judy_ai.document_extractions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id         UUID        NOT NULL REFERENCES app.contracts(id) ON DELETE CASCADE,
    bda_invocation_arn  TEXT,
    status              TEXT        NOT NULL DEFAULT 'pending'
                                    CHECK (status IN ('pending', 'running', 'succeeded', 'failed')),
    raw_output_s3_key   TEXT,
    extracted_text      TEXT,
    page_count          INTEGER,
    char_count          INTEGER,
    error_message       TEXT,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS document_extractions_contract_idx
    ON judy_ai.document_extractions (contract_id, started_at DESC);

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


-- Agent 1 — risk & clause analysis
CREATE TABLE IF NOT EXISTS judy_ai.contract_risks (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id             UUID        NOT NULL REFERENCES judy_ai.agent_runs(id) ON DELETE CASCADE,
    contract_id        UUID        NOT NULL REFERENCES app.contracts(id) ON DELETE CASCADE,
    risk_category      TEXT        NOT NULL
                                   CHECK (risk_category IN ('unusual_term', 'missing_clause',
                                                            'date_mismatch', 'compliance_gap')),
    severity           TEXT        NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
    title              TEXT        NOT NULL,
    detail             TEXT        NOT NULL,
    playbook_section   TEXT,
    suggested_language TEXT,
    source_quote       TEXT,
    source_page        INTEGER,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS contract_risks_contract_idx
    ON judy_ai.contract_risks (contract_id, severity);

CREATE INDEX IF NOT EXISTS contract_risks_run_idx
    ON judy_ai.contract_risks (run_id);


-- Agent 2 — template pre-population
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

CREATE TABLE IF NOT EXISTS judy_ai.contract_fields (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id       UUID        NOT NULL REFERENCES judy_ai.agent_runs(id) ON DELETE CASCADE,
    contract_id  UUID        NOT NULL REFERENCES app.contracts(id) ON DELETE CASCADE,
    field_type   TEXT        NOT NULL
                             CHECK (field_type IN ('signature', 'initial', 'date', 'full_name',
                                                   'title', 'company', 'text')),
    label        TEXT,
    signer_role  TEXT,
    page         INTEGER,
    position     JSONB,
    is_required  BOOLEAN     NOT NULL DEFAULT true,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS contract_fields_contract_idx
    ON judy_ai.contract_fields (contract_id);

CREATE INDEX IF NOT EXISTS contract_fields_run_idx
    ON judy_ai.contract_fields (run_id);


-- Agent 3 — plain-language summary
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


-- Agent 4 — obligation tracking
CREATE TABLE IF NOT EXISTS judy_ai.contract_obligations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID        NOT NULL REFERENCES judy_ai.agent_runs(id) ON DELETE CASCADE,
    contract_id     UUID        NOT NULL REFERENCES app.contracts(id) ON DELETE CASCADE,
    obligation_type TEXT        NOT NULL
                                CHECK (obligation_type IN ('commitment', 'renewal',
                                                           'milestone', 'other')),
    description     TEXT        NOT NULL,
    owner_party     TEXT,
    due_date        DATE,
    raw_date_text   TEXT,
    recurrence      TEXT        CHECK (recurrence IN ('one_time', 'monthly', 'quarterly', 'annual')),
    source_quote    TEXT,
    source_page     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS contract_obligations_contract_idx
    ON judy_ai.contract_obligations (contract_id);

CREATE INDEX IF NOT EXISTS contract_obligations_due_idx
    ON judy_ai.contract_obligations (due_date) WHERE due_date IS NOT NULL;


-- =============================================================================
-- Read helper
-- =============================================================================
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
