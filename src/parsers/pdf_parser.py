"""
PDF Document Parser
Location: src/parsers/pdf_parser.py

Extracts text from PDF documents page-by-page using pypdf.
Preserves physical 1-indexed page numbers, records extraction warnings for empty/unreadable pages,
and caches extracted page text for deterministic, reproducible offline pipeline runs.
"""

import hashlib
import json
import os
import time
from typing import Optional, List, Dict, Any, Tuple
import pypdf

from src.models import DocumentMetadata, DocumentSection, ParsedDocument
from src.parsers.base import BaseParser, ParsingResult, ExtractionWarning


def compute_file_sha256(file_path: str) -> str:
    """Computes cryptographic SHA-256 hash of a file by streaming binary blocks."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def _worker_extract_page_slice(args: Tuple[str, int, int]) -> List[Tuple[int, str]]:
    """Worker function for multiprocessing extraction of a slice of PDF pages."""
    pdf_path, start_idx, end_idx = args
    results: List[Tuple[int, str]] = []
    reader = pypdf.PdfReader(pdf_path)
    for idx in range(start_idx, end_idx):
        try:
            page = reader.pages[idx]
            txt = page.extract_text() or ""
        except Exception as e:
            txt = ""
        results.append((idx + 1, txt))
    return results


class PDFParser(BaseParser):
    """
    Parses PDF documentation into ParsedDocument models.
    Preserves exact 1-indexed physical page numbers.
    Features content-safe extraction caching with SHA-256 and file size invalidation checks.
    """

    def __init__(self, cache_dir: Optional[str] = None):
        self.cache_dir = cache_dir or os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "cache"
        )

    def _get_cache_path(self, file_path: str) -> str:
        base_name = os.path.basename(file_path)
        return os.path.join(self.cache_dir, f"{base_name}.extracted.json")

    def parse(
        self,
        file_path: str,
        manifest_entry: Optional[dict] = None,
        use_cache: bool = True,
        max_workers: int = 4,
    ) -> ParsingResult:
        t0 = time.time()
        filename = os.path.basename(file_path)
        warnings: List[ExtractionWarning] = []

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"PDF source document not found: {file_path}")

        # Derive metadata from manifest or defaults
        manifest_meta = manifest_entry or {}
        doc_id = manifest_meta.get("document_id") or os.path.splitext(filename)[0]
        title = manifest_meta.get("title") or "PostgreSQL 16 Documentation (US Letter)"
        topic = manifest_meta.get("topic") or "Official PostgreSQL Manual"
        source_url = (
            manifest_meta.get("source_url")
            or manifest_meta.get("official_source_url")
            or "https://www.postgresql.org/files/documentation/pdf/16/postgresql-16-US.pdf"
        )
        doc_date = manifest_meta.get("retrieval_date") or manifest_meta.get("document_date") or "2026-10-23"
        license_str = (
            manifest_meta.get("license")
            if isinstance(manifest_meta.get("license"), str)
            else "PostgreSQL License (OSI-approved, BSD-style permissive)"
        )

        metadata = DocumentMetadata(
            document_id=doc_id,
            source_filename=filename,
            file_type="pdf",
            source_url=source_url,
            document_date=doc_date,
            title=title,
            topic=topic,
            license=license_str,
        )

        cache_path = self._get_cache_path(file_path)
        page_records: List[Tuple[int, str]] = []

        # Check local extraction cache with content-safe hash and size verification
        if use_cache and os.path.exists(cache_path):
            try:
                current_size = os.path.getsize(file_path)
                with open(cache_path, "r", encoding="utf-8") as f:
                    cached_data = json.load(f)

                cached_size = cached_data.get("file_size_bytes")
                cached_hash = cached_data.get("sha256_hash")

                if cached_size == current_size:
                    current_hash = compute_file_sha256(file_path)
                    if cached_hash == current_hash:
                        page_records = [(item["page_number"], item["text"]) for item in cached_data["pages"]]
                    else:
                        warnings.append(
                            ExtractionWarning(
                                document_id=doc_id,
                                source_filename=filename,
                                warning_type="CACHE_INVALIDATED_HASH_MISMATCH",
                                message="Cached extraction invalidated because source PDF SHA-256 hash changed.",
                            )
                        )
                else:
                    warnings.append(
                        ExtractionWarning(
                            document_id=doc_id,
                            source_filename=filename,
                            warning_type="CACHE_INVALIDATED_SIZE_MISMATCH",
                            message=f"Cached extraction invalidated: file size changed ({cached_size} -> {current_size}).",
                        )
                    )
            except Exception:
                page_records = []

        if not page_records:
            reader = pypdf.PdfReader(file_path)
            total_pages = len(reader.pages)

            # Extract pages sequentially or via multiprocessing pool if large
            if total_pages > 100 and max_workers > 1:
                import multiprocessing as mp
                chunk_size = max(10, total_pages // (max_workers * 4))
                tasks = []
                for start_idx in range(0, total_pages, chunk_size):
                    end_idx = min(start_idx + chunk_size, total_pages)
                    tasks.append((file_path, start_idx, end_idx))

                try:
                    with mp.Pool(max_workers) as pool:
                        results = pool.map(_worker_extract_page_slice, tasks)
                    for batch in results:
                        page_records.extend(batch)
                    page_records.sort(key=lambda x: x[0])
                except Exception:
                    # Sequential fallback
                    page_records = []
                    for idx in range(total_pages):
                        txt = reader.pages[idx].extract_text() or ""
                        page_records.append((idx + 1, txt))
            else:
                for idx in range(total_pages):
                    try:
                        txt = reader.pages[idx].extract_text() or ""
                    except Exception as e:
                        txt = ""
                        warnings.append(
                            ExtractionWarning(
                                document_id=doc_id,
                                source_filename=filename,
                                warning_type="EXTRACTION_EXCEPTION",
                                message=f"Exception extracting page {idx + 1}: {str(e)}",
                                page_number=idx + 1,
                            )
                        )
                    page_records.append((idx + 1, txt))

            # Save content-safe cache payload with file size and SHA-256 hash
            if use_cache:
                os.makedirs(self.cache_dir, exist_ok=True)
                file_size = os.path.getsize(file_path)
                file_hash = compute_file_sha256(file_path)
                cache_payload = {
                    "schema_version": "1.1",
                    "source_filename": filename,
                    "file_size_bytes": file_size,
                    "sha256_hash": file_hash,
                    "total_pages": total_pages,
                    "cached_timestamp": time.time(),
                    "pages": [{"page_number": p_num, "text": p_txt} for p_num, p_txt in page_records],
                }
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(cache_payload, f)

        # Build DocumentSection instances and record warnings for blank pages
        sections: List[DocumentSection] = []
        total_chars = 0
        sec_index = 0

        for page_num, text in page_records:
            cleaned = text.strip()
            total_chars += len(cleaned)
            if not cleaned:
                warnings.append(
                    ExtractionWarning(
                        document_id=doc_id,
                        source_filename=filename,
                        warning_type="EMPTY_PAGE",
                        message=f"Page {page_num} contains no extractable text (blank, vector diagram, or unindexed cover).",
                        page_number=page_num,
                    )
                )
                continue

            sections.append(
                DocumentSection(
                    section_index=sec_index,
                    page_or_section=f"Page {page_num}",
                    content=cleaned,
                    heading_path=[title],
                    page_number=page_num,
                )
            )
            sec_index += 1

        parsed_doc = ParsedDocument(metadata=metadata, sections=sections, raw_text="")
        return ParsingResult(
            document=parsed_doc,
            warnings=warnings,
            page_count=len(page_records),
            raw_char_count=total_chars,
            extraction_time_seconds=time.time() - t0,
        )
