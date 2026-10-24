-- =============================================================================
-- Migration 001: Create Core RAG Entities
-- Tables: ingestion_jobs, documents, chunks, embedding_cache
-- Author: Senior Backend Engineer
-- Target: PostgreSQL 15+
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Table: ingestion_jobs (REQ-03: Background Ingestion & Progress Tracking)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    total_documents INT NOT NULL DEFAULT 0,
    processed_documents INT NOT NULL DEFAULT 0,
    total_chunks_created INT NOT NULL DEFAULT 0,
    embeddings_generated INT NOT NULL DEFAULT 0,
    cache_hits INT NOT NULL DEFAULT 0,
    current_document TEXT,
    error_details JSONB NOT NULL DEFAULT '[]'::jsonb,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_ingestion_jobs_strategy CHECK (strategy IN ('fixed_window', 'structure_aware', 'both')),
    CONSTRAINT chk_ingestion_jobs_status CHECK (status IN ('queued', 'processing', 'completed', 'failed', 'cancelled')),
    CONSTRAINT chk_ingestion_jobs_total_docs CHECK (total_documents >= 0),
    CONSTRAINT chk_ingestion_jobs_proc_docs CHECK (processed_documents >= 0),
    CONSTRAINT chk_ingestion_jobs_chunks CHECK (total_chunks_created >= 0),
    CONSTRAINT chk_ingestion_jobs_embeddings CHECK (embeddings_generated >= 0),
    CONSTRAINT chk_ingestion_jobs_cache_hits CHECK (cache_hits >= 0)
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status ON ingestion_jobs(status);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_created_at ON ingestion_jobs(created_at DESC);

-- -----------------------------------------------------------------------------
-- 2. Table: documents (REQ-01, REQ-02, REQ-06: Document Registry & Provenance)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id VARCHAR(128) NOT NULL UNIQUE,
    source_filename TEXT NOT NULL UNIQUE,
    source_url TEXT,
    file_type VARCHAR(16) NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    total_pages INT,
    total_words INT,
    total_sections INT,
    parser_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    document_date DATE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_documents_file_type CHECK (file_type IN ('pdf', 'markdown')),
    CONSTRAINT chk_documents_content_hash CHECK (length(content_hash) = 64),
    CONSTRAINT chk_documents_parser_status CHECK (parser_status IN ('pending', 'parsed', 'failed', 'skipped'))
);

CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(content_hash);
CREATE INDEX IF NOT EXISTS idx_documents_file_type ON documents(file_type);
CREATE INDEX IF NOT EXISTS idx_documents_parser_status ON documents(parser_status);
CREATE INDEX IF NOT EXISTS idx_documents_metadata_gin ON documents USING gin (metadata);

-- -----------------------------------------------------------------------------
-- 3. Table: chunks (REQ-04, REQ-05, REQ-06: Context Chunks & Strategy Isolation)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    strategy VARCHAR(32) NOT NULL,
    chunk_index INT NOT NULL,
    source_filename TEXT NOT NULL,
    source_url TEXT,
    page_or_section TEXT NOT NULL,
    document_date DATE,
    content TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    token_count INT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_chunks_doc_strat_index UNIQUE (document_id, strategy, chunk_index),
    CONSTRAINT chk_chunks_strategy CHECK (strategy IN ('fixed_window', 'structure_aware')),
    CONSTRAINT chk_chunks_chunk_index CHECK (chunk_index >= 0),
    CONSTRAINT chk_chunks_token_count CHECK (token_count > 0),
    CONSTRAINT chk_chunks_content_hash CHECK (length(content_hash) = 64)
);

CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_strategy ON chunks(strategy);
CREATE INDEX IF NOT EXISTS idx_chunks_content_hash ON chunks(content_hash);
CREATE INDEX IF NOT EXISTS idx_chunks_source_filename ON chunks(source_filename);
CREATE INDEX IF NOT EXISTS idx_chunks_metadata_gin ON chunks USING gin (metadata);

-- -----------------------------------------------------------------------------
-- 4. Table: embedding_cache (REQ-07: In-Database Deduplication of Vectors)
-- Note: The vector column is added in migration 002 once pgvector is enabled.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash VARCHAR(64) NOT NULL,
    model_name VARCHAR(64) NOT NULL,
    model_version VARCHAR(32),
    dimensions INT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (content_hash, model_name, dimensions),
    CONSTRAINT chk_embedding_cache_content_hash CHECK (length(content_hash) = 64),
    CONSTRAINT chk_embedding_cache_dimensions CHECK (dimensions > 0)
);

CREATE INDEX IF NOT EXISTS idx_embedding_cache_model ON embedding_cache(model_name, dimensions);
