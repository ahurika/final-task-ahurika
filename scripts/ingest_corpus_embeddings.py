"""
Resumable Corpus Ingestion & Persistent Embedding CLI
Location: scripts/ingest_corpus_embeddings.py

Purpose:
Orchestrates persistent, incremental corpus embedding with:
- Disk-backed SQLite cache (DiskEmbeddingCache)
- Checkpoint persistence and resume after interruption
- Dry-run mode (guaranteed zero network calls)
- Bounded batches and chunk limits (--limit)
- Strategy separation (fixed_window vs structure_aware)
- Safe signal handling (graceful Ctrl+C exit)
- Strict exclusion of security fixture (security_test_payload.md)
- Secret sanitization in all logs and checkpoints
"""

import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import math
import os
import signal
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from scripts.canary_gemini import _sanitize_output, load_env_file
from src.chunkers.fixed_size import FixedSizeChunker
from src.chunkers.structure_aware import StructureAwareChunker
from src.embeddings.base import (
    BaseEmbeddingProvider,
    EmbeddingError,
    validate_vector,
)
from src.embeddings.cache import BaseEmbeddingCache, DiskEmbeddingCache
from src.embeddings.config import (
    MODEL_GEMINI_EMBEDDING_001,
    PROVISIONAL_DEFAULT_DIMENSION,
)
from src.embeddings.gemini_provider import GeminiEmbeddingProvider, TASK_TYPE_DOCUMENT
from src.embeddings.mock_provider import DeterministicMockEmbeddingProvider
from src.embeddings.retry import RetryPolicy
from src.models import Chunk, ParsedDocument
from src.parsers.markdown_parser import MarkdownParser
from src.parsers.pdf_parser import PDFParser
from src.tokenizers import DeterministicTestTokenizer

DEFAULT_CACHE_PATH = os.path.join(PROJECT_ROOT, "data", "cache", "embedding_cache.sqlite")
DEFAULT_CHECKPOINT_PATH = os.path.join(PROJECT_ROOT, "data", "ingestion-checkpoint.json")
COST_PER_MILLION_TOKENS = 0.15


class IngestionInterruptedException(Exception):
    """Raised when an external interrupt signal (SIGINT/SIGTERM) is received."""
    pass


class IngestionRunner:
    """
    Manages the lifecycle of persistent, resumable corpus embedding runs.
    """

    def __init__(
        self,
        provider: BaseEmbeddingProvider,
        cache: BaseEmbeddingCache,
        dry_run: bool = False,
        rate_limit_rps: float = 10.0,
        batch_size: int = 50,
        checkpoint_path: str = DEFAULT_CHECKPOINT_PATH,
    ):
        self.provider = provider
        self.cache = cache
        self.dry_run = dry_run
        self.rate_limit_rps = max(0.1, rate_limit_rps)
        self.batch_size = max(1, batch_size)
        self.checkpoint_path = checkpoint_path
        self._interrupted = False

    def trigger_interruption(self, signum=None, frame=None):
        """Signal handler for graceful shutdown."""
        print("\n[INTERRUPT] Received shutdown signal. Gracefully finishing in-flight work...")
        self._interrupted = True

    def run(
        self,
        chunks: List[Chunk],
        limit: Optional[int] = None,
        strategy_label: str = "all",
    ) -> Dict[str, Any]:
        """
        Executes ingestion over the provided chunk sequence.

        Args:
            chunks: List of Chunk objects to embed.
            limit: Maximum number of new chunks to embed (None for unlimited).
            strategy_label: Identifier for this run ('fixed_window', 'structure_aware', or 'all').

        Returns:
            Dictionary containing run statistics and checkpoint status.
        """
        start_time = time.time()
        min_interval_s = 1.0 / self.rate_limit_rps

        total_chunks = len(chunks)
        already_cached_count = 0
        newly_embedded_count = 0
        failed_count = 0
        total_tokens_processed = 0

        pending_batch: List[Tuple[str, str, int, List[float], str, Optional[str], Optional[str]]] = []
        last_request_time = 0.0
        error_details = []
        strategy_counts: Dict[str, int] = {}
        per_strategy_cap = (limit // 2) if (limit is not None and strategy_label == "all") else None

        print("=" * 75)
        print("  CORPUS EMBEDDING INGESTION PIPELINE")
        print("=" * 75)
        print(f"  Execution Mode:     {'DRY RUN (Zero Network Calls Guaranteed)' if self.dry_run else 'LIVE EXECUTION'}")
        print(f"  Target Strategy:    {strategy_label}")
        print(f"  Total Chunks Input: {total_chunks}")
        print(f"  Batch Limit:        {limit if limit else 'Unlimited'}")
        print(f"  Rate Limit:         {self.rate_limit_rps} req/sec")
        print(f"  Cache Store:        {getattr(self.cache, 'db_path', type(self.cache).__name__)}")
        print("=" * 75)

        for idx, chunk in enumerate(chunks):
            if self._interrupted:
                print(f"[INTERRUPTED] Halting at chunk {idx}/{total_chunks}. Saving progress...")
                break

            # Respect --limit on NEW embeddings
            if limit is not None and newly_embedded_count >= limit:
                print(f"[LIMIT REACHED] Hit requested limit of {limit} new embeddings. Stopping.")
                break

            chunk_strategy = getattr(chunk, "strategy", "all")
            # Enforce 5-chunk cap per strategy when limit=10 across both strategies
            if per_strategy_cap is not None and strategy_counts.get(chunk_strategy, 0) >= per_strategy_cap:
                continue

            content_hash = chunk.content_hash
            model_name = self.provider.model_name
            dimensions = self.provider.dimensions
            task_type = TASK_TYPE_DOCUMENT
            title = chunk.metadata.get("title")

            # 1. Check persistent cache
            if self.cache.has(
                content_hash=content_hash,
                model_name=model_name,
                dimensions=dimensions,
                task_type=task_type,
                title=title,
                allow_legacy=False,
            ):
                already_cached_count += 1
                total_tokens_processed += chunk.token_count
                continue

            # 2. Dry run handling: estimate without calling provider
            if self.dry_run:
                newly_embedded_count += 1
                strategy_counts[chunk_strategy] = strategy_counts.get(chunk_strategy, 0) + 1
                total_tokens_processed += chunk.token_count
                continue


            # 3. Live or mock embedding generation
            # Rate pacing
            elapsed_since_last = time.time() - last_request_time
            if elapsed_since_last < min_interval_s:
                time.sleep(min_interval_s - elapsed_since_last)

            try:
                last_request_time = time.time()
                if hasattr(self.provider, "embed_document"):
                    vector = self.provider.embed_document(
                        text=chunk.content,
                        title=title,
                    )
                else:
                    vector = self.provider.embed_text(chunk.content)

                validate_vector(vector, expected_dim=dimensions)
                if sum(v * v for v in vector) <= 0.0:
                    raise EmbeddingError("Generated vector has zero norm.")

                pending_batch.append((
                    content_hash,
                    model_name,
                    dimensions,
                    vector,
                    task_type,
                    title,
                    None,
                ))
                newly_embedded_count += 1
                strategy_counts[chunk_strategy] = strategy_counts.get(chunk_strategy, 0) + 1
                total_tokens_processed += chunk.token_count


            except Exception as exc:
                failed_count += 1
                sanitized_msg = _sanitize_output(str(exc))
                error_details.append({
                    "chunk_id": chunk.chunk_id,
                    "source_filename": chunk.source_filename,
                    "error": sanitized_msg,
                })
                print(f"\n[ERROR] Failed to embed chunk {chunk.chunk_id}: {sanitized_msg}")
                # Incomplete records are NOT cached. Stop on unrecoverable failure.
                break

            # Flush batch to disk cache
            if len(pending_batch) >= self.batch_size:
                try:
                    if hasattr(self.cache, "set_batch"):
                        self.cache.set_batch(pending_batch)
                    else:
                        for item in pending_batch:
                            self.cache.set(
                                content_hash=item[0],
                                model_name=item[1],
                                dimensions=item[2],
                                vector=item[3],
                                task_type=item[4],
                                title=item[5],
                            )
                    pending_batch.clear()
                except Exception as exc:
                    failed_count += len(pending_batch)
                    sanitized_msg = _sanitize_output(str(exc))
                    error_details.append({"error": f"Cache commit failure: {sanitized_msg}"})
                    print(f"\n[ERROR] Failed to commit cache batch: {sanitized_msg}")
                    pending_batch.clear()
                    break

            # Progress heartbeat every 50 chunks
            if (idx + 1) % 50 == 0 or (idx + 1) == total_chunks:
                pct = ((idx + 1) / total_chunks) * 100
                print(
                    f"  [{idx + 1:>5}/{total_chunks}] ({pct:>5.1f}%) | "
                    f"Cached: {already_cached_count} | Embedded: {newly_embedded_count} | Failed: {failed_count}"
                )

        # Flush remaining pending batch
        if pending_batch:
            try:
                if hasattr(self.cache, "set_batch"):
                    self.cache.set_batch(pending_batch)
                else:
                    for item in pending_batch:
                        self.cache.set(
                            content_hash=item[0],
                            model_name=item[1],
                            dimensions=item[2],
                            vector=item[3],
                            task_type=item[4],
                            title=item[5],
                        )
                pending_batch.clear()
            except Exception as exc:
                failed_count += len(pending_batch)
                sanitized_msg = _sanitize_output(str(exc))
                error_details.append({"error": f"Final cache commit failure: {sanitized_msg}"})
                print(f"\n[ERROR] Failed to commit final cache batch: {sanitized_msg}")
                pending_batch.clear()


        total_runtime_s = round(time.time() - start_time, 2)
        remaining_chunks = total_chunks - (already_cached_count + newly_embedded_count)
        estimated_cost_usd = round((total_tokens_processed / 1_000_000.0) * COST_PER_MILLION_TOKENS, 4)

        summary = {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "strategy": strategy_label,
            "dry_run": self.dry_run,
            "total_chunks": total_chunks,
            "already_cached": already_cached_count,
            "newly_embedded": newly_embedded_count,
            "failed_chunks": failed_count,
            "remaining_unembedded": remaining_chunks,
            "total_tokens_processed": total_tokens_processed,
            "estimated_cost_usd": estimated_cost_usd,
            "runtime_seconds": total_runtime_s,
            "interrupted": self._interrupted,
            "cache_store": getattr(self.cache, "db_path", type(self.cache).__name__),
            "errors": error_details,
        }

        # Save checkpoint to disk
        self._write_checkpoint(summary)

        print("\n" + "=" * 75)
        print("  INGESTION RUN SUMMARY")
        print("=" * 75)
        print(f"  Status:             {'INTERRUPTED' if self._interrupted else ('FAILED' if failed_count else 'COMPLETED')}")
        print(f"  Total Chunks:       {total_chunks}")
        print(f"  Cache Hits (Reused): {already_cached_count}")
        print(f"  Newly Embedded:     {newly_embedded_count}")
        print(f"  Failed Chunks:      {failed_count}")
        print(f"  Remaining:          {remaining_chunks}")
        print(f"  Total Tokens:       {total_tokens_processed:,}")
        print(f"  Estimated Cost:     ${estimated_cost_usd:.4f} USD")
        print(f"  Runtime:            {total_runtime_s}s")
        print(f"  Checkpoint File:    {self.checkpoint_path}")
        print("=" * 75)

        return summary

    def _write_checkpoint(self, summary: Dict[str, Any]) -> None:
        """Persists checkpoint summary to disk without secrets, preserving multi-strategy history."""
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.checkpoint_path)), exist_ok=True)
            existing: Dict[str, Any] = {}
            if os.path.exists(self.checkpoint_path):
                try:
                    with open(self.checkpoint_path, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception:
                    existing = {}

            strategies = existing.get("strategies", {})
            strat = summary.get("strategy", "all")
            strategy_entry = {
                "timestamp_utc": summary["timestamp_utc"],
                "dry_run": summary["dry_run"],
                "total_chunks": summary["total_chunks"],
                "already_cached": summary["already_cached"],
                "newly_embedded": summary["newly_embedded"],
                "failed_chunks": summary["failed_chunks"],
                "remaining_unembedded": summary["remaining_unembedded"],
                "completed": summary["remaining_unembedded"] == 0 and summary["failed_chunks"] == 0,
            }

            merged = dict(summary)

            # If existing checkpoint contains a live run for this strategy and current is dry-run,
            # preserve the live progress in strategies[strat] and record dry-run under latest_dry_run
            if summary["dry_run"] and existing.get("strategies", {}).get(strat, {}).get("dry_run") is False:
                strategies[strat] = existing["strategies"][strat]
                merged["latest_dry_run"] = summary
            else:
                strategies[strat] = strategy_entry

            merged["strategies"] = strategies

            with open(self.checkpoint_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2)
        except Exception as e:
            print(f"[WARNING] Unable to write checkpoint file: {e}")


def load_corpus_documents(project_root: str = PROJECT_ROOT) -> List[ParsedDocument]:
    """
    Discovers and parses the official PostgreSQL 16 PDF and 14 Markdown chapters.
    Strictly asserts exclusion of the security fixture.
    """
    pdf_manifest_path = os.path.join(project_root, "data", "corpus-manifest.json")
    md_manifest_path = os.path.join(project_root, "data", "markdown-corpus-manifest.json")

    with open(pdf_manifest_path, "r", encoding="utf-8") as f:
        pdf_manifest = json.load(f)

    with open(md_manifest_path, "r", encoding="utf-8") as f:
        md_manifest = json.load(f)

    pdf_file_path = os.path.join(project_root, "data", "raw", "postgresql-16-US.pdf")
    md_dir = os.path.join(project_root, "data", "raw", "markdown")

    # Discover Markdown files with defensive security exclusion
    md_files = []
    for doc in md_manifest["documents"]:
        filename = doc.get("filename", "")
        if "security" in filename.lower() or "security" in doc.get("document_id", "").lower():
            continue
        full_path = os.path.join(md_dir, filename)
        if "security" in full_path.lower():
            continue
        if os.path.exists(full_path):
            md_files.append((full_path, doc))

    # Strict security fixture exclusion assertion
    security_fixture_path = os.path.join(project_root, "data", "security", "security_test_payload.md")
    assert security_fixture_path not in [f[0] for f in md_files], (
        "CRITICAL: data/security/security_test_payload.md must NEVER be included in production corpus!"
    )


    parsed_docs: List[ParsedDocument] = []
    pdf_parser = PDFParser()
    md_parser = MarkdownParser()

    # Parse PDF (uses cached section extractions if available)
    pdf_manifest_entry = {
        "document_id": "postgresql-16-US",
        "title": "PostgreSQL 16 Documentation (US Letter)",
        "topic": "Official PostgreSQL Manual",
        "source_url": pdf_manifest["target_corpus"]["official_source_url"],
        "retrieval_date": "2026-10-23",
        "license": "PostgreSQL License",
    }
    pdf_result = pdf_parser.parse(pdf_file_path, manifest_entry=pdf_manifest_entry, use_cache=True)
    parsed_docs.append(pdf_result.document)

    # Parse Markdown reference chapters
    for md_path, manifest_entry in md_files:
        md_result = md_parser.parse(md_path, manifest_entry=manifest_entry)
        parsed_docs.append(md_result.document)

    return parsed_docs


def generate_chunks_for_documents(
    documents: List[ParsedDocument],
    strategy: str,
    limit: Optional[int] = None,
) -> List[Chunk]:
    """
    Applies the specified chunking strategy across the provided documents.
    Preserves strict separation between Strategy A and Strategy B.
    When strategy == 'all' and a limit is specified, balances chunks evenly
    so each strategy contributes at most limit // 2 (e.g. 5 each for limit=10).
    """
    tokenizer = DeterministicTestTokenizer()
    chunks_a: List[Chunk] = []
    chunks_b: List[Chunk] = []

    if strategy in ("fixed_window", "all"):
        chunker_a = FixedSizeChunker(target_tokens=500, overlap_tokens=50, tokenizer=tokenizer)
        for doc in documents:
            chunks_a.extend(chunker_a.chunk(doc))

    if strategy in ("structure_aware", "all"):
        chunker_b = StructureAwareChunker(max_tokens=800, min_tokens=50, overlap_tokens=50, tokenizer=tokenizer)
        for doc in documents:
            chunks_b.extend(chunker_b.chunk(doc))

    if strategy == "all" and limit is not None:
        per_strat = max(1, limit // 2)
        return chunks_a[:per_strat] + chunks_b[:per_strat]

    if strategy == "fixed_window":
        return chunks_a
    if strategy == "structure_aware":
        return chunks_b
    return chunks_a + chunks_b



def build_cli_parser() -> argparse.ArgumentParser:
    """Configures the command-line interface arguments."""
    parser = argparse.ArgumentParser(
        description="Resumable Corpus Ingestion CLI for Persistent Embeddings"
    )
    parser.add_argument(
        "--strategy",
        choices=["fixed_window", "structure_aware", "all"],
        default="all",
        help="Chunking strategy to process (default: all)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Simulate ingestion, projecting cache hits and costs without making any API calls",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of new chunks to embed in this execution (useful for pilots)",
    )
    parser.add_argument(
        "--allow-full-corpus",
        action="store_true",
        default=False,
        help="Explicit confirmation required to process the entire corpus without a --limit",
    )
    parser.add_argument(
        "--provider",
        choices=["mock", "gemini"],
        default="mock",
        help="Embedding provider (default: mock for safe offline verification)",
    )
    parser.add_argument(
        "--cache-path",
        default=DEFAULT_CACHE_PATH,
        help=f"Path to SQLite embedding cache file (default: {DEFAULT_CACHE_PATH})",
    )
    parser.add_argument(
        "--checkpoint-path",
        default=DEFAULT_CHECKPOINT_PATH,
        help=f"Path to checkpoint status JSON (default: {DEFAULT_CHECKPOINT_PATH})",
    )
    parser.add_argument(
        "--rate-limit",
        type=float,
        default=10.0,
        help="Maximum embedding requests per second (default: 10.0)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of chunk vectors to commit per database transaction (default: 50)",
    )
    return parser


def main():
    parser = build_cli_parser()
    args = parser.parse_args()

    # Safety guard: dry-run mode strictly prevents network calls
    if args.dry_run:
        print("[DRY-RUN] Enabled: network requests are strictly disabled.")

    # Safety guard: positive limit validation
    if args.limit is not None and args.limit <= 0:
        print("[ERROR] --limit must be a positive integer greater than 0.")
        sys.exit(1)

    # Safety guard: full corpus ingestion requires explicit opt-in
    if args.limit is None and not args.allow_full_corpus and not args.dry_run:
        print("[ERROR] Safety Guard: Ingestion of the full corpus without a limit requires explicit opt-in.")
        print("To prevent accidental large-scale embedding or quota exhaustion, you must provide either:")
        print("  --limit <N>           (e.g., --limit 10 for micro-batch testing)")
        print("  --allow-full-corpus   (to explicitly authorize processing all chunks)")
        print("  --dry-run             (to simulate offline without modifying cache or calling APIs)")
        sys.exit(1)

    # Load .env file safely
    load_env_file()

    # Initialize disk cache
    cache = DiskEmbeddingCache(db_path=args.cache_path)

    # Initialize embedding provider
    if args.dry_run:
        # In dry-run mode, simulate target model (Gemini by default) offline with mock provider
        target_model = MODEL_GEMINI_EMBEDDING_001 if args.provider in ("mock", "gemini") else args.provider
        provider = DeterministicMockEmbeddingProvider(
            dimensions=PROVISIONAL_DEFAULT_DIMENSION,
            model_name=target_model,
        )
    elif args.provider == "mock":
        provider = DeterministicMockEmbeddingProvider(
            dimensions=PROVISIONAL_DEFAULT_DIMENSION,
            model_name="mock-deterministic-v1",
        )
    else:
        # Live Gemini provider requires explicit authorization and valid key
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key or not api_key.strip():
            print("[ERROR] GEMINI_API_KEY not found in environment or .env file.")
            sys.exit(1)
        provider = GeminiEmbeddingProvider(
            model_name=MODEL_GEMINI_EMBEDDING_001,
            dimensions=PROVISIONAL_DEFAULT_DIMENSION,
            api_key=api_key.strip(),
            cache=None,  # IngestionRunner orchestrates cache lookups, validation, and batched commits
            is_live_enabled=True,
            retry_policy=RetryPolicy(max_retries=0),
        )

    runner = IngestionRunner(
        provider=provider,
        cache=cache,
        dry_run=args.dry_run,
        rate_limit_rps=args.rate_limit,
        batch_size=args.batch_size,
        checkpoint_path=args.checkpoint_path,
    )

    # Register interrupt handlers for graceful termination
    signal.signal(signal.SIGINT, runner.trigger_interruption)
    signal.signal(signal.SIGTERM, runner.trigger_interruption)

    print("Loading corpus documents...")
    documents = load_corpus_documents(PROJECT_ROOT)
    print(f"Loaded {len(documents)} source documents. Generating chunks for strategy: {args.strategy}...")
    chunks = generate_chunks_for_documents(documents, strategy=args.strategy, limit=args.limit)


    # Run ingestion pipeline
    summary = runner.run(chunks=chunks, limit=args.limit, strategy_label=args.strategy)

    if summary.get("interrupted"):
        sys.exit(130)

    if summary.get("failed_chunks", 0) > 0:
        sys.exit(1)



if __name__ == "__main__":
    main()
