"""
Print current local token multipliers (runtime defaults + any live calibration).

Offline API calibration removed — multipliers self-update via ingest_calibration()
from live Gemini inference usage_metadata on every turn.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from token_counter import get_stats


def main() -> None:
    stats = get_stats()
    print("Current token multipliers (chars → tokens):")
    for preset, mult in sorted(stats.get("multipliers", {}).items()):
        print(f"  {preset}: {mult:.4f}")
    print(f"\nCache: {stats['cache_size']}/{stats['cache_max']} entries")


if __name__ == "__main__":
    main()