"""
Unit Tests for Resumable Ingestion Runner & CLI
Location: tests/unit/test_ingestion_cli.py

Verifies:
1. Dry-run mode never calls embedding provider and leaves cache unmodified.
2. Resumption safely detects and reuses cached chunks without re-embedding.
3. Failed chunks are never cached or marked complete.
4. Interruption cleanly flushes pending writes to disk cache and records checkpoint.
5. Ingestion discovery strictly excludes data/security/security_test_payload.md.
6. Fixed-window and structure-aware strategies remain strictly separated.
"""

import json
import os
import pytest
from typing import List, Optional

from scripts.ingest_corpus_embeddings import (
    IngestionRunner,
    load_corpus_documents,
    generate_chunks_for_documents,
    PROJECT_ROOT,
)
from src.chunkers.base import compute_content_hash
from src.embeddings.base import BaseEmbeddingProvider, EmbeddingError
from src.embeddings.cache import DiskEmbeddingCache
from src.models import Chunk, ParsedDocument, DocumentMetadata, DocumentSection


class SpyMockProvider(BaseEmbeddingProvider):
    """Test spy that tracks call counts and can simulate failures."""

    def __init__(self, fail_on_call: Optional[int] = None):
        self.call_count = 0
        self.fail_on_call = fail_on_call
        self.embedded_texts: List[str] = []

    @property
    def model_name(self) -> str:
        return "mock-spy-v1"

    @property
    def dimensions(self) -> int:
        return 768

    def embed_text(self, text: str) -> List[float]:
        self.call_count += 1
        if self.fail_on_call is not None and self.call_count == self.fail_on_call:
            raise EmbeddingError(f"Simulated provider failure on call {self.call_count}")
        self.embedded_texts.append(text)
        return [0.05] * 768

    def embed_document(self, text: str, title: Optional[str] = None) -> List[float]:
        return self.embed_text(text)


def create_sample_chunk(index: int, strategy: str = "fixed_window") -> Chunk:
    content = f"PostgreSQL sample documentation paragraph for chunk {index}."
    return Chunk(
        chunk_id=f"test-chunk-{strategy}-{index}",
        document_id="test-doc-001",
        strategy=strategy,
        chunk_index=index,
        source_filename="test_doc.md",
        page_or_section=f"Section {index}",
        content=content,
        content_hash=compute_content_hash(content),
        token_count=15,
        metadata={"title": "Test Title", "file_type": "markdown"},
    )


class TestIngestionRunner:
    """Verifies all offline resume, dry-run, and failure-handling guarantees."""

    def test_dry_run_never_calls_provider_and_leaves_cache_empty(self, tmp_path):
        db_file = str(tmp_path / "dry_run.sqlite")
        checkpoint_file = str(tmp_path / "dry_run_checkpoint.json")
        cache = DiskEmbeddingCache(db_file)
        provider = SpyMockProvider()

        runner = IngestionRunner(
            provider=provider,
            cache=cache,
            dry_run=True,
            checkpoint_path=checkpoint_file,
        )

        chunks = [create_sample_chunk(i) for i in range(5)]
        summary = runner.run(chunks=chunks, strategy_label="fixed_window")

        assert provider.call_count == 0
        assert cache.size() == 0
        assert summary["already_cached"] == 0
        assert summary["newly_embedded"] == 5
        assert summary["failed_chunks"] == 0
        assert summary["dry_run"] is True

        with open(checkpoint_file, "r", encoding="utf-8") as f:
            cp = json.load(f)
        assert cp["dry_run"] is True
        assert cp["total_chunks"] == 5

    def test_resumption_safely_skips_already_cached_chunks(self, tmp_path):
        """Proves incremental progress: second run reuses cached chunks without calling provider."""
        db_file = str(tmp_path / "resumption.sqlite")
        checkpoint_file = str(tmp_path / "resumption_checkpoint.json")
        cache = DiskEmbeddingCache(db_file)
        provider = SpyMockProvider()

        runner = IngestionRunner(
            provider=provider,
            cache=cache,
            dry_run=False,
            checkpoint_path=checkpoint_file,
        )

        chunks = [create_sample_chunk(i) for i in range(6)]

        # Run 1: Bounded execution with limit=3
        summary1 = runner.run(chunks=chunks, limit=3, strategy_label="fixed_window")
        assert summary1["newly_embedded"] == 3
        assert summary1["already_cached"] == 0
        assert provider.call_count == 3
        assert cache.size() == 3

        # Run 2: Resume full run across all 6 chunks
        provider_calls_before = provider.call_count
        summary2 = runner.run(chunks=chunks, limit=None, strategy_label="fixed_window")

        assert summary2["already_cached"] == 3
        assert summary2["newly_embedded"] == 3
        assert provider.call_count == provider_calls_before + 3
        assert cache.size() == 6

    def test_failure_does_not_mark_chunk_complete_or_cached(self, tmp_path):
        """When provider fails on chunk 3, chunks 1 & 2 are cached; chunk 3 is not cached."""
        db_file = str(tmp_path / "failure.sqlite")
        checkpoint_file = str(tmp_path / "failure_checkpoint.json")
        cache = DiskEmbeddingCache(db_file)
        # Fail on the 3rd API call
        failing_provider = SpyMockProvider(fail_on_call=3)

        runner = IngestionRunner(
            provider=failing_provider,
            cache=cache,
            dry_run=False,
            checkpoint_path=checkpoint_file,
        )

        chunks = [create_sample_chunk(i) for i in range(5)]
        summary = runner.run(chunks=chunks, strategy_label="fixed_window")

        assert summary["failed_chunks"] == 1
        assert summary["newly_embedded"] == 2
        assert cache.size() == 2

        # Verify chunk 0 and 1 are in cache, chunk 2 is NOT in cache
        assert cache.has(chunks[0].content_hash, failing_provider.model_name, 768, "RETRIEVAL_DOCUMENT", "Test Title")
        assert cache.has(chunks[1].content_hash, failing_provider.model_name, 768, "RETRIEVAL_DOCUMENT", "Test Title")
        assert not cache.has(chunks[2].content_hash, failing_provider.model_name, 768, "RETRIEVAL_DOCUMENT", "Test Title")

    def test_interruption_flushes_pending_writes_and_stops(self, tmp_path):
        """Simulates external SIGINT interrupt; verifies pending writes are flushed and checkpoint recorded."""
        db_file = str(tmp_path / "interrupt.sqlite")
        checkpoint_file = str(tmp_path / "interrupt_checkpoint.json")
        cache = DiskEmbeddingCache(db_file)
        provider = SpyMockProvider()

        runner = IngestionRunner(
            provider=provider,
            cache=cache,
            dry_run=False,
            checkpoint_path=checkpoint_file,
            batch_size=10,  # Batch size > 2 so items would normally be pending
        )

        chunks = [create_sample_chunk(i) for i in range(5)]

        # Simulate interruption after chunk 2
        original_embed = provider.embed_document

        def interrupt_after_two(text, title=None):
            res = original_embed(text, title)
            if provider.call_count == 2:
                runner.trigger_interruption()
            return res

        provider.embed_document = interrupt_after_two

        summary = runner.run(chunks=chunks)

        assert summary["interrupted"] is True
        assert summary["newly_embedded"] == 2
        assert cache.size() == 2  # Flushed despite batch_size=10

        with open(checkpoint_file, "r", encoding="utf-8") as f:
            cp = json.load(f)
        assert cp["interrupted"] is True
        assert cp["newly_embedded"] == 2

    def test_security_fixture_strictly_excluded_from_corpus(self):
        """Verifies load_corpus_documents() does not discover or load security_test_payload.md."""
        docs = load_corpus_documents(PROJECT_ROOT)
        filenames = [d.metadata.source_filename for d in docs]

        assert "security_test_payload.md" not in filenames
        assert all("security" not in fn.lower() for fn in filenames)
        assert len(docs) == 15  # 1 PDF + 14 Markdown

    def test_preserves_strategy_separation(self):
        """Verifies generate_chunks_for_documents keeps Strategy A and Strategy B separate."""
        doc = ParsedDocument(
            metadata=DocumentMetadata(
                document_id="doc-001",
                source_filename="test.md",
                file_type="markdown",
            ),
            sections=[
                DocumentSection(
                    section_index=0,
                    page_or_section="Chapter 1",
                    content="PostgreSQL documentation content.",
                    heading_path=["Chapter 1"],
                )
            ],
            raw_text="PostgreSQL documentation content.",
        )

        chunks_a = generate_chunks_for_documents([doc], strategy="fixed_window")
        chunks_b = generate_chunks_for_documents([doc], strategy="structure_aware")
        chunks_all = generate_chunks_for_documents([doc], strategy="all")

        assert all(c.strategy == "fixed_window" for c in chunks_a)
        assert all(c.strategy == "structure_aware" for c in chunks_b)
        assert len(chunks_all) == len(chunks_a) + len(chunks_b)

    def test_balanced_strategy_chunks_on_limit_ten(self):
        """Verifies limit=10 on strategy='all' selects at most 5 chunks per strategy."""
        doc = ParsedDocument(
            metadata=DocumentMetadata(
                document_id="doc-001",
                source_filename="test.md",
                file_type="markdown",
            ),
            sections=[
                DocumentSection(
                    section_index=i,
                    page_or_section=f"Chapter {i}",
                    content=f"PostgreSQL documentation content paragraph {i}. " * 30,
                    heading_path=[f"Chapter {i}"],
                )
                for i in range(10)
            ],
            raw_text="PostgreSQL documentation content.",
        )
        chunks = generate_chunks_for_documents([doc], strategy="all", limit=10)
        assert len(chunks) == 10
        fw_count = sum(1 for c in chunks if c.strategy == "fixed_window")
        sa_count = sum(1 for c in chunks if c.strategy == "structure_aware")
        assert fw_count == 5
        assert sa_count == 5


    def test_checkpoint_preserves_multi_strategy_history(self, tmp_path):
        """Verifies running Strategy A followed by Strategy B preserves both strategies in checkpoint."""
        db_file = str(tmp_path / "multi_strat.sqlite")
        checkpoint_file = str(tmp_path / "multi_strat_checkpoint.json")
        cache = DiskEmbeddingCache(db_file)
        provider = SpyMockProvider()

        runner = IngestionRunner(
            provider=provider,
            cache=cache,
            dry_run=False,
            checkpoint_path=checkpoint_file,
        )

        chunks_a = [create_sample_chunk(i, strategy="fixed_window") for i in range(3)]
        chunks_b = [create_sample_chunk(i + 10, strategy="structure_aware") for i in range(3)]


        # Run Strategy A
        runner.run(chunks=chunks_a, strategy_label="fixed_window")
        with open(checkpoint_file, "r", encoding="utf-8") as f:
            cp1 = json.load(f)
        assert "strategies" in cp1
        assert "fixed_window" in cp1["strategies"]
        assert cp1["strategies"]["fixed_window"]["newly_embedded"] == 3

        # Run Strategy B
        runner.run(chunks=chunks_b, strategy_label="structure_aware")
        with open(checkpoint_file, "r", encoding="utf-8") as f:
            cp2 = json.load(f)
        assert "fixed_window" in cp2["strategies"]
        assert "structure_aware" in cp2["strategies"]
        assert cp2["strategies"]["fixed_window"]["newly_embedded"] == 3
        assert cp2["strategies"]["structure_aware"]["newly_embedded"] == 3

    def test_dry_run_preserves_existing_live_checkpoint_progress(self, tmp_path):
        """Verifies that running a subsequent dry-run does not erase real live progress from checkpoint."""
        checkpoint_file = str(tmp_path / "live_cp.json")
        initial_data = {
            "strategies": {
                "fixed_window": {
                    "dry_run": False,
                    "total_chunks": 100,
                    "already_cached": 50,
                    "newly_embedded": 50,
                    "failed_chunks": 0,
                    "remaining_unembedded": 0,
                    "completed": True,
                }
            }
        }
        with open(checkpoint_file, "w", encoding="utf-8") as f:
            json.dump(initial_data, f)

        cache = DiskEmbeddingCache(str(tmp_path / "test.sqlite"))
        runner = IngestionRunner(
            provider=SpyMockProvider(),
            cache=cache,
            dry_run=True,
            checkpoint_path=checkpoint_file,
        )
        chunks = [create_sample_chunk(i, strategy="fixed_window") for i in range(5)]
        runner.run(chunks=chunks, strategy_label="fixed_window")

        with open(checkpoint_file, "r", encoding="utf-8") as f:
            cp = json.load(f)
        # Live progress preserved in strategies
        assert cp["strategies"]["fixed_window"]["dry_run"] is False
        assert cp["strategies"]["fixed_window"]["newly_embedded"] == 50
        # Dry run captured in latest_dry_run
        assert "latest_dry_run" in cp
        assert cp["latest_dry_run"]["dry_run"] is True

    def test_cli_parser_and_safety_arguments(self):
        """Verifies CLI argument parser configurations and safety defaults."""
        from scripts.ingest_corpus_embeddings import build_cli_parser
        parser = build_cli_parser()

        # Defaults
        args = parser.parse_args([])
        assert args.strategy == "all"
        assert args.dry_run is False
        assert args.limit is None
        assert args.allow_full_corpus is False
        assert args.provider == "mock"

        # Explicit allow-full-corpus
        args_full = parser.parse_args(["--allow-full-corpus"])
        assert args_full.allow_full_corpus is True

    def test_deterministic_mock_provider_embed_document_and_query(self):
        """Verifies DeterministicMockEmbeddingProvider implements document and query methods."""
        from src.embeddings.mock_provider import DeterministicMockEmbeddingProvider
        p = DeterministicMockEmbeddingProvider(dimensions=768)

        v_doc = p.embed_document("Some postgresql documentation text", title="Chapter 1")
        v_doc_no_title = p.embed_document("Some postgresql documentation text")
        v_query = p.embed_query("Some postgresql documentation text")

        assert len(v_doc) == 768
        assert len(v_doc_no_title) == 768
        assert len(v_query) == 768
        # Document with title produces different vector than document without title
        assert v_doc != v_doc_no_title

    def test_main_cli_blocks_unauthorized_full_corpus_ingestion(self, monkeypatch):
        """Verifies running CLI with no limit and no opt-in immediately exits with code 1."""
        from scripts.ingest_corpus_embeddings import main
        monkeypatch.setattr("sys.argv", ["ingest_corpus_embeddings.py"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1

    def test_main_cli_rejects_non_positive_limit(self, monkeypatch):
        """Verifies running CLI with --limit <= 0 exits with code 1."""
        from scripts.ingest_corpus_embeddings import main
        monkeypatch.setattr("sys.argv", ["ingest_corpus_embeddings.py", "--limit", "0"])
        with pytest.raises(SystemExit) as exc:
            main()
        assert exc.value.code == 1


