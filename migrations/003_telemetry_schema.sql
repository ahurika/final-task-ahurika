-- =============================================================================
-- Migration 003: Observability and Query Telemetry Table
-- Requirement: REQ-13 (Observability, Rate Limiting & Telemetry Logging)
-- Author: Senior Backend Engineer
-- =============================================================================

CREATE TABLE IF NOT EXISTS query_telemetry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_text TEXT NOT NULL,
    chunking_strategy VARCHAR(32) NOT NULL,
    retrieval_k INT NOT NULL,
    retrieved_chunk_ids UUID[] NOT NULL DEFAULT '{}',
    similarity_scores FLOAT8[] NOT NULL DEFAULT '{}',
    top_similarity_score FLOAT8 NOT NULL,
    refusal_triggered BOOLEAN NOT NULL DEFAULT FALSE,
    retrieval_latency_ms INT NOT NULL,
    llm_latency_ms INT NOT NULL,
    total_latency_ms INT NOT NULL,
    prompt_tokens INT NOT NULL DEFAULT 0,
    completion_tokens INT NOT NULL DEFAULT 0,
    total_tokens INT NOT NULL DEFAULT 0,
    estimated_cost_usd NUMERIC(10, 6) NOT NULL DEFAULT 0.000000,
    client_ip VARCHAR(45),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_query_telemetry_strategy CHECK (chunking_strategy IN ('fixed_window', 'structure_aware')),
    CONSTRAINT chk_query_telemetry_k CHECK (retrieval_k > 0),
    CONSTRAINT chk_query_telemetry_latencies CHECK (
        retrieval_latency_ms >= 0 AND llm_latency_ms >= 0 AND total_latency_ms >= 0
    ),
    CONSTRAINT chk_query_telemetry_tokens CHECK (
        prompt_tokens >= 0 AND completion_tokens >= 0 AND total_tokens >= 0
    )
);

CREATE INDEX IF NOT EXISTS idx_query_telemetry_created_at ON query_telemetry(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_query_telemetry_refusal ON query_telemetry(refusal_triggered);
CREATE INDEX IF NOT EXISTS idx_query_telemetry_strategy ON query_telemetry(chunking_strategy);
