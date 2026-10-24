-- =============================================================================
-- Migration 001 Rollback: Drop Core RAG Entities
-- Author: Senior Backend Engineer
-- Execution order: Reverse of creation (chunks -> embedding_cache -> documents -> ingestion_jobs)
-- =============================================================================

DROP TABLE IF EXISTS chunks CASCADE;
DROP TABLE IF EXISTS embedding_cache CASCADE;
DROP TABLE IF EXISTS documents CASCADE;
DROP TABLE IF EXISTS ingestion_jobs CASCADE;
