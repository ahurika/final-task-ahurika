"""
In-Memory Vector Retrieval Engine with Metadata Filtering and Threshold Refusal
Location: src/retrieval/in_memory.py

Implements an offline, in-memory cosine similarity retrieval engine providing
deterministic ranking, tie-breaking, predicate filtering, citation formatting,
and calibrated threshold-based refusal decisions (REQ-10, REQ-11, REQ-12).
"""

import math
from dataclasses import dataclass
from typing import Optional, List, Sequence, Union, Tuple, Any

from src.models import Chunk
from src.retrieval.base import (
    BaseRetriever,
    MetadataFilter,
    RetrievalDimensionError,
    RetrievalValueError,
    RetrievalResponse,
    ScoredChunk,
    DEFAULT_SIMILARITY_THRESHOLD,
    REFUSAL_MESSAGE,
)
from src.retrieval.citations import CitationFormatter


# =============================================================================
# Pure Mathematical Cosine Similarity Function
# =============================================================================

def compute_cosine_similarity(
    vec_a: Sequence[Union[float, int]],
    vec_b: Sequence[Union[float, int]],
    expected_dim: Optional[int] = None,
) -> float:
    """
    Computes exact cosine similarity between two numeric vectors with defensive validation:
    1. Validates that both vectors are sequences (list or tuple).
    2. Enforces equal dimensions and matches expected_dim if specified.
    3. Verifies every element is a real, finite float/int (rejects bools, NaNs, infinities).
    4. Rejects zero-norm vectors (zero magnitude) with RetrievalValueError.
    5. Computes dot_product / (norm_a * norm_b) clamped to [-1.0, 1.0].

    Raises:
        RetrievalValueError: For non-sequence types, non-finite values, booleans, or zero-norm vectors.
        RetrievalDimensionError: For dimension mismatches.
    """
    if not isinstance(vec_a, (list, tuple)):
        raise RetrievalValueError(f"Vector A must be a list or tuple, got {type(vec_a).__name__}.")
    if not isinstance(vec_b, (list, tuple)):
        raise RetrievalValueError(f"Vector B must be a list or tuple, got {type(vec_b).__name__}.")

    dim_a = len(vec_a)
    dim_b = len(vec_b)

    if dim_a == 0:
        raise RetrievalValueError("Vector A cannot be empty (0 dimensions).")
    if dim_b == 0:
        raise RetrievalValueError("Vector B cannot be empty (0 dimensions).")

    if dim_a != dim_b:
        raise RetrievalDimensionError(
            f"Vector dimension mismatch: vector A has dimension {dim_a}, "
            f"but vector B has dimension {dim_b}."
        )

    if expected_dim is not None and dim_a != expected_dim:
        raise RetrievalDimensionError(
            f"Vector dimension mismatch: expected {expected_dim} dimensions, got {dim_a}."
        )

    dot_product = 0.0
    norm_a_sq = 0.0
    norm_b_sq = 0.0

    for i in range(dim_a):
        val_a = vec_a[i]
        val_b = vec_b[i]

        # In Python, bool is an instance of int (isinstance(True, int) is True); reject explicitly
        if isinstance(val_a, bool) or not isinstance(val_a, (int, float)):
            raise RetrievalValueError(f"Vector A component at index {i} is not a valid number: {val_a!r}.")
        if not math.isfinite(val_a):
            raise RetrievalValueError(f"Vector A component at index {i} is non-finite: {val_a!r}.")

        if isinstance(val_b, bool) or not isinstance(val_b, (int, float)):
            raise RetrievalValueError(f"Vector B component at index {i} is not a valid number: {val_b!r}.")
        if not math.isfinite(val_b):
            raise RetrievalValueError(f"Vector B component at index {i} is non-finite: {val_b!r}.")

        f_a = float(val_a)
        f_b = float(val_b)

        dot_product += f_a * f_b
        norm_a_sq += f_a * f_a
        norm_b_sq += f_b * f_b

    if norm_a_sq <= 0.0 or math.isclose(norm_a_sq, 0.0, abs_tol=1e-15):
        raise RetrievalValueError("Vector A is a zero-norm vector (magnitude is zero).")
    if norm_b_sq <= 0.0 or math.isclose(norm_b_sq, 0.0, abs_tol=1e-15):
        raise RetrievalValueError("Vector B is a zero-norm vector (magnitude is zero).")

    norm_a = math.sqrt(norm_a_sq)
    norm_b = math.sqrt(norm_b_sq)

    sim = dot_product / (norm_a * norm_b)
    # Defensively clamp to [-1.0, 1.0] to protect against floating-point precision drift
    return max(-1.0, min(1.0, sim))


# =============================================================================
# In-Memory Index Entry
# =============================================================================

@dataclass
class _IndexedChunk:
    chunk: Chunk
    embedding: List[float]


# =============================================================================
# In-Memory Retriever Implementation
# =============================================================================

class InMemoryRetriever(BaseRetriever):
    """
    Offline in-memory retrieval engine executing exact cosine similarity ranking,
    deterministic tie-breaking, metadata pre-filtering, and similarity-threshold refusal.
    """

    def __init__(
        self,
        dimension: Optional[int] = None,
        default_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
        citation_formatter: Optional[CitationFormatter] = None,
    ):
        if dimension is not None and dimension <= 0:
            raise ValueError(f"dimension must be positive, got {dimension}")

        self._dimension: Optional[int] = dimension
        self.default_threshold: float = default_threshold
        self.citation_formatter: CitationFormatter = citation_formatter or CitationFormatter()
        self._items: List[_IndexedChunk] = []

    @property
    def dimension(self) -> Optional[int]:
        """Expected embedding vector dimension."""
        return self._dimension

    def __len__(self) -> int:
        """Returns the total number of indexed chunks."""
        return len(self._items)

    def clear(self) -> None:
        """Empties all indexed chunks and resets bound dimension if initially unbound."""
        self._items.clear()

    def add_chunk(
        self,
        chunk: Chunk,
        embedding: Sequence[Union[float, int]],
    ) -> None:
        """
        Indexes a single chunk with its embedding vector.
        Validates vector geometry, components, and dimension.
        """
        if not isinstance(chunk, Chunk):
            # Also support duck-typed objects or ChunkRecord by converting/validating
            if hasattr(chunk, "chunk_id") and hasattr(chunk, "content"):
                pass
            elif hasattr(chunk, "id") and hasattr(chunk, "content"):
                # Duck-typed ChunkRecord
                chunk = Chunk(
                    chunk_id=getattr(chunk, "id"),
                    document_id=getattr(chunk, "document_id", "unknown_doc"),
                    strategy=getattr(chunk, "strategy", "fixed_window"),
                    chunk_index=getattr(chunk, "chunk_index", 0),
                    source_filename=getattr(chunk, "source_filename", "unknown_file"),
                    page_or_section=getattr(chunk, "page_or_section", "unknown_section"),
                    content=getattr(chunk, "content", ""),
                    content_hash=getattr(chunk, "content_hash", "0" * 64),
                    token_count=getattr(chunk, "token_count", len(getattr(chunk, "content", "").split())),
                    source_url=getattr(chunk, "source_url", None),
                    document_date=getattr(chunk, "document_date", None),
                    metadata=getattr(chunk, "metadata", {}),
                )
            else:
                raise RetrievalValueError(f"Expected Chunk instance or compatible record, got {type(chunk).__name__}.")

        if not isinstance(embedding, (list, tuple)):
            raise RetrievalValueError(f"Embedding must be a list or tuple, got {type(embedding).__name__}.")

        # If dimension is not yet bound, bind it from first vector
        if self._dimension is None:
            if len(embedding) == 0:
                raise RetrievalValueError("Embedding vector cannot be empty (0 dimensions).")
            self._dimension = len(embedding)

        # Validate vector components and norm
        if len(embedding) != self._dimension:
            raise RetrievalDimensionError(
                f"Vector dimension mismatch: index expects {self._dimension} dimensions, got {len(embedding)}."
            )

        norm_sq = 0.0
        cleaned_vector: List[float] = []
        for i, val in enumerate(embedding):
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                raise RetrievalValueError(f"Vector component at index {i} is not a valid number: {val!r}.")
            if not math.isfinite(val):
                raise RetrievalValueError(f"Vector component at index {i} is non-finite: {val!r}.")
            f_val = float(val)
            cleaned_vector.append(f_val)
            norm_sq += f_val * f_val

        if norm_sq <= 0.0 or math.isclose(norm_sq, 0.0, abs_tol=1e-15):
            raise RetrievalValueError("Embedding vector is a zero-norm vector (magnitude is zero).")

        self._items.append(_IndexedChunk(chunk=chunk, embedding=cleaned_vector))

    def add_chunks(
        self,
        items: Sequence[Tuple[Chunk, Sequence[Union[float, int]]]],
    ) -> None:
        """Indexes multiple chunks and their corresponding embedding vectors."""
        for chunk, embedding in items:
            self.add_chunk(chunk, embedding)

    def retrieve(
        self,
        query_vector: Sequence[Union[float, int]],
        top_k: int = 5,
        metadata_filter: Optional[MetadataFilter] = None,
        similarity_threshold: Optional[float] = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> RetrievalResponse:
        """
        Executes offline cosine similarity retrieval against indexed chunks:
        1. Validates top_k (must be int >= 1).
        2. Validates query_vector (sequence, finite, non-bool, non-zero-norm, matching dimension).
        3. Applies metadata pre-filtering (strategy, filename, page_or_section).
        4. Computes cosine similarity scores for all qualifying candidates.
        5. Ranks candidates descending by score with deterministic tie-breaking (chunk_id ascending).
        6. Selects top_k qualifying chunks.
        7. Evaluates refusal heuristic: if top_score < similarity_threshold, sets refusal_triggered=True.
        """
        # 1. Validate top_k
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
            raise RetrievalValueError(f"top_k must be an integer >= 1, got {top_k!r}.")

        # 2. Validate query vector
        if not isinstance(query_vector, (list, tuple)):
            raise RetrievalValueError(f"Query vector must be a list or tuple, got {type(query_vector).__name__}.")

        if self._dimension is not None and len(query_vector) != self._dimension:
            raise RetrievalDimensionError(
                f"Query vector dimension mismatch: index expects {self._dimension} dimensions, "
                f"got {len(query_vector)}."
            )

        # 3. Filter candidates
        qualifying_items: List[_IndexedChunk] = []
        for item in self._items:
            if metadata_filter is None or metadata_filter.matches(item.chunk):
                qualifying_items.append(item)

        total_candidates = len(qualifying_items)

        # Handle empty qualifying candidates safely
        if total_candidates == 0:
            should_refuse = similarity_threshold is not None
            return RetrievalResponse(
                scored_chunks=[],
                top_score=None,
                refusal_triggered=should_refuse,
                refusal_message=REFUSAL_MESSAGE if should_refuse else None,
                retrieval_k=top_k,
                total_candidates_considered=0,
            )

        # 4. Score candidates
        scored_candidates: List[ScoredChunk] = []
        for item in qualifying_items:
            sim = compute_cosine_similarity(
                vec_a=query_vector,
                vec_b=item.embedding,
                expected_dim=self._dimension,
            )
            citation = self.citation_formatter.format_citation(item.chunk)
            scored_candidates.append(ScoredChunk(chunk=item.chunk, score=sim, citation=citation))

        # 5. Deterministic ranking: score DESC, chunk_id ASC for stable tie-breaking
        scored_candidates.sort(key=lambda sc: (-sc.score, sc.chunk.chunk_id))

        # 6. Top-k slicing
        selected_chunks = scored_candidates[:top_k]
        top_score = selected_chunks[0].score if selected_chunks else None

        # 7. Calibrated refusal heuristic
        refusal_triggered = False
        refusal_message = None
        if similarity_threshold is not None:
            if top_score is None or top_score < similarity_threshold:
                refusal_triggered = True
                refusal_message = REFUSAL_MESSAGE

        return RetrievalResponse(
            scored_chunks=selected_chunks,
            top_score=top_score,
            refusal_triggered=refusal_triggered,
            refusal_message=refusal_message,
            retrieval_k=top_k,
            total_candidates_considered=total_candidates,
        )
