"""
Standalone Controlled Gemini Canary Runner
Location: scripts/canary_gemini.py

Purpose:
Executes exactly ONE live embedding request against Google's Gemini API
to verify network authorization, SDK compatibility, MRL 768-dim truncation,
and vector geometry before background ingestion or corpus processing.

Boundaries & Safety Invariants:
- Single request only: max_retries=0 specifically for the canary (at most 1 HTTP attempt).
- Requires GEMINI_API_KEY environment variable (fails fast before SDK or network initialization).
- Zero secret leakage: API key is never printed or partially masked; exceptions are sanitized.
- Purely public test input: one sentence from PostgreSQL documentation.
- No corpus processing, batch ingestion, or database writes.
- Requires explicit user authorization before execution.
"""

import math
import os
import re
import sys
from typing import Any, List, Optional

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.chunkers.base import compute_content_hash
from src.embeddings.base import validate_vector
from src.embeddings.config import (
    MODEL_GEMINI_EMBEDDING_001,
    PROVISIONAL_DEFAULT_DIMENSION,
)
from src.embeddings.gemini_provider import GeminiEmbeddingProvider, TASK_TYPE_DOCUMENT
from src.embeddings.retry import RetryPolicy

# Public PostgreSQL documentation sentence (Chapter 20: Server Configuration)
CANARY_TEXT = "PostgreSQL provides a number of configuration parameters that affect the behavior of the database system."
CANARY_TITLE = "Chapter 20. Server Configuration"

# Default location for project-root .env file
DEFAULT_DOTENV_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))


def load_env_file(
    dotenv_path: Optional[str] = None,
    override: bool = False,
) -> bool:
    """
    Loads environment variables from a .env file into os.environ.
    Uses python-dotenv if installed, with a self-contained fallback parser.

    Args:
        dotenv_path: Optional path to the .env file. Defaults to DEFAULT_DOTENV_PATH.
        override: Whether to overwrite variables already present in os.environ (default: False).

    Returns:
        True if the file was found and processed, False otherwise.
    """
    target_path = dotenv_path if dotenv_path is not None else DEFAULT_DOTENV_PATH
    if not os.path.isfile(target_path):
        return False

    try:
        from dotenv import load_dotenv as _dotenv_load
        return bool(_dotenv_load(dotenv_path=target_path, override=override))
    except ImportError:
        # Robust pure-Python fallback parser for KEY=VALUE pairs
        loaded_any = False
        with open(target_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, val = line.split("=", 1)
                    key = key.strip()
                    val = val.strip()
                    if len(val) >= 2 and (
                        (val.startswith('"') and val.endswith('"'))
                        or (val.startswith("'") and val.endswith("'"))
                    ):
                        val = val[1:-1]
                    if key and (override or key not in os.environ):
                        os.environ[key] = val
                        loaded_any = True
        return loaded_any


def _sanitize_output(text: str, secret: Optional[str] = None) -> str:
    """
    Ensures no API key, partial key, or authorization credential appears in output.
    Redacts exact secret string and common API key patterns.
    """
    if not text:
        return text
    result = text
    if secret:
        result = result.replace(secret, "[REDACTED_API_KEY]")
    # Scrub common Gemini API key formats (AIzaSy...)
    result = re.sub(r"AIza[0-9A-Za-z-_]{35}", "[REDACTED_API_KEY]", result)
    # Scrub query param or header patterns
    result = re.sub(
        r"(?:api_?key|key|auth|authorization)[=:]\s*['\"]?[^\s&'\"]+",
        "key=[REDACTED]",
        result,
        flags=re.IGNORECASE,
    )
    return result


def run_canary(
    client_override: Optional[Any] = None,
    exit_on_error: bool = True,
    dotenv_path: Optional[str] = None,
    load_env: bool = True,
) -> List[float]:
    """
    Executes a single controlled Gemini embedding request.

    Args:
        client_override: Optional injected client for deterministic offline testing.
        exit_on_error: If True, calls sys.exit(1) on failure. If False, raises exception.
        dotenv_path: Optional custom path to .env file (defaults to project-root .env).
        load_env: If True, loads variables from .env file before checking environment.

    Returns:
        List of 768 floats representing the generated embedding vector.
    """
    print("=" * 70)
    print("  GEMINI EMBEDDING PROVIDER -- CONTROLLED LIVE CANARY PROBE")
    print("=" * 70)

    # 0. Load local .env file if enabled and present (override=False preserves active process environment)
    if load_env:
        load_env_file(dotenv_path=dotenv_path, override=False)

    # 1. Environment variable inspection (fails fast before SDK or network initialization)
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key or not api_key.strip():
        print("\n[ERROR] GEMINI_API_KEY environment variable is not set.")
        print("Please set it in your session before running the canary:")
        print("  PowerShell: $env:GEMINI_API_KEY = \"your-api-key\"")
        print("  CMD:        set GEMINI_API_KEY=your-api-key\n")
        if exit_on_error:
            sys.exit(1)
        raise ValueError("GEMINI_API_KEY environment variable is not set.")

    clean_key = api_key.strip()

    # Security check: never print or partially mask the key. Report status and length only.
    print(f"\n[1/5] API Key Status:       CONFIGURED (Length: {len(clean_key)} chars, value strictly hidden)")
    print(f"[2/5] Target Model:         {MODEL_GEMINI_EMBEDDING_001}")
    print(f"[3/5] Target Dimensionality: {PROVISIONAL_DEFAULT_DIMENSION} (MRL truncation)")
    print(f"[4/5] Task Type:            {TASK_TYPE_DOCUMENT}")

    # 2. Input data specification (single public documentation sentence)
    content_hash = compute_content_hash(CANARY_TEXT)
    print(f"[5/5] Test Input:           {CANARY_TEXT!r}")
    print(f"      Document Title:       {CANARY_TITLE!r}")
    print(f"      Content SHA-256:      {content_hash}")

    # 3. Instantiate provider with max_retries=0 (disabling retries specifically for the canary)
    print("\n[CANARY] Initializing GeminiEmbeddingProvider with is_live_enabled=True...")
    canary_retry_policy = RetryPolicy(max_retries=0)
    try:
        provider = GeminiEmbeddingProvider(
            model_name=MODEL_GEMINI_EMBEDDING_001,
            dimensions=PROVISIONAL_DEFAULT_DIMENSION,
            api_key=clean_key,
            client=client_override,
            retry_policy=canary_retry_policy,
            is_live_enabled=True,  # Explicitly enabled for this controlled probe
        )
    except Exception as e:
        sanitized_msg = _sanitize_output(str(e), clean_key)
        print(f"\n[CANARY FAILED during initialization]: {sanitized_msg}")
        if exit_on_error:
            sys.exit(1)
        raise

    # 4. Execute single live embedding call (at most 1 HTTP attempt)
    print("[CANARY] Submitting single live embedding request to Google Gemini API...")
    try:
        vector = provider.embed_document(text=CANARY_TEXT, title=CANARY_TITLE)
    except Exception as e:
        sanitized_msg = _sanitize_output(str(e), clean_key)
        print(f"\n[CANARY FAILED during API execution]: {sanitized_msg}")
        print(f"Total API attempts made: {provider.api_call_count} (max allowed: 1)")
        if exit_on_error:
            sys.exit(1)
        raise

    # 5. Rigorous validation of returned vector
    print("\n[CANARY] Validating returned vector invariants...")
    try:
        validate_vector(vector, expected_dim=PROVISIONAL_DEFAULT_DIMENSION)
    except Exception as e:
        sanitized_msg = _sanitize_output(str(e), clean_key)
        print(f"\n[CANARY FAILED validation]: {sanitized_msg}")
        if exit_on_error:
            sys.exit(1)
        raise

    if provider.api_call_count != 1:
        msg = f"Canary invariant violated: expected exactly 1 API call, got {provider.api_call_count}"
        print(f"\n[CANARY FAILED]: {msg}")
        if exit_on_error:
            sys.exit(1)
        raise RuntimeError(msg)

    # Calculate L2 norm for mathematical observability
    l2_norm = math.sqrt(sum(x * x for x in vector))

    print("\n" + "=" * 70)
    print("  CANARY PROBE SUCCEEDED -- ALL INVARIANTS VERIFIED")
    print("=" * 70)
    print(f"  HTTP Call Count:    {provider.api_call_count} (Single controlled request)")
    print(f"  Vector Type:        {type(vector).__name__}")
    print(f"  Vector Dimension:   {len(vector)} floats (matches {PROVISIONAL_DEFAULT_DIMENSION})")
    print(f"  Vector L2 Norm:     {round(l2_norm, 6)}")
    print(f"  First 5 Components: {[round(x, 6) for x in vector[:5]]}")
    print("=" * 70)
    print("\nRemember to clear your session key when finished:")
    print("  PowerShell: Remove-Item Env:\\GEMINI_API_KEY")
    print("  CMD:        set GEMINI_API_KEY=\n")
    return vector


if __name__ == "__main__":
    run_canary()
