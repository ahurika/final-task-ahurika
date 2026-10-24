"""
Strategy B: Structure-Aware (Heading & Page-Based) Chunker
Location: src/chunkers/structure_aware.py

Ceiling: 800 tokens (baseline max)
Floor (Coalescing): 50 tokens (baseline min)
Secondary Overlap: 50 tokens (for oversized splits)
Strategy name: 'structure_aware'
"""

import re
from typing import List, Optional, Tuple, Dict, Any

from src.models import Chunk, DocumentSection, ParsedDocument
from src.tokenizers import BaseTokenizer, DeterministicTestTokenizer
from src.chunkers.base import BaseChunker, normalize_text


def parse_markdown_sections(
    raw_text: str, doc_title: Optional[str] = None
) -> List[DocumentSection]:
    """
    Parses Markdown text into structural sections based on heading hierarchy (# to ######).
    Tracks code fences (```) so comments inside code blocks are never mistaken for headings.
    Maintains a heading stack to build full breadcrumbs (e.g. Title > Chapter > Section).
    """
    if not raw_text or not raw_text.strip():
        return []

    lines = raw_text.splitlines()
    sections: List[DocumentSection] = []

    heading_stack: List[Tuple[int, str]] = []  # List of (level, heading_text)
    if doc_title:
        heading_stack.append((0, doc_title.strip()))

    current_lines: List[str] = []
    in_code_block = False

    def build_breadcrumb() -> List[str]:
        return [h for _, h in heading_stack]

    def flush_section(lines_to_flush: List[str], sec_idx: int) -> Optional[DocumentSection]:
        body = "\n".join(lines_to_flush).strip()
        if not body:
            return None
        breadcrumb = build_breadcrumb()
        label = "Section: " + (" > ".join(breadcrumb) if breadcrumb else "Overview")
        return DocumentSection(
            section_index=sec_idx,
            page_or_section=label,
            content=body,
            heading_path=list(breadcrumb),
        )

    sec_index = 0
    heading_regex = re.compile(r"^(#{1,6})\s+(.*)$")

    for line in lines:
        stripped = line.strip()
        # Toggle code block state
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_block = not in_code_block
            current_lines.append(line)
            continue

        if not in_code_block:
            match = heading_regex.match(stripped)
            if match:
                level = len(match.group(1))
                heading_text = match.group(2).strip()

                # Flush any accumulated lines before updating heading
                if current_lines:
                    sec = flush_section(current_lines, sec_index)
                    if sec:
                        sections.append(sec)
                        sec_index += 1
                    current_lines = []

                # Pop heading stack until we find a parent level
                while heading_stack:
                    top_level, _ = heading_stack[-1]
                    if top_level >= level:
                        heading_stack.pop()
                    else:
                        break

                heading_stack.append((level, heading_text))
                # Add the heading line itself as the start of the section
                current_lines.append(line)
                continue

        current_lines.append(line)

    if current_lines:
        sec = flush_section(current_lines, sec_index)
        if sec:
            sections.append(sec)

    return sections


class StructureAwareChunker(BaseChunker):
    """
    Chunking Strategy B: Structure-aware chunker adhering to Markdown headings and PDF page hints.

    1. Preserves heading hierarchy with breadcrumb headers.
    2. Keeps code blocks intact when they fit within the 800-token limit.
    3. Coalesces small adjacent sections (< 50 tokens) without exceeding 800 tokens.
    4. Splits oversized sections (> 800 tokens) at paragraph boundaries with 50-token secondary overlap.
    5. Preserves physical PDF page numbers and page ranges.
    """

    def __init__(
        self,
        max_tokens: int = 800,
        min_tokens: int = 50,
        overlap_tokens: int = 50,
        tokenizer: Optional[BaseTokenizer] = None,
    ):
        if max_tokens <= 0:
            raise ValueError(f"max_tokens must be greater than 0, got {max_tokens}")
        if min_tokens < 0:
            raise ValueError(f"min_tokens cannot be negative, got {min_tokens}")
        if overlap_tokens < 0:
            raise ValueError(f"overlap_tokens cannot be negative, got {overlap_tokens}")
        if overlap_tokens >= max_tokens:
            raise ValueError(
                f"overlap_tokens ({overlap_tokens}) must be strictly less than max_tokens ({max_tokens})"
            )
        if min_tokens > max_tokens:
            raise ValueError(
                f"min_tokens ({min_tokens}) cannot be greater than max_tokens ({max_tokens})"
            )

        super().__init__(tokenizer=tokenizer or DeterministicTestTokenizer())
        self.max_tokens = max_tokens
        self.min_tokens = min_tokens
        self.overlap_tokens = overlap_tokens

    @property
    def strategy_name(self) -> str:
        return "structure_aware"

    def _split_into_semantic_blocks(self, text: str) -> List[str]:
        """
        Splits text into atomic blocks (paragraphs and fenced code blocks).
        Guarantees code blocks are preserved intact as single blocks.
        """
        lines = text.splitlines()
        blocks: List[str] = []
        current_block: List[str] = []
        in_code_block = False

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                if not in_code_block:
                    # Starting code block: flush preceding paragraph
                    if current_block:
                        p = "\n".join(current_block).strip()
                        if p:
                            blocks.append(p)
                        current_block = []
                    in_code_block = True
                    current_block.append(line)
                else:
                    # Closing code block: flush code block atomically
                    current_block.append(line)
                    in_code_block = False
                    blocks.append("\n".join(current_block).strip())
                    current_block = []
                continue

            if in_code_block:
                current_block.append(line)
                continue

            if not stripped:
                # Blank line outside code block indicates paragraph break
                if current_block:
                    p = "\n".join(current_block).strip()
                    if p:
                        blocks.append(p)
                    current_block = []
                continue

            current_block.append(line)

        if current_block:
            p = "\n".join(current_block).strip()
            if p:
                blocks.append(p)

        return blocks

    def _split_oversized_block(self, block: str, max_capacity: Optional[int] = None) -> List[str]:
        """
        Splits a single block that exceeds max_tokens (or max_capacity) using safe fallback:
        First at sentence boundaries, then by token slices with overlap.
        """
        limit = max_capacity if max_capacity is not None else self.max_tokens
        if self.tokenizer.count_tokens(block) <= limit:
            return [block]

        # Sentence boundary split for prose
        sentences = re.split(r"(?<=[.!?])\s+", block)
        if len(sentences) > 1:
            sub_blocks: List[str] = []
            curr_s: List[str] = []
            for s in sentences:
                candidate = " ".join(curr_s + [s]) if curr_s else s
                if self.tokenizer.count_tokens(candidate) <= limit:
                    curr_s.append(s)
                else:
                    if curr_s:
                        sub_blocks.append(" ".join(curr_s))
                    if self.tokenizer.count_tokens(s) <= limit:
                        curr_s = [s]
                    else:
                        # Fallback for an individual giant sentence: slice by tokens
                        sliced = self._slice_tokens_fallback(s, max_capacity=limit)
                        sub_blocks.extend(sliced)
                        curr_s = []
            if curr_s:
                sub_blocks.append(" ".join(curr_s))
            return sub_blocks

        # Token slice fallback for huge unbroken code lines or strings
        return self._slice_tokens_fallback(block, max_capacity=limit)

    def _slice_tokens_fallback(self, text: str, max_capacity: Optional[int] = None) -> List[str]:
        """Slices an unbreakable string by tokens with secondary overlap."""
        limit = max_capacity if max_capacity is not None else self.max_tokens
        tokens = self.tokenizer.tokenize(text)
        if not tokens:
            return []
        effective_overlap = min(self.overlap_tokens, max(0, limit // 2))
        step = max(1, limit - effective_overlap)
        slices = []
        start = 0
        while start < len(tokens):
            end = min(start + limit, len(tokens))
            slices.append(self.tokenizer.detokenize(tokens[start:end]))
            if end >= len(tokens):
                break
            start += step
        return slices

    def _split_oversized_section(
        self,
        document: ParsedDocument,
        section: DocumentSection,
        start_chunk_index: int,
    ) -> List[Chunk]:
        """
        Splits an oversized section (> max_tokens) at paragraph and code block boundaries
        while adding secondary overlap (50 tokens) between adjacent sub-chunks.
        Guarantees that final chunks (including breadcrumb headers) never exceed max_tokens.
        """
        breadcrumb_header = ""
        breadcrumb_tokens = 0
        # Omit generic title breadcrumb if section is a PDF page
        if section.heading_path and section.page_number is None:
            breadcrumb_str = " > ".join(section.heading_path)
            breadcrumb_header = f"Context: [{breadcrumb_str}]\n\n"
            breadcrumb_tokens = self.tokenizer.count_tokens(breadcrumb_header)
            min_content_tokens = min(10, max(1, self.max_tokens // 2))
            if breadcrumb_tokens > self.max_tokens - min_content_tokens:
                allowed_bc = max(0, self.max_tokens - min_content_tokens)
                if allowed_bc < 5:
                    breadcrumb_header = ""
                    breadcrumb_tokens = 0
                else:
                    bc_toks = self.tokenizer.tokenize(breadcrumb_header)
                    breadcrumb_header = self.tokenizer.detokenize(bc_toks[:allowed_bc])
                    breadcrumb_tokens = self.tokenizer.count_tokens(breadcrumb_header)

        available_capacity = max(1, self.max_tokens - breadcrumb_tokens)

        raw_blocks = self._split_into_semantic_blocks(section.content)
        # Ensure no single block exceeds available_capacity
        atomic_blocks: List[str] = []
        for b in raw_blocks:
            atomic_blocks.extend(self._split_oversized_block(b, max_capacity=available_capacity))
        sub_chunks: List[Chunk] = []
        curr_blocks: List[str] = []
        part_idx = 1
        chunk_idx = start_chunk_index

        for block in atomic_blocks:
            candidate_blocks = curr_blocks + [block]
            candidate_body = "\n\n".join(candidate_blocks)
            candidate_payload = f"{breadcrumb_header}{candidate_body}" if breadcrumb_header else candidate_body
            if self.tokenizer.count_tokens(candidate_payload) <= self.max_tokens:
                curr_blocks.append(block)
            else:
                if curr_blocks:
                    body = "\n\n".join(curr_blocks)
                    payload = f"{breadcrumb_header}{body}" if breadcrumb_header else body

                    label = (
                        f"{section.page_or_section} (Part {part_idx})"
                        if section.page_or_section
                        else f"Part {part_idx}"
                    )
                    meta = {
                        "is_split": True,
                        "part_index": part_idx,
                        "heading_path": section.heading_path,
                    }
                    if section.page_number is not None:
                        meta["page_number"] = section.page_number
                        meta["page_numbers"] = [section.page_number]

                    sub_chunks.append(
                        self.build_chunk(
                            document=document,
                            chunk_index=chunk_idx,
                            content=payload,
                            page_or_section=label,
                            extra_metadata=meta,
                        )
                    )
                    chunk_idx += 1
                    part_idx += 1

                # If block alone with breadcrumb exceeds max_tokens (rare edge case), slice it directly
                block_payload = f"{breadcrumb_header}{block}" if breadcrumb_header else block
                if self.tokenizer.count_tokens(block_payload) > self.max_tokens:
                    sliced = self._slice_tokens_fallback(block, max_capacity=available_capacity)
                    for sl in sliced:
                        sl_payload = f"{breadcrumb_header}{sl}" if breadcrumb_header else sl
                        label = (
                            f"{section.page_or_section} (Part {part_idx})"
                            if section.page_or_section
                            else f"Part {part_idx}"
                        )
                        meta = {
                            "is_split": True,
                            "part_index": part_idx,
                            "heading_path": section.heading_path,
                        }
                        if section.page_number is not None:
                            meta["page_number"] = section.page_number
                            meta["page_numbers"] = [section.page_number]

                        sub_chunks.append(
                            self.build_chunk(
                                document=document,
                                chunk_index=chunk_idx,
                                content=sl_payload,
                                page_or_section=label,
                                extra_metadata=meta,
                            )
                        )
                        chunk_idx += 1
                        part_idx += 1
                    curr_blocks = []
                else:
                    curr_blocks = [block]

        if curr_blocks:
            body = "\n\n".join(curr_blocks)
            payload = f"{breadcrumb_header}{body}" if breadcrumb_header else body
            label = (
                f"{section.page_or_section} (Part {part_idx})"
                if section.page_or_section
                else f"Part {part_idx}"
            )
            meta = {
                "is_split": True,
                "part_index": part_idx,
                "heading_path": section.heading_path,
            }
            if section.page_number is not None:
                meta["page_number"] = section.page_number
                meta["page_numbers"] = [section.page_number]

            sub_chunks.append(
                self.build_chunk(
                    document=document,
                    chunk_index=chunk_idx,
                    content=payload,
                    page_or_section=label,
                    extra_metadata=meta,
                )
            )

        return sub_chunks

    def _assemble_coalesced_chunk(
        self,
        document: ParsedDocument,
        sections: List[DocumentSection],
        chunk_index: int,
    ) -> Chunk:
        """Assembles a single Chunk from one or more coalesced sections."""
        page_numbers = [s.page_number for s in sections if s.page_number is not None]

        # Determine label and breadcrumb
        if page_numbers:
            min_page = min(page_numbers)
            max_page = max(page_numbers)
            label = f"Page {min_page}" if min_page == max_page else f"Pages {min_page}–{max_page}"
            breadcrumb_header = ""
        else:
            labels = [s.page_or_section for s in sections if s.page_or_section]
            if len(labels) == 1:
                label = labels[0]
            elif len(labels) > 1:
                first = labels[0].replace("Section: ", "")
                last = labels[-1].replace("Section: ", "")
                label = f"Section: {first} to {last}"
            else:
                label = "Document Body"

            # Prepend breadcrumb from the primary section
            primary_path = sections[0].heading_path
            if primary_path:
                breadcrumb_str = " > ".join(primary_path)
                breadcrumb_header = f"Context: [{breadcrumb_str}]\n\n"
            else:
                breadcrumb_header = ""

        body = "\n\n".join(s.content.strip() for s in sections if s.content.strip())
        payload = f"{breadcrumb_header}{body}" if breadcrumb_header else body

        meta: Dict[str, Any] = {
            "coalesced": len(sections) > 1,
            "section_count": len(sections),
            "heading_paths": [s.heading_path for s in sections if s.heading_path],
        }
        if page_numbers:
            meta["page_numbers"] = sorted(list(set(page_numbers)))
            if len(meta["page_numbers"]) == 1:
                meta["page_number"] = meta["page_numbers"][0]

        return self.build_chunk(
            document=document,
            chunk_index=chunk_index,
            content=payload,
            page_or_section=label,
            extra_metadata=meta,
        )

    def _estimate_cluster_tokens(self, sections: List[DocumentSection]) -> int:
        """Computes exact token count of a cluster of sections when assembled with breadcrumbs."""
        if not sections:
            return 0
        page_numbers = [s.page_number for s in sections if s.page_number is not None]
        breadcrumb_header = ""
        if not page_numbers and sections[0].heading_path:
            breadcrumb_str = " > ".join(sections[0].heading_path)
            breadcrumb_header = f"Context: [{breadcrumb_str}]\n\n"
        body = "\n\n".join(s.content.strip() for s in sections if s.content.strip())
        payload = f"{breadcrumb_header}{body}" if breadcrumb_header else body
        return self.tokenizer.count_tokens(payload)

    def chunk(self, document: ParsedDocument) -> List[Chunk]:
        """
        Executes structure-aware chunking over Markdown or PDF sections.
        Coalesces small adjacent sections (< min_tokens) and splits oversized sections (> max_tokens).
        Guarantees that final chunk outputs (including breadcrumb headers) strictly adhere to max_tokens.
        """
        if document.is_empty:
            return []

        # Obtain structural sections
        sections = list(document.sections) if document.sections else []
        if not sections and document.raw_text:
            if document.metadata.file_type == "markdown":
                sections = parse_markdown_sections(
                    document.raw_text, doc_title=document.metadata.title
                )
            else:
                sections = [
                    DocumentSection(
                        section_index=0,
                        page_or_section="Document Body",
                        content=document.raw_text,
                        heading_path=[document.metadata.title] if document.metadata.title else [],
                    )
                ]

        if not sections:
            return []

        chunks: List[Chunk] = []
        current_cluster: List[DocumentSection] = []
        chunk_index = 0

        for sec in sections:
            if not sec.content or not sec.content.strip():
                continue

            sec_assembled_tokens = self._estimate_cluster_tokens([sec])

            # Case A: Oversized section (> max_tokens when assembled)
            if sec_assembled_tokens > self.max_tokens:
                # Flush existing cluster first if present
                if current_cluster:
                    chunk = self._assemble_coalesced_chunk(
                        document=document,
                        sections=current_cluster,
                        chunk_index=chunk_index,
                    )
                    chunks.append(chunk)
                    chunk_index += 1
                    current_cluster = []

                # Split the oversized section into sub-chunks
                sub_chunks = self._split_oversized_section(
                    document=document,
                    section=sec,
                    start_chunk_index=chunk_index,
                )
                chunks.extend(sub_chunks)
                chunk_index += len(sub_chunks)
                continue

            # Case B: Standard or undersized section (<= max_tokens)
            if not current_cluster:
                current_cluster.append(sec)
            else:
                curr_tok = self._estimate_cluster_tokens(current_cluster)
                # If current cluster is undersized (< min_tokens), coalesce if fitting
                if curr_tok < self.min_tokens:
                    potential_tokens = self._estimate_cluster_tokens(current_cluster + [sec])
                    if potential_tokens <= self.max_tokens:
                        current_cluster.append(sec)
                    else:
                        # Cannot coalesce without exceeding max_tokens: emit current cluster
                        chunk = self._assemble_coalesced_chunk(
                            document=document,
                            sections=current_cluster,
                            chunk_index=chunk_index,
                        )
                        chunks.append(chunk)
                        chunk_index += 1
                        current_cluster = [sec]
                else:
                    # Current cluster already meets min_tokens (>= 50 tokens): emit as intact chunk
                    chunk = self._assemble_coalesced_chunk(
                        document=document,
                        sections=current_cluster,
                        chunk_index=chunk_index,
                    )
                    chunks.append(chunk)
                    chunk_index += 1
                    current_cluster = [sec]

        # Flush any remaining sections
        if current_cluster:
            cluster_tok = self._estimate_cluster_tokens(current_cluster)
            # If cluster is small (< min_tokens) and there is a previous chunk, merge if fitting
            if (
                chunks
                and cluster_tok < self.min_tokens
                and not chunks[-1].metadata.get("is_split", False)
            ):
                candidate_merged = (
                    f"{chunks[-1].content}\n\n"
                    + "\n\n".join(s.content.strip() for s in current_cluster)
                )
                if self.tokenizer.count_tokens(candidate_merged) <= self.max_tokens:
                    prev_chunk = chunks.pop()
                    merged_chunk = self.build_chunk(
                        document=document,
                        chunk_index=prev_chunk.chunk_index,
                        content=candidate_merged,
                        page_or_section=prev_chunk.page_or_section,
                        extra_metadata=prev_chunk.metadata,
                    )
                    chunks.append(merged_chunk)
                else:
                    chunk = self._assemble_coalesced_chunk(
                        document=document,
                        sections=current_cluster,
                        chunk_index=chunk_index,
                    )
                    chunks.append(chunk)
            else:
                chunk = self._assemble_coalesced_chunk(
                    document=document,
                    sections=current_cluster,
                    chunk_index=chunk_index,
                )
                chunks.append(chunk)

        # Verify strict compliance with max_tokens ceiling
        for c in chunks:
            if c.token_count > self.max_tokens:
                raise ValueError(
                    f"Strategy B chunk {c.chunk_id} in {document.metadata.document_id} exceeded max_tokens: "
                    f"{c.token_count} > {self.max_tokens}"
                )

        return chunks
