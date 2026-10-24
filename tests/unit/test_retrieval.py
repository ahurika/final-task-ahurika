"""
Unit Tests for Offline In-Memory Retrieval Engine, Metadata Filtering, and Citation Formatter
Location: tests/unit/test_retrieval.py

Validates algorithmic retrieval mechanics, vector input safety, cosine ranking,
stable tie-breaking, predicate filtering, citation formatting, and threshold refusal (REQ-10, REQ-11, REQ-12).
"""

import math
import pytest
from typing import List

from src.models import Chunk
from src.retrieval import (
    BaseRetriever,
    InMemoryRetriever,
    MetadataFilter,
    Citation,
    CitationFormatter,
    RetrievalDimensionError,
    RetrievalValueError,
    DEFAULT_SIMILARITY_THRESHOLD,
    REFUSAL_MESSAGE,
    compute_cosine_similarity,
)


# =============================================================================
# Helper Fixtures & Builders
# =============================================================================

def build_test_chunk(
    chunk_id: str = "chunk-001",
    document_id: str = "doc-001",
    strategy: str = "fixed_window",
    chunk_index: int = 0,
    source_filename: str = "mvcc-intro.md",
    page_or_section: str = "13.2. Concurrency Control",
    content: str = "PostgreSQL implements multi-version concurrency control.",
    source_url: str = "https://www.postgresql.org/docs/16/mvcc.html",
    content_hash: str = "a" * 64,
    token_count: int = 10,
) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        document_id=document_id,
        strategy=strategy,
        chunk_index=chunk_index,
        source_filename=source_filename,
        page_or_section=page_or_section,
        content=content,
        content_hash=content_hash,
        token_count=token_count,
        source_url=source_url,
    )


# =============================================================================
# 1. Cosine Similarity Function Tests
# =============================================================================

class TestCosineSimilarity:
    """Tests the mathematical properties, boundary checks, and input validation of compute_cosine_similarity."""

    def test_cosine_identical_vectors(self):
        vec = [1.0, 2.0, 3.0]
        score = compute_cosine_similarity(vec, vec)
        assert math.isclose(score, 1.0, abs_tol=1e-7)

    def test_cosine_orthogonal_vectors(self):
        vec_a = [1.0, 0.0, 0.0]
        vec_b = [0.0, 1.0, 0.0]
        score = compute_cosine_similarity(vec_a, vec_b)
        assert math.isclose(score, 0.0, abs_tol=1e-7)

    def test_cosine_opposite_vectors(self):
        vec_a = [1.0, 0.0]
        vec_b = [-1.0, 0.0]
        score = compute_cosine_similarity(vec_a, vec_b)
        assert math.isclose(score, -1.0, abs_tol=1e-7)

    def test_cosine_unnormalized_vectors(self):
        vec_a = [3.0, 0.0]
        vec_b = [10.0, 0.0]
        score = compute_cosine_similarity(vec_a, vec_b)
        assert math.isclose(score, 1.0, abs_tol=1e-7)

    def test_cosine_dimension_mismatch(self):
        vec_a = [1.0, 2.0]
        vec_b = [1.0, 2.0, 3.0]
        with pytest.raises(RetrievalDimensionError, match="Vector dimension mismatch"):
            compute_cosine_similarity(vec_a, vec_b)

    def test_cosine_expected_dim_mismatch(self):
        vec_a = [1.0, 2.0]
        vec_b = [3.0, 4.0]
        with pytest.raises(RetrievalDimensionError, match="expected 3 dimensions, got 2"):
            compute_cosine_similarity(vec_a, vec_b, expected_dim=3)

    def test_cosine_zero_norm_vector_rejected(self):
        vec_zero = [0.0, 0.0, 0.0]
        vec_valid = [1.0, 2.0, 3.0]
        with pytest.raises(RetrievalValueError, match="zero-norm vector"):
            compute_cosine_similarity(vec_zero, vec_valid)
        with pytest.raises(RetrievalValueError, match="zero-norm vector"):
            compute_cosine_similarity(vec_valid, vec_zero)

    def test_cosine_nan_and_inf_components_rejected(self):
        vec_nan = [1.0, float("nan")]
        vec_inf = [1.0, float("inf")]
        vec_valid = [1.0, 2.0]
        with pytest.raises(RetrievalValueError, match="non-finite"):
            compute_cosine_similarity(vec_nan, vec_valid)
        with pytest.raises(RetrievalValueError, match="non-finite"):
            compute_cosine_similarity(vec_valid, vec_inf)

    def test_cosine_boolean_components_rejected(self):
        vec_bool = [True, 1.0]
        vec_valid = [1.0, 1.0]
        with pytest.raises(RetrievalValueError, match="not a valid number"):
            compute_cosine_similarity(vec_bool, vec_valid)

    def test_cosine_non_numeric_components_rejected(self):
        vec_str = ["1.0", 2.0]
        vec_valid = [1.0, 2.0]
        with pytest.raises(RetrievalValueError, match="not a valid number"):
            compute_cosine_similarity(vec_str, vec_valid)

    def test_cosine_non_sequence_inputs_rejected(self):
        with pytest.raises(RetrievalValueError, match="must be a list or tuple"):
            compute_cosine_similarity("not_a_list", [1.0, 2.0])
        with pytest.raises(RetrievalValueError, match="must be a list or tuple"):
            compute_cosine_similarity([1.0, 2.0], None)

    def test_cosine_empty_vectors_rejected(self):
        with pytest.raises(RetrievalValueError, match="cannot be empty"):
            compute_cosine_similarity([], [])


# =============================================================================
# 2. Citation Formatter Tests
# =============================================================================

class TestCitationFormatter:
    """Tests provenance preservation, tag generation, and snippet bounding in CitationFormatter."""

    def test_format_citation_markdown_section(self):
        formatter = CitationFormatter()
        chunk = build_test_chunk(
            source_filename="mvcc-intro.md",
            page_or_section="13.2. Concurrency Control",
        )
        citation = formatter.format_citation(chunk)
        assert citation.chunk_id == "chunk-001"
        assert citation.source_filename == "mvcc-intro.md"
        assert citation.page_or_section == "13.2. Concurrency Control"
        assert citation.formatted_citation == "[Doc: mvcc-intro.md, Section: 13.2. Concurrency Control]"
        assert citation.source_url == "https://www.postgresql.org/docs/16/mvcc.html"
        assert citation.content_hash == "a" * 64

    def test_format_citation_pdf_page(self):
        formatter = CitationFormatter()
        chunk = build_test_chunk(
            source_filename="postgresql-16-US.pdf",
            page_or_section="Page 14",
        )
        citation = formatter.format_citation(chunk)
        assert citation.formatted_citation == "[Doc: postgresql-16-US.pdf, Page 14]"

    def test_format_citation_missing_metadata(self):
        formatter = CitationFormatter()
        chunk = build_test_chunk(
            source_filename="",
            page_or_section="",
            source_url="",
            content_hash="",
        )
        citation = formatter.format_citation(chunk)
        assert citation.source_filename is None
        assert citation.page_or_section is None
        assert citation.source_url is None
        assert citation.content_hash is None
        assert citation.formatted_citation == "[Unknown Source]"

    def test_format_citation_snippet_bounding(self):
        formatter = CitationFormatter(default_max_snippet_length=20)
        long_content = "12345678901234567890_EXTRA_TEXT"
        chunk = build_test_chunk(content=long_content)
        citation = formatter.format_citation(chunk, max_snippet_length=20)
        assert citation.evidence_snippet == "12345678901234567890..."
        assert len(citation.evidence_snippet) == 23

    def test_format_citation_short_snippet_not_truncated(self):
        formatter = CitationFormatter()
        short_content = "Short text"
        chunk = build_test_chunk(content=short_content)
        citation = formatter.format_citation(chunk)
        assert citation.evidence_snippet == short_content

    def test_format_citation_from_duck_typed_dict(self):
        formatter = CitationFormatter()
        chunk_dict = {
            "chunk_id": "dict-chunk-1",
            "source_filename": "wal.md",
            "page_or_section": "Chapter 30",
            "content": "WAL logging content",
            "source_url": "https://pg.org/wal",
            "content_hash": "b" * 64,
        }
        citation = formatter.format_citation(chunk_dict)
        assert citation.chunk_id == "dict-chunk-1"
        assert citation.formatted_citation == "[Doc: wal.md, Section: Chapter 30]"
        assert citation.evidence_snippet == "WAL logging content"

    def test_format_citations_batch(self):
        formatter = CitationFormatter()
        c1 = build_test_chunk(chunk_id="c1", source_filename="f1.md", page_or_section="s1")
        c2 = build_test_chunk(chunk_id="c2", source_filename="f2.pdf", page_or_section="Page 5")
        citations = formatter.format_citations([c1, c2])
        assert len(citations) == 2
        assert citations[0].formatted_citation == "[Doc: f1.md, Section: s1]"
        assert citations[1].formatted_citation == "[Doc: f2.pdf, Page 5]"

    def test_citation_to_dict(self):
        formatter = CitationFormatter()
        chunk = build_test_chunk()
        citation = formatter.format_citation(chunk)
        d = citation.to_dict()
        assert d["chunk_id"] == "chunk-001"
        assert d["source_filename"] == "mvcc-intro.md"
        assert "formatted_citation" in d


# =============================================================================
# 3. Metadata Filter Tests
# =============================================================================

class TestMetadataFilter:
    """Tests exact matching semantics of MetadataFilter across strategy, filename, and page/section."""

    def test_empty_filter_matches_all(self):
        empty_filter = MetadataFilter()
        assert empty_filter.is_empty is True
        chunk = build_test_chunk()
        assert empty_filter.matches(chunk) is True

    def test_filter_by_strategy(self):
        filter_fixed = MetadataFilter(strategy="fixed_window")
        filter_struct = MetadataFilter(strategy="structure_aware")

        chunk_fixed = build_test_chunk(strategy="fixed_window")
        chunk_struct = build_test_chunk(strategy="structure_aware")

        assert filter_fixed.matches(chunk_fixed) is True
        assert filter_fixed.matches(chunk_struct) is False
        assert filter_struct.matches(chunk_struct) is True
        assert filter_struct.matches(chunk_fixed) is False

    def test_filter_by_source_filename(self):
        filter_mvcc = MetadataFilter(source_filename="mvcc-intro.md")
        chunk_mvcc = build_test_chunk(source_filename="mvcc-intro.md")
        chunk_wal = build_test_chunk(source_filename="wal-intro.md")

        assert filter_mvcc.matches(chunk_mvcc) is True
        assert filter_mvcc.matches(chunk_wal) is False

    def test_filter_by_page_or_section(self):
        filter_sec = MetadataFilter(page_or_section="13.2. Concurrency Control")
        chunk_match = build_test_chunk(page_or_section="13.2. Concurrency Control")
        chunk_diff = build_test_chunk(page_or_section="13.3. Explicit Locking")

        assert filter_sec.matches(chunk_match) is True
        assert filter_sec.matches(chunk_diff) is False

    def test_filter_combined_predicates(self):
        combined = MetadataFilter(
            strategy="fixed_window",
            source_filename="mvcc-intro.md",
            page_or_section="13.2. Concurrency Control",
        )
        chunk_perfect = build_test_chunk(
            strategy="fixed_window",
            source_filename="mvcc-intro.md",
            page_or_section="13.2. Concurrency Control",
        )
        chunk_partial = build_test_chunk(
            strategy="structure_aware",
            source_filename="mvcc-intro.md",
            page_or_section="13.2. Concurrency Control",
        )
        assert combined.matches(chunk_perfect) is True
        assert combined.matches(chunk_partial) is False


# =============================================================================
# 4. InMemoryRetriever Ranking, Filtering, and Refusal Tests
# =============================================================================

class TestInMemoryRetriever:
    """Tests the in-memory retriever's cosine ranking, top-k limits, tie-breaking, and refusal decisions."""

    def test_ranking_order_descending(self):
        retriever = InMemoryRetriever(dimension=2)
        # Query along X-axis [1, 0]
        # Candidate 1: 0 degrees [1, 0] -> score 1.0
        # Candidate 2: 45 degrees [1, 1] -> score ~0.707
        # Candidate 3: 90 degrees [0, 1] -> score 0.0
        c1 = build_test_chunk(chunk_id="c1", content="Closest")
        c2 = build_test_chunk(chunk_id="c2", content="Medium")
        c3 = build_test_chunk(chunk_id="c3", content="Orthogonal")

        retriever.add_chunk(c1, [1.0, 0.0])
        retriever.add_chunk(c2, [1.0, 1.0])
        retriever.add_chunk(c3, [0.0, 1.0])

        response = retriever.retrieve(query_vector=[1.0, 0.0], top_k=3, similarity_threshold=None)
        assert len(response.scored_chunks) == 3
        assert response.scored_chunks[0].chunk.chunk_id == "c1"
        assert math.isclose(response.scored_chunks[0].score, 1.0, abs_tol=1e-5)
        assert response.scored_chunks[1].chunk.chunk_id == "c2"
        assert math.isclose(response.scored_chunks[1].score, math.cos(math.pi / 4), abs_tol=1e-5)
        assert response.scored_chunks[2].chunk.chunk_id == "c3"
        assert math.isclose(response.scored_chunks[2].score, 0.0, abs_tol=1e-5)

    def test_top_k_values_1_3_5_10(self):
        retriever = InMemoryRetriever(dimension=2)
        for i in range(15):
            angle = (i * math.pi) / 30.0  # 0 to ~90 degrees
            vec = [math.cos(angle), math.sin(angle)]
            retriever.add_chunk(build_test_chunk(chunk_id=f"c-{i:02d}"), vec)

        for k in (1, 3, 5, 10):
            response = retriever.retrieve(query_vector=[1.0, 0.0], top_k=k, similarity_threshold=None)
            assert len(response.scored_chunks) == k
            assert response.retrieval_k == k

    def test_fewer_results_than_top_k(self):
        retriever = InMemoryRetriever(dimension=2)
        retriever.add_chunk(build_test_chunk(chunk_id="c1"), [1.0, 0.0])
        retriever.add_chunk(build_test_chunk(chunk_id="c2"), [0.0, 1.0])

        response = retriever.retrieve(query_vector=[1.0, 0.0], top_k=5, similarity_threshold=None)
        assert len(response.scored_chunks) == 2
        assert response.retrieval_k == 5

    def test_stable_tie_breaking_by_chunk_id(self):
        retriever = InMemoryRetriever(dimension=2)
        # Three chunks with identical vectors and scores
        c_gamma = build_test_chunk(chunk_id="gamma")
        c_alpha = build_test_chunk(chunk_id="alpha")
        c_beta = build_test_chunk(chunk_id="beta")

        retriever.add_chunk(c_gamma, [1.0, 0.0])
        retriever.add_chunk(c_alpha, [1.0, 0.0])
        retriever.add_chunk(c_beta, [1.0, 0.0])

        response = retriever.retrieve(query_vector=[1.0, 0.0], top_k=3, similarity_threshold=None)
        # Expected stable alphabetical tie-break order: alpha, beta, gamma
        returned_ids = [sc.chunk.chunk_id for sc in response.scored_chunks]
        assert returned_ids == ["alpha", "beta", "gamma"]

    def test_filter_applied_before_top_k_selection(self):
        retriever = InMemoryRetriever(dimension=2)
        # Chunk 1: highest similarity (1.0), but fixed_window
        # Chunk 2: second highest (0.9), structure_aware
        # Chunk 3: third highest (0.8), structure_aware
        c1 = build_test_chunk(chunk_id="c1", strategy="fixed_window")
        c2 = build_test_chunk(chunk_id="c2", strategy="structure_aware")
        c3 = build_test_chunk(chunk_id="c3", strategy="structure_aware")

        retriever.add_chunk(c1, [1.0, 0.0])
        retriever.add_chunk(c2, [math.cos(0.2), math.sin(0.2)])
        retriever.add_chunk(c3, [math.cos(0.4), math.sin(0.4)])

        filter_struct = MetadataFilter(strategy="structure_aware")
        response = retriever.retrieve(
            query_vector=[1.0, 0.0],
            top_k=2,
            metadata_filter=filter_struct,
            similarity_threshold=None,
        )

        assert len(response.scored_chunks) == 2
        assert response.scored_chunks[0].chunk.chunk_id == "c2"
        assert response.scored_chunks[1].chunk.chunk_id == "c3"
        # Confirm c1 was not counted in candidates
        assert response.total_candidates_considered == 2

    def test_filter_no_matches_triggers_refusal(self):
        retriever = InMemoryRetriever(dimension=2)
        retriever.add_chunk(build_test_chunk(source_filename="mvcc.md"), [1.0, 0.0])

        filter_nonexistent = MetadataFilter(source_filename="nonexistent.md")
        response = retriever.retrieve(
            query_vector=[1.0, 0.0],
            top_k=5,
            metadata_filter=filter_nonexistent,
            similarity_threshold=DEFAULT_SIMILARITY_THRESHOLD,
        )

        assert len(response.scored_chunks) == 0
        assert response.top_score is None
        assert response.refusal_triggered is True
        assert response.refusal_message == REFUSAL_MESSAGE
        assert response.total_candidates_considered == 0

    def test_refusal_score_below_threshold(self):
        retriever = InMemoryRetriever(dimension=2)
        # Vector at ~60 degrees -> cosine similarity is 0.5 < 0.65 threshold
        vec_60_deg = [0.5, math.sqrt(3) / 2.0]
        retriever.add_chunk(build_test_chunk(chunk_id="low_sim"), vec_60_deg)

        response = retriever.retrieve(
            query_vector=[1.0, 0.0],
            top_k=3,
            similarity_threshold=0.65,
        )

        assert response.refusal_triggered is True
        assert response.refusal_message == REFUSAL_MESSAGE
        assert response.top_score < 0.65
        # Scored chunks are still returned for telemetry/audit
        assert len(response.scored_chunks) == 1

    def test_refusal_score_equal_to_threshold(self):
        retriever = InMemoryRetriever(dimension=1)
        # 1-D vector with similarity exactly 0.65
        retriever.add_chunk(build_test_chunk(chunk_id="exact"), [1.0])

        # Query vector with identical direction has similarity 1.0
        # Let's test with threshold equal to 1.0
        response = retriever.retrieve(
            query_vector=[1.0],
            top_k=1,
            similarity_threshold=1.0,
        )
        assert response.refusal_triggered is False
        assert response.refusal_message is None
        assert response.top_score == 1.0

    def test_refusal_score_above_threshold(self):
        retriever = InMemoryRetriever(dimension=2)
        # Vector at 0 degrees -> similarity 1.0 >= 0.65
        retriever.add_chunk(build_test_chunk(chunk_id="high_sim"), [1.0, 0.0])

        response = retriever.retrieve(
            query_vector=[1.0, 0.0],
            top_k=3,
            similarity_threshold=0.65,
        )
        assert response.refusal_triggered is False
        assert response.refusal_message is None
        assert response.top_score == 1.0

    def test_exact_refusal_message(self):
        expected = (
            "I cannot answer this question based on the provided documents as no sufficiently "
            "relevant information was found."
        )
        assert REFUSAL_MESSAGE == expected

    def test_empty_index_refusal_behavior(self):
        retriever = InMemoryRetriever(dimension=2)
        response = retriever.retrieve(
            query_vector=[1.0, 0.0],
            top_k=5,
            similarity_threshold=0.65,
        )
        assert response.refusal_triggered is True
        assert response.refusal_message == REFUSAL_MESSAGE
        assert response.top_score is None
        assert len(response.scored_chunks) == 0

    def test_refusal_disabled_when_threshold_is_none(self):
        retriever = InMemoryRetriever(dimension=2)
        retriever.add_chunk(build_test_chunk(chunk_id="c1"), [0.0, 1.0])  # score 0.0

        response = retriever.retrieve(
            query_vector=[1.0, 0.0],
            top_k=3,
            similarity_threshold=None,
        )
        assert response.refusal_triggered is False
        assert response.refusal_message is None
        assert response.top_score == 0.0

    def test_negative_cosine_scores_handled_correctly(self):
        retriever = InMemoryRetriever(dimension=2)
        # Opposite direction [-1, 0] gives score -1.0
        retriever.add_chunk(build_test_chunk(chunk_id="opp"), [-1.0, 0.0])

        response = retriever.retrieve(
            query_vector=[1.0, 0.0],
            top_k=1,
            similarity_threshold=-0.5,  # threshold -0.5, score -1.0 < -0.5 -> triggers refusal
        )
        assert math.isclose(response.top_score, -1.0, abs_tol=1e-5)
        assert response.refusal_triggered is True

    def test_invalid_top_k_rejected(self):
        retriever = InMemoryRetriever(dimension=2)
        with pytest.raises(RetrievalValueError, match="top_k must be an integer >= 1"):
            retriever.retrieve([1.0, 0.0], top_k=0)
        with pytest.raises(RetrievalValueError, match="top_k must be an integer >= 1"):
            retriever.retrieve([1.0, 0.0], top_k=-5)
        with pytest.raises(RetrievalValueError, match="top_k must be an integer >= 1"):
            retriever.retrieve([1.0, 0.0], top_k="invalid")
        with pytest.raises(RetrievalValueError, match="top_k must be an integer >= 1"):
            retriever.retrieve([1.0, 0.0], top_k=True)  # bool rejected

    def test_query_vector_dimension_mismatch_rejected(self):
        retriever = InMemoryRetriever(dimension=2)
        with pytest.raises(RetrievalDimensionError, match="Query vector dimension mismatch"):
            retriever.retrieve([1.0, 0.0, 0.0])

    def test_indexing_vector_dimension_mismatch_rejected(self):
        retriever = InMemoryRetriever(dimension=2)
        with pytest.raises(RetrievalDimensionError, match="Vector dimension mismatch"):
            retriever.add_chunk(build_test_chunk(), [1.0, 2.0, 3.0])

    def test_indexing_zero_norm_vector_rejected(self):
        retriever = InMemoryRetriever(dimension=2)
        with pytest.raises(RetrievalValueError, match="zero-norm vector"):
            retriever.add_chunk(build_test_chunk(), [0.0, 0.0])

    def test_retriever_convenience_accessors(self):
        retriever = InMemoryRetriever(dimension=2)
        retriever.add_chunk(build_test_chunk(chunk_id="c1"), [1.0, 0.0])
        response = retriever.retrieve([1.0, 0.0], top_k=1)
        assert response.retrieved_chunk_ids == ["c1"]
        assert len(response.similarity_scores) == 1
        assert len(response.citations) == 1

    def test_retriever_clear(self):
        retriever = InMemoryRetriever(dimension=2)
        retriever.add_chunk(build_test_chunk(chunk_id="c1"), [1.0, 0.0])
        assert len(retriever) == 1
        retriever.clear()
        assert len(retriever) == 0
