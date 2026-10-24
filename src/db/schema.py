"""
Database Schema Constants & Metadata Specifications
Location: src/db/schema.py
"""

from typing import Dict, List, Any

# Primary Table Identifiers
TABLE_INGESTION_JOBS = "ingestion_jobs"
TABLE_DOCUMENTS = "documents"
TABLE_CHUNKS = "chunks"
TABLE_EMBEDDING_CACHE = "embedding_cache"
TABLE_QUERY_TELEMETRY = "query_telemetry"

ALL_TABLES = [
    TABLE_INGESTION_JOBS,
    TABLE_DOCUMENTS,
    TABLE_CHUNKS,
    TABLE_EMBEDDING_CACHE,
    TABLE_QUERY_TELEMETRY,
]

# Supported Strategies
STRATEGY_FIXED_WINDOW = "fixed_window"
STRATEGY_STRUCTURE_AWARE = "structure_aware"
ALL_STRATEGIES = [STRATEGY_FIXED_WINDOW, STRATEGY_STRUCTURE_AWARE]

# Supported Job Statuses
JOB_STATUS_QUEUED = "queued"
JOB_STATUS_PROCESSING = "processing"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_CANCELLED = "cancelled"
ALL_JOB_STATUSES = [
    JOB_STATUS_QUEUED,
    JOB_STATUS_PROCESSING,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_CANCELLED,
]

# Supported File Types
FILE_TYPE_PDF = "pdf"
FILE_TYPE_MARKDOWN = "markdown"
ALL_FILE_TYPES = [FILE_TYPE_PDF, FILE_TYPE_MARKDOWN]

# Parser Statuses
PARSER_STATUS_PENDING = "pending"
PARSER_STATUS_PARSED = "parsed"
PARSER_STATUS_FAILED = "failed"
PARSER_STATUS_SKIPPED = "skipped"
ALL_PARSER_STATUSES = [
    PARSER_STATUS_PENDING,
    PARSER_STATUS_PARSED,
    PARSER_STATUS_FAILED,
    PARSER_STATUS_SKIPPED,
]

# Provisional Vector Dimension & Model (Subject to live verification in Phase 3E)
# Active Google candidates: gemini-embedding-001, gemini-embedding-2. (text-embedding-004 is shut down).
DEFAULT_EMBEDDING_MODEL = "gemini-embedding-001"
DEFAULT_EMBEDDING_DIMENSION = 768

# pgvector Dimension Constraints
MIN_VECTOR_DIMENSION = 1
MAX_HNSW_VECTOR_DIMENSION = 2000  # pgvector HNSW indexing limit

# HNSW Vector Index Configuration
HNSW_INDEX_NAME = "idx_chunks_embedding_hnsw_cosine"
HNSW_DISTANCE_METRIC = "vector_cosine_ops"
HNSW_M = 16
HNSW_EF_CONSTRUCTION = 64
HNSW_QUERY_EF_SEARCH = 40

# Schema Structural Definition (for offline contract testing)
SCHEMA_TABLE_COLUMNS: Dict[str, List[str]] = {
    TABLE_INGESTION_JOBS: [
        "id",
        "strategy",
        "status",
        "total_documents",
        "processed_documents",
        "total_chunks_created",
        "embeddings_generated",
        "cache_hits",
        "current_document",
        "error_details",
        "started_at",
        "completed_at",
        "created_at",
        "updated_at",
    ],
    TABLE_DOCUMENTS: [
        "id",
        "source_id",
        "source_filename",
        "source_url",
        "file_type",
        "content_hash",
        "total_pages",
        "total_words",
        "total_sections",
        "parser_status",
        "document_date",
        "metadata",
        "created_at",
        "updated_at",
    ],
    TABLE_CHUNKS: [
        "id",
        "document_id",
        "strategy",
        "chunk_index",
        "source_filename",
        "source_url",
        "page_or_section",
        "document_date",
        "content",
        "content_hash",
        "token_count",
        "metadata",
        "created_at",
    ],
    TABLE_EMBEDDING_CACHE: [
        "content_hash",
        "model_name",
        "model_version",
        "dimensions",
        "created_at",
    ],
    TABLE_QUERY_TELEMETRY: [
        "id",
        "query_text",
        "chunking_strategy",
        "retrieval_k",
        "retrieved_chunk_ids",
        "similarity_scores",
        "top_similarity_score",
        "refusal_triggered",
        "retrieval_latency_ms",
        "llm_latency_ms",
        "total_latency_ms",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "estimated_cost_usd",
        "client_ip",
        "created_at",
    ],
}
