"""Helpers for managing memory files."""

from __future__ import annotations

from pathlib import Path
from re import sub

from voice_memory.file_lock import exclusive_file_lock
from voice_memory.fs import atomic_write_text
from voice_memory.paths import get_memory_entrypoint, get_project_memory_dir
from voice_memory.scan import scan_memory_files
from voice_memory.schema import (
    DEFAULT_MEMORY_SCOPE,
    DEFAULT_MEMORY_TYPE,
    SCHEMA_VERSION,
    MemoryScope,
    MemoryType,
    coerce_int,
    compute_memory_signature,
    first_content_line,
    format_datetime,
    generate_memory_id,
    render_memory_file,
    split_memory_file,
    utc_now,
)


def list_memory_files(*, memory_dir: str | Path | None = None) -> list[Path]:
    return sorted(
        header.path for header in scan_memory_files(memory_dir=memory_dir, max_files=None)
    )


def add_memory_entry(
    title: str,
    content: str,
    *,
    memory_type: MemoryType = DEFAULT_MEMORY_TYPE,
    scope: MemoryScope = DEFAULT_MEMORY_SCOPE,
    description: str = "",
    tags: tuple[str, ...] = (),
    memory_dir: str | Path | None = None,
) -> Path:
    resolved_dir = Path(memory_dir) if memory_dir is not None else get_project_memory_dir()
    slug = sub(r"[^a-zA-Z0-9]+", "_", title.strip().lower()).strip("_") or "memory"

    with exclusive_file_lock(resolved_dir / ".memory.lock"):
        category = "knowledge"
        body = content.strip() + "\n"
        signature = compute_memory_signature(body, memory_type, category)
        existing = scan_memory_files(
            memory_dir=resolved_dir, max_files=None, include_disabled=True, include_expired=True
        )
        duplicate = next(
            (
                header
                for header in existing
                if _effective_signature(header.path, header.signature) == signature
            ),
            None,
        )
        path = duplicate.path if duplicate is not None else _next_memory_path(resolved_dir, slug)
        now = utc_now()
        now_text = format_datetime(now)
        if path.exists():
            metadata, old_body, _, _ = split_memory_file(path.read_text(encoding="utf-8"))
            created_at = str(metadata.get("created_at") or now_text)
            memory_id = str(metadata.get("id") or generate_memory_id(now))
        else:
            metadata = {}
            created_at = now_text
            memory_id = generate_memory_id(now)

        metadata.update({
            "schema_version": SCHEMA_VERSION,
            "id": memory_id,
            "name": title.strip(),
            "description": description.strip() or first_content_line(body) or title.strip(),
            "type": str(metadata.get("type") or memory_type),
            "scope": str(metadata.get("scope") or scope),
            "category": str(metadata.get("category") or category),
            "importance": coerce_int(metadata.get("importance"), default=1),
            "source": "manual",
            "signature": signature,
            "created_at": created_at,
            "updated_at": now_text,
            "ttl_days": metadata.get("ttl_days"),
            "disabled": False,
            "supersedes": metadata.get("supersedes") or [],
            "tags": list(dict.fromkeys(str(tag).strip() for tag in tags if str(tag).strip())),
        })
        atomic_write_text(path, render_memory_file(metadata, body))

        entrypoint = resolved_dir / "MEMORY.md"
        index_text = (
            entrypoint.read_text(encoding="utf-8")
            if entrypoint.exists()
            else "# Memory Index\n"
        )
        existing_names = {
            line.strip(" -[]()").split("](")[-1].rstrip(")")
            for line in index_text.splitlines()
            if line.strip().startswith("- [")
        }
        if path.name not in existing_names:
            index_text = index_text.rstrip() + f"\n- [{title}]({path.name})\n"
            atomic_write_text(entrypoint, index_text)
    return path


def remove_memory_entry(name: str, *, memory_dir: str | Path | None = None) -> bool:
    resolved_dir = Path(memory_dir) if memory_dir is not None else get_project_memory_dir()
    matches = [
        header
        for header in scan_memory_files(
            memory_dir=resolved_dir, max_files=None, include_disabled=True, include_expired=True
        )
        if name in {header.path.stem, header.path.name, header.title, header.id}
    ]
    if not matches:
        return False
    header = matches[0]
    if header.disabled:
        return False
    path = header.path
    with exclusive_file_lock(resolved_dir / ".memory.lock"):
        if path.exists():
            content = path.read_text(encoding="utf-8")
            metadata, body, _, _ = split_memory_file(content)
            metadata["disabled"] = True
            metadata["updated_at"] = format_datetime(utc_now())
            atomic_write_text(path, render_memory_file(metadata, body))

        entrypoint = resolved_dir / "MEMORY.md"
        if entrypoint.exists():
            import re
            lines = entrypoint.read_text(encoding="utf-8").splitlines()
            kept = []
            for line in lines:
                m = re.search(r"\]\(([^)]+)\)", line)
                if m and m.group(1) == path.name:
                    continue
                kept.append(line)
            atomic_write_text(entrypoint, "\n".join(kept).rstrip() + "\n")
    return True


def _next_memory_path(memory_dir: Path, slug: str) -> Path:
    path = memory_dir / f"{slug}.md"
    if not path.exists():
        return path
    index = 2
    while True:
        candidate = memory_dir / f"{slug}_{index}.md"
        if not candidate.exists():
            return candidate
        index += 1


def _effective_signature(path: Path, existing_signature: str) -> str:
    if existing_signature:
        return existing_signature
    try:
        metadata, body, _, _ = split_memory_file(path.read_text(encoding="utf-8"))
    except OSError:
        return ""
    memory_type = str(metadata.get("type") or "project")
    category = str(metadata.get("category") or "knowledge")
    return compute_memory_signature(body, memory_type, category)
