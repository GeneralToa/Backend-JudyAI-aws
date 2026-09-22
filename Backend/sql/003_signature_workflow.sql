-- =============================================================================
-- Judy AI POC — Signature Workflow Schema
-- Target: Aurora PostgreSQL 17.5, database `ragdb` (rag-app-prod-aurora-postgres)
--
-- Owner: Lloyd (app.* schema)
-- Covers JAAPESWM-5: Signature Workflow Engine — Approval Chains
--
-- Adds three tables to app.*:
--   app.approval_templates  — up to 3 configurable approval-chain templates
--   app.contract_signers    — signers assigned to a contract via a template
--   app.signer_signatures   — the actual signature data per signer
--
-- Re-runnable: every statement is IF NOT EXISTS.
-- =============================================================================


-- =============================================================================
-- app.approval_templates
-- Stores up to 3 admin-configured templates. Each template defines a routing
-- type (sequential or parallel) and an ordered list of signer roles.
-- Templates are applied manually at upload time.
-- =============================================================================
CREATE TABLE IF NOT EXISTS app.approval_templates (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    name         TEXT        NOT NULL,
    routing_type TEXT        NOT NULL CHECK (routing_type IN ('sequential', 'parallel')),
    -- Ordered list of signer roles e.g. ["supplier", "company"]
    -- For sequential routing, order determines signing sequence.
    signer_roles JSONB       NOT NULL DEFAULT '[]',
    is_default   BOOLEAN     NOT NULL DEFAULT false,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Only one template can be the default at a time
CREATE UNIQUE INDEX IF NOT EXISTS approval_templates_default_idx
    ON app.approval_templates (is_default)
    WHERE is_default = true;

-- Seed a default sequential template with two signer roles
INSERT INTO app.approval_templates (name, routing_type, signer_roles, is_default)
VALUES (
    'Default Sequential',
    'sequential',
    '["supplier", "company"]',
    true
)
ON CONFLICT DO NOTHING;


-- =============================================================================
-- app.contract_signers
-- One row per signer assigned to a contract when a template is applied.
-- For sequential routing, signer_order determines the signing sequence.
-- For parallel routing, all signers are notified at once.
-- =============================================================================
CREATE TABLE IF NOT EXISTS app.contract_signers (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id  UUID        NOT NULL REFERENCES app.contracts(id) ON DELETE CASCADE,
    template_id  UUID        NOT NULL REFERENCES app.approval_templates(id),
    signer_email TEXT        NOT NULL,
    signer_role  TEXT        NOT NULL,   -- e.g. 'supplier', 'company'
    signer_order INTEGER     NOT NULL DEFAULT 1, -- 1-indexed, used for sequential routing
    status       TEXT        NOT NULL DEFAULT 'pending'
                             CHECK (status IN ('pending', 'signed', 'declined')),
    assigned_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    signed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS contract_signers_contract_idx
    ON app.contract_signers (contract_id, signer_order);


-- =============================================================================
-- app.signer_signatures
-- Stores the actual signature data for each signer.
-- signature_data holds the base64-encoded drawn signature image.
-- field_id references the contract field detected by the template-prepopulation
-- agent (judy_ai.contract_fields) so the signature can be placed at the
-- correct position on the document.
-- =============================================================================
CREATE TABLE IF NOT EXISTS app.signer_signatures (
    id             UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    contract_id    UUID        NOT NULL REFERENCES app.contracts(id) ON DELETE CASCADE,
    signer_id      UUID        NOT NULL REFERENCES app.contract_signers(id) ON DELETE CASCADE,
    -- base64-encoded PNG of the drawn/uploaded signature
    signature_data TEXT        NOT NULL,
    -- Optional: reference to the detected signature field position
    -- from judy_ai.contract_fields (normalized 0-1 coordinates)
    field_id       UUID,
    page           INTEGER,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS signer_signatures_contract_idx
    ON app.signer_signatures (contract_id);
