"""
Tokenizer Abstractions and Implementations
Location: src/tokenizers.py

NOTICE:
The DeterministicTestTokenizer implemented here is a deterministic offline tokenizer
designed for reproducible local chunking and unit testing. It provides lossless
tokenization and detokenization. It is an engineering abstraction for local testing
and does NOT claim to match Google Gemini's proprietary online tokenizer.
"""

import re
import unicodedata
from abc import ABC, abstractmethod
from typing import List


class BaseTokenizer(ABC):
    """Abstract base class for all tokenizers used in chunking."""

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Return the number of tokens in the given text."""
        pass

    @abstractmethod
    def tokenize(self, text: str) -> List[str]:
        """Convert a text string into a list of token strings."""
        pass

    @abstractmethod
    def detokenize(self, tokens: List[str]) -> str:
        """Reconstruct the original text string from a list of tokens."""
        pass

    def slice_tokens(self, text: str, start_token: int, end_token: int) -> str:
        """Extract a substring corresponding to the specified token range [start_token, end_token)."""
        tokens = self.tokenize(text)
        if start_token >= len(tokens):
            return ""
        sliced = tokens[start_token:end_token]
        return self.detokenize(sliced)


class DeterministicTestTokenizer(BaseTokenizer):
    """
    A deterministic, offline sub-word/word regex tokenizer for local testing.

    Splits text on word boundaries, individual punctuation marks, and whitespace runs.
    Ensures exact lossless round-tripping: detokenize(tokenize(text)) == text.
    Handles continuous non-whitespace text, Unicode characters, and code listings.
    """

    # Matches word characters (up to 32 chars per sub-word token), non-word/non-whitespace characters, or whitespace runs
    TOKEN_REGEX = re.compile(r"\w{1,32}|[^\w\s]|\s+", re.UNICODE)

    def __init__(self, name: str = "DeterministicTestTokenizer (Offline Regex)"):
        self.name = name

    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        return len(self.tokenize(text))

    def tokenize(self, text: str) -> List[str]:
        if not text:
            return []
        # Normalize Unicode to NFC
        normalized = unicodedata.normalize("NFC", text)
        return self.TOKEN_REGEX.findall(normalized)

    def detokenize(self, tokens: List[str]) -> str:
        if not tokens:
            return ""
        return "".join(tokens)


class SimpleWhitespaceTokenizer(BaseTokenizer):
    """
    Fallback whitespace-delimited tokenizer for comparative testing.
    Treats each whitespace-separated token as 1 token.
    """

    def count_tokens(self, text: str) -> int:
        if not text.strip():
            return 0
        return len(text.split())

    def tokenize(self, text: str) -> List[str]:
        if not text.strip():
            return []
        return text.split()

    def detokenize(self, tokens: List[str]) -> str:
        return " ".join(tokens)
