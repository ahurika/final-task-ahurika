-- =============================================================================
-- Migration 003 Rollback: Drop Observability and Query Telemetry Table
-- Author: Senior Backend Engineer
-- =============================================================================

DROP TABLE IF EXISTS query_telemetry CASCADE;
