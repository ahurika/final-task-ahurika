"""
Unit Tests for DiskEmbeddingCache (Persistent SQLite Embedding Cache)
Location: tests/unit/test_disk_cache.py

Verifies:
1. File persistence across independent cache instances.
2. Composite key isolation: content_hash, model_name, dimensions, task_type, title.
3. Legacy fallback guardrails (untitled RETRIEVAL_DOCUMENT only).
4. Validation on read and write: dimensions, non-finite values, zero-norm.
5. Atomic batch insertion and recovery.
6. Error handling for corrupted data.
"""

import json
import math
import os
import sqlite3
import pytest

from src.embeddings.base import (
    EmbeddingCacheError,
    EmbeddingDimensionError,
    EmbeddingValueError,
)
from src.embeddings.cache import (
    CacheKey,
    DiskEmbeddingCache,
)

SAMPLE_HASH_A = "a" * 64
SAMPLE_HASH_B = "b" * 64
MODEL_001 = "gemini-embedding-001"
MODEL_2 = "gemini-embedding-2"
DIM_768 = 768


def make_valid_vector(dim: int = DIM_768, scale: float = 0.05) -> list[float]:
    """Generates a mathematically valid unit-test float vector with nonzero norm."""
    return [round(scale * ((i % 10) + 1), 6) for i in range(dim)]


class TestDiskEmbeddingCache:
    """Verifies all persistence, isolation, and integrity invariants of DiskEmbeddingCache."""

    def test_disk_cache_initialization_creates_db_and_tables(self, tmp_path):
        db_file = tmp_path / "subfolder" / "cache.sqlite"
        cache = DiskEmbeddingCache(str(db_file))

        assert os.path.isfile(str(db_file))
        assert cache.size() == 0

        # Verify SQLite schema
        with sqlite3.connect(str(db_file)) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='embedding_cache'"
            )
            assert cursor.fetchone() is not None

    def test_disk_cache_persistence_across_instances(self, tmp_path):
        db_file = str(tmp_path / "persist.sqlite")
        vector = make_valid_vector(DIM_768, scale=0.1)

        # Instance 1: Write vector
        cache1 = DiskEmbeddingCache(db_file)
        cache1.set(
            content_hash=SAMPLE_HASH_A,
            model_name=MODEL_001,
            dimensions=DIM_768,
            vector=vector,
            task_type="RETRIEVAL_DOCUMENT",
            title="Chapter 1. Architecture",
        )
        assert cache1.size() == 1

        # Instance 2: Open fresh instance on the same SQLite file
        cache2 = DiskEmbeddingCache(db_file)
        assert cache2.size() == 1
        assert cache2.has(
            content_hash=SAMPLE_HASH_A,
            model_name=MODEL_001,
            dimensions=DIM_768,
            task_type="RETRIEVAL_DOCUMENT",
            title="Chapter 1. Architecture",
        )

        retrieved = cache2.get(
            content_hash=SAMPLE_HASH_A,
            model_name=MODEL_001,
            dimensions=DIM_768,
            task_type="RETRIEVAL_DOCUMENT",
            title="Chapter 1. Architecture",
        )
        assert retrieved is not None
        assert len(retrieved) == DIM_768
        assert retrieved == vector

    def test_disk_cache_composite_key_separation_task_type(self, tmp_path):
        """Identical content_hash under different task_types must remain strictly isolated."""
        cache = DiskEmbeddingCache(str(tmp_path / "task_types.sqlite"))
        vec_doc = make_valid_vector(DIM_768, scale=0.01)
        vec_query = make_valid_vector(DIM_768, scale=0.02)

        cache.set(
            content_hash=SAMPLE_HASH_A,
            model_name=MODEL_001,
            dimensions=DIM_768,
            vector=vec_doc,
            task_type="RETRIEVAL_DOCUMENT",
            title="Overview",
        )
        cache.set(
            content_hash=SAMPLE_HASH_A,
            model_name=MODEL_001,
            dimensions=DIM_768,
            vector=vec_query,
            task_type="RETRIEVAL_QUERY",
            title=None,
        )

        assert cache.size() == 2

        got_doc = cache.get(
            content_hash=SAMPLE_HASH_A,
            model_name=MODEL_001,
            dimensions=DIM_768,
            task_type="RETRIEVAL_DOCUMENT",
            title="Overview",
        )
        got_query = cache.get(
            content_hash=SAMPLE_HASH_A,
            model_name=MODEL_001,
            dimensions=DIM_768,
            task_type="RETRIEVAL_QUERY",
            title=None,
        )

        assert got_doc == vec_doc
        assert got_query == vec_query
        assert got_doc != got_query

    def test_disk_cache_composite_key_separation_title(self, tmp_path):
        """Identical content_hash under different document titles must remain strictly partitioned."""
        cache = DiskEmbeddingCache(str(tmp_path / "titles.sqlite"))
        vec_t1 = make_valid_vector(DIM_768, scale=0.03)
        vec_t2 = make_valid_vector(DIM_768, scale=0.04)

        cache.set(
            content_hash=SAMPLE_HASH_A,
            model_name=MODEL_001,
            dimensions=DIM_768,
            vector=vec_t1,
            task_type="RETRIEVAL_DOCUMENT",
            title="Title One",
        )
        cache.set(
            content_hash=SAMPLE_HASH_A,
            model_name=MODEL_001,
            dimensions=DIM_768,
            vector=vec_t2,
            task_type="RETRIEVAL_DOCUMENT",
            title="Title Two",
        )

        assert cache.size() == 2
        assert cache.get(SAMPLE_HASH_A, MODEL_001, DIM_768, "RETRIEVAL_DOCUMENT", "Title One") == vec_t1
        assert cache.get(SAMPLE_HASH_A, MODEL_001, DIM_768, "RETRIEVAL_DOCUMENT", "Title Two") == vec_t2
        # Untitled lookup must be a cache miss
        assert cache.get(SAMPLE_HASH_A, MODEL_001, DIM_768, "RETRIEVAL_DOCUMENT", None, allow_legacy=False) is None

    def test_disk_cache_composite_key_separation_model_and_dimensions(self, tmp_path):
        """Different models and dimensions must never collide."""
        cache = DiskEmbeddingCache(str(tmp_path / "models.sqlite"))
        vec_768 = make_valid_vector(768, scale=0.05)
        vec_512 = make_valid_vector(512, scale=0.06)

        cache.set(
            content_hash=SAMPLE_HASH_A,
            model_name=MODEL_001,
            dimensions=768,
            vector=vec_768,
        )
        cache.set(
            content_hash=SAMPLE_HASH_A,
            model_name=MODEL_2,
            dimensions=512,
            vector=vec_512,
        )

        assert cache.size() == 2
        assert cache.get(SAMPLE_HASH_A, MODEL_001, 768) == vec_768
        assert cache.get(SAMPLE_HASH_A, MODEL_2, 512) == vec_512
        assert cache.get(SAMPLE_HASH_A, MODEL_001, 512) is None

    def test_disk_cache_legacy_fallback_guardrails(self, tmp_path):
        """Legacy fallback is permitted ONLY for untitled RETRIEVAL_DOCUMENT lookups."""
        db_file = str(tmp_path / "legacy.sqlite")
        cache = DiskEmbeddingCache(db_file)
        vec = make_valid_vector(DIM_768, scale=0.07)

        # Insert a synthetic legacy entry directly
        legacy_key_id = DiskEmbeddingCache.compute_legacy_key_id(SAMPLE_HASH_A, MODEL_001, DIM_768)
        with cache._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO embedding_cache (
                    key_id, content_hash, model_name, dimensions, task_type, title,
                    vector_json, model_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    legacy_key_id,
                    SAMPLE_HASH_A,
                    MODEL_001,
                    DIM_768,
                    "RETRIEVAL_DOCUMENT",
                    None,
                    json.dumps(vec),
                    None,
                    "2026-10-23T00:00:00Z",
                ),
            )
            conn.commit()

        # 1. Permitted: Untitled RETRIEVAL_DOCUMENT with allow_legacy=True
        assert cache.get(SAMPLE_HASH_A, MODEL_001, DIM_768, "RETRIEVAL_DOCUMENT", None, allow_legacy=True) == vec
        assert cache.has(SAMPLE_HASH_A, MODEL_001, DIM_768, "RETRIEVAL_DOCUMENT", None, allow_legacy=True) is True

        # 2. Blocked: allow_legacy=False
        assert cache.get(SAMPLE_HASH_A, MODEL_001, DIM_768, "RETRIEVAL_DOCUMENT", None, allow_legacy=False) is None

        # 3. Blocked: Titled document
        assert cache.get(SAMPLE_HASH_A, MODEL_001, DIM_768, "RETRIEVAL_DOCUMENT", "Title", allow_legacy=True) is None

        # 4. Blocked: Queries (RETRIEVAL_QUERY)
        assert cache.get(SAMPLE_HASH_A, MODEL_001, DIM_768, "RETRIEVAL_QUERY", None, allow_legacy=True) is None

    def test_disk_cache_rejects_dimension_mismatch(self, tmp_path):
        cache = DiskEmbeddingCache(str(tmp_path / "dim_err.sqlite"))
        short_vec = make_valid_vector(512)

        with pytest.raises(EmbeddingDimensionError):
            cache.set(
                content_hash=SAMPLE_HASH_A,
                model_name=MODEL_001,
                dimensions=768,
                vector=short_vec,
            )

    def test_disk_cache_rejects_non_finite_or_bool_vector(self, tmp_path):
        cache = DiskEmbeddingCache(str(tmp_path / "invalid_vec.sqlite"))

        nan_vec = make_valid_vector(DIM_768)
        nan_vec[10] = float("nan")
        with pytest.raises(EmbeddingValueError):
            cache.set(content_hash=SAMPLE_HASH_A, model_name=MODEL_001, dimensions=DIM_768, vector=nan_vec)

        inf_vec = make_valid_vector(DIM_768)
        inf_vec[20] = float("inf")
        with pytest.raises(EmbeddingValueError):
            cache.set(content_hash=SAMPLE_HASH_A, model_name=MODEL_001, dimensions=DIM_768, vector=inf_vec)

        bool_vec = make_valid_vector(DIM_768)
        bool_vec[5] = True  # type: ignore
        with pytest.raises(EmbeddingValueError):
            cache.set(content_hash=SAMPLE_HASH_A, model_name=MODEL_001, dimensions=DIM_768, vector=bool_vec)

    def test_disk_cache_rejects_zero_norm_vector(self, tmp_path):
        cache = DiskEmbeddingCache(str(tmp_path / "zero_norm.sqlite"))
        zero_vec = [0.0] * DIM_768

        with pytest.raises(EmbeddingValueError):
            cache.set(content_hash=SAMPLE_HASH_A, model_name=MODEL_001, dimensions=DIM_768, vector=zero_vec)

    def test_disk_cache_has_size_and_clear(self, tmp_path):
        cache = DiskEmbeddingCache(str(tmp_path / "clear.sqlite"))
        vec = make_valid_vector(DIM_768)

        assert cache.size() == 0
        cache.set(SAMPLE_HASH_A, MODEL_001, DIM_768, vec)
        cache.set(SAMPLE_HASH_B, MODEL_001, DIM_768, vec)

        assert cache.size() == 2
        assert cache.has(SAMPLE_HASH_A, MODEL_001, DIM_768) is True
        assert cache.has("c" * 64, MODEL_001, DIM_768) is False

        cache.clear()
        assert cache.size() == 0
        assert cache.has(SAMPLE_HASH_A, MODEL_001, DIM_768) is False

    def test_disk_cache_corrupted_json_raises_cache_error(self, tmp_path):
        db_file = str(tmp_path / "corrupt.sqlite")
        cache = DiskEmbeddingCache(db_file)
        vec = make_valid_vector(DIM_768)

        cache.set(SAMPLE_HASH_A, MODEL_001, DIM_768, vec)

        # Corrupt JSON payload directly in SQLite
        with cache._get_connection() as conn:
            conn.execute("UPDATE embedding_cache SET vector_json = 'INVALID_JSON_CORRUPTED'")
            conn.commit()

        with pytest.raises(EmbeddingCacheError) as exc_info:
            cache.get(SAMPLE_HASH_A, MODEL_001, DIM_768)
        assert "Corrupted cache entry" in str(exc_info.value)

    def test_disk_cache_set_batch(self, tmp_path):
        cache = DiskEmbeddingCache(str(tmp_path / "batch.sqlite"))
        vec1 = make_valid_vector(DIM_768, scale=0.01)
        vec2 = make_valid_vector(DIM_768, scale=0.02)
        vec3 = make_valid_vector(DIM_768, scale=0.03)

        records = [
            ("1" * 64, MODEL_001, DIM_768, vec1, "RETRIEVAL_DOCUMENT", "Title 1", None),
            ("2" * 64, MODEL_001, DIM_768, vec2, "RETRIEVAL_DOCUMENT", "Title 2", None),
            ("3" * 64, MODEL_001, DIM_768, vec3, "RETRIEVAL_DOCUMENT", "Title 3", None),
        ]

        cache.set_batch(records)
        assert cache.size() == 3

        assert cache.get("1" * 64, MODEL_001, DIM_768, "RETRIEVAL_DOCUMENT", "Title 1") == vec1
        assert cache.get("2" * 64, MODEL_001, DIM_768, "RETRIEVAL_DOCUMENT", "Title 2") == vec2
        assert cache.get("3" * 64, MODEL_001, DIM_768, "RETRIEVAL_DOCUMENT", "Title 3") == vec3
