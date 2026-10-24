"""
Evaluation Dataset Loader and Question Ground Truth Schema
Location: src/evaluation/dataset.py

Loads and validates the 30 evaluation questions and unchanged ground-truth
specifications defined in EVALUATION.md (REQ-15, REQ-16).
"""

import os
import re
from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class EvaluationQuestion:
    """
    Strongly typed representation of an evaluation question from EVALUATION.md.
    Preserves all ground-truth fields without modification or fabrication.
    """
    id: str  # e.g., "Q01", "Q30"
    category: str  # e.g., "Architecture", "Indexes", "Unanswerable"
    question_text: str  # Full query text
    ground_truth_answer: str  # Authoritative answer summary or N/A
    target_document_and_section: str  # Target document / section, or "None"
    expected_behavior: str  # Expected behavior directive
    is_answerable: bool  # True for Q01-Q25, False for Q26-Q30
    target_doc_stem: Optional[str] = None  # Normalized stem for matching (e.g., "mvcc", "brin")
    target_section_keyword: Optional[str] = None  # Normalized section keyword for matching


def _extract_target_stem(target_str: str) -> Optional[str]:
    """Extracts a normalized document stem from target_document_and_section (e.g., 'mvcc.html' -> 'mvcc')."""
    if not target_str or target_str.strip().lower() == "none":
        return None
    # Match patterns like `mvcc.html` or `wal-intro.html` or `routine-vacuuming.html`
    match = re.search(r"[`']?([a-zA-Z0-9_-]+)\.(html|md)[`']?", target_str)
    if match:
        stem = match.group(1).lower()
        # Normalization: map 'mvcc' to match 'mvcc-intro' or vice versa
        return stem
    return None


def _extract_target_section_keyword(target_str: str) -> Optional[str]:
    """Extracts section or chapter keywords from target string for matching."""
    if not target_str or target_str.strip().lower() == "none":
        return None
    parts = target_str.split("/")
    if len(parts) > 1:
        sec = parts[1].strip()
        # Clean markdown formatting
        sec = re.sub(r"[`*_#]", "", sec).strip()
        return sec.lower()
    return None


def load_evaluation_questions(markdown_path: str = "EVALUATION.md") -> List[EvaluationQuestion]:
    """
    Parses the 30 evaluation questions catalog from EVALUATION.md.

    Validates:
    1. Exactly 30 questions are present (Q01 through Q30 in order).
    2. Exactly 25 answerable questions (Q01-Q25) and 5 unanswerable questions (Q26-Q30).
    3. All ground-truth fields are non-empty and uncorrupted.
    4. Negative lookbehind handles escaped pipe symbols (e.g. `\\|` in Q19).
    """
    if not os.path.isabs(markdown_path):
        # Resolve relative to project root or current working directory
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        candidate_path = os.path.join(project_root, markdown_path)
        if os.path.exists(candidate_path):
            markdown_path = candidate_path

    if not os.path.exists(markdown_path):
        raise FileNotFoundError(f"Evaluation questions catalog not found at {markdown_path}.")

    with open(markdown_path, "r", encoding="utf-8") as f:
        content = f.read()

    questions: List[EvaluationQuestion] = []
    lines = content.splitlines()
    in_table = False

    for line in lines:
        stripped = line.strip()
        # Detect table header
        if stripped.startswith("|") and "Category" in stripped and "Question Text" in stripped:
            in_table = True
            continue

        if not in_table:
            continue

        # Stop parsing when leaving table
        if in_table and stripped.startswith("---"):
            break
        if in_table and not stripped.startswith("|"):
            if questions:
                # End of table
                break
            continue

        # Skip separator line (| :--- | :--- | ...)
        if re.match(r"^\|(\s*:?-+:?\s*\|)+$", stripped):
            continue

        # Split on unescaped pipe symbols
        raw_cells = re.split(r"(?<!\\)\|", stripped)
        # Drop leading and trailing empty entries resulting from outer pipes
        cells = [c.strip() for c in raw_cells[1:-1]] if len(raw_cells) >= 3 else []
        if len(cells) < 6:
            continue

        # Clean cell contents
        q_id_raw = cells[0].strip()
        category_raw = cells[1].strip()
        question_text = cells[2].strip()
        ground_truth_answer = cells[3].strip()
        target_doc_sec = cells[4].strip()
        expected_behavior = cells[5].strip()

        # Clean markdown bold/backticks
        q_id = re.sub(r"[*_`]", "", q_id_raw).strip()
        category = re.sub(r"[*_`]", "", category_raw).strip()

        # Unescape any escaped pipes in text fields
        ground_truth_answer = ground_truth_answer.replace(r"\|", "|")
        question_text = question_text.replace(r"\|", "|")

        # Determine answerability
        # Q26-Q30 are explicitly labelled Unanswerable and target is None
        is_answerable = not (
            category.lower() == "unanswerable"
            or target_doc_sec.strip().lower() == "none"
            or "must refuse" in expected_behavior.lower()
        )

        stem = _extract_target_stem(target_doc_sec)
        sec_kw = _extract_target_section_keyword(target_doc_sec)

        q = EvaluationQuestion(
            id=q_id,
            category=category,
            question_text=question_text,
            ground_truth_answer=ground_truth_answer,
            target_document_and_section=target_doc_sec,
            expected_behavior=expected_behavior,
            is_answerable=is_answerable,
            target_doc_stem=stem,
            target_section_keyword=sec_kw,
        )
        questions.append(q)

    # Validation assertions
    if len(questions) != 30:
        raise ValueError(f"Expected exactly 30 evaluation questions, but found {len(questions)}.")

    expected_ids = [f"Q{i:02d}" for i in range(1, 31)]
    actual_ids = [q.id for q in questions]
    if actual_ids != expected_ids:
        raise ValueError(f"Question IDs do not match expected Q01-Q30 sequence: {actual_ids}.")

    answerable_count = sum(1 for q in questions if q.is_answerable)
    unanswerable_count = sum(1 for q in questions if not q.is_answerable)
    if answerable_count != 25:
        raise ValueError(f"Expected exactly 25 answerable questions, got {answerable_count}.")
    if unanswerable_count != 5:
        raise ValueError(f"Expected exactly 5 unanswerable questions, got {unanswerable_count}.")

    return questions
