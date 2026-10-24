# Task 4 — Phase 4C: 30-Question Evaluation & Benchmark Report

**Execution Date (UTC):** 2026-10-24T13:03:25.095312+00:00  

**Execution Mode:** **OFFLINE PIPELINE MECHANICS AUDIT ONLY** (Deterministic Mock Vectors)  

**Embedding Model:** `mock-deterministic-v1` (768 dimensions, is_mock=True)  

**Semantic Status:** `UNMEASURED (Requires Live Embedding Provider)`  

**Total Indexed Chunks:** 464  

**Total Matrix Evaluations:** 120 (4 configurations × 30 questions)  

**Runtime:** 15.46 seconds


---

## 1. Summary Benchmark Matrix (REQ-15, REQ-16)

| Configuration | Strategy | k | Retrieval Recall@k | Refusal Accuracy (%) | Provenance Integrity | Avg Latency (ms) | Avg Cost ($) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Config 1** | Strategy A (Fixed 500/50) | 3 | 16.0% (4/25) | 100.0% (5/5) | 100.0% | 144.28 ms | $0.000000 |
| **Config 2** | Strategy A (Fixed 500/50) | 5 | 20.0% (5/25) | 100.0% (5/5) | 100.0% | 148.84 ms | $0.000000 |
| **Config 3** | Strategy B (Structure-Aware) | 3 | 16.0% (4/25) | 100.0% (5/5) | 100.0% | 119.37 ms | $0.000000 |
| **Config 4** | Strategy B (Structure-Aware) | 5 | 20.0% (5/25) | 100.0% (5/5) | 100.0% | 102.29 ms | $0.000000 |

---

## 2. Refusal Heuristic Confusion Breakdown

The similarity-threshold refusal heuristic was evaluated with $\theta_{\text{sim}} = 0.65$:

| Configuration | True Refusals (TR) | Missed Refusals (MR) | False Refusals (FR) | True Acceptances (TA) | Refusal Accuracy | False Refusal Rate |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| `fixed_window (k=3)` | 5/5 | 0/5 | 25/25 | 0/25 | 100.0% | 100.0% |
| `fixed_window (k=5)` | 5/5 | 0/5 | 25/25 | 0/25 | 100.0% | 100.0% |
| `structure_aware (k=3)` | 5/5 | 0/5 | 25/25 | 0/25 | 100.0% | 100.0% |
| `structure_aware (k=5)` | 5/5 | 0/5 | 25/25 | 0/25 | 100.0% | 100.0% |

---

## 3. Mandatory Measurement Integrity Disclosure

> [!IMPORTANT]

> **Measurement Integrity Policy Adherence**:

> - **Zero Fabricated Scores**: The benchmark numbers above reflect offline retrieval engine execution using `DeterministicMockEmbeddingProvider`.

> - **Non-Semantic Warning**: Deterministic mock vectors are cryptographically seeded pseudo-random unit vectors. They **do not model semantic relevance**.

> - **Unmeasured Deliverables**:

>   - *Answer Correctness*: Marked `Not Measurable (Pending Generation)` because generative LLM calls are deferred.

>   - *Answer Citation Precision*: Marked `Not Measurable (Pending Generation)`.

>   - *Semantic Retrieval Recall*: Semantic relevance cannot be claimed until genuine `gemini-embedding-001` vectors are populated via authorized canary/batch ingestion.
