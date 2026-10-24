"""
Embedding Provider Base Abstractions and Validation Utilities
Location: src/embeddings/base.py

Provides abstract interfaces, strongly typed exceptions, and strict validation
rules for vector embeddings within the RAG pipeline.
"""

from abc import ABC, abstractmethod
import math
from typing import List, Optional, Sequence, Union


# =============================================================================
# Embedding Exception Hierarchy
# =============================================================================

class EmbeddingError(Exception):
    """Base exception for all embedding provider and vector processing errors."""
    pass


class EmbeddingDimensionError(EmbeddingError):
    """Raised when a vector's dimension does not match the configured dimension."""
    pass


class EmbeddingValueError(EmbeddingError):
    """Raised when input text or vector values contain invalid, infinite, or NaN elements."""
    pass


class EmbeddingConfigurationError(EmbeddingError):
    """Raised when embedding provider or model configuration is invalid or unsupported."""
    pass


class EmbeddingCacheError(EmbeddingError):
    """Raised when embedding cache identity or storage invariant is violated."""
    pass


# =============================================================================
# Validation Utilities
# =============================================================================

def validate_vector(vector: Sequence[Union[float, int]], expected_dim: int) -> None:
    """
    Validates that a vector satisfies strict geometric and type invariants:
    1. Vector is a sequence (list or tuple).
    2. Vector length exactly matches expected_dim.
    3. Every component is a real, finite number (no booleans, no NaNs, no infinities).

    Raises:
        EmbeddingValueError: If the vector is not a sequence, or contains non-finite values.
        EmbeddingDimensionError: If vector length differs from expected_dim.
    """
    if not isinstance(vector, (list, tuple)):
        raise EmbeddingValueError(f"Vector must be a list or tuple of floats, got {type(vector).__name__}.")

    actual_dim = len(vector)
    if actual_dim != expected_dim:
        raise EmbeddingDimensionError(
            f"Vector dimension mismatch: expected exactly {expected_dim} dimensions, got {actual_dim}."
        )

    for i, val in enumerate(vector):
        # In Python, bool is a subclass of int (isinstance(True, int) is True). We must reject bools explicitly.
        if isinstance(val, bool) or not isinstance(val, (int, float)):
            raise EmbeddingValueError(
                f"Vector component at index {i} is not a valid number: {val!r} ({type(val).__name__})."
            )
        if not math.isfinite(val):
            raise EmbeddingValueError(
                f"Vector component at index {i} is non-finite (NaN or Infinity): {val!r}."
            )


def validate_text_input(text: str) -> str:
    """
    Validates input text before embedding:
    1. Must be a string.
    2. Must not be empty or whitespace-only.

    Raises:
        EmbeddingValueError: If text is not a string or is empty/whitespace-only.
    """
    if not isinstance(text, str):
        raise EmbeddingValueError(f"Input text must be a string, got {type(text).__name__}.")
    
    if not text.strip():
        raise EmbeddingValueError("Input text cannot be empty or whitespace-only.")

    return text


# =============================================================================
# Provider Abstract Interface
# =============================================================================

class BaseEmbeddingProvider(ABC):
    """
    Abstract interface for all embedding providers (mock and production).
    
    All implementations must guarantee:
    1. Output vectors are strictly validated against `self.dimensions`.
    2. Model identifier is canonical and non-empty.
    3. Batch operations handle empty lists safely and preserve sequence ordering.
    """

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Canonical model identifier (e.g. 'gemini-embedding-001', 'mock-deterministic-v1')."""
        pass

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Target embedding dimension (e.g. 768)."""
        pass

    @abstractmethod
    def embed_text(self, text: str) -> List[float]:
        """
        Generates an embedding vector for a single text input.

        Args:
            text: Validated non-empty string to embed.

        Returns:
            A list of finite float numbers of length `self.dimensions`.

        Raises:
            EmbeddingValueError: If input text is invalid or output values are non-finite.
            EmbeddingDimensionError: If output vector length does not match `self.dimensions`.
            EmbeddingError: If an underlying provider or computation error occurs.
        """
        pass

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        """
        Generates embedding vectors for a batch of text inputs.
        
        Default implementation delegates to `embed_text` sequentially. Subclasses
        can override this method for optimized bulk network batching.

        Args:
            texts: List of non-empty text strings to embed.

        Returns:
            A list of vector embeddings corresponding 1:1 with input order.
        """
        if not isinstance(texts, list):
            raise EmbeddingValueError(f"texts must be a list of strings, got {type(texts).__name__}.")
        if not texts:
            return []
        return [self.embed_text(t) for t in texts]

    def embed_document(self, text: str, title: Optional[str] = None) -> List[float]:
        """
        Generates an embedding vector for a document chunk with optional title.
        Default implementation delegates to `embed_text`. Subclasses can override
        to condition embeddings on document title or asymmetric task types.
        """
        return self.embed_text(text)

    def embed_query(self, query: str) -> List[float]:
        """
        Generates an embedding vector for a search query.
        Default implementation delegates to `embed_text`. Subclasses can override
        to condition embeddings on query task types.
        """
        return self.embed_text(query)

