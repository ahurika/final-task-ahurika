"""
Generation Package Exports
Location: src/generation/__init__.py
"""

from src.generation.prompt_builder import (
    PromptBuilder,
    GroundedPrompt,
    escape_xml_text,
    escape_xml_attribute,
    DEFAULT_SYSTEM_INSTRUCTION,
)

__all__ = [
    "PromptBuilder",
    "GroundedPrompt",
    "escape_xml_text",
    "escape_xml_attribute",
    "DEFAULT_SYSTEM_INSTRUCTION",
]
