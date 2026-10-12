#!/usr/bin/env python3
"""CI gate — conversational IQ registry integrity (IQ plan 0.4)."""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

EXPECTED_IDS = {
    "iq_01_name_anchored_transition",
    "iq_02_name_micro_commitment",
    "iq_04_social_graph_reference",
    "iq_05_name_silence_rescue",
    "iq_07_same_boat_disclosure",
    "iq_09_frustration_deescalation",
    "iq_11_one_two_discovery",
    "iq_14_turn_ending_hooks",
    "iq_15_topic_breadcrumb_trail",
    "iq_16_self_correction",
    "iq_17_temporal_grounding",
    "iq_18_external_context",
    "iq_21_objection_reframe_story",
    "iq_22_before_answer_bridge",
    "iq_23_risk_reversal",
    "iq_24_grounded_scarcity",
    "iq_26_prosodic_questions",
    "iq_28_next_step_ownership",
}

REQUIRED_PACKS = ("tinglish", "hte", "english")

# requires keys must be real live_config keys set somewhere in the engine
KNOWN_LIVE_CONFIG_KEYS = {"_caller_name", "_relations_line", "_relations"}


def main() -> int:
    from iq_registry import all_primitives, clear_registry_cache

    clear_registry_cache()
    prims = all_primitives()
    errors: list[str] = []

    ids = set(prims.keys())
    missing = EXPECTED_IDS - ids
    extra = ids - EXPECTED_IDS
    if missing:
        errors.append(f"Missing primitive ids: {sorted(missing)}")
    if extra:
        errors.append(f"Unexpected primitive ids: {sorted(extra)}")

    for pid, prim in sorted(prims.items()):
        if not prim.event:
            errors.append(f"{pid}: empty trigger event")
        for pack in REQUIRED_PACKS:
            if not str(prim.rules.get(pack) or "").strip():
                errors.append(f"{pid}: missing rule for pack '{pack}'")
        for key in prim.requires:
            if key not in KNOWN_LIVE_CONFIG_KEYS:
                errors.append(f"{pid}: requires unknown live_config key '{key}'")
        if prim.priority < 0:
            errors.append(f"{pid}: negative priority")

    if errors:
        print("IQ REGISTRY VALIDATION FAILED:")
        for err in errors:
            print(f"  ✗ {err}")
        return 1

    print(f"IQ registry OK — {len(prims)} primitives, all packs covered.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
