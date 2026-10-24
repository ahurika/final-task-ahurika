"""
Corpus Overlap Audit Module
Location: src/audit_overlap.py

Performs reproducible, offline overlap analysis between the PostgreSQL 16 PDF (3,220 pages)
and the 14 official Markdown documents.

Distinguishes between:
1. Exact cryptographic hash collisions (SHA-256) between chunks or pages.
2. Approximate/partial text overlap using normalized word shingles (8-grams).
3. Structural mapping of Markdown files to PDF physical page ranges.
"""

import hashlib
import json
import os
import re
from typing import Dict, Any, List, Set, Tuple


def normalize_words(text: str) -> List[str]:
    """Extracts lower-cased alphanumeric words for robust text matching."""
    return re.findall(r"\b[a-zA-Z0-9_]{3,}\b", text.lower())


def create_shingles(words: List[str], k: int = 8) -> Set[str]:
    """Generates k-word shingles (N-grams) from a sequence of words."""
    if len(words) < k:
        return set([" ".join(words)]) if words else set()
    return set(" ".join(words[i : i + k]) for i in range(len(words) - k + 1))


def audit_corpus_overlap(
    pdf_pages: List[Tuple[int, str]],
    markdown_docs: List[Dict[str, Any]],
    shingle_size: int = 8,
) -> Dict[str, Any]:
    """
    Analyzes exact and partial text overlap between Markdown documents and PDF pages.

    Args:
        pdf_pages: List of (page_number, page_text) tuples.
        markdown_docs: List of dicts with keys 'document_id', 'filename', 'title', 'content'.
        shingle_size: Number of words per shingle for partial match detection (default 8).

    Returns:
        Structured audit report dictionary.
    """
    # 1. Precompute PDF page shingles and word counts
    pdf_page_shingles: List[Tuple[int, Set[str], int]] = []
    pdf_page_hashes: Dict[str, List[int]] = {}

    for page_num, text in pdf_pages:
        clean_text = text.strip()
        p_hash = hashlib.sha256(clean_text.encode("utf-8")).hexdigest()
        pdf_page_hashes.setdefault(p_hash, []).append(page_num)

        words = normalize_words(clean_text)
        shingles = create_shingles(words, k=shingle_size)
        if shingles:
            pdf_page_shingles.append((page_num, shingles, len(words)))

    # 2. Check exact content hash collisions
    exact_hash_collisions = []
    markdown_page_matches = []

    for md in markdown_docs:
        md_id = md["document_id"]
        md_filename = md["filename"]
        md_text = md["content"].strip()
        md_hash = hashlib.sha256(md_text.encode("utf-8")).hexdigest()

        # Check if entire markdown doc matches any PDF page
        if md_hash in pdf_page_hashes:
            exact_hash_collisions.append({
                "markdown_id": md_id,
                "markdown_filename": md_filename,
                "pdf_pages": pdf_page_hashes[md_hash],
                "sha256": md_hash,
            })

        # Approximate matching via shingles
        md_words = normalize_words(md_text)
        md_shingles = create_shingles(md_words, k=shingle_size)
        total_md_shingles = len(md_shingles)

        # Match against PDF pages
        page_scores: List[Tuple[int, int, float]] = []  # (page_num, shared_shingles, jaccard/containment)
        all_shared_shingles: Set[str] = set()

        for page_num, p_shingles, p_word_count in pdf_page_shingles:
            shared = md_shingles.intersection(p_shingles)
            if shared:
                containment = len(shared) / max(1, total_md_shingles)
                all_shared_shingles.update(shared)
                # Keep if significant overlap (>= 3 shared shingles or >= 5% containment)
                if len(shared) >= 3 or containment >= 0.05:
                    page_scores.append((page_num, len(shared), round(containment * 100, 2)))

        page_scores.sort(key=lambda x: x[1], reverse=True)

        overall_containment = round((len(all_shared_shingles) / max(1, total_md_shingles)) * 100, 2)
        top_pages = sorted([p[0] for p in page_scores[:5]])
        page_range_str = (
            f"Pages {min(top_pages)}–{max(top_pages)}"
            if top_pages and min(top_pages) != max(top_pages)
            else (f"Page {top_pages[0]}" if top_pages else "None identified")
        )

        markdown_page_matches.append({
            "document_id": md_id,
            "filename": md_filename,
            "title": md.get("title", md_id),
            "word_count": len(md_words),
            "total_shingles": total_md_shingles,
            "matching_pdf_pages": [
                {"page_number": p[0], "shared_shingles": p[1], "containment_pct": p[2]}
                for p in page_scores[:5]
            ],
            "likely_page_range": page_range_str,
            "overall_shingle_containment_pct": overall_containment,
        })

    return {
        "shingle_size_words": shingle_size,
        "exact_hash_collisions_count": len(exact_hash_collisions),
        "exact_hash_collisions": exact_hash_collisions,
        "markdown_documents_audited": len(markdown_docs),
        "total_pdf_pages_audited": len(pdf_pages),
        "markdown_mappings": markdown_page_matches,
    }
