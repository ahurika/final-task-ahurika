"""
Unit tests for Edge Cases, Pathological Inputs, and Cross-Strategy Parity
Location: tests/unit/test_edge_cases.py
"""

import pytest
from src.models import DocumentMetadata, DocumentSection, ParsedDocument
from src.chunkers.fixed_size import FixedSizeChunker
from src.chunkers.structure_aware import StructureAwareChunker


@pytest.fixture
def synthetic_metadata():
    return DocumentMetadata(
        document_id="doc-synthetic-edge-01",
        source_filename="edge_cases.md",
        file_type="markdown",
        source_url="https://example.com/edge_cases.md",
        document_date="2024-02-08",
        title="Edge Cases and Security Fixtures",
        topic="Edge Cases",
        license="PostgreSQL License",
    )


class TestEdgeCasesAndSafety:
    """Rigorous tests covering extreme inputs, boundary conditions, and cross-strategy consistency."""

    def test_text_with_no_whitespace(self, synthetic_metadata):
        # 3,200 characters without any whitespace or punctuation
        long_string = "X" * 3200
        doc = ParsedDocument(metadata=synthetic_metadata, raw_text=long_string)

        fixed_chunker = FixedSizeChunker(target_tokens=50, overlap_tokens=10)
        chunks_fixed = fixed_chunker.chunk(doc)
        assert len(chunks_fixed) > 1
        for c in chunks_fixed:
            assert c.token_count <= 50
            assert len(c.chunk_id) == 36

        struct_chunker = StructureAwareChunker(max_tokens=50, min_tokens=10, overlap_tokens=5)
        chunks_struct = struct_chunker.chunk(doc)
        assert len(chunks_struct) > 1
        for c in chunks_struct:
            assert len(c.chunk_id) == 36

    def test_giant_code_block_exceeding_max_tokens(self, synthetic_metadata):
        # Generate an oversized code block (100 lines of SQL query)
        sql_lines = ["```sql"]
        for i in range(120):
            sql_lines.append(f"    SELECT id, name, val_{i} FROM metric_table_{i} WHERE status = 'active';")
        sql_lines.append("```")
        giant_code = "\n".join(sql_lines)

        doc = ParsedDocument(metadata=synthetic_metadata, raw_text=f"# Giant Query\n\n{giant_code}")
        struct_chunker = StructureAwareChunker(max_tokens=200, min_tokens=50, overlap_tokens=20)

        chunks = struct_chunker.chunk(doc)
        # Giant code block must be safely split without entering infinite loops
        assert len(chunks) > 1
        for c in chunks:
            assert len(c.chunk_id) == 36
            assert c.content_hash is not None

    def test_crlf_and_lf_produce_identical_hashes(self, synthetic_metadata):
        lf_text = "# Title\n\nFirst paragraph.\n\nSecond paragraph."
        crlf_text = "# Title\r\n\r\nFirst paragraph.\r\n\r\nSecond paragraph."

        doc_lf = ParsedDocument(metadata=synthetic_metadata, raw_text=lf_text)
        doc_crlf = ParsedDocument(metadata=synthetic_metadata, raw_text=crlf_text)

        fixed_chunker = FixedSizeChunker(target_tokens=500, overlap_tokens=50)
        chunks_lf = fixed_chunker.chunk(doc_lf)
        chunks_crlf = fixed_chunker.chunk(doc_crlf)

        assert len(chunks_lf) == len(chunks_crlf) == 1
        assert chunks_lf[0].content_hash == chunks_crlf[0].content_hash
        assert chunks_lf[0].chunk_id == chunks_crlf[0].chunk_id

    def test_unicode_combining_characters_parity(self, synthetic_metadata):
        # Decomposed vs precomposed Unicode text
        decomposed = "# De\u0301tails\n\nContenu avec cafe\u0301 et re\u0301sume\u0301."
        precomposed = "# Détails\n\nContenu avec café et résumé."

        doc_decomp = ParsedDocument(metadata=synthetic_metadata, raw_text=decomposed)
        doc_precomp = ParsedDocument(metadata=synthetic_metadata, raw_text=precomposed)

        chunker = FixedSizeChunker(target_tokens=500, overlap_tokens=50)
        chunks_decomp = chunker.chunk(doc_decomp)
        chunks_precomp = chunker.chunk(doc_precomp)

        assert chunks_decomp[0].content_hash == chunks_precomp[0].content_hash
        assert chunks_decomp[0].chunk_id == chunks_precomp[0].chunk_id

    def test_both_strategies_preserve_source_metadata(self, synthetic_metadata):
        # A synthetic PostgreSQL document with sections
        sec1 = DocumentSection(
            section_index=0,
            page_or_section="Section: WAL Configuration",
            content="Write-Ahead Logging (WAL) is a standard method for ensuring data integrity.",
            heading_path=["PostgreSQL 16", "Server Configuration", "WAL"],
        )
        sec2 = DocumentSection(
            section_index=1,
            page_or_section="Section: Checkpoints",
            content="Checkpoints are points in the sequence of transactions at which the data files have been updated.",
            heading_path=["PostgreSQL 16", "Server Configuration", "Checkpoints"],
        )

        doc = ParsedDocument(metadata=synthetic_metadata, sections=[sec1, sec2])

        fixed_chunker = FixedSizeChunker(target_tokens=500, overlap_tokens=50)
        struct_chunker = StructureAwareChunker(max_tokens=800, min_tokens=50)

        chunks_fixed = fixed_chunker.chunk(doc)
        chunks_struct = struct_chunker.chunk(doc)

        assert len(chunks_fixed) >= 1
        assert len(chunks_struct) >= 1

        for c in chunks_fixed + chunks_struct:
            # Metadata integrity check
            assert c.document_id == synthetic_metadata.document_id
            assert c.source_filename == synthetic_metadata.source_filename
            assert c.source_url == synthetic_metadata.source_url
            assert c.document_date == synthetic_metadata.document_date
            assert len(c.content_hash) == 64
            assert len(c.chunk_id) == 36
            # Dictionary serialization works cleanly
            d = c.to_dict()
            assert d["chunk_id"] == c.chunk_id
            assert d["content_hash"] == c.content_hash
