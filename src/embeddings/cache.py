"""
Embedding Cache Interface & In-Memory Contract
Location: src/embeddings/cache.py

Defines the cache contract matching both the database schema and model semantics:
    Identity: (content_hash, model_name, dimensions, task_type, title)

Guarantees:
1. Strict namespace isolation: vectors are never reused across different models,
   dimensions, asymmetric task types (document vs query), or document titles.
2. Vector dimension validation on both write (set) and read (get).
3. Defensive copying on read to prevent callers from mutating cached entries.
4. In-memory reference implementation for offline integration testing.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
import sqlite3
from typing import Dict, List, Optional, Tuple

from src.embeddings.base import (
    EmbeddingCacheError,
    EmbeddingDimensionError,
    EmbeddingValueError,
    validate_vector,
)


@dataclass(frozen=True)
class CacheKey:
    """
    Composite identity for embedding cache entries:
    (content_hash, model_name, dimensions, task_type, title)
    
    Guarantees that:
    - Queries and documents never collide even with identical text.
    - Documents with different titles never collide.
    - Different models and dimensions remain strictly partitioned.
    """
    content_hash: str
    model_name: str
    dimensions: int
    task_type: str = "RETRIEVAL_DOCUMENT"
    title: Optional[str] = None

    def __post_init__(self):
        if not isinstance(self.content_hash, str) or len(self.content_hash) != 64:
            raise EmbeddingCacheError(
                f"content_hash must be a 64-character SHA-256 hex string, got {self.content_hash!r}."
            )
        if not isinstance(self.model_name, str) or not self.model_name.strip():
            raise EmbeddingCacheError("model_name must be a non-empty string.")
        if isinstance(self.dimensions, bool) or not isinstance(self.dimensions, int) or self.dimensions <= 0:
            raise EmbeddingDimensionError(f"dimensions must be a positive integer, got {self.dimensions!r}.")
        if not isinstance(self.task_type, str) or not self.task_type.strip():
            raise EmbeddingCacheError("task_type must be a non-empty string.")


class BaseEmbeddingCache(ABC):
    """Abstract interface for embedding caching layers (in-memory or persistent database)."""

    @abstractmethod
    def get(
        self,
        content_hash: str,
        model_name: str,
        dimensions: int,
        task_type: str = "RETRIEVAL_DOCUMENT",
        title: Optional[str] = None,
        allow_legacy: bool = True,
    ) -> Optional[List[float]]:
        """
        Retrieves a cached vector by composite key.
        
        Args:
            content_hash: 64-char SHA-256 hex digest of the normalized content.
            model_name: Embedding model identifier.
            dimensions: Target vector dimension.
            task_type: Embedding task type (e.g. RETRIEVAL_DOCUMENT, RETRIEVAL_QUERY).
            title: Optional document title.
            allow_legacy: If True, allows fallback to legacy (hash, model, dim) entries
                ONLY for untitled RETRIEVAL_DOCUMENT lookups. If False, strictly
                requires exact 5-tuple match.

        Returns:
            The cached float vector if found, or None if a cache miss occurs.
        
        Raises:
            EmbeddingCacheError: If key is invalid or stored vector is corrupted.
            EmbeddingDimensionError: If cached vector length does not match dimensions.
        """
        pass

    @abstractmethod
    def set(
        self,
        content_hash: str,
        model_name: str,
        dimensions: int,
        vector: List[float],
        task_type: str = "RETRIEVAL_DOCUMENT",
        title: Optional[str] = None,
        model_version: Optional[str] = None,
    ) -> None:
        """
        Stores an embedding vector under the composite key.

        Raises:
            EmbeddingCacheError: If key parameters are invalid.
            EmbeddingDimensionError: If vector dimension does not match `dimensions`.
            EmbeddingValueError: If vector components are non-finite or invalid numbers.
        """
        pass

    @abstractmethod
    def has(
        self,
        content_hash: str,
        model_name: str,
        dimensions: int,
        task_type: str = "RETRIEVAL_DOCUMENT",
        title: Optional[str] = None,
        allow_legacy: bool = True,
    ) -> bool:
        """Checks whether an entry exists for the given composite key."""
        pass

    @abstractmethod
    def size(self) -> int:
        """Returns the number of cached entries."""
        pass

    @abstractmethod
    def clear(self) -> None:
        """Clears all cached entries."""
        pass


class InMemoryEmbeddingCache(BaseEmbeddingCache):
    """
    In-memory reference implementation of BaseEmbeddingCache.
    
    Used for unit testing, offline pipeline integration, and verifying cache
    isolation invariants without a live PostgreSQL instance.
    """

    def __init__(self):
        # Maps (content_hash, model_name, dimensions, task_type, title) -> List[float]
        self._store: Dict[Tuple[str, str, int, str, Optional[str]], List[float]] = {}

    def get(
        self,
        content_hash: str,
        model_name: str,
        dimensions: int,
        task_type: str = "RETRIEVAL_DOCUMENT",
        title: Optional[str] = None,
        allow_legacy: bool = True,
    ) -> Optional[List[float]]:
        # Normalize title: whitespace-only becomes None
        clean_title = title.strip() if title and title.strip() else None
        key = CacheKey(
            content_hash=content_hash,
            model_name=model_name,
            dimensions=dimensions,
            task_type=task_type,
            title=clean_title,
        )
        tuple_key = (key.content_hash, key.model_name, key.dimensions, key.task_type, key.title)
        
        vector = self._store.get(tuple_key)
        if vector is None and allow_legacy:
            # Backward-compatibility fallback for legacy 3-tuple keys.
            # CRITICAL SAFETY INVARIANT: Legacy fallback is ONLY permitted for untitled
            # RETRIEVAL_DOCUMENT lookups. It is strictly forbidden to return a legacy
            # entry for queries (RETRIEVAL_QUERY) or titled documents (title is not None)
            # because 3-tuple legacy entries lack task-type and title conditioning.
            if key.task_type == "RETRIEVAL_DOCUMENT" and key.title is None:
                legacy_tuple_key = (key.content_hash, key.model_name, key.dimensions)
                vector = self._store.get(legacy_tuple_key)

        if vector is None:
            return None

        # Validate stored vector integrity before returning
        try:
            validate_vector(vector, expected_dim=dimensions)
        except (EmbeddingDimensionError, EmbeddingValueError) as e:
            raise EmbeddingCacheError(f"Corrupted cache entry detected for key {key}: {e}") from e

        # Return a copy to prevent in-place mutation of cache contents
        return list(vector)

    def set(
        self,
        content_hash: str,
        model_name: str,
        dimensions: int,
        vector: List[float],
        task_type: str = "RETRIEVAL_DOCUMENT",
        title: Optional[str] = None,
        model_version: Optional[str] = None,
    ) -> None:
        clean_title = title.strip() if title and title.strip() else None
        key = CacheKey(
            content_hash=content_hash,
            model_name=model_name,
            dimensions=dimensions,
            task_type=task_type,
            title=clean_title,
        )
        validate_vector(vector, expected_dim=dimensions)
        
        tuple_key = (key.content_hash, key.model_name, key.dimensions, key.task_type, key.title)
        self._store[tuple_key] = list(vector)

    def has(
        self,
        content_hash: str,
        model_name: str,
        dimensions: int,
        task_type: str = "RETRIEVAL_DOCUMENT",
        title: Optional[str] = None,
        allow_legacy: bool = True,
    ) -> bool:
        clean_title = title.strip() if title and title.strip() else None
        key = CacheKey(
            content_hash=content_hash,
            model_name=model_name,
            dimensions=dimensions,
            task_type=task_type,
            title=clean_title,
        )
        tuple_key = (key.content_hash, key.model_name, key.dimensions, key.task_type, key.title)
        if tuple_key in self._store:
            return True
        if allow_legacy and key.task_type == "RETRIEVAL_DOCUMENT" and key.title is None:
            legacy_tuple_key = (key.content_hash, key.model_name, key.dimensions)
            return legacy_tuple_key in self._store
        return False

    def size(self) -> int:
        return len(self._store)

    def clear(self) -> None:
        self._store.clear()


class DiskEmbeddingCache(BaseEmbeddingCache):
    """
    Persistent SQLite-backed implementation of BaseEmbeddingCache.

    Guarantees:
    1. Zero external dependencies: uses Python standard library `sqlite3`.
    2. ACID persistence: immediate disk writes with WAL mode and transaction safety.
    3. Composite key isolation: distinguishes (content_hash, model_name, dimensions, task_type, title).
    4. Deterministic key IDs: single string primary key prevents NULL ambiguity in SQLite.
    5. Integrity validation: validates dimension, finiteness, and nonzero norm on read and write.
    6. Safe concurrent access and fast O(1) indexed lookups.
    """

    def __init__(self, db_path: str):
        self._db_path = os.path.abspath(db_path)
        os.makedirs(os.path.dirname(self._db_path), exist_ok=True)
        self._init_db()

    @property
    def db_path(self) -> str:
        return self._db_path

    @staticmethod
    def compute_key_id(
        content_hash: str,
        model_name: str,
        dimensions: int,
        task_type: str,
        title: Optional[str],
    ) -> str:
        clean_title = title.strip() if title and title.strip() else "__UNTITLED__"
        return f"{content_hash}:{model_name}:{dimensions}:{task_type}:{clean_title}"

    @staticmethod
    def compute_legacy_key_id(
        content_hash: str,
        model_name: str,
        dimensions: int,
    ) -> str:
        return f"{content_hash}:{model_name}:{dimensions}:RETRIEVAL_DOCUMENT:__LEGACY__"

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._get_connection() as conn:
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS embedding_cache (
                    key_id TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    dimensions INTEGER NOT NULL,
                    task_type TEXT NOT NULL,
                    title TEXT,
                    vector_json TEXT NOT NULL,
                    model_version TEXT,
                    created_at TEXT NOT NULL
                );
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_lookup
                ON embedding_cache(content_hash, model_name, dimensions, task_type);
            """)
            conn.commit()

    def get(
        self,
        content_hash: str,
        model_name: str,
        dimensions: int,
        task_type: str = "RETRIEVAL_DOCUMENT",
        title: Optional[str] = None,
        allow_legacy: bool = True,
    ) -> Optional[List[float]]:
        clean_title = title.strip() if title and title.strip() else None
        key = CacheKey(
            content_hash=content_hash,
            model_name=model_name,
            dimensions=dimensions,
            task_type=task_type,
            title=clean_title,
        )
        key_id = self.compute_key_id(key.content_hash, key.model_name, key.dimensions, key.task_type, key.title)

        with self._get_connection() as conn:
            cursor = conn.execute("SELECT vector_json FROM embedding_cache WHERE key_id = ?", (key_id,))
            row = cursor.fetchone()

            if row is None and allow_legacy:
                # Legacy fallback permitted only for untitled RETRIEVAL_DOCUMENT
                if key.task_type == "RETRIEVAL_DOCUMENT" and key.title is None:
                    legacy_key_id = self.compute_legacy_key_id(key.content_hash, key.model_name, key.dimensions)
                    cursor = conn.execute("SELECT vector_json FROM embedding_cache WHERE key_id = ?", (legacy_key_id,))
                    row = cursor.fetchone()

        if row is None:
            return None

        try:
            vector = json.loads(row["vector_json"])
        except (json.JSONDecodeError, ValueError) as e:
            raise EmbeddingCacheError(f"Corrupted cache entry detected for key {key_id}: {e}") from e

        try:
            validate_vector(vector, expected_dim=dimensions)
            if sum(v * v for v in vector) <= 0.0:
                raise EmbeddingValueError("Vector has zero norm (all zero components).")
        except (EmbeddingDimensionError, EmbeddingValueError) as e:
            raise EmbeddingCacheError(f"Corrupted vector in cache entry {key_id}: {e}") from e

        return list(vector)

    def set(
        self,
        content_hash: str,
        model_name: str,
        dimensions: int,
        vector: List[float],
        task_type: str = "RETRIEVAL_DOCUMENT",
        title: Optional[str] = None,
        model_version: Optional[str] = None,
    ) -> None:
        clean_title = title.strip() if title and title.strip() else None
        key = CacheKey(
            content_hash=content_hash,
            model_name=model_name,
            dimensions=dimensions,
            task_type=task_type,
            title=clean_title,
        )
        validate_vector(vector, expected_dim=dimensions)
        if sum(v * v for v in vector) <= 0.0:
            raise EmbeddingValueError("Vector has zero norm (all zero components).")

        key_id = self.compute_key_id(key.content_hash, key.model_name, key.dimensions, key.task_type, key.title)
        vector_json = json.dumps(vector)
        now_utc = datetime.now(timezone.utc).isoformat()

        with self._get_connection() as conn:
            conn.execute(
                """
                INSERT INTO embedding_cache (
                    key_id, content_hash, model_name, dimensions, task_type, title,
                    vector_json, model_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key_id) DO UPDATE SET
                    vector_json = excluded.vector_json,
                    model_version = excluded.model_version,
                    created_at = excluded.created_at;
                """,
                (
                    key_id,
                    key.content_hash,
                    key.model_name,
                    key.dimensions,
                    key.task_type,
                    clean_title,
                    vector_json,
                    model_version,
                    now_utc,
                ),
            )
            conn.commit()

    def set_batch(
        self,
        records: List[Tuple[str, str, int, List[float], str, Optional[str], Optional[str]]],
    ) -> None:
        """
        Atomically commits a batch of embedding records in a single transaction.
        Each tuple is: (content_hash, model_name, dimensions, vector, task_type, title, model_version)
        """
        if not records:
            return

        now_utc = datetime.now(timezone.utc).isoformat()
        rows_to_insert = []
        for item in records:
            content_hash, model_name, dimensions, vector, task_type, title, model_version = item
            clean_title = title.strip() if title and title.strip() else None
            key = CacheKey(
                content_hash=content_hash,
                model_name=model_name,
                dimensions=dimensions,
                task_type=task_type,
                title=clean_title,
            )
            validate_vector(vector, expected_dim=dimensions)
            if sum(v * v for v in vector) <= 0.0:
                raise EmbeddingValueError("Vector has zero norm (all zero components).")
            key_id = self.compute_key_id(key.content_hash, key.model_name, key.dimensions, key.task_type, key.title)
            vector_json = json.dumps(vector)
            rows_to_insert.append((
                key_id,
                key.content_hash,
                key.model_name,
                key.dimensions,
                key.task_type,
                clean_title,
                vector_json,
                model_version,
                now_utc,
            ))

        with self._get_connection() as conn:
            conn.executemany(
                """
                INSERT INTO embedding_cache (
                    key_id, content_hash, model_name, dimensions, task_type, title,
                    vector_json, model_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key_id) DO UPDATE SET
                    vector_json = excluded.vector_json,
                    model_version = excluded.model_version,
                    created_at = excluded.created_at;
                """,
                rows_to_insert,
            )
            conn.commit()

    def has(
        self,
        content_hash: str,
        model_name: str,
        dimensions: int,
        task_type: str = "RETRIEVAL_DOCUMENT",
        title: Optional[str] = None,
        allow_legacy: bool = True,
    ) -> bool:
        clean_title = title.strip() if title and title.strip() else None
        key = CacheKey(
            content_hash=content_hash,
            model_name=model_name,
            dimensions=dimensions,
            task_type=task_type,
            title=clean_title,
        )
        key_id = self.compute_key_id(key.content_hash, key.model_name, key.dimensions, key.task_type, key.title)

        with self._get_connection() as conn:
            cursor = conn.execute("SELECT 1 FROM embedding_cache WHERE key_id = ? LIMIT 1", (key_id,))
            if cursor.fetchone() is not None:
                return True
            if allow_legacy and key.task_type == "RETRIEVAL_DOCUMENT" and key.title is None:
                legacy_key_id = self.compute_legacy_key_id(key.content_hash, key.model_name, key.dimensions)
                cursor = conn.execute("SELECT 1 FROM embedding_cache WHERE key_id = ? LIMIT 1", (legacy_key_id,))
                return cursor.fetchone() is not None
        return False

    def size(self) -> int:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM embedding_cache")
            return int(cursor.fetchone()[0])

    def clear(self) -> None:
        with self._get_connection() as conn:
            conn.execute("DELETE FROM embedding_cache")
            conn.commit()
