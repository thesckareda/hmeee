"""Relevant memory selection and formatting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from voice_memory.search import find_relevant_memories
from voice_memory.schema import memory_age_label, memory_freshness_text
from voice_memory.types import MemoryHeader


@dataclass(frozen=True)
class RelevantMemory:
    header: MemoryHeader
    freshness: str = ""


def select_relevant_memories(
    query: str,
    *,
    max_results: int = 5,
    already_surfaced: set[str] | None = None,
    memory_dir: str | Path | None = None,
) -> list[RelevantMemory]:
    surfaced = already_surfaced or set()
    heuristic = [
        header
        for header in find_relevant_memories(
            query, max_results=max(10, max_results * 3), memory_dir=memory_dir
        )
        if (header.relative_path or str(header.path)) not in surfaced
    ]
    result: list[RelevantMemory] = []
    for header in heuristic[:max_results]:
        result.append(
            RelevantMemory(header=header, freshness=memory_freshness_text(header.modified_at))
        )
    return result


def format_relevant_memories(memories: Iterable[RelevantMemory], *, max_chars: int = 8000) -> str:
    lines = ["# Relevant Memories"]
    for item in memories:
        header = item.header
        try:
            content = header.path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            content = "(unable to read)"
        if item.freshness:
            lines.extend(["", f"## {header.relative_path or header.path.name}", f"> {item.freshness}"])
        else:
            lines.extend(["", f"## {header.relative_path or header.path.name}"])
        lines.extend(["```md", content[:max_chars], "```"])
    return "\n".join(lines)
