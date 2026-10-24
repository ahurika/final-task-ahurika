"""
Automated Static Unit Tests for PostgreSQL + pgvector Persistence Layer
Location: tests/unit/test_migrations.py

Validates migration files, dependency ordering, schema constraints,
rollback symmetry, dimension parameterization, and DB record models offline.
"""

import os
import re
import pytest

from src.db.migrator import MigrationManager, MigrationStep
from src.db.models import (
    DocumentRecord,
    ChunkRecord,
    EmbeddingCacheRecord,
    IngestionJobRecord,
    QueryTelemetryRecord,
)
from src.db.schema import (
    ALL_TABLES,
    TABLE_DOCUMENTS,
    TABLE_CHUNKS,
    TABLE_EMBEDDING_CACHE,
    TABLE_INGESTION_JOBS,
    TABLE_QUERY_TELEMETRY,
    HNSW_INDEX_NAME,
    HNSW_DISTANCE_METRIC,
)


@pytest.fixture
def migrator():
    return MigrationManager()


# -----------------------------------------------------------------------------
# 1. Migration File Structure & Ordering Tests
# -----------------------------------------------------------------------------

def test_migration_files_exist_and_ordered(migrator):
    """Verifies that all migration files start at 001 and have no sequence gaps."""
    steps = migrator.discover_migrations()
    assert len(steps) >= 3, f"Expected at least 3 migrations, found {len(steps)}"

    for idx, step in enumerate(steps, start=1):
        assert step.version == idx, f"Expected step {idx}, got {step.version}"
        assert os.path.exists(step.up_path), f"Missing UP file: {step.up_path}"
        assert os.path.exists(step.rollback_path), f"Missing rollback file: {step.rollback_path}"


def test_migration_rollback_symmetry(migrator):
    """Ensures every UP migration has a matching rollback script."""
    steps = migrator.discover_migrations()
    for step in steps:
        assert step.up_filename.endswith(".sql")
        assert not step.up_filename.endswith(".rollback.sql")
        assert step.rollback_filename.endswith(".rollback.sql")
        assert len(step.up_sql_template.strip()) > 0
        assert len(step.rollback_sql.strip()) > 0


def test_migrator_offline_validation_passes(migrator):
    """Verifies that static offline validation reports valid with zero errors."""
    report = migrator.validate_offline()
    assert report["valid"] is True, f"Validation failed with errors: {report.get('errors')}"
    assert len(report["errors"]) == 0
    assert report["total_migrations"] >= 3


def test_no_secrets_in_migration_files(migrator):
    """Enforces security rule: no API keys, tokens, or credentials in SQL files."""
    forbidden = [
        re.compile(r"AIza[0-9A-Za-z-_]{35}"),
        re.compile(r"sk-[a-zA-Z0-9]{32,}"),
        re.compile(r"postgres://[a-zA-Z0-9_-]+:[^@]+@"),
        re.compile(r"password\s*=\s*['\"][^'\"]+['\"]", re.IGNORECASE),
    ]
    steps = migrator.discover_migrations()
    for step in steps:
        for pattern in forbidden:
            assert not pattern.search(step.up_sql_template), f"Secret pattern found in {step.up_filename}"
            assert not pattern.search(step.rollback_sql), f"Secret pattern found in {step.rollback_filename}"


# -----------------------------------------------------------------------------
# 2. Schema Constraints & Relationship Tests
# -----------------------------------------------------------------------------

def test_documents_table_constraints(migrator):
    """Verifies source identity, filename uniqueness, parser status, and content hash constraints."""
    steps = migrator.discover_migrations()
    migration_001 = steps[0].up_sql_template

    assert "CREATE TABLE IF NOT EXISTS documents" in migration_001
    assert "source_id VARCHAR(128) NOT NULL UNIQUE" in migration_001
    assert "source_filename TEXT NOT NULL UNIQUE" in migration_001
    assert "content_hash VARCHAR(64) NOT NULL" in migration_001
    assert "chk_documents_file_type CHECK (file_type IN ('pdf', 'markdown'))" in migration_001
    assert "chk_documents_parser_status CHECK (parser_status IN ('pending', 'parsed', 'failed', 'skipped'))" in migration_001
    assert "chk_documents_content_hash CHECK (length(content_hash) = 64)" in migration_001
    assert "idx_documents_content_hash ON documents(content_hash)" in migration_001


def test_chunks_table_strategy_isolation_and_foreign_key(migrator):
    """Verifies dual-strategy coexistence constraint, cascading FK, and content hash check."""
    steps = migrator.discover_migrations()
    migration_001 = steps[0].up_sql_template

    assert "CREATE TABLE IF NOT EXISTS chunks" in migration_001
    assert "REFERENCES documents(id) ON DELETE CASCADE" in migration_001
    assert "CONSTRAINT uq_chunks_doc_strat_index UNIQUE (document_id, strategy, chunk_index)" in migration_001
    assert "chk_chunks_strategy CHECK (strategy IN ('fixed_window', 'structure_aware'))" in migration_001
    assert "chk_chunks_token_count CHECK (token_count > 0)" in migration_001
    assert "chk_chunks_content_hash CHECK (length(content_hash) = 64)" in migration_001


def test_embedding_cache_composite_primary_key(migrator):
    """Verifies composite PK (content_hash, model_name, dimensions) for cache isolation."""
    steps = migrator.discover_migrations()
    migration_001 = steps[0].up_sql_template

    assert "CREATE TABLE IF NOT EXISTS embedding_cache" in migration_001
    assert "PRIMARY KEY (content_hash, model_name, dimensions)" in migration_001
    assert "chk_embedding_cache_content_hash CHECK (length(content_hash) = 64)" in migration_001
    assert "chk_embedding_cache_dimensions CHECK (dimensions > 0)" in migration_001


def test_ingestion_jobs_status_and_counters(migrator):
    """Verifies ingestion_jobs status constraints and progress counter bounds."""
    steps = migrator.discover_migrations()
    migration_001 = steps[0].up_sql_template

    assert "CREATE TABLE IF NOT EXISTS ingestion_jobs" in migration_001
    assert "chk_ingestion_jobs_status CHECK (status IN ('queued', 'processing', 'completed', 'failed', 'cancelled'))" in migration_001
    assert "chk_ingestion_jobs_strategy CHECK (strategy IN ('fixed_window', 'structure_aware', 'both'))" in migration_001
    assert "chk_ingestion_jobs_total_docs CHECK (total_documents >= 0)" in migration_001
    assert "chk_ingestion_jobs_proc_docs CHECK (processed_documents >= 0)" in migration_001


def test_vector_storage_hnsw_and_cosine_metric(migrator):
    """Verifies pgvector extension, HNSW index parameters, and cosine distance ops."""
    steps = migrator.discover_migrations()
    migration_002 = steps[1].up_sql_template

    assert "CREATE EXTENSION IF NOT EXISTS vector;" in migration_002
    assert "ALTER TABLE chunks" in migration_002
    assert "ALTER TABLE embedding_cache" in migration_002
    assert HNSW_INDEX_NAME in migration_002
    assert HNSW_DISTANCE_METRIC in migration_002
    assert "USING hnsw (embedding vector_cosine_ops)" in migration_002
    assert "m = 16" in migration_002
    assert "ef_construction = 64" in migration_002


def test_dimension_parameterization(migrator):
    """Verifies that vector dimensions can be dynamically parameterized without code edits."""
    step2 = migrator.discover_migrations()[1]

    # Test default (768)
    rendered_768 = step2.render_up_sql(embedding_dimension=768)
    assert "vector(/* __EMBEDDING_DIM__ */ 768)" in rendered_768

    # Test custom dimension (e.g. 1536 for OpenAI or 384 for MiniLM)
    rendered_1536 = step2.render_up_sql(embedding_dimension=1536)
    assert "vector(/* __EMBEDDING_DIM__ */ 1536)" in rendered_1536
    assert "vector(/* __EMBEDDING_DIM__ */ 768)" not in rendered_1536

    rendered_384 = step2.render_up_sql(embedding_dimension=384)
    assert "vector(/* __EMBEDDING_DIM__ */ 384)" in rendered_384


def test_rollback_table_drop_order(migrator):
    """Verifies rollback script drops chunks before documents to respect foreign key constraints."""
    steps = migrator.discover_migrations()
    rb_001 = steps[0].rollback_sql

    chunks_pos = rb_001.find("DROP TABLE IF EXISTS chunks")
    docs_pos = rb_001.find("DROP TABLE IF EXISTS documents")

    assert chunks_pos != -1, "chunks drop missing"
    assert docs_pos != -1, "documents drop missing"
    assert chunks_pos < docs_pos, "chunks must be dropped before documents due to FK reference"


# -----------------------------------------------------------------------------
# 3. Python DB Record Models Unit Tests
# -----------------------------------------------------------------------------

def test_document_record_validation():
    """Validates DocumentRecord constraints."""
    valid_hash = "a" * 64

    doc = DocumentRecord(
        source_id="postgresql-16-US",
        source_filename="postgresql-16-US.pdf",
        file_type="pdf",
        content_hash=valid_hash,
        total_pages=3220,
    )
    assert doc.id is not None
    assert doc.to_dict()["total_pages"] == 3220

    # Invalid file type
    with pytest.raises(ValueError, match="Invalid file_type"):
        DocumentRecord(
            source_id="test",
            source_filename="test.docx",
            file_type="docx",
            content_hash=valid_hash,
        )

    # Invalid hash length
    with pytest.raises(ValueError, match="content_hash must be a 64-character"):
        DocumentRecord(
            source_id="test",
            source_filename="test.pdf",
            file_type="pdf",
            content_hash="short_hash",
        )


def test_chunk_record_validation():
    """Validates ChunkRecord constraints."""
    valid_hash = "b" * 64
    chunk = ChunkRecord(
        id="c56a4180-65aa-42ec-a945-5fd21dec0538",
        document_id="d56a4180-65aa-42ec-a945-5fd21dec0538",
        strategy="structure_aware",
        chunk_index=0,
        source_filename="auth-pg-hba-conf.md",
        page_or_section="Context: [pg_hba.conf]",
        content="Sample content",
        content_hash=valid_hash,
        token_count=120,
    )
    assert chunk.to_dict()["strategy"] == "structure_aware"

    # Invalid strategy
    with pytest.raises(ValueError, match="Invalid strategy"):
        ChunkRecord(
            id="c56a4180-65aa-42ec-a945-5fd21dec0538",
            document_id="d56a4180-65aa-42ec-a945-5fd21dec0538",
            strategy="invalid_strat",
            chunk_index=0,
            source_filename="file.md",
            page_or_section="sec",
            content="text",
            content_hash=valid_hash,
            token_count=50,
        )

    # Negative token count
    with pytest.raises(ValueError, match="token_count must be positive"):
        ChunkRecord(
            id="c56a4180-65aa-42ec-a945-5fd21dec0538",
            document_id="d56a4180-65aa-42ec-a945-5fd21dec0538",
            strategy="fixed_window",
            chunk_index=0,
            source_filename="file.md",
            page_or_section="sec",
            content="text",
            content_hash=valid_hash,
            token_count=0,
        )


def test_embedding_cache_record_validation():
    """Validates EmbeddingCacheRecord composite identity."""
    valid_hash = "c" * 64
    cache = EmbeddingCacheRecord(
        content_hash=valid_hash,
        model_name="gemini-embedding-001",
        dimensions=768,
        embedding=[0.1] * 768,
    )
    assert cache.to_dict()["dimensions"] == 768

    with pytest.raises(ValueError, match="dimensions must be positive"):
        EmbeddingCacheRecord(
            content_hash=valid_hash,
            model_name="gemini-embedding-001",
            dimensions=0,
        )


def test_ingestion_job_record_validation():
    """Validates IngestionJobRecord states and counters."""
    job = IngestionJobRecord(strategy="both", status="queued")
    assert job.status == "queued"
    assert job.to_dict()["strategy"] == "both"

    with pytest.raises(ValueError, match="Invalid status"):
        IngestionJobRecord(strategy="fixed_window", status="non_existent")


def test_full_ddl_render(migrator):
    """Verifies that render_full_ddl concatenates all UP migrations cleanly."""
    full_ddl = migrator.render_full_ddl(embedding_dimension=768)
    assert "CREATE TABLE IF NOT EXISTS ingestion_jobs" in full_ddl
    assert "CREATE TABLE IF NOT EXISTS documents" in full_ddl
    assert "CREATE TABLE IF NOT EXISTS chunks" in full_ddl
    assert "CREATE TABLE IF NOT EXISTS embedding_cache" in full_ddl
    assert "CREATE EXTENSION IF NOT EXISTS vector;" in full_ddl
    assert "CREATE TABLE IF NOT EXISTS query_telemetry" in full_ddl
    assert "vector(/* __EMBEDDING_DIM__ */ 768)" in full_ddl


# -----------------------------------------------------------------------------
# 4. Phase 3D.1 Correctness & Audit Tests
# -----------------------------------------------------------------------------

def test_dimension_validation_strict_bounds_and_types(migrator):
    """Verifies that invalid, non-integer, negative, zero, or oversized dimensions are strictly rejected."""
    step2 = migrator.discover_migrations()[1]

    # Non-integer types
    invalid_types = ["768", 768.5, None, True, False, [768], {"dim": 768}, "768); DROP TABLE chunks; --"]
    for val in invalid_types:
        with pytest.raises(TypeError, match="Embedding dimension must be an integer"):
            step2.render_up_sql(embedding_dimension=val)

    # Out-of-bounds integers
    with pytest.raises(ValueError, match="must be at least 1"):
        step2.render_up_sql(embedding_dimension=0)

    with pytest.raises(ValueError, match="must be at least 1"):
        step2.render_up_sql(embedding_dimension=-500)

    # Exceeding pgvector HNSW index limit (2,000 dimensions)
    with pytest.raises(ValueError, match="exceeds maximum of 2000"):
        step2.render_up_sql(embedding_dimension=2001)

    with pytest.raises(ValueError, match="exceeds maximum of 2000"):
        step2.render_up_sql(embedding_dimension=3072)

    with pytest.raises(ValueError, match="exceeds maximum of 2000"):
        step2.render_up_sql(embedding_dimension=16000)

    # Valid boundary limits (1 and 2000)
    assert "/* __EMBEDDING_DIM__ */ 1)" in step2.render_up_sql(embedding_dimension=1)
    assert "/* __EMBEDDING_DIM__ */ 2000)" in step2.render_up_sql(embedding_dimension=2000)


def test_cli_dimension_type_converter():
    """Verifies that CLI dimension parsing strictly rejects non-integers and out-of-bound dimensions."""
    import argparse
    from src.db.migrator import cli_dimension_type

    # Valid conversions
    assert cli_dimension_type("768") == 768
    assert cli_dimension_type("1536") == 1536
    assert cli_dimension_type("384") == 384
    assert cli_dimension_type("1") == 1
    assert cli_dimension_type("2000") == 2000

    # Non-integer strings
    with pytest.raises(argparse.ArgumentTypeError, match="Invalid integer dimension"):
        cli_dimension_type("not_an_int")

    with pytest.raises(argparse.ArgumentTypeError, match="Invalid integer dimension"):
        cli_dimension_type("768.5")

    # Range violations
    with pytest.raises(argparse.ArgumentTypeError, match="must be at least 1"):
        cli_dimension_type("0")

    with pytest.raises(argparse.ArgumentTypeError, match="must be at least 1"):
        cli_dimension_type("-10")

    with pytest.raises(argparse.ArgumentTypeError, match="exceeds maximum of 2000"):
        cli_dimension_type("2001")


def test_no_undefined_custom_types_in_sql_migrations(migrator):
    """
    Verifies that no undefined custom types (e.g. uuid_array, float_array) exist in SQL files.
    Ensures that native PostgreSQL 1-D arrays (UUID[], FLOAT8[]) are used.
    """
    steps = migrator.discover_migrations()
    forbidden_types = [
        re.compile(r"\buuid_array\b", re.IGNORECASE),
        re.compile(r"\bfloat_array\b", re.IGNORECASE),
        re.compile(r"\bvector_array\b", re.IGNORECASE),
    ]

    for step in steps:
        for pat in forbidden_types:
            assert not pat.search(step.up_sql_template), f"Forbidden custom type {pat.pattern} found in {step.up_filename}"
            assert not pat.search(step.rollback_sql), f"Forbidden custom type {pat.pattern} found in {step.rollback_filename}"

    # Specifically check telemetry migration for native array syntax
    telemetry_sql = steps[2].up_sql_template
    assert "retrieved_chunk_ids UUID[] NOT NULL DEFAULT '{}'" in telemetry_sql
    assert "similarity_scores FLOAT8[] NOT NULL DEFAULT '{}'" in telemetry_sql


def test_dry_run_plan_rejects_invalid_dimension(migrator):
    """Verifies that MigrationManager.get_up_plan and render_full_ddl reject invalid dimensions."""
    with pytest.raises(ValueError, match="exceeds maximum of 2000"):
        migrator.get_up_plan(embedding_dimension=4096)

    with pytest.raises(ValueError, match="must be at least 1"):
        migrator.render_full_ddl(embedding_dimension=-768)

    with pytest.raises(TypeError, match="must be an integer"):
        migrator.render_full_ddl(embedding_dimension="768")

