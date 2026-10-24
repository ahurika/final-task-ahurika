"""
Strategy A: Fixed-Size Sliding Window Chunker
Location: src/chunkers/fixed_size.py

Target: 500 tokens (baseline)
Overlap: 50 tokens (baseline, ~10%)
Strategy name: 'fixed_window'
"""

from typing import List, Optional, Tuple, Dict, Any

from src.models import Chunk, DocumentSection, ParsedDocument
from src.tokenizers import BaseTokenizer, DeterministicTestTokenizer
from src.chunkers.base import BaseChunker, normalize_text


class FixedSizeChunker(BaseChunker):
    """
    Chunking Strategy A: Fixed-size sliding window with configurable token target and overlap.

    Slices a continuous token stream with deterministic step = (target_tokens - overlap_tokens).
    Preserves document section/page references across sliding window boundaries.
    """

    def __init__(
        self,
        target_tokens: int = 500,
        overlap_tokens: int = 50,
        tokenizer: Optional[BaseTokenizer] = None,
    ):
        if target_tokens <= 0:
            raise ValueError(f"target_tokens must be greater than 0, got {target_tokens}")
        if overlap_tokens < 0:
            raise ValueError(f"overlap_tokens cannot be negative, got {overlap_tokens}")
        if overlap_tokens >= target_tokens:
            raise ValueError(
                f"overlap_tokens ({overlap_tokens}) must be strictly less than target_tokens ({target_tokens})"
            )

        super().__init__(tokenizer=tokenizer or DeterministicTestTokenizer())
        self.target_tokens = target_tokens
        self.overlap_tokens = overlap_tokens
        self.step_tokens = target_tokens - overlap_tokens

    @property
    def strategy_name(self) -> str:
        return "fixed_window"

    def _resolve_provenance(
        self, sections: List[DocumentSection]
    ) -> Tuple[str, Dict[str, Any]]:
        """Determine human-readable page/section string and structured metadata for a token window."""
        if not sections:
            return "Document Body", {}

        page_numbers = [s.page_number for s in sections if s.page_number is not None]
        if page_numbers:
            min_page = min(page_numbers)
            max_page = max(page_numbers)
            if min_page == max_page:
                label = f"Page {min_page}"
            else:
                label = f"Pages {min_page}–{max_page}"
            meta = {
                "page_number": min_page,
                "page_numbers": sorted(list(set(page_numbers))),
                "sections": [s.page_or_section for s in sections],
            }
            return label, meta

        # Markdown / heading-based sections
        if len(sections) == 1:
            label = sections[0].page_or_section
        else:
            first_label = sections[0].page_or_section.replace("Section: ", "")
            last_label = sections[-1].page_or_section.replace("Section: ", "")
            label = f"Section: {first_label} to {last_label}"

        heading_paths = [s.heading_path for s in sections if s.heading_path]
        meta = {
            "sections": [s.page_or_section for s in sections],
            "section_indices": [s.section_index for s in sections],
            "heading_paths": heading_paths,
        }
        return label, meta

    def chunk(self, document: ParsedDocument) -> List[Chunk]:
        """
        Produce fixed-size chunks with overlap across document content.
        Safely handles empty text, short text (< target), long text, and boundary conditions.
        """
        if document.is_empty:
            return []

        all_tokens: List[str] = []
        token_to_section: List[DocumentSection] = []

        if document.sections:
            for sec in document.sections:
                if not sec.content or not sec.content.strip():
                    continue
                sec_tokens = self.tokenizer.tokenize(sec.content)
                if not sec_tokens:
                    continue
                all_tokens.extend(sec_tokens)
                token_to_section.extend([sec] * len(sec_tokens))
        else:
            all_tokens = self.tokenizer.tokenize(document.raw_text)

        total_tokens = len(all_tokens)
        if total_tokens == 0:
            return []

        chunks: List[Chunk] = []
        start = 0
        chunk_index = 0

        while start < total_tokens:
            end = min(start + self.target_tokens, total_tokens)
            chunk_tokens = all_tokens[start:end]
            chunk_text = self.tokenizer.detokenize(chunk_tokens)

            # Check if normalized text is non-empty
            if not normalize_text(chunk_text):
                if end >= total_tokens:
                    break
                start += self.step_tokens
                continue

            if token_to_section:
                seen_indices = set()
                sub_sections: List[DocumentSection] = []
                for s in token_to_section[start:end]:
                    if s.section_index not in seen_indices:
                        seen_indices.add(s.section_index)
                        sub_sections.append(s)
                page_or_section, extra_meta = self._resolve_provenance(sub_sections)
            else:
                page_or_section = "Document Body"
                extra_meta = {}

            extra_meta["token_span"] = [start, end]

            chunk = self.build_chunk(
                document=document,
                chunk_index=chunk_index,
                content=chunk_text,
                page_or_section=page_or_section,
                extra_metadata=extra_meta,
            )
            chunks.append(chunk)
            chunk_index += 1

            if end >= total_tokens:
                break
            start += self.step_tokens

        return chunks
