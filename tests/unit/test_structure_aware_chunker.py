"""
Unit tests for Strategy B: Structure-Aware Chunker
Location: tests/unit/test_structure_aware_chunker.py
"""

import pytest
from src.models import DocumentMetadata, DocumentSection, ParsedDocument
from src.chunkers.structure_aware import StructureAwareChunker, parse_markdown_sections
from src.tokenizers import DeterministicTestTokenizer


@pytest.fixture
def sample_md_metadata():
    return DocumentMetadata(
        document_id="doc-md-pg16-01",
        source_filename="backup-dump.md",
        file_type="markdown",
        source_url="https://www.postgresql.org/docs/16/backup-dump.html",
        document_date="2024-02-08",
        title="PostgreSQL 16: SQL Dump Backup",
        topic="Backup and Restore",
        license="PostgreSQL License",
    )


@pytest.fixture
def sample_pdf_metadata():
    return DocumentMetadata(
        document_id="doc-pdf-pg16-01",
        source_filename="postgresql-16-US.pdf",
        file_type="pdf",
        source_url="https://www.postgresql.org/files/documentation/pdf/16/postgresql-16-US.pdf",
        document_date="2024-02-08",
        title="PostgreSQL 16 Documentation",
        topic="Database Manual",
        license="PostgreSQL License",
    )


class TestStructureAwareChunker:
    """Verifies all operational requirements of Strategy B: Structure-Aware Chunking."""

    def test_constructor_validation(self):
        with pytest.raises(ValueError, match="max_tokens must be greater than 0"):
            StructureAwareChunker(max_tokens=0)
        with pytest.raises(ValueError, match="min_tokens cannot be negative"):
            StructureAwareChunker(min_tokens=-1)
        with pytest.raises(ValueError, match="strictly less than max_tokens"):
            StructureAwareChunker(max_tokens=500, overlap_tokens=500)
        with pytest.raises(ValueError, match="cannot be greater than max_tokens"):
            StructureAwareChunker(max_tokens=200, min_tokens=300)

    def test_empty_document(self, sample_md_metadata):
        chunker = StructureAwareChunker()
        doc = ParsedDocument(metadata=sample_md_metadata, raw_text="")
        assert chunker.chunk(doc) == []

    def test_markdown_heading_hierarchy_and_breadcrumbs(self, sample_md_metadata):
        md_text = (
            "# SQL Dump\n\n"
            "The idea behind this dump method is to generate a file with SQL commands.\n\n"
            "## Restoring the Dump\n\n"
            "The SQL dump produced by pg_dump can be read in by psql.\n\n"
            "### Handling Large Databases\n\n"
            "When restoring large databases, consider tuning maintenance_work_mem and max_wal_size."
        )

        sections = parse_markdown_sections(md_text, doc_title="Backup Guide")
        assert len(sections) == 3

        # Verify heading hierarchy stack
        assert sections[0].heading_path == ["Backup Guide", "SQL Dump"]
        assert sections[1].heading_path == ["Backup Guide", "SQL Dump", "Restoring the Dump"]
        assert sections[2].heading_path == [
            "Backup Guide",
            "SQL Dump",
            "Restoring the Dump",
            "Handling Large Databases",
        ]

    def test_code_fence_comments_not_treated_as_headings(self, sample_md_metadata):
        md_text = (
            "# Database Functions\n\n"
            "Here is a sample function:\n\n"
            "```sql\n"
            "-- In SQL, double dash is a comment\n"
            "# In bash, hash is a comment\n"
            "## This is NOT a markdown heading\n"
            "CREATE FUNCTION add(integer, integer) RETURNS integer AS $$\n"
            "    SELECT $1 + $2;\n"
            "$$ LANGUAGE SQL;\n"
            "```\n\n"
            "## Next Genuine Section\n\n"
            "Genuine content follows here."
        )

        sections = parse_markdown_sections(md_text, doc_title="Functions Guide")
        # Should have exactly 2 sections: "Database Functions" and "Next Genuine Section"
        # The "# In bash" and "## This is NOT" inside ``` must NOT create sections!
        assert len(sections) == 2
        assert sections[0].heading_path == ["Functions Guide", "Database Functions"]
        assert sections[1].heading_path == [
            "Functions Guide",
            "Database Functions",
            "Next Genuine Section",
        ]
        assert "CREATE FUNCTION" in sections[0].content

    def test_code_block_kept_intact(self, sample_md_metadata):
        chunker = StructureAwareChunker(max_tokens=800, min_tokens=50)

        code_block = (
            "```sql\n"
            "SELECT \n"
            "    schemaname,\n"
            "    relname,\n"
            "    seq_scan,\n"
            "    seq_tup_read,\n"
            "    idx_scan,\n"
            "    idx_tup_fetch\n"
            "FROM pg_stat_user_tables\n"
            "ORDER BY seq_scan DESC\n"
            "LIMIT 20;\n"
            "```"
        )
        md_text = (
            "# Table Monitoring\n\n"
            "Inspect sequential scans using the following catalog query:\n\n"
            f"{code_block}\n\n"
            "Ensure queries utilize indexes when table cardinality exceeds thousands of rows."
        )

        doc = ParsedDocument(metadata=sample_md_metadata, raw_text=md_text)
        chunks = chunker.chunk(doc)

        assert len(chunks) == 1
        assert code_block in chunks[0].content

    def test_oversized_section_splits_at_paragraph_boundaries(self, sample_md_metadata):
        tokenizer = DeterministicTestTokenizer()
        # Set max_tokens = 200 to easily test splitting
        chunker = StructureAwareChunker(max_tokens=200, min_tokens=50, overlap_tokens=20, tokenizer=tokenizer)

        # 3 paragraphs, each ~100 tokens
        p1 = "First paragraph discussing PostgreSQL transaction isolation levels. " * 10
        p2 = "Second paragraph discussing read committed and repeatable read semantics. " * 10
        p3 = "Third paragraph discussing serializable snapshot isolation anomalies. " * 10

        md_text = f"# Transaction Semantics\n\n{p1}\n\n{p2}\n\n{p3}"
        doc = ParsedDocument(metadata=sample_md_metadata, raw_text=md_text)

        chunks = chunker.chunk(doc)
        # Should split into multiple chunks
        assert len(chunks) >= 2
        for c in chunks:
            assert c.token_count <= 250  # within limit (including breadcrumb header)
            assert "Part " in c.page_or_section
            assert c.metadata.get("is_split") is True

    def test_coalescing_small_adjacent_sections(self, sample_md_metadata):
        # min_tokens = 50, max_tokens = 500
        chunker = StructureAwareChunker(max_tokens=500, min_tokens=50)

        # Three short sections (each ~15 tokens, total ~45-50 tokens)
        sec1 = DocumentSection(section_index=0, page_or_section="Section: Intro", content="Brief introduction to PostgreSQL.", heading_path=["Intro"])
        sec2 = DocumentSection(section_index=1, page_or_section="Section: Scope", content="Scope covers version 16.", heading_path=["Scope"])
        sec3 = DocumentSection(section_index=2, page_or_section="Section: Goals", content="Goals are reliable high availability.", heading_path=["Goals"])

        doc = ParsedDocument(metadata=sample_md_metadata, sections=[sec1, sec2, sec3])
        chunks = chunker.chunk(doc)

        # All 3 sections must coalesce into a single chunk
        assert len(chunks) == 1
        c = chunks[0]
        assert c.metadata.get("coalesced") is True
        assert c.metadata.get("section_count") == 3
        assert "Brief introduction" in c.content
        assert "Scope covers version 16" in c.content
        assert "Goals are reliable" in c.content

    def test_pdf_page_provenance_preservation(self, sample_pdf_metadata):
        chunker = StructureAwareChunker(max_tokens=500, min_tokens=50)

        p10 = DocumentSection(section_index=0, page_or_section="Page 10", content="PostgreSQL 16 release notes page ten content. " * 10, page_number=10)
        p11 = DocumentSection(section_index=1, page_or_section="Page 11", content="PostgreSQL 16 release notes page eleven content. " * 10, page_number=11)

        doc = ParsedDocument(metadata=sample_pdf_metadata, sections=[p10, p11])
        chunks = chunker.chunk(doc)

        assert len(chunks) == 2
        assert chunks[0].page_or_section == "Page 10"
        assert chunks[0].metadata["page_number"] == 10
        assert chunks[1].page_or_section == "Page 11"
        assert chunks[1].metadata["page_number"] == 11

    def test_oversized_pdf_page_splits_into_parts(self, sample_pdf_metadata):
        chunker = StructureAwareChunker(max_tokens=150, min_tokens=30, overlap_tokens=20)
        # Giant single page of 400 tokens
        huge_page = DocumentSection(
            section_index=0,
            page_or_section="Page 42",
            content="PostgreSQL server configuration parameter description on page 42. " * 30,
            page_number=42,
        )
        doc = ParsedDocument(metadata=sample_pdf_metadata, sections=[huge_page])
        chunks = chunker.chunk(doc)

        assert len(chunks) >= 2
        for i, c in enumerate(chunks, 1):
            assert c.page_or_section == f"Page 42 (Part {i})"
            assert c.metadata["page_number"] == 42
            assert c.metadata["is_split"] is True
            assert c.metadata["part_index"] == i

    def test_adjacent_small_pdf_pages_coalesce(self, sample_pdf_metadata):
        chunker = StructureAwareChunker(max_tokens=500, min_tokens=50)

        # Two small pages (e.g. copyright and dedication: 10 tokens each)
        p1 = DocumentSection(section_index=0, page_or_section="Page 1", content="PostgreSQL 16 Documentation.", page_number=1)
        p2 = DocumentSection(section_index=1, page_or_section="Page 2", content="Copyright (c) 1996-2024.", page_number=2)

        doc = ParsedDocument(metadata=sample_pdf_metadata, sections=[p1, p2])
        chunks = chunker.chunk(doc)

        assert len(chunks) == 1
        c = chunks[0]
        assert c.page_or_section == "Pages 1–2"
        assert c.metadata["page_numbers"] == [1, 2]
        assert c.metadata["coalesced"] is True

    def test_markdown_breadcrumb_header_prepended(self, sample_md_metadata):
        chunker = StructureAwareChunker(max_tokens=800, min_tokens=50)
        md_text = (
            "# Performance Tuning\n\n"
            "## Memory Configuration\n\n"
            "shared_buffers determines the amount of dedicated memory PostgreSQL uses for caching data."
        )
        doc = ParsedDocument(metadata=sample_md_metadata, raw_text=md_text)
        chunks = chunker.chunk(doc)

        assert len(chunks) == 1
        # Chunk content must contain the breadcrumb Context header from primary section
        assert "Context: [PostgreSQL 16: SQL Dump Backup > Performance Tuning]" in chunks[0].content
        assert "shared_buffers" in chunks[0].content

    def test_trailing_small_section_merges_with_previous(self, sample_md_metadata):
        chunker = StructureAwareChunker(max_tokens=500, min_tokens=50)

        # Section 1 has ~100 tokens, Section 2 has only ~15 tokens at the end
        sec1 = DocumentSection(
            section_index=0,
            page_or_section="Section: Main Topic",
            content="Detailed discussion about PostgreSQL write ahead logging and continuous archiving. " * 8,
            heading_path=["Main Topic"],
        )
        sec2 = DocumentSection(
            section_index=1,
            page_or_section="Section: Summary",
            content="In summary, WAL ensures crash recovery.",
            heading_path=["Summary"],
        )

        doc = ParsedDocument(metadata=sample_md_metadata, sections=[sec1, sec2])
        chunks = chunker.chunk(doc)

        # Because sec2 is < 50 tokens and sec1 + sec2 < 500 tokens, sec2 merges into the previous chunk
        assert len(chunks) == 1
        assert "In summary, WAL ensures crash recovery" in chunks[0].content

    def test_strict_ceiling_enforcement_with_large_breadcrumb(self, sample_md_metadata):
        """Verifies that chunks never exceed max_tokens even when accompanied by substantial breadcrumbs."""
        chunker = StructureAwareChunker(max_tokens=800, min_tokens=50)

        # Deep hierarchy with ~40 tokens of breadcrumbs
        deep_heading_path = [
            "PostgreSQL 16 Documentation",
            "Server Administration",
            "Routine Database Maintenance Tasks",
            "Routine Vacuuming",
            "Autovacuum Daemon Configuration and Tuning",
        ]
        # Text with exactly ~790 tokens of prose
        prose_block = (
            "PostgreSQL's autovacuum subsystem automates the execution of VACUUM and ANALYZE commands. "
            "Proper tuning requires setting autovacuum_max_workers, autovacuum_vacuum_cost_limit, and "
            "autovacuum_vacuum_cost_delay according to the system's disk I/O capabilities. "
        ) * 25

        sec = DocumentSection(
            section_index=0,
            page_or_section="Section: Autovacuum Tuning",
            content=prose_block,
            heading_path=deep_heading_path,
        )

        doc = ParsedDocument(metadata=sample_md_metadata, sections=[sec])
        chunks = chunker.chunk(doc)

        assert len(chunks) >= 2
        for c in chunks:
            # EVERY chunk including its breadcrumb must be strictly <= 800 tokens
            assert c.token_count <= 800, f"Chunk {c.chunk_id} exceeded 800 tokens ({c.token_count})"
            assert "Context: [" in c.content
            assert len(c.content.strip()) > 0

    def test_oversized_pdf_page_never_exceeds_ceiling(self, sample_pdf_metadata):
        """Verifies that an oversized physical PDF page splits without exceeding max_tokens."""
        chunker = StructureAwareChunker(max_tokens=800, min_tokens=50)

        # 2,500 token single physical page
        huge_text = (
            "The pg_stat_activity view displays information related to the current activity of all server processes. "
            "Each connection is represented by a single row showing query start time, transaction state, wait events, "
            "application name, and client socket address. "
        ) * 70

        huge_page = DocumentSection(
            section_index=0,
            page_or_section="Page 842",
            content=huge_text,
            page_number=842,
        )

        doc = ParsedDocument(metadata=sample_pdf_metadata, sections=[huge_page])
        chunks = chunker.chunk(doc)

        assert len(chunks) >= 3
        for c in chunks:
            assert c.token_count <= 800
            assert c.metadata["page_number"] == 842
            assert "Page 842" in c.page_or_section

    def test_deterministic_output_across_repeated_runs(self, sample_md_metadata):
        """Verifies that repeated runs produce identical chunk IDs, hashes, and token counts."""
        chunker = StructureAwareChunker(max_tokens=800, min_tokens=50)

        md_text = (
            "# Write-Ahead Logging\n\n"
            "Write-Ahead Logging (WAL) is a standard approach to transaction logging.\n\n"
            "## Asynchronous Commit\n\n"
            "Asynchronous commit is an option that allows transactions to complete more quickly."
        )
        doc = ParsedDocument(metadata=sample_md_metadata, raw_text=md_text)

        run1 = chunker.chunk(doc)
        run2 = chunker.chunk(doc)

        assert len(run1) == len(run2)
        for c1, c2 in zip(run1, run2):
            assert c1.chunk_id == c2.chunk_id
            assert c1.content_hash == c2.content_hash
            assert c1.token_count == c2.token_count
            assert c1.content == c2.content

    def test_no_empty_chunks_or_tiny_unnecessary_fragments(self, sample_md_metadata):
        """Verifies that empty sections or trailing blanks never produce 0-token or empty chunks."""
        chunker = StructureAwareChunker(max_tokens=800, min_tokens=50)

        sec1 = DocumentSection(section_index=0, page_or_section="Empty Sec", content="   \n\n   ")
        sec2 = DocumentSection(section_index=1, page_or_section="Valid Sec", content="Genuine content with sufficient tokens. " * 5)
        sec3 = DocumentSection(section_index=2, page_or_section="Another Empty", content="")

        doc = ParsedDocument(metadata=sample_md_metadata, sections=[sec1, sec2, sec3])
        chunks = chunker.chunk(doc)

        assert len(chunks) == 1
        assert chunks[0].token_count >= 10
        assert chunks[0].content.strip() != ""


