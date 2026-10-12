"""Supabase transcript migration → voice_memory files.

Reads existing call records from Supabase and converts them into the
voice_memory file format (raw.md, caller profiles, call summaries).

Usage:
    python -m voice_memory.migrate --limit 10 --no-dry-run
    python -m voice_memory.migrate --phone +919876543210
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow running as script or module
if __name__ != "__main__":
    from voice_memory.raw_md import generate_raw_md
    from voice_memory.extract import extract_call_memories
    from voice_memory.manager import add_memory_entry
    from voice_memory.paths import (
        get_caller_memory_dir,
        get_calls_dir,
        get_project_memory_dir,
        ensure_memory_dir,
    )
else:
    # Running as script — add parent to path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from voice_memory.raw_md import generate_raw_md
    from voice_memory.extract import extract_call_memories
    from voice_memory.manager import add_memory_entry
    from voice_memory.paths import (
        get_caller_memory_dir,
        get_calls_dir,
        get_project_memory_dir,
        ensure_memory_dir,
    )


def _get_supabase_url() -> str:
    return os.environ.get("SUPABASE_URL", "").rstrip("/")


def _get_supabase_key() -> str:
    return os.environ.get(
        "SUPABASE_SERVICE_KEY",
        os.environ.get("SUPABASE_KEY", ""),
    )


async def _supabase_query(endpoint: str, *, url: str, key: str) -> dict[str, Any]:
    """Execute a Supabase REST query. Requires httpx."""
    import httpx

    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(f"{url}/rest/v1/{endpoint}", headers=headers)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]


async def migrate_supabase_transcripts(
    *,
    limit: int = 50,
    dry_run: bool = True,
    memory_dir: str | Path | None = None,
    supabase_url: str | None = None,
    supabase_key: str | None = None,
    phone_filter: str | None = None,
) -> dict[str, Any]:
    """Migrate call transcripts from Supabase to voice_memory files.

    Args:
        limit: Max calls to migrate.
        dry_run: If True, preview only — no files written.
        memory_dir: Voice memory directory (default: ~/.hermes/voice-agent-memory).
        supabase_url: Supabase project URL.
        supabase_key: Supabase service role key.
        phone_filter: Only migrate calls for this phone number.

    Returns:
        dict with migrated, skipped, errors counts and files_created list.
    """
    url = supabase_url or _get_supabase_url()
    key = supabase_key or _get_supabase_key()

    if not url or not key:
        print("[MIGRATE] SUPABASE_URL and SUPABASE_SERVICE_KEY required in environment.")
        return {"migrated": 0, "skipped": 0, "errors": 1, "files_created": [], "error": "Missing Supabase credentials"}

    # Build query
    query_parts = [
        "call_logs",
        "?select=*",
        "&transcript=not.is.null",
        "&order=created_at.desc",
        f"&limit={limit}",
    ]
    if phone_filter:
        query_parts.append(f"&phone_number=eq.{urllib.parse.quote(phone_filter, safe='')}")

    endpoint = "".join(query_parts)

    print(f"[MIGRATE] Querying: {endpoint}")
    print(f"[MIGRATE] Mode: {'DRY RUN' if dry_run else 'LIVE'}")

    try:
        records = await _supabase_query(endpoint, url=url, key=key)
    except Exception as e:
        print(f"[MIGRATE] Failed to query Supabase: {e}")
        return {"migrated": 0, "skipped": 0, "errors": 1, "files_created": [], "error": str(e)}

    if not records:
        print("[MIGRATE] No call records found.")
        return {"migrated": 0, "skipped": 0, "errors": 0, "files_created": []}

    print(f"[MIGRATE] Found {len(records)} call records.")

    migrated = 0
    skipped = 0
    errors = 0
    files_created: list[str] = []

    for i, record in enumerate(records):
        phone = record.get("phone_number") or record.get("phone", "unknown")
        if phone in ("unknown", "", None):
            print(f"[MIGRATE] Record {i+1}/{len(records)}: SKIP — unknown phone")
            skipped += 1
            continue

        # Check if already migrated
        calls_dir = get_calls_dir()
        room_name = record.get("call_room_id") or record.get("room_name") or f"supabase-{record.get('id', i)}"
        existing_raw_md = calls_dir / room_name / "raw.md"
        if existing_raw_md.exists():
            print(f"[MIGRATE] Record {i+1}/{len(records)}: SKIP — already migrated ({room_name})")
            skipped += 1
            continue

        if dry_run:
            print(f"[MIGRATE] Record {i+1}/{len(records)}: WOULD MIGRATE phone={phone} room={room_name}")
            migrated += 1
            continue

        # Actually migrate
        try:
            created = migrate_single_call(record, memory_dir=memory_dir or get_project_memory_dir())
            files_created.extend(str(p) for p in created)
            print(f"[MIGRATE] Record {i+1}/{len(records)}: MIGRATED phone={phone} ({len(created)} files)")
            migrated += 1
        except Exception as e:
            print(f"[MIGRATE] Record {i+1}/{len(records)}: ERROR phone={phone}: {e}")
            errors += 1

    summary = {
        "migrated": migrated,
        "skipped": skipped,
        "errors": errors,
        "files_created": files_created,
    }
    print(f"\n[MIGRATE] Summary: {migrated} migrated, {skipped} skipped, {errors} errors")
    if dry_run:
        print("[MIGRATE] DRY RUN — no files were written. Use --no-dry-run to commit.")

    return summary


def migrate_single_call(call_data: dict, *, memory_dir: str | Path | None = None) -> list[Path]:
    """Migrate one Supabase call record to voice_memory files.

    Args:
        call_data: Supabase row with keys like phone_number, caller_name, transcript,
                   summary, created_at, duration, call_room_id, sentiment, etc.

    Returns:
        List of created file paths.
    """
    created: list[Path] = []

    phone = call_data.get("phone_number") or call_data.get("phone", "unknown")
    name = call_data.get("caller_name") or call_data.get("name") or "Unknown"
    transcript = call_data.get("transcript") or ""
    summary = call_data.get("summary") or call_data.get("call_summary") or "No summary"
    created_at = call_data.get("created_at") or datetime.now(timezone.utc).isoformat()
    duration = int(call_data.get("duration") or 0)
    room_name = call_data.get("call_room_id") or call_data.get("room_name") or f"supabase-{call_data.get('id', '')}"
    sentiment = call_data.get("sentiment") or "unknown"
    booking_status = summary  # Use summary as booking status if "Booking" appears
    if "book" in summary.lower() and "confirm" in summary.lower():
        booking_status = f"Booking Confirmed (migrated): {summary[:100]}"
    elif "book" in summary.lower():
        booking_status = f"Booking Detected (migrated): {summary[:100]}"

    # ── 1. Generate raw.md ──
    try:
        # Parse transcript into turn entries
        transcript_lines: list[dict] = []
        for line in transcript.splitlines():
            line = line.strip()
            if line.startswith("[USER]"):
                transcript_lines.append({
                    "role": "user",
                    "content": line[len("[USER] "):].strip(),
                    "timestamp": "",
                })
            elif line.startswith("[ASSISTANT]"):
                transcript_lines.append({
                    "role": "assistant",
                    "content": line[len("[ASSISTANT] "):].strip(),
                    "timestamp": "",
                    "tool_calls": [],
                })

        if not transcript_lines:
            # Use summary as single turn
            transcript_lines = [{"role": "user", "content": f"[Migrated from Supabase] {summary[:500]}", "timestamp": ""}]

        # Parse date from created_at
        try:
            call_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            call_date = call_dt.strftime("%Y-%m-%d")
            start_time = call_dt.strftime("%I:%M %p")
        except (ValueError, TypeError):
            call_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            start_time = "Unknown"

        raw_path = generate_raw_md(
            room_name=room_name,
            call_date=call_date,
            start_time=start_time,
            end_time=start_time,
            duration=duration,
            caller_phone=phone,
            caller_name=name,
            booking_status=booking_status,
            sentiment=sentiment,
            turn_count=len(transcript_lines),
            interrupt_count=0,
            system_prompt="(Migrated from Supabase — original system prompt not preserved)",
            transcript_lines=transcript_lines,
        )
        created.append(raw_path)
    except Exception as e:
        print(f"[MIGRATE:SINGLE] raw.md failed for {phone}: {e}")

    # ── 2. Extract caller memories ──
    try:
        is_booked = "book" in summary.lower() and "confirm" in summary.lower()
        buffered = []
        if is_booked:
            buffered.append({
                "key": f"booking:migrated-{created_at[:10]}",
                "value": f"Migrated from Supabase: {summary[:200]}",
                "timestamp": created_at,
            })

        extracted = extract_call_memories(
            caller_phone=phone,
            caller_name=name,
            booking_status=booking_status,
            transcript_text=transcript,
            buffered_context=buffered,
        )
        created.extend(extracted)
    except Exception as e:
        print(f"[MIGRATE:SINGLE] Memory extraction failed for {phone}: {e}")

    return created


# ── CLI ──

def _parse_args():
    limit = 50
    dry_run = True
    phone = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--limit" and i + 1 < len(args):
            limit = int(args[i + 1])
            i += 2
        elif args[i] == "--no-dry-run":
            dry_run = False
            i += 1
        elif args[i] == "--phone" and i + 1 < len(args):
            phone = args[i + 1]
            i += 2
        elif args[i] in ("-h", "--help"):
            print(__doc__)
            print("Options: --limit N, --no-dry-run, --phone +919876543210")
            sys.exit(0)
        else:
            print(f"Unknown arg: {args[i]}")
            i += 1

    return limit, dry_run, phone


if __name__ == "__main__":
    limit, dry_run, phone = _parse_args()
    print(f"Voice Memory Migration")
    print(f"  Limit: {limit}")
    print(f"  Dry Run: {dry_run}")
    if phone:
        print(f"  Phone Filter: {phone}")
    print()

    result = asyncio.run(
        migrate_supabase_transcripts(
            limit=limit,
            dry_run=dry_run,
            phone_filter=phone,
        )
    )

    if result.get("files_created"):
        print(f"\nFiles created ({len(result['files_created'])}):")
        for f in result["files_created"][:20]:
            print(f"  {f}")
        if len(result["files_created"]) > 20:
            print(f"  ... and {len(result['files_created']) - 20} more")
