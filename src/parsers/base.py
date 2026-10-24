"""
Base Document Parser Abstractions and Result Containers
Location: src/parsers/base.py
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from src.models import ParsedDocument


@dataclass
class ExtractionWarning:
    """Warning recorded during document parsing (e.g. unreadable or blank page)."""
    document_id: str
    source_filename: str
    warning_type: str
    message: str
    page_number: Optional[int] = None


@dataclass
class ParsingResult:
    """Comprehensive outcome of a document parsing operation."""
    document: ParsedDocument
    warnings: List[ExtractionWarning] = field(default_factory=list)
    page_count: int = 0
    raw_char_count: int = 0
    extraction_time_seconds: float = 0.0

    @property
    def has_warnings(self) -> bool:
        return len(self.warnings) > 0


class BaseParser(ABC):
    """Abstract base class for format-specific document parsers."""

    @abstractmethod
    def parse(self, file_path: str, manifest_entry: Optional[dict] = None) -> ParsingResult:
        """Parse a document file into structured ParsedDocument with warnings."""
        pass
