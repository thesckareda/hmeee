"""
Core types for the voice memory system.

Defines shared data structures used across the voice memory system,
including the canonical MemoryHeader representation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class MemoryHeader:
    """Immutable header metadata describing a single memory entry."""

    path: Path
    title: str
    description: str
    modified_at: float
    memory_type: str = ""
    body_preview: str = ""
    id: str = ""
    schema_version: int = 0
    category: str = ""
    importance: int = 0
    source: str = ""
    signature: str = ""
    created_at: str = ""
    updated_at: str = ""
    ttl_days: Optional[int] = None
    disabled: bool = False
    supersedes: tuple[str, ...] = field(default_factory=tuple)
    relative_path: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    frontmatter: dict = field(default_factory=dict)
