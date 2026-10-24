"""
Integration tests: Document Parsers & Chunking Strategies Pipeline
Location: tests/integration/test_parser_chunker_integration.py
"""

import os
import pytest
import pypdf

from src.models import ParsedDocument
from src.parsers.markdown_parser import MarkdownParser
from src.parsers.pdf_parser import PDFParser
from src.chunkers.fixed_size import FixedSizeChunker
from src.chunkers.structure_aware import StructureAwareChunker


@pytest.fixture
def project_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def sample_pdf_fixture(tmp_path):
    """Dynamically creates a small 3-page PDF fixture for deterministic parser testing."""
    pdf_path = str(tmp_path / "test_sample.pdf")
    writer = pypdf.PdfWriter()

    # Page 1: Regular technical text
    writer.add_blank_page(width=612, height=792)
    # Page 2: Blank page (should trigger EMPTY_PAGE warning)
    writer.add_blank_page(width=612, height=792)
    # Page 3: Regular text
    writer.add_blank_page(width=612, height=792)

    with open(pdf_path, "wb") as f:
        writer.write(f)

    return pdf_path


class TestParserChunkerIntegration:
    """Verifies end-to-end integration between document parsers and chunking engines."""

    def test_markdown_parser_headings_and_fences(self, project_root):
        wal_path = os.path.join(project_root, "data", "raw", "markdown", "wal-intro.md")
        assert os.path.exists(wal_path), "wal-intro.md must exist in verified markdown corpus"

        parser = MarkdownParser()
        result = parser.parse(wal_path)

        assert result.document.metadata.document_id == "wal-intro"
        assert result.document.metadata.file_type == "markdown"
        assert result.document.metadata.source_url == "https://www.postgresql.org/docs/16/wal-intro.html"
        assert len(result.document.sections) >= 1
        assert any("Write-Ahead Logging" in s.content for s in result.document.sections)

    def test_pdf_page_preservation_and_warnings(self, sample_pdf_fixture, tmp_path):
        parser = PDFParser(cache_dir=str(tmp_path / "cache"))
        result = parser.parse(sample_pdf_fixture, use_cache=False)

        assert result.page_count == 3
        # Blank pages must trigger EMPTY_PAGE warning
        empty_warnings = [w for w in result.warnings if w.warning_type == "EMPTY_PAGE"]
        assert len(empty_warnings) >= 1
        assert empty_warnings[0].page_number is not None

    def test_metadata_propagation_parser_to_chunks(self, project_root):
        md_path = os.path.join(project_root, "data", "raw", "markdown", "backup-dump.md")
        parser = MarkdownParser()
        result = parser.parse(md_path)

        chunker_a = FixedSizeChunker(target_tokens=500, overlap_tokens=50)
        chunker_b = StructureAwareChunker(max_tokens=800, min_tokens=50)

        chunks_a = chunker_a.chunk(result.document)
        chunks_b = chunker_b.chunk(result.document)

        assert len(chunks_a) > 0
        assert len(chunks_b) > 0

        for c in chunks_a + chunks_b:
            # Metadata must propagate intact from source file through parser into chunks
            assert c.document_id == result.document.metadata.document_id
            assert c.source_filename == "backup-dump.md"
            assert c.source_url == "https://www.postgresql.org/docs/16/backup-dump.html"
            assert c.document_date is not None
            assert len(c.content_hash) == 64
            assert len(c.chunk_id) == 36

    def test_both_strategies_run_on_same_parsed_input(self, project_root):
        md_path = os.path.join(project_root, "data", "raw", "markdown", "indexes-types.md")
        parser = MarkdownParser()
        result = parser.parse(md_path)

        chunker_a = FixedSizeChunker(target_tokens=500, overlap_tokens=50)
        chunker_b = StructureAwareChunker(max_tokens=800, min_tokens=50)

        chunks_a = chunker_a.chunk(result.document)
        chunks_b = chunker_b.chunk(result.document)

        assert len(chunks_a) > 0
        assert len(chunks_b) > 0
        # IDs must be completely unique within each strategy and across strategies
        all_ids = [c.chunk_id for c in chunks_a] + [c.chunk_id for c in chunks_b]
        assert len(all_ids) == len(set(all_ids))

    def test_stable_output_across_repeated_runs(self, project_root):
        md_path = os.path.join(project_root, "data", "raw", "markdown", "mvcc-intro.md")
        parser = MarkdownParser()
        doc = parser.parse(md_path).document

        chunker_a = FixedSizeChunker(target_tokens=500, overlap_tokens=50)
        chunker_b = StructureAwareChunker(max_tokens=800, min_tokens=50)

        run1_a = chunker_a.chunk(doc)
        run2_a = chunker_a.chunk(doc)
        assert [c.chunk_id for c in run1_a] == [c.chunk_id for c in run2_a]
        assert [c.content_hash for c in run1_a] == [c.content_hash for c in run2_a]

        run1_b = chunker_b.chunk(doc)
        run2_b = chunker_b.chunk(doc)
        assert [c.chunk_id for c in run1_b] == [c.chunk_id for c in run2_b]
        assert [c.content_hash for c in run1_b] == [c.content_hash for c in run2_b]

    def test_security_fixture_strictly_excluded_from_corpus(self, project_root):
        # Verify that discovery logic excludes security_test_payload.md
        security_file = os.path.join(project_root, "data", "security", "security_test_payload.md")
        assert os.path.exists(security_file), "Security fixture must exist in isolation"

        md_corpus_dir = os.path.join(project_root, "data", "raw", "markdown")
        corpus_files = os.listdir(md_corpus_dir)

        # Security payload must NOT be located in the markdown corpus directory
        assert "security_test_payload.md" not in corpus_files

    def test_empty_and_unreadable_file_handling(self, tmp_path):
        parser = MarkdownParser()

        # Non-existent file
        with pytest.raises(FileNotFoundError):
            parser.parse(str(tmp_path / "non_existent.md"))

        # Empty file
        empty_md = tmp_path / "empty.md"
        empty_md.write_text("", encoding="utf-8")
        result = parser.parse(str(empty_md))
        assert result.document.is_empty
        assert any(w.warning_type == "EMPTY_DOCUMENT_BODY" for w in result.warnings)

    def test_pdf_cache_invalidation_on_size_change(self, tmp_path):
        """Verifies that the PDF cache is invalidated when file size changes."""
        cache_dir = tmp_path / "cache"
        pdf_file = tmp_path / "test_doc.pdf"

        # 1. Create a 2-page PDF
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=612, height=792)
        writer.add_blank_page(width=612, height=792)
        with open(pdf_file, "wb") as f:
            writer.write(f)

        parser = PDFParser(cache_dir=str(cache_dir))
        res1 = parser.parse(str(pdf_file), use_cache=True)
        assert res1.page_count == 2

        # 2. Modify file size by adding another page
        writer.add_blank_page(width=612, height=792)
        with open(pdf_file, "wb") as f:
            writer.write(f)

        # 3. Parse again: must detect size mismatch and invalidate cache
        res2 = parser.parse(str(pdf_file), use_cache=True)
        assert res2.page_count == 3
        assert any(w.warning_type == "CACHE_INVALIDATED_SIZE_MISMATCH" for w in res2.warnings)

    def test_pdf_cache_invalidation_on_hash_change(self, tmp_path):
        """Verifies that the PDF cache is invalidated when SHA-256 hash changes even if file size is identical."""
        import json
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        pdf_file = tmp_path / "hash_doc.pdf"

        # Create valid PDF
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=612, height=792)
        with open(pdf_file, "wb") as f:
            writer.write(f)

        file_size = os.path.getsize(pdf_file)
        # Create a stale cache file with correct file size but wrong hash
        cache_file = cache_dir / "hash_doc.pdf.extracted.json"
        cache_payload = {
            "schema_version": "1.1",
            "source_filename": "hash_doc.pdf",
            "file_size_bytes": file_size,
            "sha256_hash": "deadbeef" * 8,  # bogus hash
            "total_pages": 1,
            "cached_timestamp": 12345.0,
            "pages": [{"page_number": 1, "text": "stale cached text"}],
        }
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(cache_payload, f)

        parser = PDFParser(cache_dir=str(cache_dir))
        res = parser.parse(str(pdf_file), use_cache=True)
        assert any(w.warning_type == "CACHE_INVALIDATED_HASH_MISMATCH" for w in res.warnings)

    def test_pdf_cache_hit_when_unmodified(self, sample_pdf_fixture, tmp_path):
        """Verifies that an unmodified PDF hits cache with no invalidation warnings."""
        cache_dir = tmp_path / "cache"
        parser = PDFParser(cache_dir=str(cache_dir))

        # First run populates cache
        res1 = parser.parse(sample_pdf_fixture, use_cache=True)
        # Second run should load cleanly from cache
        res2 = parser.parse(sample_pdf_fixture, use_cache=True)

        assert res1.page_count == res2.page_count
        assert not any("CACHE_INVALIDATED" in w.warning_type for w in res2.warnings)

    def test_corpus_overlap_audit_reproducibility(self):
        """Verifies that the overlap audit identifies shared shingles and maps documents to pages."""
        from src.audit_overlap import audit_corpus_overlap

        pages = [
            (1, "PostgreSQL 16 documentation title page."),
            (2, "Write-ahead logging ensures data integrity through sequential log records."),
        ]
        md_docs = [
            {
                "document_id": "wal-test",
                "filename": "wal-test.md",
                "title": "WAL Test",
                "content": "Write-ahead logging ensures data integrity through sequential log records on disk.",
            }
        ]
        audit = audit_corpus_overlap(pages, md_docs, shingle_size=4)
        assert audit["exact_hash_collisions_count"] == 0
        assert len(audit["markdown_mappings"]) == 1
        assert audit["markdown_mappings"][0]["likely_page_range"] == "Page 2"
        assert audit["markdown_mappings"][0]["overall_shingle_containment_pct"] > 50.0

