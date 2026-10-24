"""
Unit Tests for Offline Embedding Provider Foundation
Location: tests/unit/test_embeddings.py

Covers:
- Base validation utilities (validate_vector, validate_text_input)
- Deterministic mock provider (reproducibility, dimension conformity, L2 normalization)
- Provider input validation (empty, whitespace, non-strings)
- Vector dimension bounds and non-finite value rejections (NaN, Inf, booleans)
- Embedding cache contract (composite PK isolation: content_hash, model_name, dimensions)
- Cache hits, misses, copy protection, and corruption detection
- Configuration validation (candidate models, deprecation guards, offline lock)
- Retry policy logic (exponential backoff, retry exhaustion, non-retryable errors)
"""

import math
import pytest
from typing import List

from src.embeddings.base import (
    validate_vector,
    validate_text_input,
    EmbeddingError,
    EmbeddingDimensionError,
    EmbeddingValueError,
    EmbeddingConfigurationError,
    EmbeddingCacheError,
)
from src.embeddings.mock_provider import DeterministicMockEmbeddingProvider
from src.embeddings.cache import (
    CacheKey,
    InMemoryEmbeddingCache,
)
from src.embeddings.config import (
    EmbeddingConfig,
    MODEL_GEMINI_EMBEDDING_001,
    MODEL_GEMINI_EMBEDDING_2,
    MODEL_MOCK_DETERMINISTIC,
)
from src.embeddings.retry import (
    RetryPolicy,
    execute_with_retry,
)


# =============================================================================
# 1. Base Validation Utilities Tests
# =============================================================================

class TestValidationUtilities:
    """Tests for vector and text input validation functions."""

    def test_validate_vector_valid(self):
        """Valid vectors pass without error."""
        vector = [0.1, -0.5, 0.0, 1.234]
        validate_vector(vector, expected_dim=4)

    def test_validate_vector_wrong_dimension(self):
        """Mismatched dimension raises EmbeddingDimensionError."""
        vector = [0.1, 0.2, 0.3]
        with pytest.raises(EmbeddingDimensionError, match="expected exactly 4 dimensions, got 3"):
            validate_vector(vector, expected_dim=4)

        with pytest.raises(EmbeddingDimensionError, match="expected exactly 2 dimensions, got 3"):
            validate_vector(vector, expected_dim=2)

    def test_validate_vector_non_sequence(self):
        """Non-sequence input raises EmbeddingValueError."""
        with pytest.raises(EmbeddingValueError, match="Vector must be a list or tuple"):
            validate_vector("not_a_vector", expected_dim=3)  # type: ignore

        with pytest.raises(EmbeddingValueError, match="Vector must be a list or tuple"):
            validate_vector(12345, expected_dim=1)  # type: ignore

    def test_validate_vector_non_finite_nan(self):
        """Vector containing NaN raises EmbeddingValueError."""
        vector = [1.0, float("nan"), 3.0]
        with pytest.raises(EmbeddingValueError, match="non-finite"):
            validate_vector(vector, expected_dim=3)

    def test_validate_vector_non_finite_inf(self):
        """Vector containing Infinity raises EmbeddingValueError."""
        vector = [1.0, float("inf"), 3.0]
        with pytest.raises(EmbeddingValueError, match="non-finite"):
            validate_vector(vector, expected_dim=3)

        vector_neg = [1.0, float("-inf"), 3.0]
        with pytest.raises(EmbeddingValueError, match="non-finite"):
            validate_vector(vector_neg, expected_dim=3)

    def test_validate_vector_boolean_rejection(self):
        """Booleans (which inherit from int in Python) are explicitly rejected."""
        vector = [1.0, True, 0.5]  # type: ignore
        with pytest.raises(EmbeddingValueError, match="not a valid number"):
            validate_vector(vector, expected_dim=3)

    def test_validate_vector_string_elements(self):
        """Vector containing string elements raises EmbeddingValueError."""
        vector = [1.0, "0.5", 0.5]  # type: ignore
        with pytest.raises(EmbeddingValueError, match="not a valid number"):
            validate_vector(vector, expected_dim=3)

    def test_validate_text_input_valid(self):
        """Valid text returns stripped string."""
        assert validate_text_input("PostgreSQL 16 MVCC") == "PostgreSQL 16 MVCC"

    def test_validate_text_input_empty_and_whitespace(self):
        """Empty or whitespace-only text raises EmbeddingValueError."""
        with pytest.raises(EmbeddingValueError, match="cannot be empty or whitespace-only"):
            validate_text_input("")

        with pytest.raises(EmbeddingValueError, match="cannot be empty or whitespace-only"):
            validate_text_input("   \n\t  ")

    def test_validate_text_input_non_string(self):
        """Non-string inputs raise EmbeddingValueError."""
        with pytest.raises(EmbeddingValueError, match="must be a string"):
            validate_text_input(None)  # type: ignore

        with pytest.raises(EmbeddingValueError, match="must be a string"):
            validate_text_input(12345)  # type: ignore


# =============================================================================
# 2. Deterministic Mock Provider Tests
# =============================================================================

class TestDeterministicMockProvider:
    """Tests for DeterministicMockEmbeddingProvider."""

    def test_deterministic_output_identical_input(self):
        """Identical inputs produce bit-for-bit identical vectors."""
        provider = DeterministicMockEmbeddingProvider(dimensions=768, seed=42)
        text = "PostgreSQL multi-version concurrency control documentation."

        vec1 = provider.embed_text(text)
        vec2 = provider.embed_text(text)

        assert len(vec1) == 768
        assert vec1 == vec2

    def test_distinct_inputs_produce_distinct_vectors(self):
        """Different texts produce distinct vectors."""
        provider = DeterministicMockEmbeddingProvider(dimensions=768, seed=42)
        vec1 = provider.embed_text("WAL archiving and point-in-time recovery")
        vec2 = provider.embed_text("B-tree index deduplication and vacuuming")

        assert vec1 != vec2
        # Cosine dot product between two random 768-dim vectors should be near 0
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        assert abs(dot_product) < 0.2  # Statistically nearly orthogonal

    def test_unit_length_normalization(self):
        """Mock vectors are normalized to unit L2 length (sum(x^2) == 1.0)."""
        provider = DeterministicMockEmbeddingProvider(dimensions=768)
        vec = provider.embed_text("Sample query for unit vector check")
        l2_norm = math.sqrt(sum(x * x for x in vec))
        assert pytest.approx(l2_norm, abs=1e-6) == 1.0

    def test_batch_embedding_consistency(self):
        """embed_batch produces outputs identical to single embed_text calls in order."""
        provider = DeterministicMockEmbeddingProvider(dimensions=384)
        texts = [
            "Section 1: Installation",
            "Section 2: Configuration",
            "Section 3: Replication",
        ]
        batch_vecs = provider.embed_batch(texts)
        assert len(batch_vecs) == 3

        for i, text in enumerate(texts):
            single_vec = provider.embed_text(text)
            assert batch_vecs[i] == single_vec

    def test_empty_batch_handling(self):
        """Empty batch list returns empty list immediately."""
        provider = DeterministicMockEmbeddingProvider()
        assert provider.embed_batch([]) == []

    def test_batch_invalid_type(self):
        """Non-list batch input raises EmbeddingValueError."""
        provider = DeterministicMockEmbeddingProvider()
        with pytest.raises(EmbeddingValueError, match="texts must be a list"):
            provider.embed_batch("not a list")  # type: ignore

    def test_empty_input_rejected(self):
        """Empty text is rejected with EmbeddingValueError."""
        provider = DeterministicMockEmbeddingProvider()
        with pytest.raises(EmbeddingValueError, match="cannot be empty"):
            provider.embed_text("")

        with pytest.raises(EmbeddingValueError, match="cannot be empty"):
            provider.embed_text("   ")

    def test_configurable_dimensions(self):
        """Dimensions can be configured across supported bounds (e.g. 384, 768, 1536)."""
        for dim in (64, 384, 768, 1536, 2000):
            p = DeterministicMockEmbeddingProvider(dimensions=dim)
            vec = p.embed_text("Test dimensional variation")
            assert len(vec) == dim

    def test_invalid_dimension_bounds(self):
        """Dimensions outside 1..2000 raise EmbeddingDimensionError."""
        with pytest.raises(EmbeddingDimensionError, match="out of bounds"):
            DeterministicMockEmbeddingProvider(dimensions=0)

        with pytest.raises(EmbeddingDimensionError, match="out of bounds"):
            DeterministicMockEmbeddingProvider(dimensions=-10)

        with pytest.raises(EmbeddingDimensionError, match="out of bounds"):
            DeterministicMockEmbeddingProvider(dimensions=2001)

        with pytest.raises(EmbeddingDimensionError, match="must be an integer"):
            DeterministicMockEmbeddingProvider(dimensions=768.5)  # type: ignore

        with pytest.raises(EmbeddingDimensionError, match="must be an integer"):
            DeterministicMockEmbeddingProvider(dimensions=True)  # type: ignore

    def test_invalid_model_name(self):
        """Empty or non-string model_name raises EmbeddingValueError."""
        with pytest.raises(EmbeddingValueError, match="model_name must be a non-empty string"):
            DeterministicMockEmbeddingProvider(model_name="")

        with pytest.raises(EmbeddingValueError, match="model_name must be a non-empty string"):
            DeterministicMockEmbeddingProvider(model_name="   ")

    def test_is_mock_property(self):
        """is_mock explicitly identifies mock implementation."""
        p = DeterministicMockEmbeddingProvider()
        assert p.is_mock is True


# =============================================================================
# 3. Embedding Cache Contract & In-Memory Implementation Tests
# =============================================================================

class TestEmbeddingCacheContract:
    """Tests for CacheKey and InMemoryEmbeddingCache."""

    @pytest.fixture
    def valid_hash(self) -> str:
        return "a" * 64

    def test_cache_miss_returns_none(self, valid_hash):
        """Querying a non-existent key returns None."""
        cache = InMemoryEmbeddingCache()
        res = cache.get(valid_hash, "gemini-embedding-001", 768)
        assert res is None
        assert cache.has(valid_hash, "gemini-embedding-001", 768) is False

    def test_cache_hit_and_retrieval(self, valid_hash):
        """Setting and retrieving a valid vector succeeds."""
        cache = InMemoryEmbeddingCache()
        vector = [0.25] * 768
        cache.set(valid_hash, "gemini-embedding-001", 768, vector)

        assert cache.has(valid_hash, "gemini-embedding-001", 768) is True
        assert cache.size() == 1

        retrieved = cache.get(valid_hash, "gemini-embedding-001", 768)
        assert retrieved == vector

    def test_cache_isolation_by_model_name(self, valid_hash):
        """Identical content_hash and dimension under different models DO NOT collide."""
        cache = InMemoryEmbeddingCache()
        vec_001 = [0.1] * 768
        vec_2 = [0.9] * 768

        cache.set(valid_hash, "gemini-embedding-001", 768, vec_001)
        cache.set(valid_hash, "gemini-embedding-2", 768, vec_2)

        assert cache.size() == 2
        assert cache.get(valid_hash, "gemini-embedding-001", 768) == vec_001
        assert cache.get(valid_hash, "gemini-embedding-2", 768) == vec_2

    def test_cache_isolation_by_dimensions(self, valid_hash):
        """Identical content_hash and model under different dimensions DO NOT collide."""
        cache = InMemoryEmbeddingCache()
        vec_768 = [0.1] * 768
        vec_384 = [0.2] * 384

        cache.set(valid_hash, "gemini-embedding-001", 768, vec_768)
        cache.set(valid_hash, "gemini-embedding-001", 384, vec_384)

        assert cache.size() == 2
        assert cache.get(valid_hash, "gemini-embedding-001", 768) == vec_768
        assert cache.get(valid_hash, "gemini-embedding-001", 384) == vec_384

    def test_cache_isolation_by_content_hash(self):
        """Different content hashes store distinct vectors."""
        cache = InMemoryEmbeddingCache()
        hash1 = "1" * 64
        hash2 = "2" * 64
        vec1 = [0.1] * 768
        vec2 = [0.2] * 768

        cache.set(hash1, "gemini-embedding-001", 768, vec1)
        cache.set(hash2, "gemini-embedding-001", 768, vec2)

        assert cache.get(hash1, "gemini-embedding-001", 768) == vec1
        assert cache.get(hash2, "gemini-embedding-001", 768) == vec2

    def test_cache_copy_protection(self, valid_hash):
        """Mutating the returned cached vector does not mutate internal cache."""
        cache = InMemoryEmbeddingCache()
        vec = [0.5] * 768
        cache.set(valid_hash, "gemini-embedding-001", 768, vec)

        retrieved = cache.get(valid_hash, "gemini-embedding-001", 768)
        assert retrieved is not None
        retrieved[0] = 999.0  # Mutate retrieved list

        second_retrieved = cache.get(valid_hash, "gemini-embedding-001", 768)
        assert second_retrieved is not None
        assert second_retrieved[0] == 0.5  # Remains pristine

    def test_cache_key_validation(self):
        """Invalid cache keys raise appropriate exceptions."""
        with pytest.raises(EmbeddingCacheError, match="64-character"):
            CacheKey(content_hash="too_short", model_name="gemini-embedding-001", dimensions=768)

        with pytest.raises(EmbeddingCacheError, match="model_name must be a non-empty string"):
            CacheKey(content_hash="a" * 64, model_name="", dimensions=768)

        with pytest.raises(EmbeddingDimensionError, match="positive integer"):
            CacheKey(content_hash="a" * 64, model_name="gemini-embedding-001", dimensions=0)

    def test_cache_rejects_corrupted_vector_on_set(self, valid_hash):
        """Cache set() validates vector dimensions and finite numbers."""
        cache = InMemoryEmbeddingCache()
        
        # Wrong dimension
        with pytest.raises(EmbeddingDimensionError):
            cache.set(valid_hash, "gemini-embedding-001", 768, [0.1] * 767)

        # Non-finite value
        with pytest.raises(EmbeddingValueError):
            cache.set(valid_hash, "gemini-embedding-001", 768, [float("nan")] + [0.1] * 767)

    def test_cache_detects_corrupted_entry_on_get(self, valid_hash):
        """If an entry is somehow corrupted internally, get() raises EmbeddingCacheError."""
        cache = InMemoryEmbeddingCache()
        key_tuple = (valid_hash, "gemini-embedding-001", 768)
        # Directly inject corrupted vector into internal store to simulate memory corruption
        cache._store[key_tuple] = [0.1] * 500  # Truncated length

        with pytest.raises(EmbeddingCacheError, match="Corrupted cache entry detected"):
            cache.get(valid_hash, "gemini-embedding-001", 768)

    def test_cache_clear(self, valid_hash):
        """cache.clear() removes all entries."""
        cache = InMemoryEmbeddingCache()
        cache.set(valid_hash, "gemini-embedding-001", 768, [0.1] * 768)
        assert cache.size() == 1
        cache.clear()
        assert cache.size() == 0
        assert cache.get(valid_hash, "gemini-embedding-001", 768) is None


# =============================================================================
# 4. Configuration & Candidate Model Registry Tests
# =============================================================================

class TestEmbeddingConfiguration:
    """Tests for EmbeddingConfig and candidate model deprecation checks."""

    def test_default_config_valid(self):
        """Default configuration uses mock provider in offline mode."""
        cfg = EmbeddingConfig()
        assert cfg.provider == "mock"
        assert cfg.model_name == MODEL_GEMINI_EMBEDDING_001
        assert cfg.dimensions == 768
        assert cfg.is_live_enabled is False

    def test_deprecated_model_rejected(self):
        """Deprecated and shut-down model text-embedding-004 is strictly rejected."""
        with pytest.raises(EmbeddingConfigurationError, match="text-embedding-004.*deprecated and shut down"):
            EmbeddingConfig(model_name="text-embedding-004")

        with pytest.raises(EmbeddingConfigurationError, match="text-embedding-004.*deprecated and shut down"):
            EmbeddingConfig(model_name="models/text-embedding-004")

    def test_active_candidates_accepted(self):
        """Active candidates gemini-embedding-001 and gemini-embedding-2 are accepted."""
        cfg1 = EmbeddingConfig(model_name=MODEL_GEMINI_EMBEDDING_001)
        assert cfg1.model_name == "gemini-embedding-001"

        cfg2 = EmbeddingConfig(model_name=MODEL_GEMINI_EMBEDDING_2)
        assert cfg2.model_name == "gemini-embedding-2"

    def test_live_provider_guard_enforced(self):
        """Requesting provider='gemini' with is_live_enabled=False raises error."""
        with pytest.raises(EmbeddingConfigurationError, match="Live Gemini provider requested while is_live_enabled=False"):
            EmbeddingConfig(provider="gemini", is_live_enabled=False)

    def test_invalid_provider_name(self):
        """Unsupported provider name raises EmbeddingConfigurationError."""
        with pytest.raises(EmbeddingConfigurationError, match="Invalid provider"):
            EmbeddingConfig(provider="openai_unauthorized")

    def test_dimension_bounds_in_config(self):
        """Dimensions outside 1..2000 raise EmbeddingDimensionError."""
        with pytest.raises(EmbeddingDimensionError, match="out of bounds"):
            EmbeddingConfig(dimensions=0)

        with pytest.raises(EmbeddingDimensionError, match="out of bounds"):
            EmbeddingConfig(dimensions=3072)


# =============================================================================
# 5. Retry Policy & Rate Limit Utilities Tests
# =============================================================================

class TestRetryPolicy:
    """Tests for exponential backoff logic without external network calls."""

    def test_delay_computation(self):
        """Computes deterministic exponential delay with maximum ceiling."""
        policy = RetryPolicy(initial_delay_s=1.0, backoff_factor=2.0, max_delay_s=5.0)
        assert policy.compute_delay(0) == 1.0
        assert policy.compute_delay(1) == 2.0
        assert policy.compute_delay(2) == 4.0
        assert policy.compute_delay(3) == 5.0  # Capped at max_delay_s

    def test_execute_with_retry_succeeds_first_try(self):
        """Successful operation succeeds without retries."""
        calls = []

        def op():
            calls.append(1)
            return "success"

        result = execute_with_retry(op, retryable_exceptions=(ValueError,))
        assert result == "success"
        assert len(calls) == 1

    def test_execute_with_retry_succeeds_after_failures(self):
        """Transient errors are retried until success."""
        attempts = 0
        sleep_durations = []

        def op():
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise ConnectionError("Transient network glitch")
            return "recovered"

        def mock_sleep(duration):
            sleep_durations.append(duration)

        result = execute_with_retry(
            operation=op,
            retryable_exceptions=(ConnectionError,),
            policy=RetryPolicy(max_retries=3, initial_delay_s=0.1, backoff_factor=2.0),
            sleeper=mock_sleep,
        )

        assert result == "recovered"
        assert attempts == 3
        assert len(sleep_durations) == 2
        assert sleep_durations == [0.1, 0.2]

    def test_execute_with_retry_exhaustion(self):
        """Exhausting max_retries re-raises the underlying exception."""
        def op():
            raise ConnectionError("Permanent network outage")

        with pytest.raises(ConnectionError, match="Permanent network outage"):
            execute_with_retry(
                operation=op,
                retryable_exceptions=(ConnectionError,),
                policy=RetryPolicy(max_retries=2, initial_delay_s=0.01),
                sleeper=lambda _: None,
            )

    def test_execute_with_retry_unhandled_exception(self):
        """Non-retryable exception fails immediately without retrying."""
        calls = 0

        def op():
            nonlocal calls
            calls += 1
            raise KeyError("Fatal programming error")

        with pytest.raises(KeyError):
            execute_with_retry(
                operation=op,
                retryable_exceptions=(ConnectionError,),
                sleeper=lambda _: None,
            )

        assert calls == 1
