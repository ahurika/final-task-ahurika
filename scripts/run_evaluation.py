"""
Offline 30-Question Evaluation Harness and Benchmark Runner (CLI)
Location: scripts/run_evaluation.py

Executes the 30-question evaluation matrix across:
- Strategy A (fixed-window 500/50)
- Strategy B (structure-aware headings)
- Retrieval depth k in {3, 5}
Total evaluations: 120 question-configuration runs.

Usage:
  python scripts/run_evaluation.py --mode offline-mechanics
  python scripts/run_evaluation.py --mode semantic
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from src.models import Chunk, ParsedDocument
from src.tokenizers import DeterministicTestTokenizer
from src.chunkers.fixed_size import FixedSizeChunker
from src.chunkers.structure_aware import StructureAwareChunker
from src.parsers.markdown_parser import MarkdownParser
from src.parsers.pdf_parser import PDFParser
from src.embeddings.mock_provider import DeterministicMockEmbeddingProvider
from src.retrieval.in_memory import InMemoryRetriever
from src.retrieval.base import DEFAULT_SIMILARITY_THRESHOLD
from src.evaluation.dataset import load_evaluation_questions
from src.evaluation.runner import EvaluationRunner


def load_corpus_documents(include_pdf: bool = False) -> List[ParsedDocument]:
    """
    Loads and parses corpus documents offline without network access.
    By default parses the 14 Markdown reference documents.
    If include_pdf is True, uses cached extraction of postgresql-16-US.pdf.
    """
    md_manifest_path = os.path.join(PROJECT_ROOT, "data", "markdown-corpus-manifest.json")
    with open(md_manifest_path, "r", encoding="utf-8") as f:
        md_manifest = json.load(f)

    md_dir = os.path.join(PROJECT_ROOT, "data", "raw", "markdown")
    md_parser = MarkdownParser()
    parsed_docs: List[ParsedDocument] = []

    for doc_entry in md_manifest["documents"]:
        full_path = os.path.join(md_dir, doc_entry["filename"])
        if os.path.exists(full_path):
            result = md_parser.parse(full_path, manifest_entry=doc_entry)
            parsed_docs.append(result.document)

    if include_pdf:
        pdf_path = os.path.join(PROJECT_ROOT, "data", "raw", "postgresql-16-US.pdf")
        if os.path.exists(pdf_path):
            pdf_manifest_entry = {
                "document_id": "postgresql-16-US",
                "title": "PostgreSQL 16 Documentation (US Letter)",
                "topic": "Official PostgreSQL Manual",
                "source_url": "https://www.postgresql.org/files/documentation/pdf/16/postgresql-16-US.pdf",
                "retrieval_date": "2026-10-23",
                "license": "PostgreSQL License",
            }
            pdf_parser = PDFParser()
            pdf_result = pdf_parser.parse(pdf_path, manifest_entry=pdf_manifest_entry, use_cache=True)
            parsed_docs.append(pdf_result.document)

    return parsed_docs


def build_chunk_indices(
    documents: List[ParsedDocument],
    embedding_provider: DeterministicMockEmbeddingProvider,
) -> InMemoryRetriever:
    """
    Chunks all documents across Strategy A and Strategy B, generates unit vector
    embeddings, and indexes them into an InMemoryRetriever.
    """
    tokenizer = DeterministicTestTokenizer()
    chunker_a = FixedSizeChunker(target_tokens=500, overlap_tokens=50, tokenizer=tokenizer)
    chunker_b = StructureAwareChunker(max_tokens=800, min_tokens=50, overlap_tokens=50, tokenizer=tokenizer)

    retriever = InMemoryRetriever(dimension=embedding_provider.dimensions)

    chunks_strategy_a: List[Chunk] = []
    chunks_strategy_b: List[Chunk] = []

    for doc in documents:
        chunks_strategy_a.extend(chunker_a.chunk(doc))
        chunks_strategy_b.extend(chunker_b.chunk(doc))

    # Index Strategy A chunks
    for chunk in chunks_strategy_a:
        vec = embedding_provider.embed_text(chunk.content)
        retriever.add_chunk(chunk, vec)

    # Index Strategy B chunks
    for chunk in chunks_strategy_b:
        vec = embedding_provider.embed_text(chunk.content)
        retriever.add_chunk(chunk, vec)

    return retriever


def check_genuine_embeddings_available() -> Tuple[bool, str]:
    """
    Audits whether genuine reusable offline embeddings from gemini-embedding-001
    exist on disk and have verified provenance.
    """
    # Currently, no genuine vector files exist in data/cache or data/
    return False, (
        "No verified genuine vector embeddings from gemini-embedding-001 exist on disk. "
        "Live API calls and batch embedding ingestion are pending authorization."
    )


def main():
    parser = argparse.ArgumentParser(
        description="Offline 30-Question Evaluation Harness and Benchmark Runner",
    )
    parser.add_argument(
        "--mode",
        choices=["offline-mechanics", "semantic"],
        default="offline-mechanics",
        help="Evaluation execution mode. 'offline-mechanics' runs reproducible pipeline wiring audit. 'semantic' runs real semantic benchmark (requires genuine embeddings).",
    )
    parser.add_argument(
        "--output-dir",
        default="evaluation_results",
        help="Directory to store evaluation JSON and Markdown report.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_SIMILARITY_THRESHOLD,
        help="Similarity refusal threshold (default: 0.65).",
    )
    parser.add_argument(
        "--corpus",
        choices=["markdown", "full"],
        default="markdown",
        help="Corpus source: 'markdown' (14 Markdown docs, fast ~1s) or 'full' (Markdown + PDF cache).",
    )

    args = parser.parse_args()

    print("=" * 80)
    print("Task 4 — Phase 4C: 30-Question Evaluation Harness & Benchmark Runner")
    print("=" * 80)

    # 1. Enforce Measurement Integrity Check
    if args.mode == "semantic":
        available, reason = check_genuine_embeddings_available()
        if not available:
            print("\n[MEASUREMENT INTEGRITY AUDIT: BLOCKED]")
            print(f"Error: Genuine semantic benchmark cannot be executed: {reason}")
            print("Per AGENTS.md and PRD.md measurement integrity rules:")
            print("  - Never label mock-embedding results as semantic retrieval performance.")
            print("  - Do not use arbitrary vectors to claim benchmark scores.")
            print("Next step required: Complete Phase 3E.6 live canary and Phase 3E.8 batch ingestion.\n")
            sys.exit(2)

    # 2. Load and validate the 30-question dataset
    print("\n[1/4] Loading and validating 30-question evaluation dataset from EVALUATION.md...")
    questions = load_evaluation_questions()
    answerable_count = sum(1 for q in questions if q.is_answerable)
    unanswerable_count = sum(1 for q in questions if not q.is_answerable)
    print(f"  Loaded {len(questions)} questions ({answerable_count} answerable, {unanswerable_count} unanswerable).")
    print("  Dataset integrity verified (Q01-Q30 in sequence).")

    # 3. Load corpus documents and construct indexed retriever
    include_pdf = (args.corpus == "full")
    print(f"\n[2/4] Parsing offline corpus documents (mode: {'full (MD + PDF)' if include_pdf else 'markdown (14 docs)'})...")
    t0_parse = time.perf_counter()
    docs = load_corpus_documents(include_pdf=include_pdf)
    t_parse = time.perf_counter() - t0_parse
    print(f"  Parsed {len(docs)} documents in {t_parse:.2f}s.")

    print("\n[3/4] Chunking corpus and generating deterministic mock embeddings (dim=768)...")
    t0_index = time.perf_counter()
    provider = DeterministicMockEmbeddingProvider(dimensions=768)
    retriever = build_chunk_indices(docs, provider)
    t_index = time.perf_counter() - t0_index
    print(f"  Indexed {len(retriever)} total chunks in {t_index:.2f}s.")

    # 4. Run Matrix Evaluation
    print("\n[4/4] Executing 120-evaluation matrix (2 strategies × 2 k values × 30 questions)...")
    runner = EvaluationRunner(
        retriever=retriever,
        embedding_provider=provider,
        questions=questions,
        similarity_threshold=args.threshold,
    )

    results = runner.run_matrix(
        strategies=["fixed_window", "structure_aware"],
        k_values=[3, 5],
    )

    # 5. Save Outputs
    output_dir = os.path.join(PROJECT_ROOT, args.output_dir) if not os.path.isabs(args.output_dir) else args.output_dir
    os.makedirs(output_dir, exist_ok=True)

    json_path = os.path.join(output_dir, "evaluation_results.json")
    report_path = os.path.join(output_dir, "EVALUATION_REPORT.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    markdown_report = runner.generate_markdown_report(results)
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(markdown_report)

    print("\n" + "=" * 80)
    print("EVALUATION MATRIX SUMMARY")
    print("=" * 80)
    print(f"{'Config':<10} | {'Strategy':<18} | {'k':<3} | {'Recall@k':<18} | {'Refusal Acc':<12} | {'Avg Latency':<12}")
    print("-" * 80)

    for i, cfg in enumerate(results["configurations"], 1):
        m = cfg["metrics"]
        strat = "Strategy A (Fixed)" if cfg["strategy"] == "fixed_window" else "Strategy B (Struct)"
        recall_str = f"{m['retrieval_recall_at_k'] * 100:.1f}% ({m['recalled_count']}/{m['answerable_questions']})"
        refusal_str = f"{m['refusal_accuracy'] * 100:.1f}% ({m['true_refusals']}/{m['unanswerable_questions']})"
        lat_str = f"{m['avg_latency_ms']:.2f} ms"
        print(f"Config {i:<3} | {strat:<18} | {cfg['k']:<3} | {recall_str:<18} | {refusal_str:<12} | {lat_str:<12}")

    print("=" * 80)
    print(f"Results JSON:   {json_path}")
    print(f"Report MD:      {report_path}")
    print("Measurement Integrity Note: Results above verify offline pipeline mechanics only.")
    print("Zero live API costs incurred. Generation and semantic claims remain unmeasured.")
    print("=" * 80)


if __name__ == "__main__":
    main()
