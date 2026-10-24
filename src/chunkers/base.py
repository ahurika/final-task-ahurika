"""
Base Chunker Abstractions and Hashing Utilities
Location: src/chunkers/base.py
"""

import hashlib
import re
import unicodedata
import uuid
from abc import ABC, abstractmethod
from typing import List

from src.models import Chunk, ParsedDocument
from src.tokenizers import BaseTokenizer, DeterministicTestTokenizer

# Deterministic namespace for RAG chunk UUIDs
RAG_CHUNK_NAMESPACE = uuid.UUID("6ba7b810-9dad-11d1-80b4-00c04fd430c8")


def normalize_text(text: str) -> str:
    """
    Deterministically normalizes text before chunking and hashing.
    1. Normalizes Unicode to NFC form.
    2. Converts Windows (\r\n) and legacy Mac (\r) line breaks to standard Unix (\n).
    3. Normalizes excessive horizontal whitespace.
    4. Strips leading and trailing whitespace.
    """
    if not text:
        return ""
    # Unicode NFC normalization
    text = unicodedata.normalize("NFC", text)
    # Line break normalization
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse 3 or more newlines into double newlines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def compute_content_hash(text: str) -> str:
    """Computes a cryptographic SHA-256 hash over normalized UTF-8 text."""
    normalized = normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def generate_chunk_id(document_id: str, strategy: str, chunk_index: int, content_hash: str) -> str:
    """
    Generates a deterministic UUIDv5 for a chunk based on its document, strategy,
    sequential index, and content hash. Guarantees idempotent IDs across runs.
    """
    unique_key = f"{document_id}::{strategy}::{chunk_index}::{content_hash}"
    return str(uuid.uuid5(RAG_CHUNK_NAMESPACE, unique_key))


class BaseChunker(ABC):
    """Abstract base class for all chunking strategy implementations."""

    def __init__(self, tokenizer: BaseTokenizer = None):
        self.tokenizer = tokenizer or DeterministicTestTokenizer()

    @property
    @abstractmethod
    def strategy_name(self) -> str:
        """Return the unique string identifier for this chunking strategy."""
        pass

    @abstractmethod
    def chunk(self, document: ParsedDocument) -> List[Chunk]:
        """Process a parsed document into a list of structured, traceable chunks."""
        pass

    def build_chunk(
        self,
        document: ParsedDocument,
        chunk_index: int,
        content: str,
        page_or_section: str,
        extra_metadata: dict = None,
    ) -> Chunk:
        """Helper factory to assemble a Chunk with normalized content, hash, and deterministic UUID."""
        normalized_content = normalize_text(content)
        content_hash = compute_content_hash(normalized_content)
        chunk_id = generate_chunk_id(
            document_id=document.metadata.document_id,
            strategy=self.strategy_name,
            chunk_index=chunk_index,
            content_hash=content_hash,
        )
        token_count = self.tokenizer.count_tokens(normalized_content)

        meta = {
            "title": document.metadata.title,
            "topic": document.metadata.topic,
            "file_type": document.metadata.file_type,
            "license": document.metadata.license,
        }
        if extra_metadata:
            meta.update(extra_metadata)

        return Chunk(
            chunk_id=chunk_id,
            document_id=document.metadata.document_id,
            strategy=self.strategy_name,
            chunk_index=chunk_index,
            source_filename=document.metadata.source_filename,
            source_url=document.metadata.source_url,
            page_or_section=page_or_section,
            document_date=document.metadata.document_date,
            content=normalized_content,
            content_hash=content_hash,
            token_count=token_count,
            metadata=meta,
        )
