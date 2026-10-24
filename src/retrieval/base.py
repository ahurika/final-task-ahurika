"""
Retrieval Base Abstractions, Data Structures, and Exceptions
Location: src/retrieval/base.py

Provides abstract interfaces, strongly typed request/response containers,
and exceptions for the RAG retrieval engine.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Sequence, Union

from src.models import Chunk
from src.embeddings.base import EmbeddingDimensionError, EmbeddingValueError


# =============================================================================
# Constants & Defaults (REQ-11, EVALUATION.md)
# =============================================================================

DEFAULT_SIMILARITY_THRESHOLD: float = 0.65

REFUSAL_MESSAGE: str = (
    "I cannot answer this question based on the provided documents as no sufficiently "
    "relevant information was found."
)


# =============================================================================
# Retrieval Exception Hierarchy
# =============================================================================

class RetrievalError(Exception):
    """Base exception for all retrieval engine errors."""
    pass


class RetrievalDimensionError(RetrievalError, EmbeddingDimensionError):
    """Raised when query vector dimension does not match indexed chunk dimension."""
    pass


class RetrievalValueError(RetrievalError, EmbeddingValueError):
    """Raised when query vector or retrieval parameters (e.g., top_k) contain invalid values."""
    pass


# =============================================================================
# Metadata Filter (REQ-12)
# =============================================================================

@dataclass(frozen=True)
class MetadataFilter:
    """
    Structured criteria for pre-filtering candidate chunks before similarity ranking.
    All non-None fields require an exact string match against the chunk's attributes.
    """
    strategy: Optional[str] = None
    source_filename: Optional[str] = None
    page_or_section: Optional[str] = None

    @property
    def is_empty(self) -> bool:
        """Returns True if no filtering conditions are specified."""
        return (
            self.strategy is None
            and self.source_filename is None
            and self.page_or_section is None
        )

    def matches(self, chunk: Any) -> bool:
        """
        Evaluates whether a chunk satisfies all configured filter predicates.
        Supports Chunk objects, ChunkRecord objects, or duck-typed mappings.
        """
        if self.is_empty:
            return True

        # 1. Strategy filter (exact match, e.g., 'fixed_window' or 'structure_aware')
        if self.strategy is not None:
            chunk_strategy = getattr(chunk, "strategy", None)
            if chunk_strategy != self.strategy:
                return False

        # 2. Source filename filter (exact match, e.g., 'mvcc-intro.md')
        if self.source_filename is not None:
            chunk_filename = getattr(chunk, "source_filename", None)
            if chunk_filename != self.source_filename:
                return False

        # 3. Page or section filter (exact match, e.g., 'Page 14' or 'Section 13.2')
        if self.page_or_section is not None:
            chunk_page_or_sec = getattr(chunk, "page_or_section", None)
            if chunk_page_or_sec != self.page_or_section:
                return False

        return True


# =============================================================================
# Scored Chunk & Retrieval Response Containers
# =============================================================================

@dataclass(frozen=True)
class ScoredChunk:
    """A single retrieved chunk with its computed similarity score and citation."""
    chunk: Chunk
    score: float
    citation: Any  # Citation instance from src.retrieval.citations


@dataclass(frozen=True)
class RetrievalResponse:
    """
    Structured outcome of a retrieval query, including scored chunks,
    calibrated refusal heuristic decision, and execution metadata.
    """
    scored_chunks: List[ScoredChunk]
    top_score: Optional[float]
    refusal_triggered: bool
    refusal_message: Optional[str]
    retrieval_k: int
    total_candidates_considered: int

    @property
    def retrieved_chunk_ids(self) -> List[str]:
        """Convenience accessor for chunk IDs (maps to query_telemetry.retrieved_chunk_ids)."""
        return [sc.chunk.chunk_id for sc in self.scored_chunks]

    @property
    def similarity_scores(self) -> List[float]:
        """Convenience accessor for similarity scores (maps to query_telemetry.similarity_scores)."""
        return [sc.score for sc in self.scored_chunks]

    @property
    def citations(self) -> List[Any]:
        """Convenience accessor for citation objects."""
        return [sc.citation for sc in self.scored_chunks]


# =============================================================================
# Abstract Retriever Interface
# =============================================================================

class BaseRetriever(ABC):
    """Abstract base class defining the retrieval engine interface."""

    @property
    @abstractmethod
    def dimension(self) -> Optional[int]:
        """Expected embedding vector dimension, or None if not yet bound."""
        pass

    @abstractmethod
    def add_chunk(self, chunk: Chunk, embedding: Sequence[Union[float, int]]) -> None:
        """Index a single chunk with its embedding vector."""
        pass

    @abstractmethod
    def retrieve(
        self,
        query_vector: Sequence[Union[float, int]],
        top_k: int = 5,
        metadata_filter: Optional[MetadataFilter] = None,
        similarity_threshold: Optional[float] = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> RetrievalResponse:
        """
        Retrieves top-k chunks matching the query vector and optional metadata filter.
        Applies similarity threshold refusal heuristic when top score < threshold.
        """
        pass

    @abstractmethod
    def clear(self) -> None:
        """Clears all indexed chunks and embeddings."""
        pass

    @abstractmethod
    def __len__(self) -> int:
        """Returns the number of indexed chunks."""
        pass
