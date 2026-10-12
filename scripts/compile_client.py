#!/usr/bin/env python3
"""CLI for the KB compiler — same pipeline the dashboard drives.

Usage:
  python3 scripts/compile_client.py <client_id>              # compile
  python3 scripts/compile_client.py <client_id> --approve    # human gate → install
  python3 scripts/compile_client.py --list                   # cartridges + status
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main() -> int:
    args = sys.argv[1:]
    from kb_compiler.bake import approve_and_apply, client_dir, clients_root, load_cartridge_summary

    if not args or args[0] == "--list":
        root = clients_root(_ROOT)
        if not root.is_dir():
            print("No clients/ directory yet.")
            return 0
        for cdir in sorted(p for p in root.iterdir() if p.is_dir()):
            print(json.dumps(load_cartridge_summary(cdir), indent=2, ensure_ascii=False))
        return 0

    client_id = args[0]
    cdir = client_dir(_ROOT, client_id)
    if not cdir.is_dir():
        print(f"Unknown client '{client_id}' — create clients/{client_id}/client.yaml first")
        return 1

    if "--approve" in args:
        result = approve_and_apply(cdir, _ROOT, approved_by=os.environ.get("USER", "cli"))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("success") else 1

    from kb_compiler.pipeline import run_compile

    result = asyncio.run(run_compile(client_id, _ROOT))
    print(json.dumps({k: v for k, v in result.items() if k != "lock"}, indent=2, ensure_ascii=False))
    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
