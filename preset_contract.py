"""Load preset contract from data/preset_schema.json — single source for language_router."""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger("inbound-voice-agent")

_SCHEMA_PATH = Path(__file__).resolve().parent / "data" / "preset_schema.json"


@lru_cache(maxsize=1)
def load_preset_schema() -> dict[str, Any]:
    if not _SCHEMA_PATH.is_file():
        return {"presets": {}}
    try:
        return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("[PRESET-SCHEMA] Failed to load %s: %s", _SCHEMA_PATH, exc)
        return {"presets": {}}


def get_preset_contract(preset_id: str | None) -> dict[str, Any] | None:
    key = (preset_id or "tinglish").strip().lower()
    presets = load_preset_schema().get("presets") or {}
    contract = presets.get(key)
    return dict(contract) if isinstance(contract, dict) else None


def interrupt_pool_for(preset_id: str | None) -> list[str]:
    contract = get_preset_contract(preset_id)
    pool = (contract or {}).get("interrupt_pool") or []
    return [str(p) for p in pool if p]


def knowledge_pack_id_for(preset_id: str | None) -> str:
    contract = get_preset_contract(preset_id)
    if contract:
        return str(contract.get("knowledge_pack_id") or preset_id or "tinglish")
    return preset_id or "tinglish"