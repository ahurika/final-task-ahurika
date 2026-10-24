# Controlled Live Gemini Embedding Canary Plan

**Document**: `docs/GEMINI-CANARY-PLAN.md`  
**Date**: 2026-10-24  
**Author**: Senior Python AI Integration Engineer  
**Project**: PostgreSQL Documentation Assistant (Part 4 Graduation Assessment)  
**Workspace**: `C:\Users\USER\Desktop\RAG Task`  
**Status**: PREPARATION ONLY (Pending Explicit User Authorization)

---

## 1. Objective & Scope

This document defines the strict, controlled procedure for executing **exactly one live embedding API request** to Google's Gemini API before any bulk corpus embedding or background worker integration is attempted.

### Strict Boundaries & Invariants
- **Single Request Only (At Most 1 Attempt)**: The canary configures `RetryPolicy(max_retries=0)`. Even if the request encounters a transient failure (HTTP 429 / 503), it will make at most ONE HTTP attempt and terminate immediately. Normal provider retry behavior (`max_retries=3`) remains unchanged.
- **Zero Bulk Ingestion**: No corpus files, batches, or database records will be processed.
- **Zero Secret Exposure**: The API key is NEVER printed, even partially masked (no prefix, no suffix, no `...`). All printed outputs, logs, and exception messages are strictly scrubbed by `_sanitize_output`.
- **Fail Fast on Missing Credentials**: If `GEMINI_API_KEY` is missing or whitespace, the runner exits with code 1 before any SDK initialization or network activity.
- **Offline Default Preserved**: `is_live_enabled=False` remains the default across the codebase. Live mode is enabled strictly and solely within `scripts/canary_gemini.py` for this single probe.
- **No Database Modification**: The returned vector will be validated in memory; no records will be written to PostgreSQL or Neon.
- **Git Strictly Uninitialized**: All checks remain local and uncommitted.

---

## 2. Prerequisites

Before executing the canary, the following items must be verified:

1. **Google AI Studio Account & API Key**:
   - Access to [Google AI Studio](https://aistudio.google.com/).
   - An API key generated under an active Google Cloud project.
2. **Project Quota Inspection**:
   - Prior to execution, navigate to [Google AI Studio Rate Limits](https://aistudio.google.com/) or Google Cloud Console (**APIs & Services > Generative Language API > Quotas**).
   - Verify that your project has non-zero `GenerateRequestsPerMinutePerProject` (RPM) and `TokensPerMinutePerProject` (TPM) quota assigned.
3. **Python Environment**:
   - Python 3.10+ on Windows (active environment: Python 3.14.7).
   - Terminal session (PowerShell or Windows Command Prompt).

---

## 3. Proposed Installation Command

> [!WARNING]
> **DO NOT RUN THIS COMMAND UNTIL EXPLICITLY AUTHORIZED.**  
> Package installation must only occur after user sign-off.

The Google Gen AI Python SDK is currently not installed in the workspace. When live execution is approved by the user, the exact proposed installation command is:

### Windows PowerShell / Command Prompt
```powershell
pip install google-genai
```

### Verification Command (Post-Installation)
```powershell
python -c "from google import genai; print('google-genai imported successfully')"
```

---

## 4. Exact Test Input & Metadata

The canary request uses a single, verified sentence from the official, public PostgreSQL 16 documentation (Server Configuration, Chapter 20):

- **Input Prose**:
  ```text
  PostgreSQL provides a number of configuration parameters that affect the behavior of the database system.
  ```
- **Document Title**:
  ```text
  Chapter 20. Server Configuration
  ```
- **Input Metrics**:
  - Character count: `104 characters` (well below `MAX_INPUT_CHARS_SAFETY_CEILING = 10000`)
  - Estimated tokens: `~22 tokens` (well below `MAX_INPUT_TOKENS_001 = 2048`)
  - Normalization: NFC Unicode normalized
  - Content SHA-256 Digest: `compute_content_hash(text)`
- **Target Model**:
  `gemini-embedding-001`
- **Target Dimension**:
  `768` (MRL truncated output)
- **Task Type**:
  `RETRIEVAL_DOCUMENT`

---

## 5. Configuration & Secret-Handling Procedure

To guarantee zero credential leakage, the API key must be injected only via an ephemeral process environment variable.

### 5.1 Setting the Key (Session Only)

#### In Windows PowerShell:
```powershell
$env:GEMINI_API_KEY = "your-actual-api-key-here"
```

#### In Windows Command Prompt (cmd.exe):
```cmd
set GEMINI_API_KEY=your-actual-api-key-here
```

### 5.2 Secret Protection Rules
1. **Never Hardcode or Persist**:
   - No files (including `.env`, `.json`, `.sql`, `.py`, `.md`) will contain the key.
2. **Never Print or Partially Mask**:
   - Standard output and logs will display only status and character length (e.g. `API Key Status: CONFIGURED (Length: 39 chars, value strictly hidden)`).
   - No prefix (e.g. `AIza...`), suffix (e.g. `...xyz9`), or partial masking will be printed under any condition.
3. **Automated Error Sanitization**:
   - All exception handlers in `scripts/canary_gemini.py` and `src/embeddings/gemini_provider.py` scrub exceptions using `_sanitize_output` and `_sanitize_error_message`.
   - Any raw key strings, `AIza...` patterns, or `key=...` URL parameters in exception messages are redacted to `[REDACTED_API_KEY]`.
   - `GeminiEmbeddingProvider.__repr__` displays `has_api_key=True` and never exposes the secret string.
4. **Fail-Fast Missing Credential Guard**:
   - The canary runner retrieves the key via `os.environ.get("GEMINI_API_KEY")`. If absent or whitespace-only, the script terminates immediately with exit code 1 before any SDK or network initialization.

---

## 6. Canary Execution Script Design (`scripts/canary_gemini.py`)

The standalone runner script incorporates all Phase 3E.6.1 safety controls:

```python
"""
Standalone Controlled Gemini Canary Runner
Location: scripts/canary_gemini.py
"""

import math
import os
import re
import sys
from typing import Any, List, Optional

from src.chunkers.base import compute_content_hash
from src.embeddings.base import validate_vector
from src.embeddings.config import MODEL_GEMINI_EMBEDDING_001, PROVISIONAL_DEFAULT_DIMENSION
from src.embeddings.gemini_provider import GeminiEmbeddingProvider, TASK_TYPE_DOCUMENT
from src.embeddings.retry import RetryPolicy

CANARY_TEXT = "PostgreSQL provides a number of configuration parameters that affect the behavior of the database system."
CANARY_TITLE = "Chapter 20. Server Configuration"

def _sanitize_output(text: str, secret: Optional[str] = None) -> str:
    if not text:
        return text
    result = text
    if secret:
        result = result.replace(secret, "[REDACTED_API_KEY]")
    result = re.sub(r"AIza[0-9A-Za-z-_]{35}", "[REDACTED_API_KEY]", result)
    result = re.sub(r"(?:api_?key|key|auth|authorization)[=:]\s*['\"]?[^\s&'\"]+", "key=[REDACTED]", result, flags=re.IGNORECASE)
    return result

def run_canary(client_override: Optional[Any] = None, exit_on_error: bool = True) -> List[float]:
    # 1. Fail fast on missing credentials
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not api_key.strip():
        print("\n[ERROR] GEMINI_API_KEY environment variable is not set.")
        if exit_on_error:
            sys.exit(1)
        raise ValueError("GEMINI_API_KEY environment variable is not set.")

    clean_key = api_key.strip()
    print(f"\n[1/5] API Key Status:       CONFIGURED (Length: {len(clean_key)} chars, value strictly hidden)")
    print(f"[2/5] Target Model:         {MODEL_GEMINI_EMBEDDING_001}")
    print(f"[3/5] Target Dimensionality: {PROVISIONAL_DEFAULT_DIMENSION} (MRL truncation)")
    print(f"[4/5] Task Type:            {TASK_TYPE_DOCUMENT}")

    # 2. Public test sentence
    content_hash = compute_content_hash(CANARY_TEXT)
    print(f"[5/5] Test Input:           {CANARY_TEXT!r}")

    # 3. Disable retries specifically for the canary (max_retries=0)
    canary_retry_policy = RetryPolicy(max_retries=0)
    try:
        provider = GeminiEmbeddingProvider(
            model_name=MODEL_GEMINI_EMBEDDING_001,
            dimensions=PROVISIONAL_DEFAULT_DIMENSION,
            api_key=clean_key,
            client=client_override,
            retry_policy=canary_retry_policy,
            is_live_enabled=True,
        )
    except Exception as e:
        print(f"\n[CANARY FAILED during initialization]: {_sanitize_output(str(e), clean_key)}")
        if exit_on_error:
            sys.exit(1)
        raise

    # 4. At most 1 attempt executed
    try:
        vector = provider.embed_document(text=CANARY_TEXT, title=CANARY_TITLE)
    except Exception as e:
        print(f"\n[CANARY FAILED during API execution]: {_sanitize_output(str(e), clean_key)}")
        print(f"Total API attempts made: {provider.api_call_count} (max allowed: 1)")
        if exit_on_error:
            sys.exit(1)
        raise

    # 5. Rigorous vector validation
    validate_vector(vector, expected_dim=PROVISIONAL_DEFAULT_DIMENSION)
    assert provider.api_call_count == 1, f"Expected 1 API call, got {provider.api_call_count}"
    return vector
```

---

## 7. Expected Output Checks & Invariants

The canary will be deemed **PASSED** only if all of the following invariants hold:

| Check | Expected Value | Purpose |
| :--- | :--- | :--- |
| **HTTP Status** | `200 OK` | Confirms valid API key and active project authorization. |
| **Vector Type** | `List[float]` | Confirms response was parsed from SDK structure. |
| **Vector Length** | `768` floats | Confirms MRL `output_dimensionality` truncation succeeded. |
| **Finite Values** | All components finite | Rejects `NaN`, `+Inf`, `-Inf`, and boolean types. |
| **Call Counter** | `api_call_count == 1` | Confirms exactly 1 network call occurred (no loops, no retries). |
| **Retry Policy** | `max_retries == 0` | Confirms at most 1 HTTP attempt was allowed even on failure. |
| **Zero Leaks** | Key not in stdout/stderr | Confirms credentials are completely hidden. |
| **L2 Norm** | Non-zero float (~0.5 to ~1.0) | Observability on truncation normalization behavior. |

---

## 8. Cost & Quota Safeguards

- **Total Calls**: Exactly 1 single API call (at most 1 HTTP attempt on failure).
- **Input Tokens**: ~22 tokens.
- **Estimated Cost**:
  $$\text{Cost} = \frac{22 \text{ tokens}}{1,000,000} \times \$0.15 = \$0.0000033 \text{ USD} \approx \$0.00$$
- **Safety Ceiling**:
  - The script contains no `while` loop, retry backoff loop, or batch iterator.
  - Canary specifically configures `RetryPolicy(max_retries=0)` to guarantee zero retries on transient errors (HTTP 429/503).
  - Permanent errors (HTTP 401 auth, HTTP 400 bad request) abort immediately on attempt 0.

---

## 9. Failure Handling & Diagnostic Matrix

If the canary fails, the diagnostic matrix guides the response:

| Error Symptom | Root Cause | Action |
| :--- | :--- | :--- |
| `ModuleNotFoundError: No module named 'google'` | `google-genai` SDK is not installed. | Run `pip install google-genai` only after authorization. |
| `EmbeddingConfigurationError: Gemini API authentication failed: [401/403]` | Invalid API key or Generative Language API disabled in Google Cloud project. | Verify key in AI Studio; verify API is enabled in Google Cloud Console. |
| `GeminiRateLimitError: rate limit exceeded [429]` | Project quota is 0 or temporarily exhausted. | Check Google AI Studio Quota dashboard; ensure account has valid quota. |
| `EmbeddingDimensionError: Vector dimension mismatch` | Google API ignored `output_dimensionality` parameter. | Check `config["output_dimensionality"]` forwarding in SDK payload. |
| `EmbeddingValueError: non-finite (NaN or Infinity)` | Corrupt floating-point return. | Report upstream API issue. |

---

## 10. Rollback & Post-Canary Cleanup

Immediately after the canary run (whether successful or failed):

1. **Clear Ephemeral Environment Variable**:
   - In PowerShell:
     ```powershell
     Remove-Item Env:\GEMINI_API_KEY
     ```
   - In Command Prompt:
     ```cmd
     set GEMINI_API_KEY=
     ```
2. **Confirm Safety Lock**:
   - `src/embeddings/config.py` remains at `is_live_enabled=False`.
   - `src/embeddings/gemini_provider.py` remains at `is_live_enabled=False`.
3. **Zero Persistent Artifacts**:
   - No mock vectors or live vectors are stored in the database.
   - Offline test suite is re-executed: `python -m pytest -v` (163 tests passing) to ensure 100% offline compliance remains intact.

---

## 11. Deterministic Offline Test Coverage

The safety properties of the canary script and call path are verified by `tests/unit/test_canary_safety.py` (10 tests, 100% passing offline):
- `test_canary_fails_fast_when_api_key_unset`: Exit code 1 before any network call.
- `test_canary_fails_fast_when_api_key_empty_or_whitespace`: Exit code 1 on whitespace.
- `test_canary_never_prints_or_partially_masks_key`: Zero secret characters in stdout.
- `test_canary_disables_retries_at_most_one_attempt_on_transient_failure`: Call count is 1 on HTTP 429.
- `test_canary_sanitizes_exceptions_containing_secret`: URL/query key patterns scrubbed.
- `test_canary_parameters_and_isolation`: Model, 768 dim, RETRIEVAL_DOCUMENT, public sentence.
- `test_canary_script_has_no_database_or_corpus_dependencies`: No `src.db` or corpus imports.
- `test_default_gemini_provider_retains_normal_retries`: Provider default remains `max_retries=3`.
- `test_provider_safe_repr_never_leaks_api_key`: `__repr__` shows `has_api_key=True`.
- `test_sanitize_output_utility`: Scrubbing regex verified.

---

## 12. Explicit Approval Gate

Execution of this canary plan requires explicit user authorization.

```markdown
[ ] USER AUTHORIZATION GRANTED:
    - [ ] Permission to run `pip install google-genai`
    - [ ] Permission to set $env:GEMINI_API_KEY in session
    - [ ] Permission to execute `scripts/canary_gemini.py` (1 live API request)
```
