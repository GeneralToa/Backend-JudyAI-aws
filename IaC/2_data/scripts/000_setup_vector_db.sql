-- =============================================================
-- Aurora PostgreSQL Vector Database Setup for Amazon Bedrock Knowledge Bases
-- =============================================================
-- DDL only — role creation and grants are managed by Terraform (sql.tf).
-- Re-runnable: all statements use IF NOT EXISTS.
-- =============================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS bedrock_integration;

CREATE TABLE IF NOT EXISTS bedrock_integration.bedrock_kb (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    embedding       vector(1024),
    chunks          TEXT,
    metadata        JSON,
    custom_metadata JSONB
);

CREATE INDEX IF NOT EXISTS bedrock_kb_embedding_idx
    ON bedrock_integration.bedrock_kb
    USING hnsw (embedding vector_cosine_ops);

CREATE INDEX IF NOT EXISTS bedrock_kb_chunks_idx
    ON bedrock_integration.bedrock_kb
    USING gin (to_tsvector('simple', chunks));
