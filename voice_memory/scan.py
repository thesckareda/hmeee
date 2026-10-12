"""Scan project memory files."""

from __future__ import annotations

from pathlib import Path

from voice_memory.paths import get_project_memory_dir
from voice_memory.schema import (
    coerce_bool,
    coerce_int,
    coerce_optional_int,
    coerce_str_list,
    first_content_line,
    is_memory_expired,
    split_memory_file,
)
from voice_memory.types import MemoryHeader


def scan_memory_files(
    *,
    max_files: int | None = 50,
    include_disabled: bool = False,
    include_expired: bool = False,
    memory_dir: str | Path | None = None,
) -> list[MemoryHeader]:
    memory_dir = Path(memory_dir) if memory_dir is not None else get_project_memory_dir()
    if not memory_dir.exists():
        return []

    paths = sorted(
        memory_dir.rglob("*.md"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )

    headers: list[MemoryHeader] = []
    for path in paths:
        if path.name == "MEMORY.md" or path.parent.name == "calls":
            continue
        if max_files is not None and len(headers) >= max_files:
            break
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        header = _parse_memory_file(path, text)
        if header.disabled and not include_disabled:
            continue
        if is_memory_expired(_metadata_from_header(header)) and not include_expired:
            continue
        headers.append(header)

    # headers are already sorted by mtime from path sorting; _parse_memory_file
    # may yield a different modified_at from frontmatter, so re-sort for correctness
    headers.sort(key=lambda item: item.modified_at, reverse=True)
    return headers


def _parse_memory_file(path: Path, content: str) -> MemoryHeader:
    metadata, body, _, _ = split_memory_file(content)
    lines = body.splitlines()
    title = path.stem
    description = ""
    memory_type = ""
    if metadata.get("name"):
        title = str(metadata["name"])
    if metadata.get("description"):
        description = str(metadata["description"])
    if metadata.get("type"):
        memory_type = str(metadata["type"])

    desc_line_idx: int | None = None
    if not description:
        fallback = first_content_line("\n".join(lines[:10]))
        if fallback:
            description = fallback
        for idx, line in enumerate(lines[:10]):
            stripped = line.strip()
            if stripped[:200] == description:
                desc_line_idx = idx
                break

    body_lines = [
        line.strip()
        for idx, line in enumerate(lines)
        if line.strip() and not line.strip().startswith("#") and idx != desc_line_idx
    ]
    body_preview = " ".join(body_lines)[:300]

    return MemoryHeader(
        path=path,
        title=title,
        description=description,
        modified_at=path.stat().st_mtime,
        memory_type=memory_type,
        body_preview=body_preview,
        id=str(metadata.get("id") or ""),
        schema_version=coerce_int(metadata.get("schema_version"), default=0),
        category=str(metadata.get("category") or ""),
        importance=coerce_int(metadata.get("importance"), default=0),
        source=str(metadata.get("source") or ""),
        signature=str(metadata.get("signature") or ""),
        created_at=str(metadata.get("created_at") or ""),
        updated_at=str(metadata.get("updated_at") or ""),
        ttl_days=coerce_optional_int(metadata.get("ttl_days")),
        disabled=coerce_bool(metadata.get("disabled"), default=False),
        supersedes=coerce_str_list(metadata.get("supersedes")),
        relative_path=path.name,
        tags=coerce_str_list(metadata.get("tags")),
        frontmatter=metadata,
    )


def _metadata_from_header(header: MemoryHeader) -> dict[str, object]:
    return {
        "created_at": header.created_at,
        "updated_at": header.updated_at,
        "ttl_days": header.ttl_days,
    }
