-- =============================================================================
-- Migration 002: Vector Storage and HNSW Indexing
-- Prerequisites: PostgreSQL pgvector extension available
-- Target Model: Google Gemini text-embedding-004 (768 dimensions)
-- Metric: Cosine Distance (<=>) using vector_cosine_ops
-- Author: Senior Backend Engineer
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. Enable pgvector Extension
-- -----------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector;

-- -----------------------------------------------------------------------------
-- 2. Add Vector Column to chunks Table
-- Default dimension: 768 (matching Google Gemini text-embedding-004)
-- Migration-safe design: can be parameterized by migrator or altered via ALTER COLUMN.
-- -----------------------------------------------------------------------------
ALTER TABLE chunks 
ADD COLUMN IF NOT EXISTS embedding vector(/* __EMBEDDING_DIM__ */ 768);

-- -----------------------------------------------------------------------------
-- 3. Add Vector Column to embedding_cache Table
-- -----------------------------------------------------------------------------
ALTER TABLE embedding_cache 
ADD COLUMN IF NOT EXISTS embedding vector(/* __EMBEDDING_DIM__ */ 768);

-- -----------------------------------------------------------------------------
-- 4. Create HNSW Vector Index on chunks for Fast Approximate Nearest Neighbor
-- Graph Parameters:
--   m = 16: Number of bidirectional links per vector node.
--   ef_construction = 64: Dynamic candidate list size during index construction.
-- Distance Metric:
--   vector_cosine_ops: Uses Cosine Distance (<=>).
--   Retrieval similarity score: s = 1 - (embedding <=> query_vector)
-- -----------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw_cosine 
ON chunks 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);
