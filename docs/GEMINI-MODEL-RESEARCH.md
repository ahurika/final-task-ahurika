# Official Google Gemini Embedding Model, Pricing, and Access Verification

**Document**: `docs/GEMINI-MODEL-RESEARCH.md`  
**Date**: 2026-10-24  
**Author**: Senior AI Infrastructure Engineer & API Cost Analyst  
**Project**: PostgreSQL Documentation Assistant (Part 4 Graduation Assessment)  
**Workspace**: `C:\Users\USER\Desktop\RAG Task`

---

## 1. Executive Summary

This research report evaluates the current official Google Gemini embedding models to recommend the safest, most cost-effective, and architecturally sound configuration for our PostgreSQL documentation RAG system.

### Key Conclusions
1. **Model Landscape**:
   - `text-embedding-004` has been deprecated and shut down in Google's API lifecycle.
   - `gemini-embedding-001` is Google's active, stable, text-optimized embedding model ($0.15 / 1M tokens, 2,048 token window).
   - `gemini-embedding-2` is Google's newest natively multimodal embedding model ($0.20 / 1M tokens, 8,192 token window).
2. **Cost & Sizing Alignment**:
   - Our text-only PostgreSQL documentation chunks are capped at 500 tokens (Strategy A) and 800 tokens (Strategy B). Both models easily accommodate our chunk sizes.
   - Total corpus embedding cost for all 11,731 chunks (~4.2 million tokens) is estimated at **$0.63** for `gemini-embedding-001` vs. **$0.84** for `gemini-embedding-2`.
3. **MRL & Normalization Nuance**:
   - Both models support Matryoshka Representation Learning (MRL) dimension reduction to 768.
   - *Critical Engineering Discovery*: `gemini-embedding-2` automatically re-normalizes truncated output vectors to unit length ($L_2$ norm = 1.0). For `gemini-embedding-001`, Google's official documentation advises client-side $L_2$ normalization if dot-product similarity is utilized (our pgvector HNSW index uses cosine distance `<=>`, which handles normalization natively).
4. **Privacy & Terms**:
   - Unpaid tier data in Google AI Studio is subject to human review and model training.
   - Paid tier (pay-as-you-go) strictly forbids using customer data for training and excludes human reviewers.
5. **Recommendation**:
   - **Retain `gemini-embedding-001` with 768 dimensions** as the primary baseline. It provides maximum budget defensibility, complete codebase compatibility, and the lowest cost per token.

---

## 2. Official Google Source References

The findings in this report were verified against official Google documentation and service terms:

- **Google Gemini API Pricing**: [https://ai.google.dev/pricing](https://ai.google.dev/pricing)
- **Gemini Embeddings Guide**: [https://ai.google.dev/gemini-api/docs/embeddings](https://ai.google.dev/gemini-api/docs/embeddings)
- **Gemini Model Specifications & Context Limits**: [https://ai.google.dev/gemini-api/docs/models/gemini](https://ai.google.dev/gemini-api/docs/models/gemini)
- **Google AI Studio Rate Limits & Quotas**: [https://ai.google.dev/gemini-api/docs/rate-limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- **Google AI Studio Terms of Service & Data Privacy**: [https://ai.google.dev/gemini-api/terms](https://ai.google.dev/gemini-api/terms)
- **Google AI Studio Console**: [https://aistudio.google.com/](https://aistudio.google.com/)

---

## 3. Comprehensive Model Comparison

| Feature / Dimension | `gemini-embedding-001` | `gemini-embedding-2` | Impact on Task 4 |
| :--- | :--- | :--- | :--- |
| **Model Status & Lifecycle** | Active, widely deployed stable text model | Active, newest multimodal model (includes preview) | Both are currently active and supported by Google. |
| **Primary Modalities** | Text-only (100+ natural languages, SQL, code) | Native Multimodal (Text, Images, Audio, Video, PDF) | Our corpus is pure text (PDF text extracts + Markdown). |
| **Input Token Context Limit** | **2,048 tokens** | **8,192 tokens** | Both easily fit Strategy A (500 tokens) and Strategy B (800 tokens). |
| **Native Vector Dimensionality** | 3,072 float dimensions | 3,072 float dimensions | Both provide deep semantic expressiveness. |
| **Matryoshka Truncation (MRL)** | Supported (3072, 1536, 768) | Supported (3072, 1536, 768, 128) | Both support our 768-dim baseline via `output_dimensionality`. |
| **MRL Output Normalization** | Full 3072 is pre-normalized; truncated 768 requires client-side normalization for dot product | **Automatically re-normalized** to unit length ($L_2 = 1.0$) upon truncation | In pgvector, cosine distance `<=>` normalizes natively. |
| **Asymmetric Task Types** | `RETRIEVAL_DOCUMENT`, `RETRIEVAL_QUERY`, `SEMANTIC_SIMILARITY`, `CLASSIFICATION`, etc. | `RETRIEVAL_DOCUMENT`, `RETRIEVAL_QUERY`, `SEMANTIC_SIMILARITY`, etc. | Both optimize vector geometry for document vs. search query distinction. |
| **Title Conditioning** | Supported (`title` parameter on `RETRIEVAL_DOCUMENT`) | Supported (`title` parameter on `RETRIEVAL_DOCUMENT`) | Both allow conditioning chunk embeddings on document/chapter titles. |
| **Pay-As-You-Go Pricing** | **$0.15** per 1,000,000 input tokens | **$0.20** per 1,000,000 input tokens | `gemini-embedding-001` is **25% cheaper**. |
| **Batch API Pricing** | **$0.075** per 1,000,000 input tokens (50% discount) | **$0.10** per 1,000,000 input tokens (50% discount) | High-volume offline ingestion can achieve further savings. |
| **SDK Method Signature** | `client.models.embed_content` | `client.models.embed_content` | Identical invocation pattern in `google-genai` SDK. |

---

## 4. Pricing, Quotas, and Privacy Findings

### 4.1 Cost Model for Task 4 Corpus
Based on our measured chunking manifest (`data/chunking-manifest.json`):
- **Strategy A Chunks**: 6,413 chunks $\times$ ~360 avg tokens $\approx 2,308,000$ tokens.
- **Strategy B Chunks**: 5,318 chunks $\times$ ~355 avg tokens $\approx 1,887,000$ tokens.
- **Total Corpus Volume**: $\approx 4,195,000$ tokens across both strategies.

| Model Candidate | Rate per 1M Tokens | Total Corpus Embedding Cost | With 50% Batch API Discount |
| :--- | :--- | :--- | :--- |
| **`gemini-embedding-001`** | **$0.15** | **~$0.63 USD** | **~$0.31 USD** |
| **`gemini-embedding-2`** | **$0.20** | **~$0.84 USD** | **~$0.42 USD** |

*Conclusion*: Total corpus embedding cost is negligible (<$1.00 USD) under either model, but `gemini-embedding-001` offers the lowest possible expenditure for budget defensibility.

### 4.2 Free Tier Availability & Uncertainty Statement
- Google AI Studio provides a free tier with rate limits (typically ~1,500 requests per day and 15 requests per minute).
- **Explicit Uncertainty Statement**: Model-specific free-tier quotas are dynamically assigned by Google based on user tier, project age, billing account linkage, and geographic availability. Free tier availability cannot be guaranteed across all projects without verifying the active Google Cloud project dashboard. We do not assume unlimited or guaranteed free access.

### 4.3 Inspecting Project-Specific Quotas (RPM, TPM, RPD)
To inspect your actual active quotas in Google AI Studio without guessing:
1. Navigate to **Google AI Studio**: [https://aistudio.google.com/](https://aistudio.google.com/).
2. Log in and select the target Google Cloud project from the top navigation bar.
3. In the left-hand navigation menu or project settings, select **Rate Limits / Quotas**.
4. Alternatively, in the **Google Cloud Console** (`console.cloud.google.com`):
   - Navigate to **APIs & Services > Generative Language API > Quotas & System Limits**.
   - Filter by metric:
     - `GenerateRequestsPerMinutePerProject` (RPM)
     - `TokensPerMinutePerProject` (TPM)
     - `RequestsPerDayPerProject` (RPD)
5. Review the assigned ceilings. Quotas apply project-wide across all API keys sharing that project.

### 4.4 Data Use Terms & Human Review (Privacy)

Under Google's official Gemini API Additional Terms of Service:

| Service Tier | Data Training Policy | Human Review Policy | Applicability to Task 4 |
| :--- | :--- | :--- | :--- |
| **Unpaid Tier (Free API)** | Google uses prompts, files, and outputs to improve Google products, services, and machine learning models. | **Human reviewers may read, annotate, and process** de-identified user inputs and outputs. | Safe for public open-source PostgreSQL documentation (no PII or secrets), but unsuitable for confidential data. |
| **Paid Tier (Pay-As-You-Go)** | Google **DOES NOT** use customer content or vector embeddings to train Google models. | **No human review** of customer data. | Best practice for enterprise compliance and private user search queries. |

*Official Warning*: Google advises users on unpaid tiers: *"Do not submit sensitive, confidential, or personal information to the Unpaid Services."*

---

## 5. Implementation & Codebase Compatibility Assessment

Our existing codebase was evaluated for compatibility with both model candidates:

### 5.1 `src/embeddings/config.py`
- Both `gemini-embedding-001` and `gemini-embedding-2` are registered as valid candidate models under `SUPPORTED_MODELS`.
- Deprecated models (`text-embedding-004`) are actively rejected by `EmbeddingConfig.__post_init__()`.
- Provisional default model is `gemini-embedding-001`, and default dimension is `768`.
- Safety gate `is_live_enabled = False` blocks unintentional external requests.
- **Status**: 100% compatible with both models.

### 5.2 `src/embeddings/gemini_provider.py`
- SDK Invocation: Uses `client.models.embed_content(model=self._model_name, contents=text, config=config)`.
- Parameter Forwarding: Correctly formats `output_dimensionality`, `task_type`, and `title`.
- Context Window Checks: `_validate_input_length()` enforces a 2,048-token heuristic proxy limit (matching `gemini-embedding-001`). If switching to `gemini-embedding-2`, `max_input_tokens` can be raised to 8,192 tokens.
- Vector Extraction: Handles both SDK object structures and dictionary responses.
- Validation: Verifies that returned vector lengths match configured dimensions and components are finite floats.
- **Status**: 100% compatible with both models.

### 5.3 `src/embeddings/cache.py`
- Composite Key: `CacheKey(content_hash, model_name, dimensions, task_type, title)`.
- Namespace Partitioning: Vectors for `gemini-embedding-001` and `gemini-embedding-2` are stored under distinct keys and cannot cross-contaminate.
- Legacy Fallback Lock: `allow_legacy=False` is strictly enforced for Gemini provider lookups, ensuring unlabelled 3-part legacy cache entries are never returned.
- **Status**: 100% compatible with both models.

### 5.4 `migrations/002_vector_storage.sql`
- Vector Column: `vector(/* __EMBEDDING_DIM__ */ 768)` in both `chunks` and `embedding_cache` tables.
- HNSW Index: `idx_chunks_embedding_hnsw_cosine` using `vector_cosine_ops` (`m=16`, `ef_construction=64`).
- **Status**: 100% compatible with both models when operating at 768 dimensions.

---

## 6. Impact of Changing Model or Dimensions

### 6.1 Vector Non-Interchangeability
Embeddings from different models (or even different dimensions of the same model) exist in mathematically distinct vector spaces:
- A query embedded with `gemini-embedding-2` **cannot** retrieve chunks embedded with `gemini-embedding-001`.
- A 768-dimension vector cannot be compared against a 1536- or 3072-dimension vector.

### 6.2 Cache Invalidation & Re-Embedding
- Because cache identity includes `model_name` and `dimensions`, switching models or dimensions will cause a 100% cache miss rate for prior vectors.
- The entire corpus (11,731 chunks) must be re-embedded to populate the cache under the new model.

### 6.3 Database Schema & Index Rebuild Impact
- If dimensions remain 768: Zero database schema changes or index rebuilds are required.
- If dimensions are changed to 1536 or 3072:
  1. The vector columns must be altered: `ALTER TABLE chunks ALTER COLUMN embedding TYPE vector(1536);`
  2. The HNSW index must be dropped and rebuilt:
     ```sql
     DROP INDEX idx_chunks_embedding_hnsw_cosine;
     CREATE INDEX idx_chunks_embedding_hnsw_cosine ON chunks USING hnsw (embedding vector_cosine_ops) WITH (m=16, ef_construction=64);
     ```
  3. Note: pgvector's HNSW implementation supports up to 2,000 dimensions. Native 3,072-dimension vectors **cannot** be indexed using pgvector HNSW indexes (exceeds the 2,000-dim ceiling), requiring MRL truncation to $\le 2000$ (e.g. 768 or 1536).

### 6.4 Evaluation Comparability (REQ-16, REQ-17)
- Similarity scores and thresholds ($s = 1 - d$) calibrated on one model do not transfer to another model.
- Switching models invalidates previously recorded similarity calibration tests and requires repeating the 30-question evaluation catalog.

---

## 7. Engineering Recommendation & Unresolved Questions

### Fact vs. Recommendation Separation

#### Verified Facts
1. `gemini-embedding-001` is cheaper ($0.15 vs $0.20 per 1M tokens).
2. Our corpus chunks never exceed 800 tokens, well below `gemini-embedding-001`'s 2,048 context window limit.
3. Our entire offline implementation, database migrations, and 153 unit/integration tests are built and verified around `gemini-embedding-001` at 768 dimensions.
4. Native 3,072 dimensions exceed PostgreSQL pgvector's HNSW index ceiling of 2,000 dimensions; 768 dimensions fits cleanly within pgvector constraints.

#### Engineering Recommendation
**Retain `gemini-embedding-001` at 768 dimensions as the primary project baseline.**
- **Rationale**: It satisfies every assessment requirement with lowest token cost, fits our chunk sizes perfectly, requires zero refactoring or migration edits, and guarantees full compliance with PostgreSQL pgvector index ceilings.
- `gemini-embedding-2` remains an excellent future upgrade candidate if multimodal document ingestion (e.g., embedded diagram images in PDFs) is pursued as a stretch goal.

### Open Questions Requiring User Confirmation Prior to Live Canary
1. **Google AI Studio Project & Key**: Has a Google Cloud / Google AI Studio project been created, and is an API key available for live execution?
2. **Quota Verification**: What are the actual assigned RPM and TPM quotas on the student's Google AI Studio project?
3. **Billing Tier Preference**: Will the canary run under Google AI Studio's free tier (subject to human review of de-identified inputs) or on a billing-enabled project (guaranteed private, zero model training)?
