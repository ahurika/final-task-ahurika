# Product Requirements Document (PRD): PostgreSQL Documentation Assistant

> **STATUS SPECIFICATION & ALIGNMENT**:
> - **Confirmed Assessment Requirements**: The 18 mandatory deliverables defined by Part 4 Graduation Assessment (preserved in Section 2 & 3).
> - **Provisional Architectural Decisions**: PostgreSQL Documentation Assistant domain, Python/FastAPI backend, PostgreSQL/pgvector vector store, React+Vite frontend, Fixed (Strategy A) and Heading-based (Strategy B) chunking.
> - **Open Questions**: Final selection of embedding and LLM provider, final cloud deployment host selection.
> - **Implementation Work**: **NOT STARTED (0% code implemented)**. All implementation steps are strictly gated behind architectural sign-off.

---

## 1. Executive Summary & Objective

The objective of this project is to design, implement, evaluate, and deploy a production-grade Retrieval-Augmented Generation (RAG) system as the Part 4 Graduation Assessment: the **PostgreSQL Documentation Assistant**.

The system enables developers and database administrators to query the official PostgreSQL technical documentation via a clean conversational interface. Answers must be strictly grounded in the ingested corpus, accompanied by direct citations that open the exact source chunks, and protected against adversarial manipulation (prompt injection). If the corpus does not contain the answer, the system must reliably refuse to answer rather than hallucinate.

---

## 2. Core Assessment Requirements (The 18 Mandatory Deliverables)

The following 18 assessment requirements represent the authoritative scope of the project:

| ID | Requirement | Description | Status |
| :--- | :--- | :--- | :--- |
| **REQ-01** | **Document Corpus Scale** | Document collection containing at least 100 pages or 200 documents. | **VERIFIED & PASSED** (3,220 pages measured; see [CORPUS-VERIFICATION.md](file:///c:/Users/USER/Desktop/RAG%20Task/data/CORPUS-VERIFICATION.md)) |
| **REQ-02** | **Multi-Format Ingestion** | Robust ingestion pipeline supporting both PDF and Markdown (`.md`) files. | **Source Inputs Ready** (1 PDF: 3,220 pages; 14 Markdown docs verified; pipeline unstarted) |
| **REQ-03** | **Background Ingestion** | Asynchronous ingestion jobs with visible progress indicator / status tracking. | **Schema & Models Implemented** (Table `ingestion_jobs` and `IngestionJobRecord` created; background worker pending Phase 3G) |
| **REQ-04** | **Dual Chunking Strategies** | Implementation of two distinct chunking strategies and an empirical comparison between them. | **IMPLEMENTED & AUDITED** (6,413 Strategy A vs 5,318 Strategy B chunks; see [CHUNKING-REPORT.md](file:///c:/Users/USER/Desktop/RAG%20Task/data/CHUNKING-REPORT.md)) |
| **REQ-05** | **Rich Chunk Metadata** | Every chunk must track: source document, page or section header, source URL, ingestion/document date, and content hash. | **IMPLEMENTED & AUDITED** (Schema constraint `uq_chunks_doc_strat_index`, cascading FK, provenance tracking verified) |
| **REQ-06** | **Deduplication on Re-ingestion** | Content-hash-based duplicate prevention when unchanged documents are re-ingested. | **Schema & Transaction Designed** (File SHA-256 skip & atomic chunk replacement in `001_create_core_tables.sql`) |
| **REQ-07** | **Embedding Caching** | Cache layer preventing re-computing vector embeddings for identical text content. | **IMPLEMENTED & VERIFIED OFFLINE** (Cache contract `CacheKey(content_hash, model_name, dimensions)`, `InMemoryEmbeddingCache`, and database composite PK; 117 tests passing) |
| **REQ-08** | **Defensible Vector Store** | Vector store using pgvector/PostgreSQL, Qdrant, or Chroma, with a justified choice and documented index settings. | **Schema & Migrations Implemented** (PostgreSQL + pgvector migrations `001`-`003`, HNSW cosine index `vector_cosine_ops`, `m=16`, `ef_construction=64`; verified offline) |
| **REQ-09** | **Chat Interface** | Intuitive web or mobile chat interface for seamless conversational interaction. | **Provisional**: React with Vite |
| **REQ-10** | **Interactive Citations** | Generated answers must include clickable/expandable citations that open the exact retrieved chunk content. | **Planned** (Implementation not started) |
| **REQ-11** | **Similarity Threshold & Refusal** | Calibrated similarity threshold causing the system to refuse politely when the corpus lacks grounding info. | **Planned** (Implementation not started) |
| **REQ-12** | **Metadata Filtering** | Ability for queries to filter retrieval by metadata fields (e.g., document source, date range, or topic). | **Planned** (Implementation not started) |
| **REQ-13** | **Observability & Telemetry** | Rate limiting, query audit logs tracking retrieved chunk IDs, similarity scores, latency, token costs, and average cost per query. | **Schema Designed** (`query_telemetry` table in `003_telemetry_schema.sql` and `QueryTelemetryRecord` in `src/db/models.py`) |
| **REQ-14** | **Prompt Injection Defense Test** | Formal security test using a poisoned document with adversarial instructions to verify defense mechanisms. | **Test Fixture Created** (`security_test_payload.md`; testing pending app implementation) |
| **REQ-15** | **30-Question Evaluation Dataset** | Ground-truth dataset of 30 questions with known answers and sources, including at least 5 unanswerable questions. | **Planned** (PostgreSQL questions drafted; run pending) |
| **REQ-16** | **Matrix Evaluation (2x2)** | Systematic evaluation across 2 chunking strategies × 2 values of $k$ (120 test executions), tracking retrieval, answer accuracy, citations, and refusal. | **Planned** (Structure ready; execution pending) |
| **REQ-17** | **Evaluation & Mitigation Report** | Complete report in `EVALUATION.md` documenting baseline metrics, failures, architectural changes, and re-test results. | **Planned** (Structure ready; results empty) |
| **REQ-18** | **Project Delivery Package** | Pipeline diagram, publicly accessible deployed application, 3-minute video walkthrough, and 2 LinkedIn project posts. | **Planned** (Diagram ready; deployment pending) |

---

## 3. Detailed Acceptance Criteria & Specifications

### 3.1 Document Ingestion & Storage
- **AC-01 (Corpus Scale & Verification — VERIFIED)**: 
  - Target: Official PostgreSQL 16 documentation (US Letter PDF: `postgresql-16-US.pdf`).
  - *Verification Completed (2026-10-23)*: Downloaded to `data/raw/postgresql-16-US.pdf` (15,162,468 bytes, SHA-256: `f5316371...`). Measured page count is **3,220 pages**, exceeding the 100-page minimum by 32.2×. Validated deterministically via [verify_corpus.py](file:///c:/Users/USER/Desktop/RAG%20Task/scripts/verify_corpus.py) and recorded in [corpus-manifest.json](file:///c:/Users/USER/Desktop/RAG%20Task/data/corpus-manifest.json).
- **AC-02 (Multi-Format Corpus Inputs & Parsers — INPUTS PREPARED)**: 
  - *Markdown Inputs Prepared (2026-10-23)*: 14 official documentation chapters collected into `data/raw/markdown/` (306,557 bytes, 44,738 words, 168 headings, 123 code blocks). All files verified deterministically via [verify_markdown_corpus.py](file:///c:/Users/USER/Desktop/RAG%20Task/scripts/verify_markdown_corpus.py) and recorded in [markdown-corpus-manifest.json](file:///c:/Users/USER/Desktop/RAG%20Task/data/markdown-corpus-manifest.json).
  - *Status*: Source documents (PDF and Markdown) are collected and verified on disk. The software ingestion pipeline, background parsing worker, chunking, and embedding generation remain **unimplemented**.
- **AC-03 (Background Jobs)**: Ingestion executes asynchronously via background workers (e.g., FastAPI background tasks or worker queue) without blocking the API thread. Exposes `/api/ingestion/status` reporting: `queued`, `processing`, `completed`, `failed`, `total_files`, `processed_files`, `chunks_created`, `embeddings_generated`, `cache_hits`.
- **AC-04 (Dual Chunking Strategies Definition)**:
  - **Strategy A (Fixed-Size Sliding Window)**:
    - Initial baseline size: 500 tokens (approx. 2,000 characters).
    - Initial baseline overlap: 50 tokens (approx. 200 characters, 10% overlap).
    - Advances sliding window by 450 tokens.
    - *Design note*: Documented as an initial experimental setting, not a proven optimal value.
  - **Strategy B (Structure-Aware / Heading-Based)**:
    - Splits on document structure: Top-level headers (`#`, `##`) and subheadings (`###`).
    - Upper bound splitting: If any section exceeds 800 tokens, it is recursively split at paragraph breaks (`\n\n`) and sentence boundaries (`. `), with a 50-token overlap between sub-chunks.
    - Lower bound coalescing: Sections smaller than 50 tokens are coalesced with the following subsection to avoid orphan headers.
    - Context breadcrumbs: Each chunk prepends its hierarchical path (e.g., `Context: SQL Commands > SELECT > WHERE Clause`).
- **AC-05 (Metadata Preservation & Schema)**:
  Each chunk record stored in the database must persist:
  1. `chunk_id`: Unique identifier (UUID).
  2. `document_id`: Parent document UUID.
  3. `source_filename`: Exact filename (e.g., `postgresql-16-US.pdf`, `mvcc.md`).
  4. `source_url`: Canonical PostgreSQL documentation URL (e.g., `https://www.postgresql.org/docs/16/mvcc.html`).
  5. `page_or_section`: Physical page number (PDF) or heading breadcrumb path (Markdown).
  6. `document_date`: Release date of documentation (e.g., `2024-05-09`).
  7. `content_hash`: Cryptographic SHA-256 hash computed over the normalized chunk text.
  8. `chunk_index`: Sequence position within parent document.
- **AC-06 (Duplicate Prevention)**: SHA-256 hash of entire files is checked before ingestion. If file hash matches an existing record, ingestion is bypassed. Re-ingested chunks with identical `content_hash` are skipped.
- **AC-07 (Embedding Cache)**: A dedicated `embedding_cache` table maps `content_hash` $\to$ `vector`. Identical chunk texts reuse cached vectors without incurring external API latency or costs.

### 3.2 Vector Storage & Retrieval
- **AC-08 (Vector Store & Indexing)**:
  - Provisional choice: PostgreSQL with `pgvector` extension.
  - Justification: Enables unified transactional schema (documents, chunks, embedding cache, telemetry, and vectors in a single ACID PostgreSQL database).
  - Indexing: HNSW index on the vector column (`vector_cosine_ops`, `m = 16`, `ef_construction = 64`).
- **AC-11 (Similarity Threshold & Refusal)**:
  - System measures cosine similarity score $s \in [0.0, 1.0]$.
  - If the top retrieved chunk score $s_{\text{top}} < \theta_{\text{sim}}$ (calibrated baseline: 0.65), system immediately returns:
    *"I cannot answer this question based on the provided documents as no sufficiently relevant information was found."*
- **AC-12 (Metadata Filters)**:
  - Retrieval engine supports SQL-level pre-filtering (e.g., `WHERE source_filename = 'backup.md'`).

### 3.3 Chat Interface & Generation
- **AC-09 (Chat Interface)**:
  - Built with React and Vite. Clean, modern typography and dark/light mode.
  - Interactive chat history with streaming or loading spinners.
  - Filter drawer to select document sub-collections or chunking strategies.
- **AC-10 (Citations & Evidence Viewer)**:
  - Every answer claim cites a bracketed reference: `[Doc: mvcc.md, Section: Concurrency Control]`.
  - Clicking opens an inspection drawer showing verbatim chunk text, cosine similarity score, and source URL.

### 3.4 Security & Governance
- **AC-13 (Telemetry & Observability)**:
  - Rate limiting (e.g., 20 queries/min per IP).
  - Query audit log table: `query_id`, `timestamp`, `query_text`, `retrieved_chunk_ids`, `similarity_scores`, `latency_ms`, `prompt_tokens`, `completion_tokens`, `estimated_cost_usd`.
- **AC-14 (Prompt Injection Defense)**:
  - Poisoned test document ingested containing adversarial commands.
  - System prompt enforces XML context delimitation (`<context>...</context>`) and instructions treating retrieved text as passive data.

### 3.5 Evaluation, Delivery & Public Artifacts
- **AC-15 (Evaluation Dataset)**:
  - 30 curated PostgreSQL questions: 25 factual/procedural queries + 5 unanswerable questions.
- **AC-16 (Evaluation Matrix)**:
  - 4 test configurations: (Strategy A, $k_1=3$), (Strategy A, $k_2=5$), (Strategy B, $k_1=3$), (Strategy B, $k_2=5$). Total 120 recorded evaluation runs.
- **AC-17 (Evaluation Report)**:
  - Complete report in `EVALUATION.md` documenting baseline metrics, failures, architectural changes, and re-test results.
- **AC-18 (Final Deliverables)**:
  - Mermaid architecture diagram (`docs/diagrams/rag-pipeline.mmd`).
  - Publicly accessible deployed URL (e.g., Vercel frontend + Render/Neon backend).
  - 3-minute video walkthrough.
  - Two LinkedIn project posts.

---

## 4. Confirmed Architecture & Service Decisions

### Confirmed Technical Stack
1. **Domain**: PostgreSQL Documentation Assistant.
2. **Corpus**: Official PostgreSQL 16 documentation (PDF & Markdown verified on disk).
3. **Backend Framework**: Python 3.10+ with FastAPI.
4. **Vector Store & Database**: Neon Serverless PostgreSQL with `pgvector` extension.
5. **Embedding & LLM Provider (Provisional)**: Google Gemini API (Active candidates: `gemini-embedding-001` / `gemini-embedding-2` with configurable dimension, provisional default 768; `text-embedding-004` is shut down by Google; `gemini-1.5-flash` for generation). *Offline foundation implemented; live verification deferred.*
6. **Frontend Framework**: React with Vite.
7. **Chunking Strategies**: Fixed-size sliding window (Strategy A) vs. Heading-based structural with recursive splitting (Strategy B).

*Note: Credentials, embedding API calls, and cloud provisioning remain deferred. No API keys or cloud database connections are created during planning phases.*

---

## 5. Scope Boundaries

### Explicitly Out of Scope (Stretch Goals)
- Hybrid search (BM25 + Dense RRF) — deferred until all 18 requirements pass.
- Cross-encoder reranking — deferred.
- Multi-user RBAC — deferred.

---

## 6. Phased Implementation Checklist

The implementation is structured into deliberate, verifiable engineering phases:

- [x] **Phase 1: Project Scaffold & Specification**
  - [x] Baseline documentation suite created (`AGENTS.md`, `PRD.md`, `RAG-NOTES.md`, `ARCHITECTURE.md`, `EVALUATION.md`, `LEARNING-LOG.md`, `README.md`).
  - [x] RAG conceptual literature and official documentation synthesized without fabricated claims.
  - [x] System sequence and pipeline diagrams authored.
- [x] **Phase 2: Corpus Collection & Verification**
  - [x] **Phase 2A (PDF)**: Downloaded and verified official PostgreSQL 16 US Letter PDF (`3,220 pages`, REQ-01 passed).
  - [x] **Phase 2B (Markdown & Security)**: Extracted and verified 14 official documentation chapters (`306,557 bytes`, REQ-02 preparation). Created synthetic prompt injection fixture (`data/security/security_test_payload.md`, REQ-14).
- [ ] **Phase 3: Ingestion Pipeline & Vector Storage**
  - [x] **Phase 3A: Ingestion Architecture & Data Flow Design** (Detailed in [ARCHITECTURE.md](file:///c:/Users/USER/Desktop/RAG%20Task/ARCHITECTURE.md) and [ingestion-pipeline.mmd](file:///c:/Users/USER/Desktop/RAG%20Task/docs/diagrams/ingestion-pipeline.mmd)).
  - [x] **Phase 3B: Chunking Engines & Unit Tests** (Implemented `FixedSizeChunker` and `StructureAwareChunker` with 40 automated unit tests).
  - [x] **Phase 3C & 3C.1: Parser Integration, Chunking Execution & Quality Audit** (Implemented parsers and executed chunking on full corpus; 55 tests passing).
  - [x] **Phase 3D & 3D.1: Database Schema, Vector Migrations & Integrity Audit** (Implemented versioned migrations `001_create_core_tables.sql`, `002_vector_storage.sql`, `003_telemetry_schema.sql`, symmetric rollback scripts, `MigrationManager` with strict dimension validation ($1 \le \text{dim} \le 2000$), typed records in `src/db/models.py`, `migrations/README.md`; native array types `UUID[]` and `FLOAT8[]` confirmed; 75 tests passing. Local PostgreSQL 17 detected; live execution deferred pending user authorization).
  - [ ] **Phase 3E: Gemini Embedding Client** (Batching, local cache lookup, exponential backoff on HTTP 429).
  - [ ] **Phase 3F: Ingestion Worker & Deduplication** (File-hash duplicate skipping, atomic transactional chunk replacement).
  - [ ] **Phase 3G: Ingestion Progress Tracking** (Status reporting endpoint, job state tracking).
- [ ] **Phase 4: Query Engine & Generation Pipeline**
  - [ ] Vector similarity retrieval with cosine distance and metadata filtering.
  - [ ] Calibrated similarity threshold check and polite refusal short-circuit.
  - [ ] Grounded prompt assembly with XML context isolation.
  - [ ] Interactive citation tag parser and link generator.
  - [ ] Query telemetry logging (latency, token costs, similarity scores).
- [ ] **Phase 5: React Chat Interface**
  - [ ] Modern conversational UI with dark/light mode.
  - [ ] Clickable citations opening verbatim chunk inspection drawer.
  - [ ] Metadata filter controls and ingestion progress dashboard.
- [ ] **Phase 6: Systematic Evaluation & Security Auditing**
  - [ ] Execution of 30-question catalog across 2 chunking strategies × 2 $k$ values (120 test runs).
  - [ ] Prompt injection defense verification with poisoned fixture.
  - [ ] Failure analysis, prompt tuning, and post-mitigation re-testing recorded in `EVALUATION.md`.
- [ ] **Phase 7: Cloud Deployment & Graduation Deliverables**
  - [ ] Public deployment (Vercel frontend + Render/HuggingFace backend + Neon pgvector).
  - [ ] 3-minute video demonstration recording.
  - [ ] 2 LinkedIn engineering posts.
