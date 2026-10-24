"""
Core Data Models for RAG Pipeline
Location: src/models.py
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass(frozen=True)
class DocumentMetadata:
    """Provenance and identity metadata for an ingested source document."""
    document_id: str
    source_filename: str
    file_type: str  # 'pdf' or 'markdown'
    source_url: Optional[str] = None
    document_date: Optional[str] = None
    title: Optional[str] = None
    topic: Optional[str] = None
    license: Optional[str] = "PostgreSQL License"


@dataclass
class DocumentSection:
    """A structural segment extracted by a parser (e.g., Markdown section or PDF page)."""
    section_index: int
    page_or_section: str
    content: str
    heading_path: List[str] = field(default_factory=list)
    page_number: Optional[int] = None


@dataclass
class ParsedDocument:
    """Full representation of a parsed document ready for chunking."""
    metadata: DocumentMetadata
    sections: List[DocumentSection] = field(default_factory=list)
    raw_text: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.raw_text.strip() and not any(s.content.strip() for s in self.sections)


@dataclass(frozen=True)
class Chunk:
    """A discrete contextual chunk ready for embedding and retrieval."""
    chunk_id: str
    document_id: str
    strategy: str  # 'fixed_window' or 'structure_aware'
    chunk_index: int
    source_filename: str
    page_or_section: str
    content: str
    content_hash: str
    token_count: int
    source_url: Optional[str] = None
    document_date: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert chunk to a dictionary suitable for database serialization."""
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "strategy": self.strategy,
            "chunk_index": self.chunk_index,
            "source_filename": self.source_filename,
            "source_url": self.source_url,
            "page_or_section": self.page_or_section,
            "document_date": self.document_date,
            "content": self.content,
            "content_hash": self.content_hash,
            "token_count": self.token_count,
            "metadata": self.metadata,
        }
