"""
Evaluation Metrics Calculations
Location: src/evaluation/metrics.py

Implements mathematically exact, defensible metric calculations defined in
EVALUATION.md (REQ-15, REQ-16):
- Retrieval Recall@k (source-level and section-level matching against ground truth)
- Citation Precision (verifiable chunk provenance; answer-level marked unmeasured)
- Refusal Accuracy and Confusion Matrix (True Refusals, False Refusals, Missed Refusals)
- Query Latency and Cost
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any, Set
import re

from src.retrieval.base import ScoredChunk
from src.evaluation.dataset import EvaluationQuestion


@dataclass(frozen=True)
class RefusalConfusionMatrix:
    """Confusion counts for the similarity-threshold refusal heuristic."""
    true_refusals: int  # Unanswerable question correctly refused (Target = Refuse, System = Refuse)
    missed_refusals: int  # Unanswerable question falsely accepted (Target = Refuse, System = Accept)
    false_refusals: int  # Answerable question falsely refused (Target = Answer, System = Refuse)
    true_acceptances: int  # Answerable question correctly accepted (Target = Answer, System = Accept)

    @property
    def total_unanswerable(self) -> int:
        return self.true_refusals + self.missed_refusals

    @property
    def total_answerable(self) -> int:
        return self.false_refusals + self.true_acceptances

    @property
    def refusal_accuracy(self) -> Optional[float]:
        """Accuracy on unanswerable queries: TR / (TR + MR)."""
        if self.total_unanswerable == 0:
            return None
        return self.true_refusals / float(self.total_unanswerable)

    @property
    def false_refusal_rate(self) -> Optional[float]:
        """False refusal rate on answerable queries: FR / (FR + TA)."""
        if self.total_answerable == 0:
            return None
        return self.false_refusals / float(self.total_answerable)


@dataclass(frozen=True)
class ConfigurationMetrics:
    """Summary metrics for one evaluation configuration (strategy, k)."""
    strategy: str
    k: int
    total_questions: int
    answerable_questions: int
    unanswerable_questions: int
    recalled_count: int
    retrieval_recall_at_k: Optional[float]
    refusal_metrics: RefusalConfusionMatrix
    avg_latency_ms: float
    avg_cost_usd: float
    total_retrieved_chunks: int
    valid_provenance_chunks: int
    provenance_integrity_rate: float
    answer_correctness_status: str = "Not Measurable (Pending Generation)"
    citation_precision_status: str = "Not Measurable (Pending Generation)"


def normalize_target_token(token: str) -> str:
    """Normalizes document names and section titles for fuzzy containment matching."""
    cleaned = re.sub(r"[`*_#\(\)\[\]:,.]", " ", token.lower())
    # Strip common file extensions
    cleaned = re.sub(r"\b(html|md|pdf)\b", " ", cleaned)
    return " ".join(cleaned.split())


def check_chunk_matches_target(chunk: Any, question: EvaluationQuestion) -> bool:
    """
    Determines whether a retrieved chunk matches the question's expected target
    document and section.

    Matching rules:
    1. If the question is unanswerable (target is None), returns False.
    2. Check source filename / source URL against target document stem.
       (e.g., stem 'mvcc' matches 'mvcc-intro.md' or 'mvcc.html').
    3. Check page_or_section against target section keywords.
       (e.g., 'Chapter 70. BRIN Indexes' matches 'BRIN Indexes').
    """
    if not question.is_answerable or not question.target_doc_stem:
        return False

    chunk_fn = (getattr(chunk, "source_filename", None) or "").lower()
    chunk_url = (getattr(chunk, "source_url", None) or "").lower()
    chunk_section = (getattr(chunk, "page_or_section", None) or "").lower()
    chunk_content = (getattr(chunk, "content", None) or "").lower()

    target_stem = question.target_doc_stem.lower()

    # Rule A: Direct stem match in filename or URL
    # e.g., target_stem 'mvcc' matches 'mvcc-intro.md' or 'mvcc.html'
    stem_matched = (
        target_stem in chunk_fn
        or (target_stem.replace("-", "") in chunk_fn.replace("-", ""))
        or target_stem in chunk_url
    )

    # Rule B: Section keyword match in page_or_section or beginning of content
    section_matched = False
    if question.target_section_keyword:
        kw = question.target_section_keyword.lower()
        norm_kw = normalize_target_token(kw)
        norm_sec = normalize_target_token(chunk_section)
        # Check if significant keywords from the target section appear in chunk section
        kw_tokens = [t for t in norm_kw.split() if len(t) > 3 and t not in {"chapter", "section"}]
        if kw_tokens:
            matches_count = sum(1 for t in kw_tokens if t in norm_sec or t in chunk_content[:200])
            if matches_count >= max(1, len(kw_tokens) // 2):
                section_matched = True

    # A chunk is a hit if either the document stem matches or (for PDF) the section keyword matches
    return stem_matched or section_matched


def calculate_question_recall(
    question: EvaluationQuestion,
    scored_chunks: List[ScoredChunk],
) -> float:
    """
    Computes Recall@k for a single question.
    Returns 1.0 if at least one retrieved chunk satisfies the ground-truth target, else 0.0.
    For unanswerable questions, returns 0.0 (recall is undefined/zero for out-of-domain).
    """
    if not question.is_answerable:
        return 0.0

    for sc in scored_chunks:
        if check_chunk_matches_target(sc.chunk, question):
            return 1.0

    return 0.0


def calculate_recall_at_k(recalled_count: int, total_answerable: int) -> Optional[float]:
    """Computes overall Recall@k = recalled_count / total_answerable."""
    if total_answerable <= 0:
        return None
    return recalled_count / float(total_answerable)


def calculate_refusal_matrix(
    evaluation_records: List[Dict[str, Any]],
) -> RefusalConfusionMatrix:
    """
    Builds the refusal confusion matrix from question evaluation records.
    Each record must have 'is_answerable': bool and 'refusal_triggered': bool.
    """
    tr = 0
    mr = 0
    fr = 0
    ta = 0

    for rec in evaluation_records:
        ans = rec["is_answerable"]
        ref = rec["refusal_triggered"]

        if not ans:
            # Expected to refuse
            if ref:
                tr += 1
            else:
                mr += 1
        else:
            # Expected to answer
            if ref:
                fr += 1
            else:
                ta += 1

    return RefusalConfusionMatrix(
        true_refusals=tr,
        missed_refusals=mr,
        false_refusals=fr,
        true_acceptances=ta,
    )
