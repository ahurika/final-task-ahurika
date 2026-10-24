"""
Embedding Provider & Vector Management Package
Location: src/embeddings/

Provides:
- Base interfaces and validation utilities (BaseEmbeddingProvider, validate_vector, validate_text_input)
- Exception hierarchy (EmbeddingError, EmbeddingDimensionError, EmbeddingValueError, etc.)
- Deterministic mock provider for offline testing (DeterministicMockEmbeddingProvider)
- Gemini embedding provider with offline client injection (GeminiEmbeddingProvider)
- Embedding cache contract and in-memory implementation (CacheKey, BaseEmbeddingCache, InMemoryEmbeddingCache)
- Configuration and candidate model registry (EmbeddingConfig, MODEL_GEMINI_EMBEDDING_2, etc.)
- Retry policy primitives (RetryPolicy, execute_with_retry)
"""

from src.embeddings.base import (
    BaseEmbeddingProvider,
    EmbeddingError,
    EmbeddingDimensionError,
    EmbeddingValueError,
    EmbeddingConfigurationError,
    EmbeddingCacheError,
    validate_vector,
    validate_text_input,
)
from src.embeddings.mock_provider import DeterministicMockEmbeddingProvider
from src.embeddings.cache import (
    CacheKey,
    BaseEmbeddingCache,
    InMemoryEmbeddingCache,
    DiskEmbeddingCache,
)
from src.embeddings.config import (
    EmbeddingConfig,
    MODEL_GEMINI_EMBEDDING_2,
    MODEL_GEMINI_EMBEDDING_001,
    MODEL_MOCK_DETERMINISTIC,
    DEPRECATED_MODELS,
    SUPPORTED_MODELS,
    PROVISIONAL_DEFAULT_MODEL,
    PROVISIONAL_DEFAULT_DIMENSION,
)
from src.embeddings.retry import (
    RetryPolicy,
    execute_with_retry,
)
from src.embeddings.gemini_provider import (
    GeminiEmbeddingProvider,
    GeminiAPIError,
    GeminiTransientError,
    GeminiRateLimitError,
    TASK_TYPE_DOCUMENT,
    TASK_TYPE_QUERY,
    TASK_TYPE_SEMANTIC_SIMILARITY,
    TASK_TYPE_CLASSIFICATION,
    TASK_TYPE_CLUSTERING,
)

__all__ = [
    "BaseEmbeddingProvider",
    "EmbeddingError",
    "EmbeddingDimensionError",
    "EmbeddingValueError",
    "EmbeddingConfigurationError",
    "EmbeddingCacheError",
    "validate_vector",
    "validate_text_input",
    "DeterministicMockEmbeddingProvider",
    "GeminiEmbeddingProvider",
    "GeminiAPIError",
    "GeminiTransientError",
    "GeminiRateLimitError",
    "TASK_TYPE_DOCUMENT",
    "TASK_TYPE_QUERY",
    "TASK_TYPE_SEMANTIC_SIMILARITY",
    "TASK_TYPE_CLASSIFICATION",
    "TASK_TYPE_CLUSTERING",
    "CacheKey",
    "BaseEmbeddingCache",
    "InMemoryEmbeddingCache",
    "DiskEmbeddingCache",
    "EmbeddingConfig",
    "MODEL_GEMINI_EMBEDDING_2",
    "MODEL_GEMINI_EMBEDDING_001",
    "MODEL_MOCK_DETERMINISTIC",
    "DEPRECATED_MODELS",
    "SUPPORTED_MODELS",
    "PROVISIONAL_DEFAULT_MODEL",
    "PROVISIONAL_DEFAULT_DIMENSION",
    "RetryPolicy",
    "execute_with_retry",
]
