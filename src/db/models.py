"""
Database Domain Record Models
Location: src/db/models.py

Typed dataclass definitions corresponding to the PostgreSQL tables defined in migrations:
- documents
- chunks
- embedding_cache
- ingestion_jobs
- query_telemetry
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any
import uuid


@dataclass
class DocumentRecord:
    """Represents a row in the documents table."""
    source_id: str
    source_filename: str
    file_type: str  # 'pdf' or 'markdown'
    content_hash: str
    id: Optional[str] = None
    source_url: Optional[str] = None
    total_pages: Optional[int] = None
    total_words: Optional[int] = None
    total_sections: Optional[int] = None
    parser_status: str = "pending"  # 'pending', 'parsed', 'failed', 'skipped'
    document_date: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        if self.id is None:
            self.id = str(uuid.uuid4())
        if self.file_type not in ("pdf", "markdown"):
            raise ValueError(f"Invalid file_type: {self.file_type}. Must be 'pdf' or 'markdown'.")
        if self.parser_status not in ("pending", "parsed", "failed", "skipped"):
            raise ValueError(f"Invalid parser_status: {self.parser_status}.")
        if len(self.content_hash) != 64:
            raise ValueError(f"content_hash must be a 64-character SHA-256 hex string, got {len(self.content_hash)}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "source_filename": self.source_filename,
            "source_url": self.source_url,
            "file_type": self.file_type,
            "content_hash": self.content_hash,
            "total_pages": self.total_pages,
            "total_words": self.total_words,
            "total_sections": self.total_sections,
            "parser_status": self.parser_status,
            "document_date": self.document_date,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class ChunkRecord:
    """Represents a row in the chunks table."""
    id: str  # Deterministic UUIDv5
    document_id: str  # Foreign key to documents.id
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
    embedding: Optional[List[float]] = None
    created_at: Optional[datetime] = None

    def __post_init__(self):
        if self.strategy not in ("fixed_window", "structure_aware"):
            raise ValueError(f"Invalid strategy: {self.strategy}. Must be 'fixed_window' or 'structure_aware'.")
        if self.chunk_index < 0:
            raise ValueError(f"chunk_index must be non-negative, got {self.chunk_index}")
        if self.token_count <= 0:
            raise ValueError(f"token_count must be positive, got {self.token_count}")
        if len(self.content_hash) != 64:
            raise ValueError(f"content_hash must be a 64-character SHA-256 hex string, got {len(self.content_hash)}")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
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
            "embedding": self.embedding,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class EmbeddingCacheRecord:
    """Represents a row in the embedding_cache table."""
    content_hash: str
    model_name: str
    dimensions: int
    embedding: Optional[List[float]] = None
    model_version: Optional[str] = None
    created_at: Optional[datetime] = None

    def __post_init__(self):
        if len(self.content_hash) != 64:
            raise ValueError(f"content_hash must be 64 characters, got {len(self.content_hash)}")
        if self.dimensions <= 0:
            raise ValueError(f"dimensions must be positive, got {self.dimensions}")
        if not self.model_name.strip():
            raise ValueError("model_name cannot be empty")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "content_hash": self.content_hash,
            "model_name": self.model_name,
            "model_version": self.model_version,
            "dimensions": self.dimensions,
            "embedding": self.embedding,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class IngestionJobRecord:
    """Represents a row in the ingestion_jobs table."""
    strategy: str  # 'fixed_window', 'structure_aware', or 'both'
    id: Optional[str] = None
    status: str = "queued"  # 'queued', 'processing', 'completed', 'failed', 'cancelled'
    total_documents: int = 0
    processed_documents: int = 0
    total_chunks_created: int = 0
    embeddings_generated: int = 0
    cache_hits: int = 0
    current_document: Optional[str] = None
    error_details: List[Dict[str, Any]] = field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def __post_init__(self):
        if self.id is None:
            self.id = str(uuid.uuid4())
        if self.strategy not in ("fixed_window", "structure_aware", "both"):
            raise ValueError(f"Invalid strategy: {self.strategy}.")
        if self.status not in ("queued", "processing", "completed", "failed", "cancelled"):
            raise ValueError(f"Invalid status: {self.status}.")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "strategy": self.strategy,
            "status": self.status,
            "total_documents": self.total_documents,
            "processed_documents": self.processed_documents,
            "total_chunks_created": self.total_chunks_created,
            "embeddings_generated": self.embeddings_generated,
            "cache_hits": self.cache_hits,
            "current_document": self.current_document,
            "error_details": self.error_details,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class QueryTelemetryRecord:
    """Represents a row in the query_telemetry table."""
    query_text: str
    chunking_strategy: str
    retrieval_k: int
    top_similarity_score: float
    retrieval_latency_ms: int
    llm_latency_ms: int
    total_latency_ms: int
    id: Optional[str] = None
    retrieved_chunk_ids: List[str] = field(default_factory=list)
    similarity_scores: List[float] = field(default_factory=list)
    refusal_triggered: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    client_ip: Optional[str] = None
    created_at: Optional[datetime] = None

    def __post_init__(self):
        if self.id is None:
            self.id = str(uuid.uuid4())
        if self.chunking_strategy not in ("fixed_window", "structure_aware"):
            raise ValueError(f"Invalid chunking_strategy: {self.chunking_strategy}")
        if self.retrieval_k <= 0:
            raise ValueError(f"retrieval_k must be positive, got {self.retrieval_k}")
        if any(lat < 0 for lat in (self.retrieval_latency_ms, self.llm_latency_ms, self.total_latency_ms)):
            raise ValueError("Latencies cannot be negative")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "query_text": self.query_text,
            "chunking_strategy": self.chunking_strategy,
            "retrieval_k": self.retrieval_k,
            "retrieved_chunk_ids": self.retrieved_chunk_ids,
            "similarity_scores": self.similarity_scores,
            "top_similarity_score": self.top_similarity_score,
            "refusal_triggered": self.refusal_triggered,
            "retrieval_latency_ms": self.retrieval_latency_ms,
            "llm_latency_ms": self.llm_latency_ms,
            "total_latency_ms": self.total_latency_ms,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "client_ip": self.client_ip,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
