"""
Unit tests for Text Normalization, Hashing, and Identity Generation
Location: tests/unit/test_hasher.py
"""

import uuid
from src.chunkers.base import (
    normalize_text,
    compute_content_hash,
    generate_chunk_id,
    RAG_CHUNK_NAMESPACE,
)


class TestNormalizationAndHashing:
    """Verifies that text normalization, SHA-256 hashing, and UUIDv5 chunk IDs are deterministic and stable."""

    def test_normalize_text_empty_and_whitespace(self):
        assert normalize_text("") == ""
        assert normalize_text("   \n\t\r   ") == ""

    def test_normalize_line_breaks(self):
        crlf_text = "Line 1\r\nLine 2\r\n\r\nLine 3"
        mac_text = "Line 1\rLine 2\r\rLine 3"
        unix_text = "Line 1\nLine 2\n\nLine 3"

        assert normalize_text(crlf_text) == unix_text
        assert normalize_text(mac_text) == unix_text

    def test_normalize_excessive_newlines(self):
        text = "Paragraph 1\n\n\n\n\n\nParagraph 2"
        normalized = normalize_text(text)
        assert normalized == "Paragraph 1\n\nParagraph 2"

    def test_unicode_nfc_normalization(self):
        # Combining diacritic: 'e' + combining acute accent (U+0301) vs precomposed 'é' (U+00E9)
        decomposed = "e\u0301cole"
        precomposed = "\u00e9cole"
        assert decomposed != precomposed  # Different raw byte representations
        assert normalize_text(decomposed) == normalize_text(precomposed)  # Both normalized to NFC

    def test_compute_content_hash_stability(self):
        text_a = "SELECT * FROM pg_stat_activity WHERE state = 'active';"
        text_b = "   SELECT * FROM pg_stat_activity WHERE state = 'active';\r\n  "

        hash_a = compute_content_hash(text_a)
        hash_b = compute_content_hash(text_b)

        # Because normalize_text strips leading/trailing spaces and CRLF, hashes must match
        assert hash_a == hash_b
        assert len(hash_a) == 64
        # Verify hex format
        int(hash_a, 16)

    def test_different_content_produces_different_hash(self):
        hash1 = compute_content_hash("SELECT * FROM table1;")
        hash2 = compute_content_hash("SELECT * FROM table2;")
        assert hash1 != hash2

    def test_generate_chunk_id_determinism(self):
        doc_id = "doc-pg16-001"
        strategy = "fixed_window"
        chunk_idx = 0
        content_hash = compute_content_hash("Sample chunk content for testing.")

        id_1 = generate_chunk_id(doc_id, strategy, chunk_idx, content_hash)
        id_2 = generate_chunk_id(doc_id, strategy, chunk_idx, content_hash)

        # Must be identical across calls
        assert id_1 == id_2
        # Must be a valid UUID string
        parsed = uuid.UUID(id_1)
        assert parsed.version == 5

    def test_chunk_id_varies_with_index_and_strategy(self):
        doc_id = "doc-pg16-001"
        content_hash = compute_content_hash("Identical text")

        id_fixed_0 = generate_chunk_id(doc_id, "fixed_window", 0, content_hash)
        id_fixed_1 = generate_chunk_id(doc_id, "fixed_window", 1, content_hash)
        id_struct_0 = generate_chunk_id(doc_id, "structure_aware", 0, content_hash)

        assert id_fixed_0 != id_fixed_1
        assert id_fixed_0 != id_struct_0
