#!/usr/bin/env python3
"""Pre-flight checks for VK Realty HME deployment."""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv

load_dotenv(_ROOT / ".env")

OK = "✓"
WARN = "!"
FAIL = "✗"


def _check(label: str, ok: bool, detail: str = "") -> bool:
    mark = OK if ok else FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {mark} {label}{suffix}")
    return ok


def main() -> int:
    print("VK Realty HME — setup verification\n")
    passed = 0
    total = 0

    # Env
    print("Environment (.env)")
    for key in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET", "GEMINI_API_KEY", "SUPABASE_URL", "SUPABASE_KEY"):
        total += 1
        if _check(key, bool(os.getenv(key, "").strip())):
            passed += 1

    srk = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    total += 1
    if _check("SUPABASE_SERVICE_ROLE_KEY", bool(srk), "recommended for production" if not srk else ""):
        passed += 1
    elif not srk:
        print(f"  {WARN} Using anon key only — run sql/supabase_fix_rls.sql")

    bucket = os.getenv("SUPABASE_RECORDINGS_BUCKET") or os.getenv("SUPABASE_BUCKET") or "call-recordings"
    total += 1
    if _check("SUPABASE_RECORDINGS_BUCKET", True, bucket):
        passed += 1

    # Files
    print("\nBundled assets")
    for rel in ("CICD-1.ogg", "config.json", "sql/supabase_schema.sql"):
        total += 1
        if _check(rel, (_ROOT / rel).is_file()):
            passed += 1

    # Python modules
    print("\nCore modules")
    modules = (
        "token_counter",
        "turn_context_injection",
        "background_audio",
        "re_voice_memory.caller_history",
        "org_store",
        "tenancy",
    )
    for mod in modules:
        total += 1
        try:
            __import__(mod)
            if _check(mod, True):
                passed += 1
        except Exception as exc:
            _check(mod, False, str(exc))

    # Token counter smoke
    print("\nToken counter")
    from token_counter import count_tokens, get_stats

    total += 1
    n = count_tokens("Namaste, budget 2 crore", "hme")
    if _check("count_tokens smoke", n > 0, f"~{n} tokens"):
        passed += 1
    stats = get_stats()
    print(f"      cache: {stats.get('cache_size', 0)} entries")

    # Ambient
    print("\nAmbient audio")
    from background_audio import _resolve_custom_sound_path

    total += 1
    if _check("cicd_1 resolves", bool(_resolve_custom_sound_path("cicd_1"))):
        passed += 1

    # Config flags
    print("\nConfig flags (config.json)")
    import json

    raw = json.loads((_ROOT / "config.json").read_text(encoding="utf-8"))
    for flag in ("parallel_turn_injectors_enabled", "llm_context_prewarm", "speculative_reply_enabled"):
        total += 1
        val = raw.get(flag)
        if _check(flag, val is not None, str(val)):
            passed += 1

    amb = raw.get("ambient_sound") or {}
    total += 1
    if _check("ambient_sound.sound_type", amb.get("sound_type") == "cicd_1", amb.get("sound_type", "")):
        passed += 1

    # Supabase ping (optional)
    print("\nSupabase (live)")
    url = os.getenv("SUPABASE_URL", "").strip()
    key = srk or os.getenv("SUPABASE_KEY", "").strip()
    if url and key:
        try:
            from supabase import create_client

            sb = create_client(url, key)
            sb.table("call_logs").select("id").limit(1).execute()
            total += 1
            if _check("call_logs reachable", True):
                passed += 1
        except Exception as exc:
            total += 1
            _check("call_logs reachable", False, str(exc)[:120])
    else:
        print(f"  {WARN} Skipped — no Supabase credentials")

    print(f"\n{passed}/{total} checks passed")
    return 0 if passed >= total - 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())