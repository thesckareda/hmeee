"""
Conversational IQ primitive registry — IQ plan 0.4.

One JSON (data/conversational_iq/registry.json), loaded once at startup into
frozen structures. Runtime access is a dict lookup: find_by_event() is O(1)
per event type. Per-primitive kill switch via live_config["iq_disabled_primitives"].
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger("inbound-voice-agent")

_REGISTRY_PATH = Path(__file__).resolve().parent / "data" / "conversational_iq" / "registry.json"

# Wave-6-eligible latency class; static/precall/audio entries are implemented
# in their own modules and skipped by the assembler.
PROMPT_CLASS = "P0_prompt"


@dataclass(frozen=True)
class Primitive:
    pid: str
    event: str
    latency_class: str
    priority: int
    cooldown_turns: int
    max_fires_per_call: int
    requires: tuple[str, ...] = ()
    requires_ctx: tuple[str, ...] = ()
    requires_missing_slot: bool = False
    min_filled_values: int = 0
    stages: tuple[str, ...] = ()       # current planner stage filter
    to_stages: tuple[str, ...] = ()    # stage_change target filter
    preempts_all: bool = False
    preempts: tuple[str, ...] = ()
    rules: dict = field(default_factory=dict)

    def rule_for(self, pack: str) -> str:
        return str(self.rules.get(pack) or self.rules.get("tinglish") or "")


_by_event: dict[str, list[Primitive]] | None = None
_all: dict[str, Primitive] = {}


def _load() -> dict[str, list[Primitive]]:
    global _by_event, _all
    if _by_event is not None:
        return _by_event

    by_event: dict[str, list[Primitive]] = {}
    all_prims: dict[str, Primitive] = {}
    try:
        with open(_REGISTRY_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[IQ-REGISTRY] Load failed: %s", exc)
        _by_event, _all = {}, {}
        return _by_event

    for pid, entry in raw.items():
        if pid.startswith("_") or not isinstance(entry, dict):
            continue
        trigger = entry.get("trigger") or {}
        prim = Primitive(
            pid=pid,
            event=str(trigger.get("event") or ""),
            latency_class=str(entry.get("latency_class") or PROMPT_CLASS),
            priority=int(entry.get("priority", 50)),
            cooldown_turns=int(entry.get("cooldown_turns", 0)),
            max_fires_per_call=int(entry.get("max_fires_per_call", 0)),
            requires=tuple(trigger.get("requires") or ()),
            requires_ctx=tuple(trigger.get("requires_ctx") or ()),
            requires_missing_slot=bool(trigger.get("requires_missing_slot", False)),
            min_filled_values=int(trigger.get("min_filled_values", 0)),
            stages=tuple(trigger.get("stage") or ()),
            to_stages=tuple(trigger.get("to") or ()),
            preempts_all=bool(entry.get("preempts_all", False)),
            preempts=tuple(entry.get("preempts") or ()),
            rules=dict(entry.get("rule") or {}),
        )
        all_prims[pid] = prim
        by_event.setdefault(prim.event, []).append(prim)

    for prims in by_event.values():
        prims.sort(key=lambda p: p.priority)

    _by_event, _all = by_event, all_prims
    logger.info("[IQ-REGISTRY] Loaded %d primitives", len(all_prims))
    return _by_event


def find_by_event(event: str) -> list[Primitive]:
    return _load().get(event, [])


def all_primitives() -> dict[str, Primitive]:
    _load()
    return _all


def clear_registry_cache() -> None:
    """Test hook."""
    global _by_event, _all
    _by_event, _all = None, {}
