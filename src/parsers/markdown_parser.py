"""
Markdown Document Parser
Location: src/parsers/markdown_parser.py

Parses Markdown files, extracting YAML frontmatter metadata, heading hierarchies,
code fences, and content sections.
"""

import os
import re
import time
from typing import Optional, Dict, Any, List

from src.models import DocumentMetadata, DocumentSection, ParsedDocument
from src.chunkers.structure_aware import parse_markdown_sections
from src.parsers.base import BaseParser, ParsingResult, ExtractionWarning


def parse_frontmatter(text: str) -> tuple[Dict[str, str], str]:
    """
    Extracts YAML-like frontmatter enclosed between leading '---' fences.
    Returns (metadata_dict, body_text).
    Does not require external YAML library.
    """
    if not text.startswith("---"):
        return {}, text

    # Find closing fence
    lines = text.splitlines(keepends=True)
    closing_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            closing_idx = i
            break

    if closing_idx == -1:
        # No closing fence found; treat entire text as body
        return {}, text

    frontmatter_lines = lines[1:closing_idx]
    body_text = "".join(lines[closing_idx + 1:]).lstrip("\r\n")

    metadata: Dict[str, str] = {}
    for line in frontmatter_lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, val = line.split(":", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            metadata[key] = val

    return metadata, body_text


class MarkdownParser(BaseParser):
    """Parses Markdown documentation files into ParsedDocument models."""

    def parse(self, file_path: str, manifest_entry: Optional[dict] = None) -> ParsingResult:
        t0 = time.time()
        filename = os.path.basename(file_path)
        warnings: List[ExtractionWarning] = []

        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Source Markdown file not found: {file_path}")

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                raw_text = f.read()
        except UnicodeDecodeError:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                raw_text = f.read()
            warnings.append(
                ExtractionWarning(
                    document_id=filename,
                    source_filename=filename,
                    warning_type="UNICODE_DECODE_FALLBACK",
                    message="Encountered non-UTF8 bytes during read; replaced with fallback characters.",
                )
            )

        fm_meta, body = parse_frontmatter(raw_text)

        # Merge frontmatter with manifest metadata if available
        manifest_meta = manifest_entry or {}

        doc_id = (
            fm_meta.get("document_id")
            or manifest_meta.get("document_id")
            or os.path.splitext(filename)[0]
        )
        title = (
            fm_meta.get("title")
            or manifest_meta.get("title")
            or doc_id.replace("-", " ").title()
        )
        topic = (
            fm_meta.get("topic")
            or manifest_meta.get("topic")
            or "PostgreSQL Documentation"
        )
        source_url = (
            fm_meta.get("source_url")
            or manifest_meta.get("source_url")
        )
        doc_date = (
            fm_meta.get("retrieval_date")
            or manifest_meta.get("retrieval_date")
            or "2026-10-23"
        )
        license_str = (
            fm_meta.get("license")
            or manifest_meta.get("license")
            or "PostgreSQL License (OSI-approved, BSD-style permissive)"
        )

        metadata = DocumentMetadata(
            document_id=doc_id,
            source_filename=filename,
            file_type="markdown",
            source_url=source_url,
            document_date=doc_date,
            title=title,
            topic=topic,
            license=license_str,
        )

        if not body.strip():
            warnings.append(
                ExtractionWarning(
                    document_id=doc_id,
                    source_filename=filename,
                    warning_type="EMPTY_DOCUMENT_BODY",
                    message="Document frontmatter was extracted but markdown body is empty.",
                )
            )
            parsed_doc = ParsedDocument(metadata=metadata, sections=[], raw_text="")
            return ParsingResult(
                document=parsed_doc,
                warnings=warnings,
                page_count=1,
                raw_char_count=len(raw_text),
                extraction_time_seconds=time.time() - t0,
            )

        sections = parse_markdown_sections(body, doc_title=title)

        if not sections:
            # Fallback if no headings exist: create single body section
            warnings.append(
                ExtractionWarning(
                    document_id=doc_id,
                    source_filename=filename,
                    warning_type="NO_HEADINGS_FOUND",
                    message="No Markdown headings found; text ingested as single root section.",
                )
            )
            sections = [
                DocumentSection(
                    section_index=0,
                    page_or_section="Document Body",
                    content=body.strip(),
                    heading_path=[title],
                )
            ]

        parsed_doc = ParsedDocument(metadata=metadata, sections=sections, raw_text=body.strip())
        return ParsingResult(
            document=parsed_doc,
            warnings=warnings,
            page_count=1,
            raw_char_count=len(raw_text),
            extraction_time_seconds=time.time() - t0,
        )
