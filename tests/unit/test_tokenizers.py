"""
Unit tests for Tokenizer Abstractions and Implementations
Location: tests/unit/test_tokenizers.py
"""

import pytest
from src.tokenizers import DeterministicTestTokenizer, SimpleWhitespaceTokenizer


class TestDeterministicTestTokenizer:
    """Tests verifying the deterministic local test tokenizer."""

    @pytest.fixture
    def tokenizer(self):
        return DeterministicTestTokenizer()

    def test_empty_and_whitespace_input(self, tokenizer):
        assert tokenizer.count_tokens("") == 0
        assert tokenizer.tokenize("") == []
        assert tokenizer.detokenize([]) == ""

        # Whitespace runs count as whitespace tokens
        ws = "   \n\t   "
        tokens = tokenizer.tokenize(ws)
        assert len(tokens) >= 1
        assert tokenizer.detokenize(tokens) == ws

    def test_lossless_roundtrip_prose(self, tokenizer):
        text = "PostgreSQL is a powerful, open-source object-relational database system."
        tokens = tokenizer.tokenize(text)
        assert len(tokens) > 5
        assert tokenizer.detokenize(tokens) == text

    def test_lossless_roundtrip_code(self, tokenizer):
        sql = (
            "CREATE TABLE employees (\n"
            "    id SERIAL PRIMARY KEY,\n"
            "    name VARCHAR(100) NOT NULL,\n"
            "    salary NUMERIC(10, 2) CHECK (salary > 0)\n"
            ");"
        )
        tokens = tokenizer.tokenize(sql)
        assert len(tokens) > 10
        assert tokenizer.detokenize(tokens) == sql

    def test_lossless_roundtrip_unicode(self, tokenizer):
        unicode_samples = [
            "PostgreSQL 16 は優れたデータベースです。",  # Japanese
            "Hällo Wörld, Grüße aus München!",  # German umlauts
            "Système de gestion de base de données relationnelle.",  # French accents
            "База данных PostgreSQL с открытым исходным кодом.",  # Russian Cyrillic
            "PostgreSQL 🐘 is awesome! 🚀",  # Emojis
        ]
        for sample in unicode_samples:
            tokens = tokenizer.tokenize(sample)
            assert len(tokens) > 0
            # Tokenizer normalizes to NFC on tokenize
            import unicodedata
            expected = unicodedata.normalize("NFC", sample)
            assert tokenizer.detokenize(tokens) == expected

    def test_continuous_text_without_whitespace(self, tokenizer):
        # 320 consecutive characters without whitespace or punctuation
        long_word = "A" * 320
        tokens = tokenizer.tokenize(long_word)
        # Should be split into 10 tokens of 32 characters each
        assert len(tokens) == 10
        assert all(len(t) == 32 for t in tokens)
        assert tokenizer.detokenize(tokens) == long_word

    def test_slice_tokens(self, tokenizer):
        text = "One two three four five six seven eight nine ten."
        # Slicing first 3 tokens
        sliced = tokenizer.slice_tokens(text, 0, 3)
        assert len(sliced) > 0
        assert text.startswith(sliced)


class TestSimpleWhitespaceTokenizer:
    """Tests verifying the baseline whitespace tokenizer."""

    @pytest.fixture
    def tokenizer(self):
        return SimpleWhitespaceTokenizer()

    def test_count_and_tokenize(self, tokenizer):
        text = "PostgreSQL 16 High Availability Configuration"
        assert tokenizer.count_tokens(text) == 5
        assert tokenizer.tokenize(text) == ["PostgreSQL", "16", "High", "Availability", "Configuration"]
        assert tokenizer.detokenize(tokenizer.tokenize(text)) == text

    def test_empty_string(self, tokenizer):
        assert tokenizer.count_tokens("") == 0
        assert tokenizer.count_tokens("   ") == 0
        assert tokenizer.tokenize("") == []
