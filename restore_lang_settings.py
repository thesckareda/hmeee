#!/usr/bin/env python3
"""Restore Telugu language settings from data/lang_settings_backup.json."""
from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
BACKUP_FILE = BASE_DIR / "data" / "lang_settings_backup.json"
CONFIG_FILE = BASE_DIR / "config.json"


def main() -> None:
    if not BACKUP_FILE.exists():
        raise SystemExit(f"Backup not found: {BACKUP_FILE}")
    backup = json.loads(BACKUP_FILE.read_text(encoding="utf-8"))
    if not CONFIG_FILE.exists():
        raise SystemExit(f"Config not found: {CONFIG_FILE}")

    config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    restored = []
    for key, value in backup.items():
        if key.startswith("_"):
            continue
        config[key] = value
        restored.append(key)

    CONFIG_FILE.write_text(
        json.dumps(config, indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print("Restored language settings:", ", ".join(restored))
    print("Restart the voice agent (or Save & Apply in dashboard) to pick up changes.")


if __name__ == "__main__":
    main()