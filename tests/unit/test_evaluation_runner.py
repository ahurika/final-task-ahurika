"""
Unit Tests for 30-Question Evaluation Harness and Benchmark Runner
Location: tests/unit/test_evaluation_runner.py

Validates:
1. Dataset loading and validation (30 questions, Q01-Q30, 25 answerable, 5 unanswerable).
2. Ground-truth preservation and escaped character handling.
3. Hand-verifiable metric calculations for Recall@k, Refusal matrix, and Latency.
4. Exactly 120 matrix evaluations across 4 configurations (Strategy A/B, k=3/5).
5. Strict measurement integrity: mock embeddings explicitly marked non-semantic.
6. Refusal confusion matrix tracking (TR, MR, FR, TA).
7. Provenance integrity verification and CLI exit status behavior.
"""

import json
import pytest
import subprocess
import sys
from typing import List

from src.models import Chunk
from src.retrieval.base import ScoredChunk, RetrievalResponse
from src.retrieval.in_memory import InMemoryRetriever
from src.retrieval.citations import CitationFormatter
from src.embeddings.mock_provider import DeterministicMockEmbeddingProvider
from src.evaluation.dataset import (
    EvaluationQuestion,
    load_evaluation_questions,
    _extract_target_stem,
    _extract_target_section_keyword,
)
from src.evaluation.metrics import (
    calculate_question_recall,
    calculate_recall_at_k,
    calculate_refusal_matrix,
    check_chunk_matches_target,
    RefusalConfusionMatrix,
)
from src.evaluation.runner import EvaluationRunner, EvaluationConfig


# =============================================================================
# 1. Dataset Loading & Ground Truth Preservation Tests
# =============================================================================

class TestEvaluationDatasetLoading:
    """Verifies that EVALUATION.md is parsed with 100% fidelity without data corruption."""

    def test_load_all_30_questions_in_exact_order(self):
        questions = load_evaluation_questions()
        assert len(questions) == 30

        expected_ids = [f"Q{i:02d}" for i in range(1, 31)]
        actual_ids = [q.id for q in questions]
        assert actual_ids == expected_ids

    def test_answerability_split_25_grounded_5_unanswerable(self):
        questions = load_evaluation_questions()
        answerable = [q for q in questions if q.is_answerable]
        unanswerable = [q for q in questions if not q.is_answerable]

        assert len(answerable) == 25
        assert len(unanswerable) == 5

        # Verify Q01-Q25 are answerable
        for q in answerable:
            assert int(q.id[1:]) <= 25
            assert q.target_document_and_section.lower() != "none"
            assert "must refuse" not in q.expected_behavior.lower()

        # Verify Q26-Q30 are unanswerable
        for q in unanswerable:
            assert int(q.id[1:]) >= 26
            assert q.category == "Unanswerable"
            assert q.target_document_and_section.lower() == "none"
            assert "must refuse" in q.expected_behavior.lower()

    def test_q19_escaped_pipe_preservation(self):
        questions = load_evaluation_questions()
        q19 = next(q for q in questions if q.id == "Q19")

        assert q19.category == "Full Text Search"
        assert "tsvector" in q19.question_text and "tsquery" in q19.question_text
        # Ensure pipe symbol was unescaped cleanly without truncating the cell
        assert "|" in q19.ground_truth_answer
        assert "datatype-textsearch.html" in q19.target_document_and_section
        assert q19.is_answerable is True

    def test_target_stem_extraction(self):
        assert _extract_target_stem("`mvcc.html` / Chapter 13. Concurrency Control") == "mvcc"
        assert _extract_target_stem("`brin.html` / Chapter 70. BRIN Indexes") == "brin"
        assert _extract_target_stem("`routine-vacuuming.html` / Section 25.1") == "routine-vacuuming"
        assert _extract_target_stem("None") is None
        assert _extract_target_stem("") is None

    def test_target_section_keyword_extraction(self):
        assert _extract_target_section_keyword("`mvcc.html` / Chapter 13. Concurrency Control") == "chapter 13. concurrency control"
        assert _extract_target_section_keyword("`brin.html` / Chapter 70. BRIN Indexes") == "chapter 70. brin indexes"
        assert _extract_target_section_keyword("None") is None


# =============================================================================
# 2. Metric Calculation Tests (Hand-Verifiable Fixtures)
# =============================================================================

class TestMetricCalculations:
    """Verifies metric mathematics against simple, hand-crafted fixtures."""

    @pytest.fixture
    def mock_question(self):
        return EvaluationQuestion(
            id="Q01",
            category="Architecture",
            question_text="How does PostgreSQL implement MVCC at the tuple level?",
            ground_truth_answer="Writes new tuple versions with xmin/xmax.",
            target_document_and_section="`mvcc.html` / Chapter 13. Concurrency Control",
            expected_behavior="Grounded answer citing MVCC",
            is_answerable=True,
            target_doc_stem="mvcc",
            target_section_keyword="concurrency control",
        )

    def test_check_chunk_matches_target_stem_match(self, mock_question):
        chunk = Chunk(
            chunk_id="chk_mvcc_01",
            document_id="mvcc-intro",
            strategy="fixed_window",
            chunk_index=0,
            source_filename="mvcc-intro.md",
            page_or_section="Introduction to MVCC",
            content="PostgreSQL uses multi-version concurrency control...",
            content_hash="a" * 64,
            token_count=100,
            source_url="https://www.postgresql.org/docs/16/mvcc-intro.html",
        )
        assert check_chunk_matches_target(chunk, mock_question) is True

    def test_check_chunk_matches_target_pdf_section_match(self, mock_question):
        chunk = Chunk(
            chunk_id="chk_pdf_01",
            document_id="postgresql-16-US",
            strategy="fixed_window",
            chunk_index=45,
            source_filename="postgresql-16-US.pdf",
            page_or_section="Chapter 13. Concurrency Control",
            content="This chapter explains concurrency control and MVCC in PostgreSQL.",
            content_hash="b" * 64,
            token_count=150,
            source_url=None,
        )
        assert check_chunk_matches_target(chunk, mock_question) is True

    def test_check_chunk_matches_target_no_match(self, mock_question):
        chunk = Chunk(
            chunk_id="chk_wal_01",
            document_id="wal-intro",
            strategy="fixed_window",
            chunk_index=0,
            source_filename="wal-intro.md",
            page_or_section="Chapter 30. Write-Ahead Logging",
            content="Write-ahead logging guarantees data durability.",
            content_hash="c" * 64,
            token_count=120,
            source_url=None,
        )
        assert check_chunk_matches_target(chunk, mock_question) is False

    def test_calculate_question_recall_with_hit(self, mock_question):
        matching_chunk = Chunk(
            chunk_id="chk_mvcc_01",
            document_id="mvcc-intro",
            strategy="fixed_window",
            chunk_index=0,
            source_filename="mvcc-intro.md",
            page_or_section="Overview",
            content="MVCC overview",
            content_hash="a" * 64,
            token_count=50,
        )
        scored = [ScoredChunk(chunk=matching_chunk, score=0.88, citation="[Doc: mvcc-intro.md]")]
        recall = calculate_question_recall(mock_question, scored)
        assert recall == 1.0

    def test_calculate_question_recall_unanswerable_is_zero(self):
        unans_q = EvaluationQuestion(
            id="Q26",
            category="Unanswerable",
            question_text="How does MongoDB execute $lookup?",
            ground_truth_answer="N/A",
            target_document_and_section="None",
            expected_behavior="Must refuse",
            is_answerable=False,
            target_doc_stem=None,
            target_section_keyword=None,
        )
        chunk = Chunk(
            chunk_id="chk_01",
            document_id="doc_01",
            strategy="fixed_window",
            chunk_index=0,
            source_filename="doc.md",
            page_or_section="Sec 1",
            content="Some postgres info",
            content_hash="d" * 64,
            token_count=50,
        )
        scored = [ScoredChunk(chunk=chunk, score=0.95, citation="citation")]
        assert calculate_question_recall(unans_q, scored) == 0.0

    def test_calculate_recall_at_k_fractions(self):
        assert calculate_recall_at_k(20, 25) == 0.8
        assert calculate_recall_at_k(0, 25) == 0.0
        assert calculate_recall_at_k(25, 25) == 1.0
        assert calculate_recall_at_k(5, 0) is None

    def test_refusal_matrix_confusion_counts(self):
        records = [
            # 3 True Refusals (Unanswerable & refused)
            {"is_answerable": False, "refusal_triggered": True},
            {"is_answerable": False, "refusal_triggered": True},
            {"is_answerable": False, "refusal_triggered": True},
            # 2 Missed Refusals (Unanswerable & NOT refused)
            {"is_answerable": False, "refusal_triggered": False},
            {"is_answerable": False, "refusal_triggered": False},
            # 4 False Refusals (Answerable & refused)
            {"is_answerable": True, "refusal_triggered": True},
            {"is_answerable": True, "refusal_triggered": True},
            {"is_answerable": True, "refusal_triggered": True},
            {"is_answerable": True, "refusal_triggered": True},
            # 21 True Acceptances (Answerable & NOT refused)
            *[{"is_answerable": True, "refusal_triggered": False} for _ in range(21)],
        ]
        matrix = calculate_refusal_matrix(records)

        assert matrix.true_refusals == 3
        assert matrix.missed_refusals == 2
        assert matrix.false_refusals == 4
        assert matrix.true_acceptances == 21
        assert matrix.total_unanswerable == 5
        assert matrix.total_answerable == 25
        assert matrix.refusal_accuracy == 3 / 5.0  # 0.60
        assert matrix.false_refusal_rate == 4 / 25.0  # 0.16


# =============================================================================
# 3. Matrix Runner & Deterministic Execution Tests
# =============================================================================

class TestEvaluationRunnerMatrix:
    """Verifies that EvaluationRunner executes all 120 matrix evaluations deterministically."""

    @pytest.fixture
    def small_test_environment(self):
        """Creates a minimal in-memory retriever with 4 indexed chunks across both strategies."""
        formatter = CitationFormatter()
        retriever = InMemoryRetriever(dimension=768, citation_formatter=formatter)
        provider = DeterministicMockEmbeddingProvider(dimensions=768)

        # Chunks for Strategy A
        chunk_a1 = Chunk(
            chunk_id="chk_a_mvcc",
            document_id="mvcc-intro",
            strategy="fixed_window",
            chunk_index=0,
            source_filename="mvcc-intro.md",
            page_or_section="Overview",
            content="PostgreSQL MVCC concurrency control implementation",
            content_hash="1" * 64,
            token_count=60,
        )
        chunk_a2 = Chunk(
            chunk_id="chk_a_wal",
            document_id="wal-intro",
            strategy="fixed_window",
            chunk_index=0,
            source_filename="wal-intro.md",
            page_or_section="Reliable Write-Ahead Logging",
            content="Write-ahead log details and checkpointing",
            content_hash="2" * 64,
            token_count=70,
        )

        # Chunks for Strategy B
        chunk_b1 = Chunk(
            chunk_id="chk_b_mvcc",
            document_id="mvcc-intro",
            strategy="structure_aware",
            chunk_index=0,
            source_filename="mvcc-intro.md",
            page_or_section="Overview",
            content="PostgreSQL MVCC concurrency control implementation structure-aware",
            content_hash="3" * 64,
            token_count=80,
        )
        chunk_b2 = Chunk(
            chunk_id="chk_b_wal",
            document_id="wal-intro",
            strategy="structure_aware",
            chunk_index=0,
            source_filename="wal-intro.md",
            page_or_section="Reliable Write-Ahead Logging",
            content="Write-ahead log details and checkpointing structure-aware",
            content_hash="4" * 64,
            token_count=90,
        )

        for c in [chunk_a1, chunk_a2, chunk_b1, chunk_b2]:
            vec = provider.embed_text(c.content)
            retriever.add_chunk(c, vec)

        return retriever, provider

    def test_runner_matrix_generates_exact_120_evaluations(self, small_test_environment):
        retriever, provider = small_test_environment
        questions = load_evaluation_questions()
        runner = EvaluationRunner(
            retriever=retriever,
            embedding_provider=provider,
            questions=questions,
            similarity_threshold=0.65,
        )

        results = runner.run_matrix(
            strategies=["fixed_window", "structure_aware"],
            k_values=[3, 5],
        )

        # 4 configurations
        assert len(results["configurations"]) == 4
        # 120 total individual question-configuration evaluations
        assert len(results["individual_evaluations"]) == 120
        assert results["provenance"]["total_evaluations"] == 120

        # Verify all 4 expected configurations are present in summary
        configs = [(c["strategy"], c["k"]) for c in results["configurations"]]
        assert configs == [
            ("fixed_window", 3),
            ("fixed_window", 5),
            ("structure_aware", 3),
            ("structure_aware", 5),
        ]

    def test_runner_preserves_mock_embedding_provenance(self, small_test_environment):
        retriever, provider = small_test_environment
        runner = EvaluationRunner(retriever=retriever, embedding_provider=provider)
        results = runner.run_matrix()

        prov = results["provenance"]
        assert prov["is_mock_embedding"] is True
        assert prov["embedding_model"] == "mock-deterministic-v1"
        assert prov["embedding_dimensions"] == 768
        assert prov["evaluation_mode"] == "offline_pipeline_mechanics_only"
        assert "UNMEASURED" in prov["semantic_benchmark_status"]

    def test_unmeasured_metrics_explicitly_documented(self, small_test_environment):
        retriever, provider = small_test_environment
        runner = EvaluationRunner(retriever=retriever, embedding_provider=provider)
        results = runner.run_matrix()

        unmeasured = results["unmeasured_metrics_explanation"]
        assert "Answer Correctness (%)" in unmeasured
        assert "Citation Precision (%)" in unmeasured
        assert "Semantic Retrieval Recall@k" in unmeasured

    def test_markdown_report_generation(self, small_test_environment):
        retriever, provider = small_test_environment
        runner = EvaluationRunner(retriever=retriever, embedding_provider=provider)
        results = runner.run_matrix()

        md_report = runner.generate_markdown_report(results)
        assert "# Task 4 — Phase 4C: 30-Question Evaluation & Benchmark Report" in md_report
        assert "OFFLINE PIPELINE MECHANICS AUDIT ONLY" in md_report
        assert "Summary Benchmark Matrix" in md_report
        assert "Refusal Heuristic Confusion Breakdown" in md_report
        assert "Measurement Integrity Policy Adherence" in md_report


# =============================================================================
# 4. CLI Execution & Safety Exit Status Tests
# =============================================================================

class TestEvaluationCLISafety:
    """Verifies that the CLI runner terminates with correct status codes and prevents falsified benchmarks."""

    def test_semantic_mode_blocks_when_genuine_embeddings_missing(self):
        cmd = [sys.executable, "scripts/run_evaluation.py", "--mode", "semantic"]
        result = subprocess.run(cmd, capture_output=True, text=True)

        # Must exit non-zero (code 2)
        assert result.returncode != 0
        assert "MEASUREMENT INTEGRITY AUDIT: BLOCKED" in result.stdout
        assert "Genuine semantic benchmark cannot be executed" in result.stdout
        assert "Never label mock-embedding results as semantic retrieval performance" in result.stdout

    def test_offline_mechanics_mode_succeeds_and_generates_artifacts(self, tmp_path):
        out_dir = str(tmp_path / "test_eval_results")
        cmd = [
            sys.executable,
            "scripts/run_evaluation.py",
            "--mode",
            "offline-mechanics",
            "--output-dir",
            out_dir,
            "--corpus",
            "markdown",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)

        assert result.returncode == 0
        assert "Loaded 30 questions (25 answerable, 5 unanswerable)" in result.stdout
        assert "EVALUATION MATRIX SUMMARY" in result.stdout

        # Verify JSON and Markdown were written
        json_file = tmp_path / "test_eval_results" / "evaluation_results.json"
        md_file = tmp_path / "test_eval_results" / "EVALUATION_REPORT.md"
        assert json_file.exists()
        assert md_file.exists()

        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            assert data["provenance"]["total_evaluations"] == 120
            assert len(data["individual_evaluations"]) == 120
