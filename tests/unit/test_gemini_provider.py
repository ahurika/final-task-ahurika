"""
Unit Tests for GeminiEmbeddingProvider (Offline Mock Tests)
Location: tests/unit/test_gemini_provider.py

Validates GeminiEmbeddingProvider behavior using mock SDK clients:
- Configuration and model forwarding (output_dimensionality, task_type, title)
- Task type separation (RETRIEVAL_DOCUMENT vs RETRIEVAL_QUERY)
- Vector validation (dimension match, finite numbers, rejection of NaNs/Infs/booleans)
- Over-limit input detection without silent truncation
- Malformed response structures
- Transient error classification and exponential backoff retry (HTTP 429 / 503)
- Permanent error handling (HTTP 401 / 400 fail fast without retrying)
- Offline safety (is_live_enabled=False blocks live calls)
- In-memory cache integration (zero client calls on cache hit)
- Batch embedding ordering and fidelity
"""

from datetime import datetime, timezone
import math
import pytest
from typing import Any, Dict, List, Optional

from src.chunkers.base import compute_content_hash
from src.embeddings.base import (
    EmbeddingConfigurationError,
    EmbeddingDimensionError,
    EmbeddingError,
    EmbeddingValueError,
)
from src.embeddings.cache import InMemoryEmbeddingCache
from src.embeddings.gemini_provider import (
    GeminiEmbeddingProvider,
    GeminiAPIError,
    GeminiTransientError,
    GeminiRateLimitError,
    TASK_TYPE_DOCUMENT,
    TASK_TYPE_QUERY,
    TASK_TYPE_SEMANTIC_SIMILARITY,
)
from src.embeddings.retry import RetryPolicy, execute_with_retry, parse_retry_after


# =============================================================================
# Mock SDK Objects
# =============================================================================

class MockEmbeddingValues:
    """Simulates an individual embedding object with .values."""
    def __init__(self, values: List[float]):
        self.values = values


class MockEmbedContentResponse:
    """Simulates the SDK response from client.models.embed_content."""
    def __init__(self, values: List[float]):
        self.embedding = MockEmbeddingValues(values)
        self.embeddings = [self.embedding]


class FakeGenAIModels:
    """Simulates client.models."""
    def __init__(self, response_values: Optional[List[float]] = None):
        self.default_values = response_values or ([0.1] * 768)
        self.last_model: Optional[str] = None
        self.last_contents: Optional[str] = None
        self.last_config: Optional[Dict[str, Any]] = None
        self.call_count: int = 0
        self.side_effect: Optional[Any] = None

    def embed_content(self, model: str, contents: Any, config: Optional[Dict[str, Any]] = None):
        self.call_count += 1
        self.last_model = model
        self.last_contents = contents
        self.last_config = config or {}

        if self.side_effect is not None:
            if callable(self.side_effect):
                return self.side_effect(model, contents, config)
            if isinstance(self.side_effect, Exception):
                raise self.side_effect

        return MockEmbedContentResponse(self.default_values)


class FakeGenAIClient:
    """Simulates genai.Client."""
    def __init__(self, response_values: Optional[List[float]] = None):
        self.models = FakeGenAIModels(response_values)


# =============================================================================
# Test Suite
# =============================================================================

class TestGeminiEmbeddingProviderOffline:
    """Comprehensive offline unit tests for GeminiEmbeddingProvider."""

    def test_initialization_defaults(self):
        """Provider initializes with default model, dimensions, and offline lock."""
        provider = GeminiEmbeddingProvider()
        assert provider.model_name == "gemini-embedding-001"
        assert provider.dimensions == 768
        assert provider.is_live_enabled is False

    def test_model_and_config_forwarding(self):
        """Verifies model_name, output_dimensionality, task_type, and title are passed."""
        fake_client = FakeGenAIClient([0.2] * 768)
        provider = GeminiEmbeddingProvider(client=fake_client, dimensions=768)

        vec = provider.embed_text(
            text="PostgreSQL MVCC overview",
            task_type=TASK_TYPE_DOCUMENT,
            title="Concurrency Control",
        )

        assert len(vec) == 768
        assert fake_client.models.last_model == "gemini-embedding-001"
        assert fake_client.models.last_contents == "PostgreSQL MVCC overview"
        assert fake_client.models.last_config["output_dimensionality"] == 768
        assert fake_client.models.last_config["task_type"] == "RETRIEVAL_DOCUMENT"
        assert fake_client.models.last_config["title"] == "Concurrency Control"

    def test_embed_document_convenience_method(self):
        """embed_document applies RETRIEVAL_DOCUMENT task type and forwards title."""
        fake_client = FakeGenAIClient([0.05] * 768)
        provider = GeminiEmbeddingProvider(client=fake_client)

        provider.embed_document("WAL logging and crash recovery", title="Reliability")
        assert fake_client.models.last_config["task_type"] == TASK_TYPE_DOCUMENT
        assert fake_client.models.last_config["title"] == "Reliability"

    def test_embed_query_convenience_method(self):
        """embed_query applies RETRIEVAL_QUERY task type and omits title."""
        fake_client = FakeGenAIClient([0.05] * 768)
        provider = GeminiEmbeddingProvider(client=fake_client)

        provider.embed_query("How does VACUUM freeze transaction IDs?")
        assert fake_client.models.last_config["task_type"] == TASK_TYPE_QUERY
        assert "title" not in fake_client.models.last_config

    def test_title_omitted_for_non_document_tasks(self):
        """Title parameter is excluded when task_type is not RETRIEVAL_DOCUMENT."""
        fake_client = FakeGenAIClient([0.1] * 768)
        provider = GeminiEmbeddingProvider(client=fake_client)

        provider.embed_text("Query text", task_type=TASK_TYPE_QUERY, title="Unused Title")
        assert "title" not in fake_client.models.last_config

    def test_dimension_mismatch_in_response_raises_error(self):
        """If API returns wrong number of dimensions, EmbeddingDimensionError is raised."""
        fake_client = FakeGenAIClient([0.1] * 512)  # Returns 512 instead of expected 768
        provider = GeminiEmbeddingProvider(client=fake_client, dimensions=768)

        with pytest.raises(EmbeddingDimensionError, match="expected exactly 768 dimensions, got 512"):
            provider.embed_text("Test dimension mismatch")

    def test_non_finite_values_in_response_rejected(self):
        """If API response contains NaN or Infinity, EmbeddingValueError is raised."""
        corrupted = [0.1] * 767 + [float("nan")]
        fake_client = FakeGenAIClient(corrupted)
        provider = GeminiEmbeddingProvider(client=fake_client, dimensions=768)

        with pytest.raises(EmbeddingValueError, match="non-finite"):
            provider.embed_text("Test NaN rejection")

    def test_over_limit_text_rejected_without_silent_truncation(self):
        """Inputs exceeding safety boundaries raise EmbeddingValueError instead of truncating."""
        fake_client = FakeGenAIClient([0.1] * 768)
        provider = GeminiEmbeddingProvider(client=fake_client, max_input_tokens=500)

        # Huge string (> 10,000 chars)
        huge_text = "word " * 3000  # 15,000 chars
        with pytest.raises(EmbeddingValueError, match="exceeds safety ceiling.*silent truncation is disabled"):
            provider.embed_text(huge_text)

        # Token limit exceedance (~800 words > 500 max tokens)
        large_text = "word " * 800
        with pytest.raises(EmbeddingValueError, match="exceeds the offline heuristic token proxy limit.*chunk your text"):
            provider.embed_text(large_text)

    def test_empty_and_whitespace_input_rejected(self):
        """Empty or whitespace strings are rejected before any API call."""
        fake_client = FakeGenAIClient([0.1] * 768)
        provider = GeminiEmbeddingProvider(client=fake_client)

        with pytest.raises(EmbeddingValueError, match="cannot be empty"):
            provider.embed_text("")

        with pytest.raises(EmbeddingValueError, match="cannot be empty"):
            provider.embed_text("   \n\t  ")

        assert fake_client.models.call_count == 0

    def test_malformed_response_structures(self):
        """Various malformed response formats raise EmbeddingError."""
        fake_client = FakeGenAIClient()
        provider = GeminiEmbeddingProvider(client=fake_client)

        # Response is None
        fake_client.models.side_effect = lambda m, c, cfg: None
        with pytest.raises(EmbeddingError, match="returned None"):
            provider.embed_text("Valid text")

        # Missing values attribute
        fake_client.models.side_effect = lambda m, c, cfg: object()
        with pytest.raises(EmbeddingError, match="Unable to extract vector values"):
            provider.embed_text("Valid text")

    def test_dict_response_format_supported(self):
        """Provider parses raw dictionary responses (e.g. from REST mock)."""
        fake_client = FakeGenAIClient()
        fake_client.models.side_effect = lambda m, c, cfg: {"embedding": {"values": [0.5] * 768}}
        provider = GeminiEmbeddingProvider(client=fake_client, dimensions=768)

        vec = provider.embed_text("Test dict format")
        assert len(vec) == 768
        assert vec[0] == 0.5

    def test_transient_rate_limit_retry_and_recovery(self):
        """HTTP 429 rate limit triggers exponential backoff retry and succeeds upon recovery."""
        attempts = 0
        sleeps = []

        def side_effect(model, contents, config):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise Exception("Resource has been exhausted (e.g. check quota) - HTTP 429")
            return MockEmbedContentResponse([0.3] * 768)

        fake_client = FakeGenAIClient()
        fake_client.models.side_effect = side_effect

        retry_policy = RetryPolicy(max_retries=3, initial_delay_s=0.1, backoff_factor=2.0)
        provider = GeminiEmbeddingProvider(
            client=fake_client,
            retry_policy=retry_policy,
            sleeper=lambda d: sleeps.append(d),
        )

        vec = provider.embed_text("Test transient retry")
        assert len(vec) == 768
        assert attempts == 3
        assert len(sleeps) == 2
        assert sleeps == [0.1, 0.2]

    def test_transient_error_exhaustion_raises_rate_limit_error(self):
        """Continuous HTTP 429 errors exhaust max_retries and raise GeminiRateLimitError."""
        fake_client = FakeGenAIClient()
        fake_client.models.side_effect = Exception("429 RESOURCE_EXHAUSTED: rate limit")

        provider = GeminiEmbeddingProvider(
            client=fake_client,
            retry_policy=RetryPolicy(max_retries=2, initial_delay_s=0.01),
            sleeper=lambda _: None,
        )

        with pytest.raises(GeminiRateLimitError, match="rate limit exceeded"):
            provider.embed_text("Test exhaustion")

    def test_permanent_authentication_error_fails_immediately(self):
        """HTTP 401/403 authentication failure raises EmbeddingConfigurationError without retrying."""
        calls = 0

        def side_effect(model, contents, config):
            nonlocal calls
            calls += 1
            raise Exception("API_KEY_INVALID: Permission denied (403)")

        fake_client = FakeGenAIClient()
        fake_client.models.side_effect = side_effect
        provider = GeminiEmbeddingProvider(
            client=fake_client,
            retry_policy=RetryPolicy(max_retries=5),
            sleeper=lambda _: None,
        )

        with pytest.raises(EmbeddingConfigurationError, match="authentication failed"):
            provider.embed_text("Test auth error")

        assert calls == 1  # Exactly 1 call, zero retries

    def test_permanent_validation_error_fails_immediately(self):
        """HTTP 400 invalid argument error fails immediately without retrying."""
        calls = 0

        def side_effect(model, contents, config):
            nonlocal calls
            calls += 1
            raise Exception("INVALID_ARGUMENT: 400 Bad Request")

        fake_client = FakeGenAIClient()
        fake_client.models.side_effect = side_effect
        provider = GeminiEmbeddingProvider(
            client=fake_client,
            retry_policy=RetryPolicy(max_retries=5),
            sleeper=lambda _: None,
        )

        with pytest.raises(EmbeddingValueError, match="Invalid request"):
            provider.embed_text("Test 400 error")

        assert calls == 1

    def test_non_transient_gemini_api_error_fails_immediately_without_retry(self):
        """Unclassified / non-transient GeminiAPIError (is_transient=False) fails fast on attempt 1."""
        calls = 0

        def side_effect(model, contents, config):
            nonlocal calls
            calls += 1
            raise Exception("Unknown internal failure: 500 Internal Server Error")

        fake_client = FakeGenAIClient()
        fake_client.models.side_effect = side_effect
        provider = GeminiEmbeddingProvider(
            client=fake_client,
            retry_policy=RetryPolicy(max_retries=5),
            sleeper=lambda _: None,
        )

        with pytest.raises(GeminiAPIError) as exc_info:
            provider.embed_text("Test non-transient error")

        assert calls == 1  # Exactly 1 call, zero retries
        assert exc_info.value.is_transient is False
        assert not isinstance(exc_info.value, GeminiTransientError)

    def test_execute_with_retry_rejects_explicitly_non_transient_error(self):
        """execute_with_retry fails fast if an exception has is_transient=False, even if type is in retryable_exceptions."""
        calls = 0

        def op():
            nonlocal calls
            calls += 1
            raise GeminiAPIError("Custom error", is_transient=False)

        policy = RetryPolicy(max_retries=3)
        with pytest.raises(GeminiAPIError):
            execute_with_retry(
                operation=op,
                retryable_exceptions=(GeminiAPIError,),
                policy=policy,
                sleeper=lambda _: None,
            )
        assert calls == 1

    def test_transient_503_retry_and_recovery(self):
        """HTTP 503 transient server error triggers exponential backoff retry and succeeds upon recovery."""
        attempts = 0
        sleeps = []

        def side_effect(model, contents, config):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise Exception("503 Service Unavailable: backend overloaded")
            return MockEmbedContentResponse([0.3] * 768)

        fake_client = FakeGenAIClient()
        fake_client.models.side_effect = side_effect
        provider = GeminiEmbeddingProvider(
            client=fake_client,
            retry_policy=RetryPolicy(max_retries=3, initial_delay_s=0.1, backoff_factor=2.0),
            sleeper=lambda d: sleeps.append(d),
        )

        vec = provider.embed_text("Test 503 retry")
        assert len(vec) == 768
        assert attempts == 3
        assert len(sleeps) == 2
        assert sleeps == [0.1, 0.2]

    def test_transient_503_exhaustion_raises_gemini_transient_error(self):
        """Persistent HTTP 503 exhausts retries and raises GeminiTransientError with is_transient=True."""
        fake_client = FakeGenAIClient()
        fake_client.models.side_effect = Exception("503 Service Unavailable")
        provider = GeminiEmbeddingProvider(
            client=fake_client,
            retry_policy=RetryPolicy(max_retries=2, initial_delay_s=0.01),
            sleeper=lambda _: None,
        )

        with pytest.raises(GeminiTransientError) as exc_info:
            provider.embed_text("Test 503 exhaustion")

        assert exc_info.value.status_code == 503
        assert exc_info.value.is_transient is True
        assert isinstance(exc_info.value, GeminiAPIError)

    def test_permanent_http_401_authentication_error_fails_immediately(self):
        """HTTP 401 authentication failure fails immediately on attempt 1 without retries."""
        calls = 0

        def side_effect(model, contents, config):
            nonlocal calls
            calls += 1
            raise Exception("API_KEY_INVALID: 401 Unauthorized")

        fake_client = FakeGenAIClient()
        fake_client.models.side_effect = side_effect
        provider = GeminiEmbeddingProvider(
            client=fake_client,
            retry_policy=RetryPolicy(max_retries=5),
            sleeper=lambda _: None,
        )

        with pytest.raises(EmbeddingConfigurationError, match="authentication failed"):
            provider.embed_text("Test 401 error")

        assert calls == 1

    def test_offline_mode_without_client_blocks_live_execution(self):
        """In offline mode (is_live_enabled=False), embed_text raises error without contacting network."""
        provider = GeminiEmbeddingProvider(is_live_enabled=False)
        with pytest.raises(EmbeddingConfigurationError, match="offline mode.*strictly disabled"):
            provider.embed_text("Test offline mode safety")

    def test_live_client_initialization_configures_bounded_timeout(self, monkeypatch):
        """
        Verifies that _get_client initializes genai.Client with http_options={'timeout': 15000}
        when is_live_enabled=True and api_key is provided, without executing any network calls.
        """
        captured_kwargs = {}

        class MockClientClass:
            def __init__(self, **kwargs):
                nonlocal captured_kwargs
                captured_kwargs = kwargs

        import google.genai as genai
        monkeypatch.setattr(genai, "Client", MockClientClass)

        provider = GeminiEmbeddingProvider(
            api_key="AIzaFakeTestKeyForTimeoutInspection",
            is_live_enabled=True,
        )

        client = provider._get_client()
        assert client is not None
        assert captured_kwargs["api_key"] == "AIzaFakeTestKeyForTimeoutInspection"
        assert captured_kwargs["http_options"] == {"timeout": 15000}

    def test_real_sdk_client_timeout_configured_without_network_call(self):
        """
        Verifies that the real installed google-genai Client accepts http_options={'timeout': 15000}
        and sets timeout=15000 ms on its internal ApiClient without transmitting any HTTP requests.
        """
        from google import genai
        client = genai.Client(api_key="fake-offline-key", http_options={"timeout": 15000})
        assert client._api_client._http_options.timeout == 15000

    def test_cache_hit_bypasses_api_calls(self):
        """When cache is provided, repeated identical text results in 0 API calls."""
        fake_client = FakeGenAIClient([0.42] * 768)
        cache = InMemoryEmbeddingCache()
        provider = GeminiEmbeddingProvider(client=fake_client, cache=cache)

        text = "PostgreSQL B-tree deduplication documentation chunk."

        # First call: cache miss -> calls client
        vec1 = provider.embed_document(text, title="Indexing")
        assert fake_client.models.call_count == 1
        assert provider.api_call_count == 1
        assert provider.cache_hit_count == 0

        # Second call: cache hit -> 0 client calls
        vec2 = provider.embed_document(text, title="Indexing")
        assert fake_client.models.call_count == 1  # Unchanged!
        assert provider.api_call_count == 1        # Unchanged!
        assert provider.cache_hit_count == 1       # Incremented!
        assert vec1 == vec2

    def test_batch_embedding_fidelity_and_ordering(self):
        """embed_batch embeds multiple texts preserving strict 1:1 ordering."""
        fake_client = FakeGenAIClient([0.1] * 768)
        provider = GeminiEmbeddingProvider(client=fake_client)

        texts = ["Chunk 1", "Chunk 2", "Chunk 3"]
        titles = ["Title 1", "Title 2", "Title 3"]

        batch_vecs = provider.embed_batch(texts, titles=titles)
        assert len(batch_vecs) == 3
        assert fake_client.models.call_count == 3
        assert fake_client.models.last_contents == "Chunk 3"

    def test_batch_embedding_invalid_titles_length(self):
        """Mismatched titles length raises EmbeddingValueError."""
        fake_client = FakeGenAIClient()
        provider = GeminiEmbeddingProvider(client=fake_client)

        with pytest.raises(EmbeddingValueError, match="titles length.*must match texts length"):
            provider.embed_batch(["Text 1", "Text 2"], titles=["Only One Title"])

    def test_cache_distinguishes_document_vs_query_task_types(self):
        """Identical text embedded as document vs query MUST NOT collide in cache."""
        fake_client = FakeGenAIClient()
        # Return distinct vectors for distinct task types
        def side_effect(model, contents, config):
            task_type = config.get("task_type")
            if task_type == TASK_TYPE_DOCUMENT:
                return MockEmbedContentResponse([0.11] * 768)
            elif task_type == TASK_TYPE_QUERY:
                return MockEmbedContentResponse([0.99] * 768)
            return MockEmbedContentResponse([0.5] * 768)

        fake_client.models.side_effect = side_effect
        cache = InMemoryEmbeddingCache()
        provider = GeminiEmbeddingProvider(client=fake_client, cache=cache)

        text = "PostgreSQL write-ahead logging details."

        # 1. Embed as document
        doc_vec = provider.embed_document(text)
        assert fake_client.models.call_count == 1
        assert doc_vec[0] == 0.11

        # 2. Embed as query (same text) -> MUST trigger second API call, not collide!
        query_vec = provider.embed_query(text)
        assert fake_client.models.call_count == 2
        assert query_vec[0] == 0.99

        # 3. Third calls should hit cache individually
        doc_vec_cached = provider.embed_document(text)
        query_vec_cached = provider.embed_query(text)
        assert fake_client.models.call_count == 2  # Still 2!
        assert provider.cache_hit_count == 2
        assert doc_vec_cached == doc_vec
        assert query_vec_cached == query_vec

    def test_cache_distinguishes_document_titles(self):
        """Identical text embedded with different document titles MUST NOT collide in cache."""
        fake_client = FakeGenAIClient()
        def side_effect(model, contents, config):
            title = config.get("title")
            if title == "Title A":
                return MockEmbedContentResponse([0.1] * 768)
            elif title == "Title B":
                return MockEmbedContentResponse([0.2] * 768)
            return MockEmbedContentResponse([0.3] * 768)

        fake_client.models.side_effect = side_effect
        cache = InMemoryEmbeddingCache()
        provider = GeminiEmbeddingProvider(client=fake_client, cache=cache)

        text = "PostgreSQL configuration parameter shared_buffers."

        vec_a = provider.embed_document(text, title="Title A")
        vec_b = provider.embed_document(text, title="Title B")
        vec_none = provider.embed_document(text, title=None)

        assert fake_client.models.call_count == 3
        assert vec_a[0] == 0.1
        assert vec_b[0] == 0.2
        assert vec_none[0] == 0.3

    def test_token_estimate_proxy_handles_continuous_unbroken_text(self):
        """Giant continuous string with zero spaces is caught by character proxy."""
        fake_client = FakeGenAIClient([0.1] * 768)
        provider = GeminiEmbeddingProvider(client=fake_client, max_input_tokens=500)

        # 3,500 continuous characters with no whitespace
        unbroken_text = "a" * 3500
        with pytest.raises(EmbeddingValueError, match="offline heuristic token proxy limit"):
            provider.embed_text(unbroken_text)

    def test_retry_after_server_guidance_respected(self):
        """Server-directed Retry-After delay is parsed and respected by execute_with_retry."""
        attempts = 0
        sleeps = []

        def side_effect(model, contents, config):
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                # Server response with explicit retry guidance
                raise Exception("Resource exhausted. Please retry in 2.5s. [HTTP 429]")
            return MockEmbedContentResponse([0.7] * 768)

        fake_client = FakeGenAIClient()
        fake_client.models.side_effect = side_effect

        retry_policy = RetryPolicy(max_retries=3, initial_delay_s=0.1, max_delay_s=10.0)
        provider = GeminiEmbeddingProvider(
            client=fake_client,
            retry_policy=retry_policy,
            sleeper=lambda d: sleeps.append(d),
        )

        vec = provider.embed_text("Test Retry-After parsing")
        assert len(vec) == 768
        assert attempts == 2
        assert len(sleeps) == 1
        assert pytest.approx(sleeps[0], abs=1e-3) == 2.5  # Exactly 2.5s from server message!

    def test_retry_policy_with_jitter_bounded(self):
        """RetryPolicy with jitter computes bounded delays within [base, base + jitter_max]."""
        policy = RetryPolicy(initial_delay_s=1.0, backoff_factor=2.0, max_delay_s=10.0, jitter=True, jitter_max_s=0.5)

        delays = [policy.compute_delay(0) for _ in range(20)]
        for d in delays:
            assert 1.0 <= d <= 1.5

        delays_attempt_1 = [policy.compute_delay(1) for _ in range(20)]
        for d in delays_attempt_1:
            assert 2.0 <= d <= 2.5

    # =========================================================================
    # Phase 3E.3.2 Regression Tests: Legacy Cache Fallback & Retry-After Safety
    # =========================================================================

    def test_legacy_cache_entry_rejected_for_query(self):
        """
        A legacy 3-tuple cache entry (hash, model, dim) CANNOT be returned
        for a RETRIEVAL_QUERY lookup, because query embeddings require distinct geometry.
        """
        cache = InMemoryEmbeddingCache()
        text = "SELECT * FROM pg_stat_activity;"
        content_hash = compute_content_hash(text)
        legacy_key = (content_hash, "gemini-embedding-001", 768)
        cache._store[legacy_key] = [0.42] * 768

        # Direct cache lookup for RETRIEVAL_QUERY must miss
        result = cache.get(
            content_hash=content_hash,
            model_name="gemini-embedding-001",
            dimensions=768,
            task_type=TASK_TYPE_QUERY,
            title=None,
        )
        assert result is None
        assert cache.has(
            content_hash=content_hash,
            model_name="gemini-embedding-001",
            dimensions=768,
            task_type=TASK_TYPE_QUERY,
            title=None,
        ) is False

    def test_legacy_cache_entry_rejected_for_titled_document(self):
        """
        A legacy 3-tuple cache entry CANNOT be returned for a titled document,
        because titled embeddings are conditioned on the document title.
        """
        cache = InMemoryEmbeddingCache()
        text = "PostgreSQL shared_buffers configuration."
        content_hash = compute_content_hash(text)
        legacy_key = (content_hash, "gemini-embedding-001", 768)
        cache._store[legacy_key] = [0.42] * 768

        result = cache.get(
            content_hash=content_hash,
            model_name="gemini-embedding-001",
            dimensions=768,
            task_type=TASK_TYPE_DOCUMENT,
            title="Chapter 20: Resource Consumption",
        )
        assert result is None
        assert cache.has(
            content_hash=content_hash,
            model_name="gemini-embedding-001",
            dimensions=768,
            task_type=TASK_TYPE_DOCUMENT,
            title="Chapter 20: Resource Consumption",
        ) is False

    def test_legacy_cache_entry_rejected_by_gemini_provider(self):
        """
        GeminiEmbeddingProvider strictly enforces allow_legacy=False.
        Even for an untitled document, an unlabelled 3-part legacy entry
        cannot guarantee Gemini task-type equivalence and must trigger a fresh API call.
        """
        cache = InMemoryEmbeddingCache()
        text = "PostgreSQL WAL archiving."
        content_hash = compute_content_hash(text)
        legacy_key = (content_hash, "gemini-embedding-001", 768)
        cache._store[legacy_key] = [0.99] * 768

        fake_client = FakeGenAIClient([0.55] * 768)
        provider = GeminiEmbeddingProvider(client=fake_client, cache=cache)

        # Provider embedding for untitled document
        vec = provider.embed_document(text, title=None)
        # Client was called because legacy cache entry was ignored
        assert fake_client.models.call_count == 1
        assert provider.cache_hit_count == 0
        assert vec[0] == 0.55  # Returned fresh vector, not legacy 0.99

    def test_legacy_cache_entry_accepted_for_generic_untitled_document(self):
        """
        Direct cache lookup with allow_legacy=True accepts a legacy entry
        only for untitled RETRIEVAL_DOCUMENT.
        """
        cache = InMemoryEmbeddingCache()
        text = "Generic text"
        content_hash = compute_content_hash(text)
        legacy_key = (content_hash, "mock-model", 768)
        cache._store[legacy_key] = [0.33] * 768

        retrieved = cache.get(
            content_hash=content_hash,
            model_name="mock-model",
            dimensions=768,
            task_type=TASK_TYPE_DOCUMENT,
            title=None,
            allow_legacy=True,
        )
        assert retrieved == [0.33] * 768
        assert cache.has(
            content_hash=content_hash,
            model_name="mock-model",
            dimensions=768,
            task_type=TASK_TYPE_DOCUMENT,
            title=None,
            allow_legacy=True,
        ) is True

    def test_legacy_cache_entry_rejected_when_allow_legacy_false(self):
        """Direct cache lookup with allow_legacy=False rejects legacy 3-tuple."""
        cache = InMemoryEmbeddingCache()
        text = "Generic text"
        content_hash = compute_content_hash(text)
        legacy_key = (content_hash, "mock-model", 768)
        cache._store[legacy_key] = [0.33] * 768

        retrieved = cache.get(
            content_hash=content_hash,
            model_name="mock-model",
            dimensions=768,
            task_type=TASK_TYPE_DOCUMENT,
            title=None,
            allow_legacy=False,
        )
        assert retrieved is None

    def test_parse_retry_after_numeric_and_float(self):
        """Numeric values (int, float, numeric string) parse to float seconds."""
        assert parse_retry_after(10) == 10.0
        assert parse_retry_after(4.5) == 4.5
        assert parse_retry_after("30") == 30.0
        assert parse_retry_after("1.25") == 1.25
        assert parse_retry_after("-5") is None
        assert parse_retry_after(True) is None  # Boolean not treated as int 1

    def test_parse_retry_after_http_date(self):
        """HTTP-date string parses relative to fixed now_fn."""
        fixed_now = datetime(2026, 10, 21, 7, 27, 45, tzinfo=timezone.utc)
        header_date = "Wed, 21 Oct 2026 07:28:00 GMT"

        parsed = parse_retry_after(header_date, now_fn=lambda: fixed_now)
        assert parsed == 15.0

        # Date in the past clamps to 0.0s
        past_date = "Wed, 21 Oct 2026 07:20:00 GMT"
        parsed_past = parse_retry_after(past_date, now_fn=lambda: fixed_now)
        assert parsed_past == 0.0

    def test_parse_retry_after_provider_error_messages(self):
        """Provider error messages in various formats are parsed correctly."""
        assert parse_retry_after("Resource exhausted. Please retry in 12s. [429]") == 12.0
        assert parse_retry_after("Rate limit reached. Please retry after 3.5 seconds.") == 3.5
        assert parse_retry_after("retry-after: 20") == 20.0
        assert parse_retry_after("quota error retry_delay { seconds: 45 }") == 45.0
        assert parse_retry_after("quota limit retry_after_seconds: 18") == 18.0
        # Natural language without numbers falls back safely to None
        assert parse_retry_after("Please retry in a moment") is None

    def test_parse_retry_after_from_exception_attributes_and_headers(self):
        """Exception objects with .retry_after_s, .retry_after, or .response.headers are parsed."""
        class MockHttpException(Exception):
            pass

        exc = MockHttpException("API Error 429")
        exc.retry_after = "8.0"
        assert parse_retry_after(exc) == 8.0

        class MockResp:
            headers = {"Retry-After": "14"}
        exc2 = MockHttpException("API Error 429")
        exc2.response = MockResp()
        assert parse_retry_after(exc2) == 14.0

    def test_retry_delay_exceeding_max_delay_aborts_immediately_without_early_retry(self):
        """
        CRITICAL SAFETY RULE: If server delay exceeds policy.max_delay_s,
        stop retrying immediately and propagate error; do NOT retry early or sleep.
        """
        attempts = 0
        sleeps = []

        def failing_operation():
            nonlocal attempts
            attempts += 1
            # Server requests 60s, but client policy ceiling is 10s
            exc = GeminiRateLimitError("Rate limited", retry_after_s=60.0)
            raise exc

        policy = RetryPolicy(max_retries=3, max_delay_s=10.0)

        with pytest.raises(GeminiRateLimitError):
            execute_with_retry(
                operation=failing_operation,
                retryable_exceptions=(GeminiRateLimitError,),
                policy=policy,
                sleeper=lambda d: sleeps.append(d),
            )

        # Aborted on attempt 0 without retrying early!
        assert attempts == 1
        assert len(sleeps) == 0  # No sleep performed!

    def test_retry_never_retries_earlier_than_server_delay(self):
        """
        CRITICAL SAFETY RULE: When server requests a delay within max_delay_s,
        wait time is at least the server-requested delay (never earlier).
        """
        attempts = 0
        sleeps = []

        def failing_operation():
            nonlocal attempts
            attempts += 1
            if attempts < 2:
                # Server requests 4.5s; standard initial backoff would be 0.5s
                exc = GeminiRateLimitError("Rate limited", retry_after_s=4.5)
                raise exc
            return "success"

        policy = RetryPolicy(max_retries=3, initial_delay_s=0.5, max_delay_s=10.0)

        result = execute_with_retry(
            operation=failing_operation,
            retryable_exceptions=(GeminiRateLimitError,),
            policy=policy,
            sleeper=lambda d: sleeps.append(d),
        )

        assert result == "success"
        assert attempts == 2
        assert len(sleeps) == 1
        assert sleeps[0] >= 4.5  # Never earlier than server requested delay!

    def test_batch_embedding_with_partial_cache_hits(self):
        """
        Batch embedding with a mixture of cached and uncached items preserves
        strict 1:1 ordering, retrieves cached items without API calls, and
        stores newly embedded items into the cache.
        """
        cache = InMemoryEmbeddingCache()
        text_cached = "Cached document text."
        text_new = "Uncached new document text."
        vec_cached = [0.11] * 768
        vec_new = [0.22] * 768

        # Pre-seed cache with text_cached under title "Section 1"
        cache.set(
            content_hash=compute_content_hash(text_cached),
            model_name="gemini-embedding-001",
            dimensions=768,
            vector=vec_cached,
            task_type=TASK_TYPE_DOCUMENT,
            title="Section 1",
        )

        fake_client = FakeGenAIClient(vec_new)
        provider = GeminiEmbeddingProvider(client=fake_client, cache=cache)

        batch_texts = [text_cached, text_new]
        batch_titles = ["Section 1", "Section 2"]

        results = provider.embed_batch(batch_texts, titles=batch_titles)

        assert len(results) == 2
        assert results[0] == vec_cached
        assert results[1] == vec_new
        assert fake_client.models.call_count == 1  # Only the uncached item invoked API
        assert provider.cache_hit_count == 1       # Exactly 1 cache hit recorded
        assert provider.api_call_count == 1        # Exactly 1 API call recorded


