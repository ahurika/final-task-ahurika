"""
Retrieval Package Exports
Location: src/retrieval/__init__.py
"""

from src.retrieval.base import (
    BaseRetriever,
    MetadataFilter,
    RetrievalError,
    RetrievalDimensionError,
    RetrievalValueError,
    RetrievalResponse,
    ScoredChunk,
    DEFAULT_SIMILARITY_THRESHOLD,
    REFUSAL_MESSAGE,
)
from src.retrieval.citations import (
    Citation,
    CitationFormatter,
    DEFAULT_MAX_SNIPPET_LENGTH,
)
from src.retrieval.in_memory import (
    InMemoryRetriever,
    compute_cosine_similarity,
)

__all__ = [
    "BaseRetriever",
    "MetadataFilter",
    "RetrievalError",
    "RetrievalDimensionError",
    "RetrievalValueError",
    "RetrievalResponse",
    "ScoredChunk",
    "DEFAULT_SIMILARITY_THRESHOLD",
    "REFUSAL_MESSAGE",
    "Citation",
    "CitationFormatter",
    "DEFAULT_MAX_SNIPPET_LENGTH",
    "InMemoryRetriever",
    "compute_cosine_similarity",
]
