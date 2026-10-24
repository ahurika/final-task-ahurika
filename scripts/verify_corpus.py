#!/usr/bin/env python3
"""
Corpus Verification Script: Task 4 (RAG Graduation Assessment)
Location: scripts/verify_corpus.py

Purpose:
Deterministically verifies that the collected official PostgreSQL 16 documentation
file exists on disk, is a readable and non-corrupt PDF, computes its cryptographic
SHA-256 hash and byte size, counts its actual pages using pypdf, verifies it meets
the minimum assessment threshold of >= 100 pages (REQ-01), and updates
data/corpus-manifest.json with empirical evidence.
"""

import os
import sys
import json
import hashlib
from datetime import datetime, timezone

# Ensure project root is determined dynamically relative to script location
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
RELATIVE_PDF_PATH = os.path.join("data", "raw", "postgresql-16-US.pdf")
FULL_PDF_PATH = os.path.join(PROJECT_ROOT, RELATIVE_PDF_PATH)
MANIFEST_PATH = os.path.join(PROJECT_ROOT, "data", "corpus-manifest.json")

MINIMUM_REQUIRED_PAGES = 100
OFFICIAL_SOURCE_URL = "https://www.postgresql.org/files/documentation/pdf/16/postgresql-16-US.pdf"
DOCUMENTATION_HUB_URL = "https://www.postgresql.org/docs/16/"
LICENSE_URL = "https://www.postgresql.org/about/licence/"
LICENSE_NAME = "PostgreSQL License (OSI-approved, BSD-style permissive)"
POSTGRESQL_VERSION = "16 (US Letter format)"


def compute_sha256(filepath: str) -> str:
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(1024 * 1024):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_corpus(update_manifest: bool = True) -> int:
    print("=" * 70)
    print("TASK 4 — REPRODUCIBLE CORPUS SIZE VERIFICATION (REQ-01)")
    print("=" * 70)
    print(f"Project Root         : {PROJECT_ROOT}")
    print(f"Target PDF Path      : {FULL_PDF_PATH}")
    print(f"Minimum Page Target  : {MINIMUM_REQUIRED_PAGES} pages")
    print("-" * 70)

    # 1. Check existence
    if not os.path.exists(FULL_PDF_PATH):
        print(f"FAIL: Corpus file not found at: {FULL_PDF_PATH}", file=sys.stderr)
        return 1

    # 2. Check file size
    file_size_bytes = os.path.getsize(FULL_PDF_PATH)
    if file_size_bytes == 0:
        print("FAIL: Corpus file is 0 bytes (empty).", file=sys.stderr)
        return 1

    # 3. Check PDF magic bytes
    with open(FULL_PDF_PATH, "rb") as f:
        magic = f.read(5)
    if magic != b"%PDF-":
        print(f"FAIL: Invalid PDF magic bytes. Expected b'%PDF-', got {magic!r}.", file=sys.stderr)
        return 1

    # 4. Compute SHA-256 hash dynamically
    print("Computing SHA-256 checksum...")
    sha256_hash = compute_sha256(FULL_PDF_PATH)

    # 5. Check PDF parsing library and count pages
    try:
        from pypdf import PdfReader
    except ImportError:
        print("FAIL: 'pypdf' library is not installed. Install via: pip install pypdf", file=sys.stderr)
        return 1

    print("Opening PDF and parsing page catalog via pypdf...")
    try:
        reader = PdfReader(FULL_PDF_PATH)
        actual_page_count = len(reader.pages)
    except Exception as e:
        print(f"FAIL: Could not parse PDF structure: {e}", file=sys.stderr)
        return 1

    # 6. Compare against assessment minimum
    is_sufficient = actual_page_count >= MINIMUM_REQUIRED_PAGES
    status_str = "PASSED" if is_sufficient else "FAILED"

    print("-" * 70)
    print(f"File Name            : {os.path.basename(FULL_PDF_PATH)}")
    print(f"File Size            : {file_size_bytes:,} bytes (~{file_size_bytes / (1024 * 1024):.2f} MB)")
    print(f"SHA-256 Hash         : {sha256_hash}")
    print(f"Measured Page Count  : {actual_page_count:,} pages")
    print(f"Assessment Minimum   : {MINIMUM_REQUIRED_PAGES} pages")
    print(f"Verification Result  : {status_str} ({actual_page_count:,} >= {MINIMUM_REQUIRED_PAGES})")
    print("-" * 70)

    if not is_sufficient:
        print(f"FAIL: Measured page count ({actual_page_count}) is below minimum ({MINIMUM_REQUIRED_PAGES}).", file=sys.stderr)
        return 1

    # 7. Write/Update manifest if requested
    if update_manifest:
        os.makedirs(os.path.dirname(MANIFEST_PATH), exist_ok=True)
        manifest_data = {
            "schema_version": "1.0",
            "verification_status": "VERIFIED_PASSED",
            "verification_timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "target_corpus": {
                "name": "Official PostgreSQL Documentation",
                "version": POSTGRESQL_VERSION,
                "format": "PDF (US Letter)",
                "official_source_url": OFFICIAL_SOURCE_URL,
                "documentation_hub_url": DOCUMENTATION_HUB_URL,
                "license": {
                    "name": LICENSE_NAME,
                    "url": LICENSE_URL,
                    "permissive": True
                }
            },
            "verified_artifact": {
                "relative_path": RELATIVE_PDF_PATH.replace("\\", "/"),
                "file_name": os.path.basename(FULL_PDF_PATH),
                "file_size_bytes": file_size_bytes,
                "file_size_mb": round(file_size_bytes / (1024 * 1024), 2),
                "sha256_hash": sha256_hash,
                "measured_page_count": actual_page_count,
                "minimum_required_pages": MINIMUM_REQUIRED_PAGES,
                "meets_requirement_req_01": is_sufficient
            },
            "pipeline_status_note": (
                "Corpus collection and page count verified. "
                "Document parsing, chunking, and embedding ingestion pipelines are NOT yet implemented."
            )
        }

        with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2)

        print(f"Manifest written to  : {MANIFEST_PATH}")

    print("=" * 70)
    print("SUCCESS: REQ-01 Corpus size requirement mathematically verified.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(verify_corpus())
