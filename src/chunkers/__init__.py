"""
Chunkers Package for PostgreSQL Documentation Assistant
Location: src/chunkers/__init__.py
"""

from src.chunkers.base import (
    BaseChunker,
    normalize_text,
    compute_content_hash,
    generate_chunk_id,
    RAG_CHUNK_NAMESPACE,
)
from src.chunkers.fixed_size import FixedSizeChunker
from src.chunkers.structure_aware import StructureAwareChunker, parse_markdown_sections

__all__ = [
    "BaseChunker",
    "FixedSizeChunker",
    "StructureAwareChunker",
    "parse_markdown_sections",
    "normalize_text",
    "compute_content_hash",
    "generate_chunk_id",
    "RAG_CHUNK_NAMESPACE",
]
