"""
Source Citation Construction and Traceability Formatter
Location: src/retrieval/citations.py

Implements verifiable interactive citation formatting linking retrieved claims
back to exact source chunk provenance, document identifiers, physical pages,
and cryptographic hashes (REQ-10, AC-10).
"""

from dataclasses import dataclass
from typing import Optional, Any, Sequence, List, Union


DEFAULT_MAX_SNIPPET_LENGTH: int = 200


@dataclass(frozen=True)
class Citation:
    """
    Verifiable source citation linking answer claims back to raw chunk provenance.
    Retains full provenance metadata without inventing missing fields.
    """
    chunk_id: str
    source_filename: Optional[str]
    source_url: Optional[str]
    page_or_section: Optional[str]
    content_hash: Optional[str]
    evidence_snippet: str
    formatted_citation: str

    def to_dict(self) -> dict:
        """Serializes citation to a dictionary representation."""
        return {
            "chunk_id": self.chunk_id,
            "source_filename": self.source_filename,
            "source_url": self.source_url,
            "page_or_section": self.page_or_section,
            "content_hash": self.content_hash,
            "evidence_snippet": self.evidence_snippet,
            "formatted_citation": self.formatted_citation,
        }


class CitationFormatter:
    """
    Constructs standardized, human-readable citation tags and traceable evidence records
    from chunk metadata objects (supporting Chunk, ChunkRecord, and duck-typed mappings).
    """

    def __init__(self, default_max_snippet_length: int = DEFAULT_MAX_SNIPPET_LENGTH):
        if default_max_snippet_length <= 0:
            raise ValueError(f"default_max_snippet_length must be positive, got {default_max_snippet_length}")
        self.default_max_snippet_length = default_max_snippet_length

    def format_citation(
        self,
        chunk: Any,
        max_snippet_length: Optional[int] = None,
    ) -> Citation:
        """
        Extracts provenance metadata from a chunk and formats a verifiable Citation object.
        Preserves original verbatim text without rewriting or inventing missing fields.
        """
        snippet_limit = max_snippet_length if max_snippet_length is not None else self.default_max_snippet_length
        if snippet_limit <= 0:
            raise ValueError(f"max_snippet_length must be positive, got {snippet_limit}")

        # Extract attributes defensively (supports Chunk, ChunkRecord, and dict)
        chunk_id = self._get_field(chunk, "chunk_id", "id")
        if not chunk_id:
            chunk_id = "unknown_chunk_id"

        source_filename = self._get_field(chunk, "source_filename")
        if isinstance(source_filename, str):
            source_filename = source_filename.strip() or None

        source_url = self._get_field(chunk, "source_url")
        if isinstance(source_url, str):
            source_url = source_url.strip() or None

        page_or_section = self._get_field(chunk, "page_or_section")
        if isinstance(page_or_section, str):
            page_or_section = page_or_section.strip() or None

        content_hash = self._get_field(chunk, "content_hash")
        if isinstance(content_hash, str):
            content_hash = content_hash.strip() or None

        # Verbatim evidence snippet bounding (preserving original text without modification)
        raw_content = self._get_field(chunk, "content") or ""
        if len(raw_content) <= snippet_limit:
            evidence_snippet = raw_content
        else:
            evidence_snippet = raw_content[:snippet_limit] + "..."

        # Format standardized human-readable bracketed tag (AC-10)
        formatted_tag = self._build_formatted_tag(source_filename, page_or_section)

        return Citation(
            chunk_id=str(chunk_id),
            source_filename=source_filename,
            source_url=source_url,
            page_or_section=page_or_section,
            content_hash=content_hash,
            evidence_snippet=evidence_snippet,
            formatted_citation=formatted_tag,
        )

    def format_citations(
        self,
        chunks: Sequence[Any],
        max_snippet_length: Optional[int] = None,
    ) -> List[Citation]:
        """Formats citations for a sequence of chunks."""
        return [self.format_citation(c, max_snippet_length=max_snippet_length) for c in chunks]

    @staticmethod
    def _get_field(obj: Any, *field_names: str) -> Optional[Any]:
        """Safely extracts field from object attribute or dictionary key."""
        for name in field_names:
            if isinstance(obj, dict):
                if name in obj and obj[name] is not None:
                    return obj[name]
            else:
                val = getattr(obj, name, None)
                if val is not None:
                    return val
        return None

    @staticmethod
    def _build_formatted_tag(
        source_filename: Optional[str],
        page_or_section: Optional[str],
    ) -> str:
        """
        Constructs the bracketed citation label:
        - PDF: [Doc: manual.pdf, Page: Page 14] or [Doc: manual.pdf, Page 14]
        - Markdown: [Doc: mvcc.md, Section: Concurrency Control]
        - Fallbacks for partially missing metadata without hallucinating.
        """
        if source_filename and page_or_section:
            pos_lower = page_or_section.lower()
            if pos_lower.startswith("page"):
                # e.g. "Page 14" -> "[Doc: manual.pdf, Page 14]"
                return f"[Doc: {source_filename}, {page_or_section}]"
            else:
                # e.g. "13.2. Concurrency Control" -> "[Doc: mvcc.md, Section: 13.2. Concurrency Control]"
                return f"[Doc: {source_filename}, Section: {page_or_section}]"

        if source_filename and not page_or_section:
            return f"[Doc: {source_filename}]"

        if not source_filename and page_or_section:
            pos_lower = page_or_section.lower()
            if pos_lower.startswith("page"):
                return f"[{page_or_section}]"
            else:
                return f"[Section: {page_or_section}]"

        return "[Unknown Source]"
