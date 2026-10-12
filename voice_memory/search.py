"""Heuristic memory search — keyword overlap scoring."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path

from voice_memory.paths import get_project_memory_dir
from voice_memory.scan import scan_memory_files
from voice_memory.schema import parse_datetime, utc_now
from voice_memory.types import MemoryHeader


def find_relevant_memories(
    query: str,
    *,
    max_results: int = 5,
    memory_dir: str | Path | None = None,
) -> list[MemoryHeader]:
    tokens = _tokenize(query)
    if not tokens:
        return []

    scored: list[tuple[float, MemoryHeader]] = []
    scan_limit = max(max_results * 5, 100)
    for header in scan_memory_files(memory_dir=memory_dir, max_files=scan_limit):
        meta = f"{header.title} {header.description}".lower()
        body = header.body_preview.lower()

        meta_hits = sum(1 for t in tokens if t in meta)
        body_hits = sum(1 for t in tokens if t in body)
        usage_count = _get_usage_count(memory_dir or get_project_memory_dir(), header.id)
        score = (
            meta_hits * 2.0
            + body_hits
            + header.importance * 0.4
            + min(usage_count, 5) * 0.1
            + _recency_boost(header)
        )
        if meta_hits or body_hits:
            scored.append((score, header))

    scored.sort(key=lambda item: (-item[0], -item[1].modified_at))
    return [header for _, header in scored[:max_results]]


def _tokenize(text: str) -> set[str]:
    ascii_tokens = {t for t in re.findall(r"[A-Za-z0-9_]+", text.lower()) if len(t) >= 3}
    han_chars = set(re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf]", text))
    return ascii_tokens | han_chars


def _recency_boost(header: MemoryHeader) -> float:
    timestamp = parse_datetime(header.updated_at) or parse_datetime(header.created_at)
    if timestamp is None:
        return 0.0
    age = utc_now() - timestamp
    if age <= timedelta(days=14):
        return 0.3
    if age <= timedelta(days=30):
        return 0.1
    return 0.0


def _get_usage_count(memory_dir: str | Path, memory_id: str) -> int:
    if not memory_id:
        return 0
    usage_path = Path(memory_dir) / "usage_index.json"
    if not usage_path.exists():
        return 0
    try:
        data = json.loads(usage_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(data, dict):
        return 0
    memories = data.get("memories", {})
    record = memories.get(memory_id, {})
    try:
        return max(0, int(record.get("use_count", 0)))
    except (TypeError, ValueError):
        return 0
