"""Paths for persistent voice-agent memory."""

from __future__ import annotations

import re
from pathlib import Path

MEMORY_DIR = Path.home() / ".hermes" / "voice-agent-memory"


def ensure_memory_dir() -> Path:
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    return MEMORY_DIR


def get_project_memory_dir() -> Path:
    return ensure_memory_dir()


def get_memory_entrypoint() -> Path:
    return ensure_memory_dir() / "MEMORY.md"


def get_caller_memory_dir(caller_phone: str, *, memory_dir: str | Path | None = None) -> Path:
    base = Path(memory_dir) if memory_dir is not None else ensure_memory_dir()
    safe = re.sub(r"[^a-zA-Z0-9_+\-]", "_", caller_phone.strip())
    d = base / safe
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_calls_dir() -> Path:
    d = ensure_memory_dir() / "calls"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_clinic_dir() -> Path:
    d = ensure_memory_dir() / "clinic"
    d.mkdir(parents=True, exist_ok=True)
    return d
