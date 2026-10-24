"""
Corpus Download Script: PostgreSQL 16 Official US Letter PDF
Target: data/raw/postgresql-16-US.pdf
Source: https://www.postgresql.org/files/documentation/pdf/16/postgresql-16-US.pdf
"""

import os
import sys
import hashlib
import urllib.request
import urllib.error

OFFICIAL_PDF_URL = "https://www.postgresql.org/files/documentation/pdf/16/postgresql-16-US.pdf"
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGET_DIR = os.path.join(PROJECT_ROOT, "data", "raw")
TARGET_FILE = os.path.join(TARGET_DIR, "postgresql-16-US.pdf")


def download_pdf():
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Ensuring target directory exists: {TARGET_DIR}")
    os.makedirs(TARGET_DIR, exist_ok=True)

    print(f"Connecting to official HTTPS source:\n  {OFFICIAL_PDF_URL}")
    req = urllib.request.Request(
        OFFICIAL_PDF_URL,
        headers={"User-Agent": "PostgreSQL-RAG-Project-Downloader/1.0 (Academic/Graduation Assessment)"}
    )

    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            if response.status != 200:
                print(f"ERROR: Received non-200 HTTP status: {response.status}", file=sys.stderr)
                sys.exit(1)

            content_type = response.headers.get("Content-Type", "")
            content_length = response.headers.get("Content-Length")
            total_bytes = int(content_length) if content_length else None

            print(f"HTTP Status: {response.status}")
            print(f"Content-Type: {content_type}")
            if total_bytes:
                print(f"Content-Length: {total_bytes:,} bytes (~{total_bytes / (1024 * 1024):.2f} MB)")
            else:
                print("Content-Length header not provided by server; streaming until completion...")

            hasher = hashlib.sha256()
            downloaded_bytes = 0
            chunk_size = 1024 * 1024  # 1 MB chunks

            with open(TARGET_FILE, "wb") as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    hasher.update(chunk)
                    downloaded_bytes += len(chunk)
                    if total_bytes:
                        pct = (downloaded_bytes / total_bytes) * 100
                        print(f"\rDownloaded {downloaded_bytes:,} / {total_bytes:,} bytes ({pct:.1f}%)...", end="", flush=True)
                    else:
                        print(f"\rDownloaded {downloaded_bytes:,} bytes...", end="", flush=True)

            print("\nDownload complete.")

    except urllib.error.URLError as e:
        print(f"\nERROR: Network transfer failed: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\nERROR: Unexpected failure during download: {e}", file=sys.stderr)
        sys.exit(1)

    # Verification of downloaded file
    if not os.path.exists(TARGET_FILE):
        print(f"ERROR: Expected target file does not exist on disk: {TARGET_FILE}", file=sys.stderr)
        sys.exit(1)

    file_size = os.path.getsize(TARGET_FILE)
    if file_size == 0:
        print("ERROR: Downloaded file is empty (0 bytes).", file=sys.stderr)
        sys.exit(1)

    # Check magic bytes for PDF
    with open(TARGET_FILE, "rb") as f:
        magic_bytes = f.read(5)
    if magic_bytes != b"%PDF-":
        print(f"ERROR: File does not appear to be a valid PDF (header was {magic_bytes!r}).", file=sys.stderr)
        sys.exit(1)

    sha256_hash = hasher.hexdigest()
    print("=" * 60)
    print("SUCCESSFUL DOWNLOAD VERIFICATION")
    print(f"File: {TARGET_FILE}")
    print(f"Size: {file_size:,} bytes (~{file_size / (1024 * 1024):.2f} MB)")
    print(f"Magic Header: {magic_bytes.decode('ascii', errors='ignore')} (Valid PDF header)")
    print(f"SHA-256: {sha256_hash}")
    print("=" * 60)


if __name__ == "__main__":
    download_pdf()
