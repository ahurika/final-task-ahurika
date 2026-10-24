"""
Unit tests for Strategy A: Fixed-Size Sliding Window Chunker
Location: tests/unit/test_fixed_size_chunker.py
"""

import pytest
from src.models import DocumentMetadata, DocumentSection, ParsedDocument
from src.chunkers.fixed_size import FixedSizeChunker
from src.tokenizers import SimpleWhitespaceTokenizer, DeterministicTestTokenizer


@pytest.fixture
def sample_metadata():
    return DocumentMetadata(
        document_id="doc-postgres-01",
        source_filename="high-availability.md",
        file_type="markdown",
        source_url="https://www.postgresql.org/docs/16/high-availability.html",
        document_date="2024-02-08",
        title="High Availability, Load Balancing, and Replication",
        topic="High Availability",
        license="PostgreSQL License",
    )


class TestFixedSizeChunker:
    """Verifies all operational requirements of Strategy A: Fixed-Size Sliding Window."""

    def test_constructor_validation(self):
        with pytest.raises(ValueError, match="target_tokens must be greater than 0"):
            FixedSizeChunker(target_tokens=0)

        with pytest.raises(ValueError, match="overlap_tokens cannot be negative"):
            FixedSizeChunker(target_tokens=500, overlap_tokens=-5)

        with pytest.raises(ValueError, match="strictly less than target_tokens"):
            FixedSizeChunker(target_tokens=500, overlap_tokens=500)

        with pytest.raises(ValueError, match="strictly less than target_tokens"):
            FixedSizeChunker(target_tokens=500, overlap_tokens=600)

    def test_empty_and_whitespace_document(self, sample_metadata):
        chunker = FixedSizeChunker(target_tokens=500, overlap_tokens=50)

        doc_empty = ParsedDocument(metadata=sample_metadata, raw_text="")
        assert chunker.chunk(doc_empty) == []

        doc_ws = ParsedDocument(metadata=sample_metadata, raw_text="   \n\n\t   ")
        assert chunker.chunk(doc_ws) == []

    def test_short_document_produces_single_chunk(self, sample_metadata):
        chunker = FixedSizeChunker(target_tokens=500, overlap_tokens=50)
        short_text = "PostgreSQL is an open-source object-relational database system."
        doc = ParsedDocument(metadata=sample_metadata, raw_text=short_text)

        chunks = chunker.chunk(doc)
        assert len(chunks) == 1
        c = chunks[0]
        assert c.chunk_index == 0
        assert c.strategy == "fixed_window"
        assert c.source_filename == sample_metadata.source_filename
        assert c.source_url == sample_metadata.source_url
        assert c.document_date == sample_metadata.document_date
        assert c.token_count > 0
        assert c.token_count < 500
        assert c.content == short_text

    def test_target_size_and_overlap(self, sample_metadata):
        tokenizer = DeterministicTestTokenizer()
        chunker = FixedSizeChunker(target_tokens=500, overlap_tokens=50, tokenizer=tokenizer)

        # Generate a text of exactly 1,200 tokens
        # Each word + space = 2 tokens. 600 words = 1,200 tokens.
        words = [f"word{i}" for i in range(600)]
        long_text = " ".join(words)
        doc = ParsedDocument(metadata=sample_metadata, raw_text=long_text)

        chunks = chunker.chunk(doc)
        # Total tokens = 1199 (600 words + 599 spaces).
        # Chunk 0: tokens 0..500
        # Chunk 1: tokens 450..950 (step 450)
        # Chunk 2: tokens 900..1199 (step 450)
        assert len(chunks) == 3

        # Chunk indices must be strictly sequential
        assert [c.chunk_index for c in chunks] == [0, 1, 2]

        # Verify token counts
        assert chunks[0].token_count <= 500
        assert chunks[1].token_count <= 500

        # Verify overlap: Chunk 0's trailing content overlaps with Chunk 1's leading content
        words_0 = chunks[0].content.split()
        words_1 = chunks[1].content.split()
        
        # Overlap window: exactly 25 words (word225 to word249), which corresponds to 50 tokens
        overlap_len = 25
        assert words_0[-overlap_len:] == words_1[:overlap_len]
        assert words_1[0] == "word225"
        assert words_0[-1] == "word249"

    def test_provenance_preservation_across_sections(self, sample_metadata):
        chunker = FixedSizeChunker(target_tokens=100, overlap_tokens=10)

        # 3 sections, each representing a PDF page
        sec1 = DocumentSection(section_index=0, page_or_section="Page 1", content="Text on page one " * 15, page_number=1)
        sec2 = DocumentSection(section_index=1, page_or_section="Page 2", content="Text on page two " * 15, page_number=2)
        sec3 = DocumentSection(section_index=2, page_or_section="Page 3", content="Text on page three " * 15, page_number=3)

        doc = ParsedDocument(metadata=sample_metadata, sections=[sec1, sec2, sec3])
        chunks = chunker.chunk(doc)

        assert len(chunks) > 1
        for c in chunks:
            # Source metadata must be fully preserved
            assert c.source_filename == "high-availability.md"
            assert c.document_id == "doc-postgres-01"
            assert "page_number" in c.metadata or "page_numbers" in c.metadata
            assert c.page_or_section.startswith("Page")

    def test_replaceable_tokenizer(self, sample_metadata):
        # Use SimpleWhitespaceTokenizer instead of DeterministicTestTokenizer
        ws_tokenizer = SimpleWhitespaceTokenizer()
        chunker = FixedSizeChunker(target_tokens=50, overlap_tokens=10, tokenizer=ws_tokenizer)

        # 100 whitespace-separated words
        text = " ".join([f"token_{i}" for i in range(100)])
        doc = ParsedDocument(metadata=sample_metadata, raw_text=text)

        chunks = chunker.chunk(doc)
        assert len(chunks) == 3
        # Step size = 50 - 10 = 40.
        # Chunk 0: 0..50
        # Chunk 1: 40..90
        # Chunk 2: 80..100
        assert chunks[0].token_count == 50
        assert chunks[1].token_count == 50
        assert chunks[2].token_count == 20

    def test_no_infinite_loops_large_input(self, sample_metadata):
        chunker = FixedSizeChunker(target_tokens=500, overlap_tokens=50)
        # Large continuous text of 10,000 words
        large_text = "PostgreSQL database administration guide section. " * 2000
        doc = ParsedDocument(metadata=sample_metadata, raw_text=large_text)

        chunks = chunker.chunk(doc)
        assert len(chunks) > 10
        # Verify monotonically increasing chunk indices
        for i, c in enumerate(chunks):
            assert c.chunk_index == i
            assert len(c.chunk_id) == 36  # UUID length
