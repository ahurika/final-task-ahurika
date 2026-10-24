"""
RAG Evaluation Harness and Benchmark Subsystem
Location: src/evaluation/__init__.py
"""

from src.evaluation.dataset import EvaluationQuestion, load_evaluation_questions
from src.evaluation.metrics import (
    calculate_question_recall,
    calculate_recall_at_k,
    calculate_refusal_matrix,
    check_chunk_matches_target,
    RefusalConfusionMatrix,
    ConfigurationMetrics,
)
from src.evaluation.runner import EvaluationRunner, EvaluationConfig

__all__ = [
    "EvaluationQuestion",
    "load_evaluation_questions",
    "calculate_question_recall",
    "calculate_recall_at_k",
    "calculate_refusal_matrix",
    "check_chunk_matches_target",
    "RefusalConfusionMatrix",
    "ConfigurationMetrics",
    "EvaluationRunner",
    "EvaluationConfig",
]
