-- =============================================================================
-- Migration 002 Rollback: Remove Vector Columns and HNSW Index
-- Author: Senior Backend Engineer
-- =============================================================================

DROP INDEX IF EXISTS idx_chunks_embedding_hnsw_cosine;
ALTER TABLE chunks DROP COLUMN IF EXISTS embedding;
ALTER TABLE embedding_cache DROP COLUMN IF EXISTS embedding;

-- Note: We intentionally do not DROP EXTENSION vector here to avoid
-- breaking any external or concurrent schemas that also utilize pgvector.
