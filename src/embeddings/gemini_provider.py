"""
Google Gemini Embedding Provider (Offline-Ready & Pluggable)
Location: src/embeddings/gemini_provider.py

Implements BaseEmbeddingProvider for Google's Gemini embedding models
(provisionally gemini-embedding-001) using the official GenAI API conventions.

Key Features & Boundaries:
1. Decoupled & Offline-Testable: Can be imported and tested without google-genai
   installed or API keys configured, via client dependency injection.
2. Asymmetric Retrieval Tasks: Supports RETRIEVAL_DOCUMENT vs RETRIEVAL_QUERY.
3. Input Limit Enforcement: Detects inputs exceeding model token context limits
   using an offline heuristic proxy (word/char and optional tokenizer) and raises
   clear errors without silent truncation.
4. Cache-Integrated: Integrates with BaseEmbeddingCache (composite key:
   content_hash, model_name, dimensions, task_type, title).
5. Transient Failure Resilience: Configurable exponential backoff with jitter on
   transient rate-limit (429) and network errors, respecting server Retry-After.
6. Safe Credentials: Never logs, prints, or exposes API keys.
7. Safety Lock: Live execution is strictly blocked unless is_live_enabled=True.
"""

import logging
import math
import random
import re
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Union

from src.chunkers.base import compute_content_hash, normalize_text
from src.embeddings.base import (
    BaseEmbeddingProvider,
    EmbeddingConfigurationError,
    EmbeddingDimensionError,
    EmbeddingError,
    EmbeddingValueError,
    validate_text_input,
    validate_vector,
)
from src.embeddings.cache import BaseEmbeddingCache
from src.embeddings.config import (
    MODEL_GEMINI_EMBEDDING_001,
    PROVISIONAL_DEFAULT_DIMENSION,
)
from src.embeddings.retry import RetryPolicy, execute_with_retry, parse_retry_after
from src.tokenizers import BaseTokenizer

logger = logging.getLogger(__name__)

# Official context limit for gemini-embedding-001
MAX_INPUT_TOKENS_001 = 2048

# Approximate character safety ceiling (1 token ~ 3 to 4 chars; 2048 tokens ~ 8000 chars)
# Used as a defensive pre-check to reject gigantic unbroken payloads before API submission
MAX_INPUT_CHARS_SAFETY_CEILING = 10000

# Supported Task Types for gemini-embedding-001
TASK_TYPE_DOCUMENT = "RETRIEVAL_DOCUMENT"
TASK_TYPE_QUERY = "RETRIEVAL_QUERY"
TASK_TYPE_SEMANTIC_SIMILARITY = "SEMANTIC_SIMILARITY"
TASK_TYPE_CLASSIFICATION = "CLASSIFICATION"
TASK_TYPE_CLUSTERING = "CLUSTERING"

VALID_TASK_TYPES = {
    TASK_TYPE_DOCUMENT,
    TASK_TYPE_QUERY,
    TASK_TYPE_SEMANTIC_SIMILARITY,
    TASK_TYPE_CLASSIFICATION,
    TASK_TYPE_CLUSTERING,
}


def _sanitize_error_message(msg: str, secret: Optional[str] = None) -> str:
    """Removes API keys and secrets from error strings."""
    if not msg:
        return msg
    sanitized = msg
    if secret:
        sanitized = sanitized.replace(secret, "[REDACTED_API_KEY]")
    sanitized = re.sub(r"AIza[0-9A-Za-z-_]{35}", "[REDACTED_API_KEY]", sanitized)
    sanitized = re.sub(
        r"(?:api_?key|key|auth|authorization)[=:]\s*['\"]?[^\s&'\"]+",
        "key=[REDACTED]",
        sanitized,
        flags=re.IGNORECASE,
    )
    return sanitized


class GeminiAPIError(EmbeddingError):
    """Raised when an external Gemini API error occurs."""
    def __init__(
        self,
        message: str,
        status_code: Optional[int] = None,
        is_transient: bool = False,
        retry_after_s: Optional[float] = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.is_transient = is_transient
        self.retry_after_s = retry_after_s


class GeminiTransientError(GeminiAPIError):
    """Raised when a transient Gemini API error occurs (HTTP 503, timeout, network error)."""
    def __init__(
        self,
        message: str = "Transient Gemini API error",
        status_code: Optional[int] = 503,
        retry_after_s: Optional[float] = None,
    ):
        super().__init__(
            message=message,
            status_code=status_code,
            is_transient=True,
            retry_after_s=retry_after_s,
        )


class GeminiRateLimitError(GeminiTransientError):
    """Raised when HTTP 429 RESOURCE_EXHAUSTED rate limit is encountered."""
    def __init__(
        self,
        message: str = "Gemini API rate limit exceeded (HTTP 429)",
        retry_after_s: Optional[float] = None,
    ):
        super().__init__(
            message=message,
            status_code=429,
            retry_after_s=retry_after_s,
        )


class GeminiEmbeddingProvider(BaseEmbeddingProvider):
    """
    Production-conscious embedding provider for Google Gemini models.
    
    Provides offline testability through pluggable client injection, strict
    dimension enforcement via Matryoshka Representation Learning (MRL),
    asymmetric retrieval task configuration, and defensive caching.
    """

    def __init__(
        self,
        model_name: str = MODEL_GEMINI_EMBEDDING_001,
        dimensions: int = PROVISIONAL_DEFAULT_DIMENSION,
        api_key: Optional[str] = None,
        client: Optional[Any] = None,
        cache: Optional[BaseEmbeddingCache] = None,
        retry_policy: Optional[RetryPolicy] = None,
        is_live_enabled: bool = False,
        default_task_type: str = TASK_TYPE_DOCUMENT,
        sleeper: Callable[[float], None] = time.sleep,
        max_input_tokens: int = MAX_INPUT_TOKENS_001,
        tokenizer: Optional[BaseTokenizer] = None,
    ):
        """
        Initializes the Gemini embedding provider.

        Args:
            model_name: Canonical model identifier. Default: 'gemini-embedding-001'.
            dimensions: Target vector dimension (e.g. 768). Default: 768.
            api_key: Google AI Studio API key (masked, never logged).
            client: Optional injected SDK client (used for tests and decoupling).
            cache: Optional BaseEmbeddingCache implementation for caching hits.
            retry_policy: Configurable exponential backoff policy for transient errors.
            is_live_enabled: Explicit safety gate. Must be True to make network calls.
            default_task_type: Default task type for embed_text ('RETRIEVAL_DOCUMENT').
            sleeper: Injected sleep callable for deterministic 0-latency testing.
            max_input_tokens: Hard ceiling for input text tokens (default: 2048).
            tokenizer: Optional BaseTokenizer for local heuristic token estimation.
        """
        if not isinstance(model_name, str) or not model_name.strip():
            raise EmbeddingConfigurationError("model_name cannot be empty.")

        if isinstance(dimensions, bool) or not isinstance(dimensions, int) or not (1 <= dimensions <= 2000):
            raise EmbeddingDimensionError(f"dimensions ({dimensions}) must be an integer between 1 and 2000.")

        if default_task_type not in VALID_TASK_TYPES:
            raise EmbeddingConfigurationError(
                f"Invalid default_task_type: {default_task_type!r}. Must be one of {VALID_TASK_TYPES}."
            )

        self._model_name = model_name.strip()
        self._dimensions = dimensions
        self._api_key = api_key
        self._client = client
        self._cache = cache
        self._retry_policy = retry_policy or RetryPolicy(max_retries=3, initial_delay_s=0.5, backoff_factor=2.0)
        self._is_live_enabled = is_live_enabled
        self._default_task_type = default_task_type
        self._sleeper = sleeper
        self._max_input_tokens = max_input_tokens
        self._tokenizer = tokenizer

        # Telemetry counters (in-memory)
        self.api_call_count = 0
        self.cache_hit_count = 0

    @property
    def model_name(self) -> str:
        return self._model_name

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def is_live_enabled(self) -> bool:
        return self._is_live_enabled

    def __repr__(self) -> str:
        return (
            f"GeminiEmbeddingProvider(model_name={self._model_name!r}, "
            f"dimensions={self._dimensions}, is_live_enabled={self._is_live_enabled}, "
            f"has_api_key={bool(self._api_key)})"
        )

    def _validate_input_length(self, text: str) -> None:
        """
        Ensures input text does not exceed model capacity.
        Does NOT silently truncate text; raises an actionable EmbeddingValueError.

        NOTE ON TOKEN ESTIMATION:
        This check uses an offline heuristic proxy (derived from word and character
        counts, or an injected BaseTokenizer if provided), NOT Google's server-side
        BPE tokenizer. It acts as a defensive guard to prevent sending oversized
        payloads. Exact token counts can only be verified via Google's live API.
        """
        # Hard character safety ceiling to catch giant unbroken payloads or binary data
        if len(text) > MAX_INPUT_CHARS_SAFETY_CEILING:
            raise EmbeddingValueError(
                f"Input text length ({len(text)} characters) exceeds safety ceiling "
                f"({MAX_INPUT_CHARS_SAFETY_CEILING} chars). Text must be chunked prior "
                f"to embedding; silent truncation is disabled."
            )

        # Sub-word token proxy estimate:
        # In English prose/code, 1 token is roughly 3.5 to 4 characters or ~0.75 words.
        # To prevent unspaced continuous strings from evading word-based splitting,
        # we evaluate both character-derived and word-derived token estimates.
        if self._tokenizer is not None:
            estimated_tokens = self._tokenizer.count_tokens(text)
        else:
            word_token_estimate = len(text.split()) * 1.3
            char_token_estimate = len(text) / 3.5
            estimated_tokens = int(max(word_token_estimate, char_token_estimate))

        if estimated_tokens > self._max_input_tokens:
            raise EmbeddingValueError(
                f"Input text exceeds the offline heuristic token proxy limit "
                f"(~{estimated_tokens} estimated tokens > {self._max_input_tokens} model limit). "
                f"Note: Google's proprietary tokenizer may count tokens slightly differently. "
                f"To prevent unexpected API rejections, chunk your text before embedding; "
                f"silent truncation is disabled."
            )

    def _get_client(self) -> Any:
        """
        Retrieves or initializes the SDK client.
        In offline mode, injected client is required; live creation requires is_live_enabled=True.
        """
        if self._client is not None:
            return self._client

        if not self._is_live_enabled:
            raise EmbeddingConfigurationError(
                "GeminiEmbeddingProvider is in offline mode (is_live_enabled=False) and no client was injected. "
                "Live network execution is strictly disabled."
            )

        # Lazy import of google.genai
        try:
            from google import genai
        except ImportError as e:
            raise EmbeddingConfigurationError(
                "The 'google-genai' package is not installed. "
                "Install it when live integration is approved or inject a mock client."
            ) from e

        if not self._api_key:
            raise EmbeddingConfigurationError(
                "API key not provided. Set api_key or pass a client instance."
            )

        self._client = genai.Client(
            api_key=self._api_key,
            http_options={"timeout": 15000},
        )
        return self._client

    def _execute_api_call(
        self,
        text: str,
        task_type: str,
        title: Optional[str] = None,
    ) -> List[float]:
        """
        Executes single-text embedding call with exponential backoff on transient errors.
        """
        client = self._get_client()

        # Build request configuration
        config: Dict[str, Any] = {
            "output_dimensionality": self._dimensions,
            "task_type": task_type,
        }
        if title and task_type == TASK_TYPE_DOCUMENT:
            config["title"] = title

        def _call():
            self.api_call_count += 1
            try:
                # Official GenAI SDK invocation pattern: client.models.embed_content(...)
                response = client.models.embed_content(
                    model=self._model_name,
                    contents=text,
                    config=config,
                )
            except Exception as exc:
                clean_exc = _sanitize_error_message(str(exc), self._api_key)
                exc_str = clean_exc.lower()

                # Parse server Retry-After guidance from message, headers, or exception attributes
                retry_after_s = parse_retry_after(exc)

                # Classify transient errors (HTTP 429 / resource exhausted / timeouts)
                if "429" in exc_str or "resource_exhausted" in exc_str or "quota" in exc_str:
                    raise GeminiRateLimitError(
                        f"Gemini API rate limit exceeded: {clean_exc}",
                        retry_after_s=retry_after_s,
                    ) from None
                if "503" in exc_str or "unavailable" in exc_str or "timeout" in exc_str:
                    raise GeminiTransientError(
                        f"Transient Gemini API server error: {clean_exc}",
                        status_code=503,
                        retry_after_s=retry_after_s,
                    ) from None
                # Permanent errors
                if "400" in exc_str or "invalid_argument" in exc_str:
                    raise EmbeddingValueError(f"Invalid request to Gemini API: {clean_exc}") from None
                if "401" in exc_str or "403" in exc_str or "permission" in exc_str or "api_key" in exc_str:
                    raise EmbeddingConfigurationError(f"Gemini API authentication failed: {clean_exc}") from None
                raise GeminiAPIError(
                    f"Gemini API call failed: {clean_exc}",
                    is_transient=False,
                ) from None

            # Parse vector from response object
            return self._extract_vector_from_response(response)

        # Retry transient failures (HTTP 429, 503, timeouts) using our tested execute_with_retry
        return execute_with_retry(
            operation=_call,
            retryable_exceptions=(GeminiTransientError,),
            policy=self._retry_policy,
            sleeper=self._sleeper,
        )

    def _extract_vector_from_response(self, response: Any) -> List[float]:
        """
        Extracts and verifies raw vector values from various SDK response formats.
        """
        if response is None:
            raise EmbeddingError("Gemini API returned None response.")

        vector = None
        # Pattern 1: response.embedding.values
        if hasattr(response, "embedding") and response.embedding is not None:
            if hasattr(response.embedding, "values"):
                vector = response.embedding.values
            elif isinstance(response.embedding, (list, tuple)):
                vector = response.embedding

        # Pattern 2: response.embeddings[0].values
        elif hasattr(response, "embeddings") and response.embeddings:
            first = response.embeddings[0]
            if hasattr(first, "values"):
                vector = first.values
            elif isinstance(first, (list, tuple)):
                vector = first

        # Pattern 3: Dict response (mock / raw REST)
        elif isinstance(response, dict):
            if "embedding" in response and "values" in response["embedding"]:
                vector = response["embedding"]["values"]
            elif "embeddings" in response and response["embeddings"]:
                vector = response["embeddings"][0].get("values", response["embeddings"][0])

        if vector is None:
            raise EmbeddingError(f"Unable to extract vector values from response structure: {type(response)}")

        # Convert to standard Python float list
        vector_list = [float(v) for v in vector]
        # Strict validation against configured dimension and finite numbers
        validate_vector(vector_list, expected_dim=self._dimensions)
        return vector_list

    def embed_text(
        self,
        text: str,
        task_type: Optional[str] = None,
        title: Optional[str] = None,
    ) -> List[float]:
        """
        Embeds a single text string.

        Args:
            text: Non-empty UTF-8 text string to embed.
            task_type: Optional task type ('RETRIEVAL_DOCUMENT' or 'RETRIEVAL_QUERY').
            title: Optional document title for RETRIEVAL_DOCUMENT chunks.

        Returns:
            A list of floats of exact length `self.dimensions`.
        """
        validate_text_input(text)
        self._validate_input_length(text)

        effective_task_type = task_type or self._default_task_type
        if effective_task_type not in VALID_TASK_TYPES:
            raise EmbeddingConfigurationError(f"Unsupported task_type: {effective_task_type!r}.")

        effective_title = title if effective_task_type == TASK_TYPE_DOCUMENT else None

        # Check Cache if configured (keyed by hash, model, dimensions, task_type, title)
        # Note: Gemini embeddings require explicit task_type and title conditioning.
        # Safe equivalence cannot be established with unlabelled legacy 3-part entries;
        # therefore allow_legacy=False is strictly enforced for Gemini provider lookups.
        content_hash = compute_content_hash(text)
        if self._cache is not None:
            cached_vector = self._cache.get(
                content_hash=content_hash,
                model_name=self._model_name,
                dimensions=self._dimensions,
                task_type=effective_task_type,
                title=effective_title,
                allow_legacy=False,
            )
            if cached_vector is not None:
                self.cache_hit_count += 1
                return cached_vector

        # Cache miss or unconfigured: execute API call
        vector = self._execute_api_call(text=text, task_type=effective_task_type, title=effective_title)

        # Store in cache if configured
        if self._cache is not None:
            self._cache.set(
                content_hash=content_hash,
                model_name=self._model_name,
                dimensions=self._dimensions,
                vector=vector,
                task_type=effective_task_type,
                title=effective_title,
            )

        return vector

    def embed_document(self, text: str, title: Optional[str] = None) -> List[float]:
        """Convenience method: Embeds chunk with RETRIEVAL_DOCUMENT task type."""
        return self.embed_text(text=text, task_type=TASK_TYPE_DOCUMENT, title=title)

    def embed_query(self, query: str) -> List[float]:
        """Convenience method: Embeds user query with RETRIEVAL_QUERY task type."""
        return self.embed_text(text=query, task_type=TASK_TYPE_QUERY, title=None)

    def embed_batch(
        self,
        texts: List[str],
        task_type: Optional[str] = None,
        titles: Optional[List[Optional[str]]] = None,
    ) -> List[List[float]]:
        """
        Embeds a batch of texts sequentially or via batched execution.
        Preserves strict input-to-output 1:1 index ordering.
        """
        if not isinstance(texts, list):
            raise EmbeddingValueError(f"texts must be a list, got {type(texts).__name__}.")
        if not texts:
            return []

        if titles is not None and len(titles) != len(texts):
            raise EmbeddingValueError(f"titles length ({len(titles)}) must match texts length ({len(texts)}).")

        results = []
        for i, text in enumerate(texts):
            title = titles[i] if titles else None
            vec = self.embed_text(text=text, task_type=task_type, title=title)
            results.append(vec)
        return results
