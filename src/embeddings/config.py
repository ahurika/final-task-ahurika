"""
Embedding Provider Configuration & Candidate Model Registry
Location: src/embeddings/config.py

Manages model configuration, dimensions, and candidate Google Gemini models.

CRITICAL MODEL LIFECYCLE NOTE:
Google's deprecation documentation lists `text-embedding-004` as shut down.
The currently researched active Google candidates are:
- `gemini-embedding-2`
- `gemini-embedding-001`

These two models DO NOT produce interchangeable vectors. Vectors from
`gemini-embedding-2` cannot be queried against an index or cache built with
`gemini-embedding-001`. Switching models requires a separate cache namespace
and complete corpus re-embedding.

Working defaults use 768 as a PROVISIONAL dimension only. Live external API calls
are disabled by default via `is_live_enabled=False`.
"""

from dataclasses import dataclass
from typing import Set

from src.embeddings.base import (
    EmbeddingConfigurationError,
    EmbeddingDimensionError,
)

# Researched Active Google Candidates
MODEL_GEMINI_EMBEDDING_2 = "gemini-embedding-2"
MODEL_GEMINI_EMBEDDING_001 = "gemini-embedding-001"
MODEL_MOCK_DETERMINISTIC = "mock-deterministic-v1"

# Deprecated Models (Strictly Rejected)
DEPRECATED_MODELS: Set[str] = {
    "text-embedding-004",
    "models/text-embedding-004",
}

# Supported Active Model Identifiers
SUPPORTED_MODELS = [
    MODEL_GEMINI_EMBEDDING_001,
    MODEL_GEMINI_EMBEDDING_2,
    MODEL_MOCK_DETERMINISTIC,
]

# Provisional Defaults
PROVISIONAL_DEFAULT_MODEL = MODEL_GEMINI_EMBEDDING_001
PROVISIONAL_DEFAULT_DIMENSION = 768

# Allowed dimension bounds matching pgvector HNSW limits
MIN_EMBEDDING_DIMENSION = 1
MAX_EMBEDDING_DIMENSION = 2000


@dataclass
class EmbeddingConfig:
    """
    Configuration specification for embedding providers.
    
    Attributes:
        provider: Provider type ('mock' or 'gemini'). Default: 'mock'.
        model_name: Canonical model identifier. Default: 'gemini-embedding-001'.
        dimensions: Output vector dimension. Default: 768 (provisional).
        batch_size: Number of texts per chunking/embedding batch. Default: 16.
        is_live_enabled: Explicit guard. True allows live calls; False enforces offline mode.
    """
    provider: str = "mock"
    model_name: str = PROVISIONAL_DEFAULT_MODEL
    dimensions: int = PROVISIONAL_DEFAULT_DIMENSION
    batch_size: int = 16
    is_live_enabled: bool = False

    def __post_init__(self):
        # 1. Provider validation
        valid_providers = ("mock", "gemini")
        if self.provider not in valid_providers:
            raise EmbeddingConfigurationError(
                f"Invalid provider: {self.provider!r}. Must be one of {valid_providers}."
            )

        # 2. Model deprecation check
        if self.model_name in DEPRECATED_MODELS:
            raise EmbeddingConfigurationError(
                f"Model '{self.model_name}' is deprecated and shut down by Google. "
                f"Please configure '{MODEL_GEMINI_EMBEDDING_2}' or '{MODEL_GEMINI_EMBEDDING_001}'."
            )

        # 3. Model identifier non-empty check
        if not isinstance(self.model_name, str) or not self.model_name.strip():
            raise EmbeddingConfigurationError("model_name cannot be empty.")

        # 4. Dimension validation
        if isinstance(self.dimensions, bool) or not isinstance(self.dimensions, int):
            raise EmbeddingDimensionError(f"dimensions must be an integer, got {type(self.dimensions).__name__}.")

        if not (MIN_EMBEDDING_DIMENSION <= self.dimensions <= MAX_EMBEDDING_DIMENSION):
            raise EmbeddingDimensionError(
                f"dimensions ({self.dimensions}) out of bounds. Must be between "
                f"{MIN_EMBEDDING_DIMENSION} and {MAX_EMBEDDING_DIMENSION}."
            )

        # 5. Batch size validation
        if isinstance(self.batch_size, bool) or not isinstance(self.batch_size, int) or self.batch_size <= 0:
            raise EmbeddingConfigurationError(f"batch_size must be a positive integer, got {self.batch_size!r}.")

        # 6. Safety check for live provider
        if self.provider == "gemini" and not self.is_live_enabled:
            raise EmbeddingConfigurationError(
                "Live Gemini provider requested while is_live_enabled=False. "
                "Live integration is not authorized in this offline phase. Set provider='mock'."
            )
