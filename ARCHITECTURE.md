# System Architecture Specification: PostgreSQL Documentation Assistant

> **ARCHITECTURE STATUS & GOVERNANCE**:
> - **Confirmed Assessment Requirements**: The 18 mandatory deliverables from [PRD.md](file:///c:/Users/USER/Desktop/RAG%20Task/PRD.md).
> - **Confirmed Service Selection**:
>   - **Backend Framework**: Python 3.10+ with FastAPI.
>   - **Embedding & Generation Models**: Google Gemini API (Active candidates: `gemini-embedding-001` / `gemini-embedding-2` for vectors; `text-embedding-004` is shut down by Google; `gemini-1.5-flash` for generation). Configurable embedding dimension (provisional default: 768).
>   - **Vector Store & Relational Database**: Neon Serverless PostgreSQL with `pgvector` extension.
>   - **Frontend Framework**: React with Vite.
>   - **Corpus Verified on Disk**: 1 official PostgreSQL 16 US Letter PDF (3,220 pages) + 14 official Markdown reference chapters.
> - **Implementation Status**: **OFFLINE PERSISTENCE & EMBEDDING FOUNDATION IMPLEMENTED (Phase 3E.1)**. 117 automated unit and integration tests passing. Live database connections, API keys, and cloud services remain deferred.

---

## 1. High-Level System Architecture

The PostgreSQL Documentation Assistant consists of two decoupled processing pipelines sharing a unified PostgreSQL + pgvector storage backbone:

1. **Ingestion & Indexing Pipeline (Asynchronous & Idempotent)**:
   - Discovers, hashes, and parses PDF and Markdown documents.
   - Applies selectable chunking: **Strategy A** (Fixed-Size Window) or **Strategy B** (Structure-Aware Headings).
   - Preserves complete provenance metadata (`source_url`, `page_or_section`, `document_date`, `content_hash`).
   - Checks chunk content hashes against the `embedding_cache` table to eliminate redundant Gemini API spend.
   - Batches new chunks and invokes Gemini Embeddings with exponential backoff on HTTP 429 rate limits.
   - Atomically persists vectors into the `chunks` table with an HNSW index.
   - Updates progress counters in the `ingestion_jobs` table.
2. **Query & Generation Pipeline (Synchronous)**:
   - Receives user questions via the React chat UI.
   - Enforces rate limiting (requests/min) to prevent quota exhaustion.
   - Embeds the query and queries `pgvector` using cosine distance (`<=>`) with optional metadata filters.
   - Evaluates the top chunk similarity against a calibrated refusal threshold $\theta_{\text{sim}}$.
   - Assembles prompt with strict XML context isolation (`<context><chunk>...</chunk></context>`) to neutralize prompt injection.
   - Generates grounded answers with interactive citations opening the exact database chunk.
   - Records query telemetry (latency, token costs, chunk IDs) in `query_telemetry`.

---

## 2. Ingestion Pipeline Detailed Specification

The ingestion pipeline is designed for local testability, fault tolerance, and deterministic idempotency without requiring heavyweight distributed message brokers (such as Celery or Redis).

```
[Raw Documents]
   │
   ▼
[1. Source Discovery] ──► [Filter *.pdf & *.md (Exclude security fixture)]
   │
   ▼
[2. Deduplication] ────► [File SHA-256 Match?]
   │                           ├── Yes ──► [Skip Ingestion]
   │                           └── No  ──► [Begin DB Transaction]
   ▼
[3. Parsing] ──────────► [PDF: pypdf page streams | MD: frontmatter & AST]
   │
   ▼
[4. Chunking Engine] ──► [Strategy A: 500/50 sliding | Strategy B: Heading-based]
   │
   ▼
[5. Embedding Cache] ──► [Chunk Hash in DB?]
   │                           ├── Hit  ──► [Reuse Vector (0 API calls)]
   │                           └── Miss ──► [Batch Gemini Call with Backoff]
   ▼
[6. Atomic Persistence] ► [COMMIT: Chunks & Cache | Update Ingestion Job Status]
```

### 2.1 Source Discovery & Provenance Tracking
- **Discovery Engine**: Recursively inspects `data/raw/` for supported file formats (`.pdf` and `.md`).
- **Fixture Isolation**: Explicitly ignores `data/security/` from standard ingestion runs. Security fixtures are only ingested during isolated prompt-injection evaluation runs.
- **Provenance Extraction**:
  - For Markdown (`.md`): Parses YAML frontmatter headers to extract `document_id`, `source_url`, `title`, `topic`, `retrieval_date`, and `license`.
  - For PDF (`.pdf`): Extracts document metadata from `data/corpus-manifest.json` and internal PDF catalog properties.
- **Audit Output**: Produces a standardized discovery manifest list:
  `[ { "path": "...", "file_type": "markdown", "sha256": "...", "source_url": "..." }, ... ]`

### 2.2 Content Parsing Engine
- **PDF Extraction (`pypdf`)**:
  - Iterates through page objects (`reader.pages`).
  - Normalizes text whitespace, strips form feeds, and tags extracted text with the 1-indexed physical page number (`Page 1`, `Page 2`, ...).
  - Handles blank or image-only pages gracefully without crashing.
- **Markdown Extraction**:
  - Strips frontmatter block.
  - Retains heading tokens (`#`, `##`, `###`), preserving hierarchical context paths.
  - Retains code blocks (fenced with language tags: ````sql ````, ````text ````).
  - Converts table structures into compact textual tables.

### 2.3 Chunking Strategy Specifications (REQ-04)

Both strategies are implemented as swappable interfaces (`BaseChunker`), allowing deterministic evaluation across identical documents.

#### Strategy A: Fixed-Size Sliding Window
- **Target Size**: 500 tokens (approximated via tokenizer or whitespace word-token ratio ~2,000 characters).
- **Overlap**: 50 tokens (~200 characters, 10% overlap).
- **Step Size**: $S = \text{target\_size} - \text{overlap} = 450$ tokens.
- **Applicability**: Runs identically over PDF page text streams and Markdown text streams.
- **Boundary Handling**: Chunks preserve sequential token order with `chunk_index` incremented monotonically ($0, 1, 2, \dots$).

#### Strategy B: Structure-Aware (Heading-Based) with Safe PDF Fallback
- **Markdown Mode**:
  - Segments text along structural markdown heading boundaries (`#`, `##`, `###`).
  - **Upper Bound (Recursive Splitting)**: If any section exceeds **800 tokens**, it is recursively split at paragraph breaks (`\n\n`), then sentence boundaries (`. `), with a **50-token overlap** between sub-chunks.
  - **Lower Bound (Coalescing)**: If a section is under **50 tokens** (e.g., an introductory heading followed by a single line), it is coalesced into the subsequent subsection to avoid context fragmentation.
  - **Context Breadcrumb Prepending**: Prepends hierarchical breadcrumb context to the chunk content:
    ```text
    Context: [PostgreSQL 16 Manual > Chapter 13. Concurrency Control > 13.2. Transaction Isolation]
    ```
- **PDF Fallback Mode**:
  - PDFs lack Markdown heading tags. The Strategy B PDF fallback operates on **physical page structural boundaries**:
    - Groups text by page.
    - If a page has $> 800$ tokens, recursively splits by paragraph (`\n\n`) with 50-token overlap.
    - If a page has $< 50$ tokens, merges with the following page.
    - Prepends page breadcrumbs: `Context: [postgresql-16-US.pdf > Page X]`.

### 2.4 Metadata Schema (REQ-05)
Every generated chunk record tracks 9 distinct fields:
1. `id`: UUID (Primary Key).
2. `document_id`: UUID (Foreign Key referencing `documents.id`).
3. `strategy`: VARCHAR(32) (`fixed_window` or `structure_aware`).
4. `chunk_index`: INT (0-indexed position within the document).
5. `source_filename`: TEXT (e.g., `postgresql-16-US.pdf` or `mvcc-intro.md`).
6. `source_url`: TEXT (e.g., `https://www.postgresql.org/docs/16/mvcc-intro.html`).
7. `page_or_section`: TEXT (e.g., `Page 14` or `13.2. Transaction Isolation > Read Committed`).
8. `document_date`: DATE (Release/documentation date).
9. `content_hash`: VARCHAR(64) (Cryptographic SHA-256 hash of normalized chunk text).

### 2.5 Deduplication & Re-ingestion Policy (REQ-06)
1. **Unchanged Document**:
   - Compute SHA-256 of candidate file: `current_file_hash`.
   - Query `documents` table:
     `SELECT id, file_hash_sha256 FROM documents WHERE source_filename = :name;`
   - If `file_hash_sha256 == current_file_hash`:
     - **Bypass entire document parsing and ingestion**.
     - Log `document_skipped_duplicate` with zero DB writes or embedding calls.
2. **Modified Document (Idempotent Replacement)**:
   - If `file_hash_sha256 != current_file_hash`:
     - Within a database transaction:
       ```sql
       -- Purge stale chunks belonging to this document and strategy
       DELETE FROM chunks WHERE document_id = :doc_id AND strategy = :strategy;
       -- Update document hash and timestamp
       UPDATE documents 
       SET file_hash_sha256 = :current_file_hash, updated_at = CURRENT_TIMESTAMP 
       WHERE id = :doc_id;
       ```
     - Re-parse and insert fresh chunks. This prevents duplicate or orphaned chunks.

### 2.6 Embedding Cache Engine (REQ-07)
- **Cache Composite Identity**:
  `CacheKey = (content_hash, model_name, dimensions, task_type, title)`
  - Distinguishes model, dimension, asymmetric task types (`RETRIEVAL_DOCUMENT` vs `RETRIEVAL_QUERY`), and document titles.
  - Safe equivalence cannot be established with unlabelled legacy 3-part entries; therefore `GeminiEmbeddingProvider` strictly enforces `allow_legacy=False` on all cache lookups.
- **Cache Lookup**: Before calling the Gemini API, each chunk is checked in memory and database:
  ```sql
  SELECT embedding FROM embedding_cache 
  WHERE content_hash = :chunk_hash AND model_name = :model_name AND dimensions = :dimensions;
  ```
- **Cache Hit**: Reuses the stored vector, increments the `cache_hits` counter, and incurs zero API cost.
- **Cache Miss**: New chunks are buffered into batches (e.g., 16–32 chunks per batch).
- **Gemini Embedding API Execution**:
  - Model: `gemini-embedding-001` (Provisional Google candidate; `text-embedding-004` is shut down).
  - Implementation: `GeminiEmbeddingProvider` (`src/embeddings/gemini_provider.py`) implementing `BaseEmbeddingProvider`.
  - Task Types: Asymmetric retrieval support (`RETRIEVAL_DOCUMENT` for chunk indexing; `RETRIEVAL_QUERY` for user queries).
  - Configurable Dimension: Default `768` (MRL truncated prefix compatible with pgvector `vector(768)`).
  - Input Length Protection: Dual heuristic proxy (`max(words * 1.3, chars / 3.5)`) and 10,000-char ceiling prevent oversized payloads without silent truncation.
  - Rate Limit & Retry Safety: Configurable exponential backoff with bounded jitter on transient HTTP `429 (RESOURCE_EXHAUSTED)` and `503 (UNAVAILABLE)` errors. Server-directed `Retry-After` delay guidance (numeric seconds, RFC 7231 HTTP-dates, gRPC protobuf strings) is respected; delays exceeding `max_delay_s` abort immediately without retrying early.
  - Offline Safety: `is_live_enabled=False` by default; client dependency injection allows 100% offline unit testing without external network traffic.
- **Cache Write**: Newly generated vectors are written to `embedding_cache` under the composite key so repeated re-runs never re-embed identical text.

### 2.7 Failure Handling & Transactional Atomicity
- **Per-Document Unit of Work**:
  - Chunks, hashes, and embeddings for a single document are generated and validated in memory.
  - Insertion into `chunks` and updating `documents` is executed within an atomic transaction:
    ```sql
    BEGIN;
    -- Delete stale chunks if re-ingesting
    DELETE FROM chunks WHERE document_id = :doc_id AND strategy = :strategy;
    -- Insert all chunks for this document
    INSERT INTO chunks (document_id, strategy, chunk_index, page_or_section, content, content_hash, source_filename, source_url, document_date, embedding) VALUES (...);
    -- Update job progress counters
    UPDATE ingestion_jobs SET processed_documents = processed_documents + 1 WHERE id = :job_id;
    COMMIT;
    ```
- **Error Behavior**:
  - If embedding generation fails after retries, or if database insertion raises an error:
    - The transaction issues `ROLLBACK`.
    - No partial chunks from the failed document remain in `chunks`.
    - `ingestion_jobs` records the document filename and exception message under `error_log`.
    - The pipeline resumes processing subsequent documents.

### 2.8 Progress Reporting & Job State (REQ-03)
- An in-database `ingestion_jobs` table tracks execution without requiring Redis:
  - `id`: UUID (Job ID).
  - `strategy`: VARCHAR(32).
  - `status`: VARCHAR(32) (`queued`, `processing`, `completed`, `failed`).
  - `total_documents`: INT.
  - `processed_documents`: INT.
  - `total_chunks_created`: INT.
  - `embeddings_generated`: INT.
  - `cache_hits`: INT.
  - `current_document`: TEXT.
  - `error_log`: JSONB (List of error strings).
  - `started_at`, `completed_at`: TIMESTAMPTZ.
- FastAPI endpoint `GET /api/ingestion/status/{job_id}` returns real-time progress for UI bars.

### 2.9 Security & Content Boundary Rules
- **Untrusted Ingested Text**: All document content (PDF text, Markdown bodies) is classified as untrusted external data.
- **No In-Database Code Execution**: Document text is never evaluated, executed, or used dynamically in SQL queries; always parameterized (`:content`).
- **Prompt Isolation**: During the query phase, retrieved chunks are encapsulated within strict `<context><chunk id="...">...</chunk></context>` XML delimiters with instructions forbidding obedience to embedded directives.

### 2.10 Observability & Audit Logging
- **Structured JSON Logging**: Standard logging format:
  ```json
  {"timestamp": "2026-10-23T17:00:00Z", "level": "INFO", "event": "document_ingested", "job_id": "...", "filename": "mvcc-intro.md", "strategy": "structure_aware", "chunks": 4, "cache_hits": 2, "api_calls": 2, "duration_ms": 340}
  ```
- **Secret Sanitization**: All loggers scrub `GEMINI_API_KEY`, database passwords in `DATABASE_URL`, and authorization headers.

---

## 3. Database Schema (PostgreSQL + pgvector Persistence Layer)

The persistence layer is structured as a decoupled, reproducible migration pipeline located in [`migrations/`](file:///c:/Users/USER/Desktop/RAG%20Task/migrations/). It separates core relational logic from native C-extension vector operations, ensuring portability across environments and safe, deterministic rollbacks.

### 3.1 Migration Architecture & Version Sequence

| Migration File | Rollback Script | Purpose & Entities Defined |
| :--- | :--- | :--- |
| [`001_create_core_tables.sql`](file:///c:/Users/USER/Desktop/RAG%20Task/migrations/001_create_core_tables.sql) | [`001_create_core_tables.rollback.sql`](file:///c:/Users/USER/Desktop/RAG%20Task/migrations/001_create_core_tables.rollback.sql) | Standard relational schema: `ingestion_jobs`, `documents`, `chunks`, `embedding_cache` with B-Tree and GIN indexes. Runs on any standard PostgreSQL instance without requiring `pgvector`. |
| [`002_vector_storage.sql`](file:///c:/Users/USER/Desktop/RAG%20Task/migrations/002_vector_storage.sql) | [`002_vector_storage.rollback.sql`](file:///c:/Users/USER/Desktop/RAG%20Task/migrations/002_vector_storage.rollback.sql) | Enables `vector` extension, adds `embedding` vector column to `chunks` and `embedding_cache`, and builds the HNSW cosine vector index. |
| [`003_telemetry_schema.sql`](file:///c:/Users/USER/Desktop/RAG%20Task/migrations/003_telemetry_schema.sql) | [`003_telemetry_schema.rollback.sql`](file:///c:/Users/USER/Desktop/RAG%20Task/migrations/003_telemetry_schema.rollback.sql) | Creates `query_telemetry` table and indexes for audit logging, query latency, token counts, and cost telemetry (REQ-13). |

---

### 3.2 SQL DDL Specification

```sql
-- =============================================================================
-- Migration 001: Core Relational Entities
-- =============================================================================

-- 1. Ingestion Jobs Table (REQ-03)
CREATE TABLE IF NOT EXISTS ingestion_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    strategy VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    total_documents INT NOT NULL DEFAULT 0,
    processed_documents INT NOT NULL DEFAULT 0,
    total_chunks_created INT NOT NULL DEFAULT 0,
    embeddings_generated INT NOT NULL DEFAULT 0,
    cache_hits INT NOT NULL DEFAULT 0,
    current_document TEXT,
    error_details JSONB NOT NULL DEFAULT '[]'::jsonb,
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_ingestion_jobs_strategy CHECK (strategy IN ('fixed_window', 'structure_aware', 'both')),
    CONSTRAINT chk_ingestion_jobs_status CHECK (status IN ('queued', 'processing', 'completed', 'failed', 'cancelled')),
    CONSTRAINT chk_ingestion_jobs_total_docs CHECK (total_documents >= 0),
    CONSTRAINT chk_ingestion_jobs_proc_docs CHECK (processed_documents >= 0),
    CONSTRAINT chk_ingestion_jobs_chunks CHECK (total_chunks_created >= 0),
    CONSTRAINT chk_ingestion_jobs_embeddings CHECK (embeddings_generated >= 0),
    CONSTRAINT chk_ingestion_jobs_cache_hits CHECK (cache_hits >= 0)
);

CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_status ON ingestion_jobs(status);
CREATE INDEX IF NOT EXISTS idx_ingestion_jobs_created_at ON ingestion_jobs(created_at DESC);

-- 2. Document Registry Table (REQ-01, REQ-02, REQ-06)
CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_id VARCHAR(128) NOT NULL UNIQUE,
    source_filename TEXT NOT NULL UNIQUE,
    source_url TEXT,
    file_type VARCHAR(16) NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    total_pages INT,
    total_words INT,
    total_sections INT,
    parser_status VARCHAR(32) NOT NULL DEFAULT 'pending',
    document_date DATE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_documents_file_type CHECK (file_type IN ('pdf', 'markdown')),
    CONSTRAINT chk_documents_content_hash CHECK (length(content_hash) = 64),
    CONSTRAINT chk_documents_parser_status CHECK (parser_status IN ('pending', 'parsed', 'failed', 'skipped'))
);

CREATE INDEX IF NOT EXISTS idx_documents_content_hash ON documents(content_hash);
CREATE INDEX IF NOT EXISTS idx_documents_file_type ON documents(file_type);
CREATE INDEX IF NOT EXISTS idx_documents_parser_status ON documents(parser_status);
CREATE INDEX IF NOT EXISTS idx_documents_metadata_gin ON documents USING gin (metadata);

-- 3. Chunks Table (REQ-04, REQ-05, REQ-06)
CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    strategy VARCHAR(32) NOT NULL,
    chunk_index INT NOT NULL,
    source_filename TEXT NOT NULL,
    source_url TEXT,
    page_or_section TEXT NOT NULL,
    document_date DATE,
    content TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    token_count INT NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_chunks_doc_strat_index UNIQUE (document_id, strategy, chunk_index),
    CONSTRAINT chk_chunks_strategy CHECK (strategy IN ('fixed_window', 'structure_aware')),
    CONSTRAINT chk_chunks_chunk_index CHECK (chunk_index >= 0),
    CONSTRAINT chk_chunks_token_count CHECK (token_count > 0),
    CONSTRAINT chk_chunks_content_hash CHECK (length(content_hash) = 64)
);

CREATE INDEX IF NOT EXISTS idx_chunks_document_id ON chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_chunks_strategy ON chunks(strategy);
CREATE INDEX IF NOT EXISTS idx_chunks_content_hash ON chunks(content_hash);
CREATE INDEX IF NOT EXISTS idx_chunks_source_filename ON chunks(source_filename);
CREATE INDEX IF NOT EXISTS idx_chunks_metadata_gin ON chunks USING gin (metadata);

-- 4. Embedding Cache Table (REQ-07)
CREATE TABLE IF NOT EXISTS embedding_cache (
    content_hash VARCHAR(64) NOT NULL,
    model_name VARCHAR(64) NOT NULL,
    model_version VARCHAR(32),
    dimensions INT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (content_hash, model_name, dimensions),
    CONSTRAINT chk_embedding_cache_content_hash CHECK (length(content_hash) = 64),
    CONSTRAINT chk_embedding_cache_dimensions CHECK (dimensions > 0)
);

CREATE INDEX IF NOT EXISTS idx_embedding_cache_model ON embedding_cache(model_name, dimensions);

-- =============================================================================
-- Migration 002: Vector Storage & HNSW Indexing
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- Vector column with parameterizable dimension (default: 768 for Gemini text-embedding-004)
ALTER TABLE chunks ADD COLUMN IF NOT EXISTS embedding vector(/* __EMBEDDING_DIM__ */ 768);
ALTER TABLE embedding_cache ADD COLUMN IF NOT EXISTS embedding vector(/* __EMBEDDING_DIM__ */ 768);

-- HNSW Vector Index with Cosine Distance
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw_cosine 
ON chunks 
USING hnsw (embedding vector_cosine_ops)
WITH (m = 16, ef_construction = 64);

-- =============================================================================
-- Migration 003: Observability & Telemetry (REQ-13)
-- =============================================================================

CREATE TABLE IF NOT EXISTS query_telemetry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    query_text TEXT NOT NULL,
    chunking_strategy VARCHAR(32) NOT NULL,
    retrieval_k INT NOT NULL,
    retrieved_chunk_ids UUID[] NOT NULL DEFAULT '{}',
    similarity_scores FLOAT8[] NOT NULL DEFAULT '{}',
    top_similarity_score FLOAT8 NOT NULL,
    refusal_triggered BOOLEAN NOT NULL DEFAULT FALSE,
    retrieval_latency_ms INT NOT NULL,
    llm_latency_ms INT NOT NULL,
    total_latency_ms INT NOT NULL,
    prompt_tokens INT NOT NULL DEFAULT 0,
    completion_tokens INT NOT NULL DEFAULT 0,
    total_tokens INT NOT NULL DEFAULT 0,
    estimated_cost_usd NUMERIC(10, 6) NOT NULL DEFAULT 0.000000,
    client_ip VARCHAR(45),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_query_telemetry_strategy CHECK (chunking_strategy IN ('fixed_window', 'structure_aware')),
    CONSTRAINT chk_query_telemetry_k CHECK (retrieval_k > 0),
    CONSTRAINT chk_query_telemetry_latencies CHECK (
        retrieval_latency_ms >= 0 AND llm_latency_ms >= 0 AND total_latency_ms >= 0
    ),
    CONSTRAINT chk_query_telemetry_tokens CHECK (
        prompt_tokens >= 0 AND completion_tokens >= 0 AND total_tokens >= 0
    )
);

CREATE INDEX IF NOT EXISTS idx_query_telemetry_created_at ON query_telemetry(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_query_telemetry_refusal ON query_telemetry(refusal_triggered);
CREATE INDEX IF NOT EXISTS idx_query_telemetry_strategy ON query_telemetry(chunking_strategy);
```

---

### 3.3 Vector Dimension Management & HNSW Indexing Specification

1. **Embedding Dimension Decision (Provisional)**:
   - Working default is provisionally set to **768 dimensions** (Google Gemini candidates: `gemini-embedding-001` or `gemini-embedding-2`; `text-embedding-004` is shut down), as provisionally evaluated. This remains unverified until official Google AI Studio documentation and API quotas are reviewed in Phase 3E.
   - Hardcoded arbitrary values are strictly avoided: `migrations/002_vector_storage.sql` uses parameterizable tokens (`/* __EMBEDDING_DIM__ */ 768`) dynamically rendered by `src/db/migrator.py`.
   - **Strict Dimension Bounds (1 to 2,000 Dimensions)**:
     `src/db/migrator.py` validates that any dimension passed programmatically or via `--dim` satisfies $1 \le \text{dim} \le 2000$. The 2,000-dimension ceiling is an architectural requirement of `pgvector` for HNSW indexes; dimensions $> 2000$ are rejected before SQL generation.
   - **Migration-Safe Alter Path**: If an alternative model is selected prior to Phase 3E (e.g., OpenAI `text-embedding-3-small` with 1536 dim, or local `all-MiniLM-L6-v2` with 384 dim), the schema can be altered without data loss:
     ```sql
     DROP INDEX IF EXISTS idx_chunks_embedding_hnsw_cosine;
     ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(NEW_DIM);
     ALTER TABLE embedding_cache ALTER COLUMN embedding TYPE vector(NEW_DIM);
     CREATE INDEX idx_chunks_embedding_hnsw_cosine ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
     ```
2. **Cache Isolation by Model & Dimension**:
   `embedding_cache` enforces a composite primary key on `(content_hash, model_name, dimensions)`. Changing the model or truncated dimension never produces vector collisions or stale cache returns. `model_name` must always be the canonical model identifier (e.g., `'gemini-embedding-001'`). The offline cache contract is implemented in `src/embeddings/cache.py` (`InMemoryEmbeddingCache`).
3. **Retrieval Alignment via Cosine Distance**:
   The HNSW index utilizes `vector_cosine_ops` (`<=>`). Cosine similarity score $s \in [0.0, 1.0]$ is computed as:
   $$s = 1 - (u \Leftrightarrow v)$$
   Retrieval ordering: `ORDER BY embedding <=> :query_vector ASC LIMIT :k;`
4. **HNSW Graph Hyperparameters**:
   - `m = 16`: Number of bidirectional connections per element in the graph.
   - `ef_construction = 64`: Size of the dynamic candidate list during graph construction.
   - `hnsw.ef_search = 40`: Dynamic search list size during query execution (configured via `SET LOCAL hnsw.ef_search = 40`).
5. **Native Array Types in Telemetry (REQ-13)**:
   In `query_telemetry`, `retrieved_chunk_ids UUID[]` and `similarity_scores FLOAT8[]` use standard native PostgreSQL 1-D arrays. No custom composite types (such as `uuid_array` or `float_array`) are defined or required.
6. **Re-ingestion Idempotency vs. UPSERT Zombie Chunks**:
   Re-ingestion utilizes an atomic transaction (`DELETE FROM chunks WHERE document_id = :id AND strategy = :strat; INSERT INTO chunks ...`). An `UPSERT` (`ON CONFLICT DO UPDATE`) is intentionally avoided because if a new revision of a document produces fewer chunks than the previous revision, `UPSERT` leaves trailing chunks as unreferenced zombie chunks in the database.

---

### 3.4 Local Environment Status & Authorization Boundary

- **Local PostgreSQL Service Detected**: Windows service `postgresql-x64-17` is **Running** on `localhost:5432`.
- **Safety Boundary**: In accordance with project instructions, **no live database operations, connections, or DDL migrations have been executed** against this service.
- **pgvector Availability**: Unverified on the local PostgreSQL 17 instance (standard Windows installers do not bundle pgvector by default).
- **Verification Guarantee**: All schema syntax, ordering, rollback symmetry, and constraints are fully covered by 20 automated static analysis tests (`tests/unit/test_migrations.py`), bringing the full passing test suite to 75 tests.

---

## 4. Testing & Quality Assurance Plan

The ingestion pipeline will be verified using four automated test suites:

1. **Unit Tests (`tests/unit/`)**:
   - `test_fixed_chunker.py`: Validates 500-token chunks, 50-token sliding overlap, token count limits, and deterministic output.
   - `test_heading_chunker.py`: Validates Markdown heading extraction, 800-token recursive splitting, 50-token coalescing, and breadcrumb prepending.
   - `test_pdf_extractor.py`: Validates physical page extraction and whitespace normalization.
   - `test_content_hasher.py`: Validates deterministic SHA-256 calculation over UTF-8 text.
2. **Integration Tests (`tests/integration/`)**:
   - `test_ingest_markdown_mocked.py`: Runs end-to-end ingestion on sample Markdown files using a mocked embedding provider; checks DB row insertion, metadata fidelity, and foreign keys.
   - `test_ingest_pdf_mocked.py`: Validates ingestion of multi-page PDF slices into `chunks`.
3. **Idempotency & Deduplication Tests**:
   - `test_duplicate_file_skip.py`: Ingests a file, re-runs ingestion without changes, verifies 0 new DB writes and 0 new embedding calls.
   - `test_file_modification_replace.py`: Modifies a document, re-ingests, verifies that stale chunks are deleted and replaced without orphan records.
4. **Resilience & Failure Recovery Tests**:
   - `test_rate_limit_backoff.py`: Simulates HTTP 429 exceptions; verifies that exponential backoff retries and succeeds.
   - `test_db_rollback_on_failure.py`: Injects an error during embedding generation midway through a document; verifies that the transaction rolls back cleanly with zero partial chunks in `chunks`.

---

## 5. Next Implementation Milestones (Phase 3B)

The implementation will proceed in strictly gated stages:
- **Stage 1**: Core Data Models & Base Interfaces (`Document`, `Chunk`, `BaseChunker`, `BaseParser`).
- **Stage 2**: Fixed-Size Chunker & Heading-Based Chunker Implementation with unit tests.
- **Stage 3**: Embedding Provider Client (Gemini API with exponential backoff & mock interface for testing).
- **Stage 4**: Database Migrations & Ingestion Service (`IngestionWorker` with deduplication & cache).
- **Stage 5**: End-to-End Local Ingestion Verification on the verified 14 Markdown docs + PDF subset.
