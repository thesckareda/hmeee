"""
Migration tool — backfill existing Supabase transcripts into RE memory.
CLI: python -m re_voice_memory.migrate --limit 500 --dry-run
"""
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

from re_voice_memory.constants import MEMORY_DIR
from re_voice_memory.lead import get_or_create_lead, save_lead_profile
from re_voice_memory.extract import extract_lead_facts, extract_call_memories


async def run_migration(limit: int = 500, dry_run: bool = False) -> dict:
    """
    Query Supabase call_logs, backfill into RE memory.
    Returns migration report dict.
    """
    supabase_url = os.environ.get("SUPABASE_URL", "")
    supabase_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "") or os.environ.get("SUPABASE_KEY", "")

    report = {
        "processed": 0,
        "profiles_created": 0,
        "profiles_updated": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    if not supabase_url or not supabase_key:
        logger.warning("[MIGRATE] No Supabase credentials — skipping")
        return report

    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Fetch unmigrated call logs
            resp = await client.get(
                f"{supabase_url}/rest/v1/call_logs",
                headers={
                    "apikey": supabase_key,
                    "Authorization": f"Bearer {supabase_key}",
                },
                params={
                    "select": "id,caller_phone,caller_name,transcript,sentiment,created_at,booking_status,duration",
                    "migrated_to_re_memory": "is.null",
                    "order": "created_at.desc",
                    "limit": str(limit),
                },
            )
            resp.raise_for_status()
            rows = resp.json()
    except Exception as e:
        logger.warning("[MIGRATE] Supabase query failed: %s", e)
        return report

    semaphore = asyncio.Semaphore(10)  # max 10 concurrent LLM extractions

    async def _process_row(row: dict) -> bool:
        async with semaphore:
            try:
                phone = row.get("caller_phone", "unknown")
                name = row.get("caller_name", "")
                transcript = row.get("transcript", "")

                if not transcript:
                    return True

                # Load or create profile
                profile = get_or_create_lead(phone, name)
                profile.call_count += 1
                is_new = False

                # Extract facts from transcript
                facts = await extract_lead_facts(transcript)
                if facts:
                    from re_voice_memory.extract import _merge_unique
                    budget_str = facts.get("budget_mentioned")
                    if budget_str:
                        from re_voice_memory.extract import _parse_budget
                        bmin, bmax = _parse_budget(budget_str)
                        if bmin:
                            profile.budget_min = bmin
                        if bmax:
                            profile.budget_max = bmax

                    areas = facts.get("areas_mentioned") or []
                    if areas:
                        profile.preferred_areas = _merge_unique(profile.preferred_areas, areas)

                # Write call summary
                date_str = row.get("created_at", "")[:10]
                time_str = row.get("created_at", "")[11:16].replace(":", "")
                summary_path = MEMORY_DIR / "leads" / phone / f"call_{date_str}_{time_str}.md"
                if not dry_run:
                    summary_path.parent.mkdir(parents=True, exist_ok=True)
                    from voice_memory.fs import atomic_write_text
                    atomic_write_text(summary_path, f"# Historical Call\n\nDate: {date_str}\nPhone: {phone}\n")

                if not dry_run:
                    save_lead_profile(profile)

                return True
            except Exception as e:
                logger.warning("[MIGRATE] Row error: %s", e)
                return False

    tasks = [_process_row(row) for row in rows]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    report["processed"] = sum(1 for r in results if r is True)
    report["errors"] = sum(1 for r in results if isinstance(r, Exception) or r is False)

    logger.info("[MIGRATE] Report: %s", json.dumps(report))
    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Backfill Supabase transcripts into RE memory")
    parser.add_argument("--limit", type=int, default=500, help="Max rows to process")
    parser.add_argument("--dry-run", action="store_true", help="Report only, no writes")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO)
    result = asyncio.run(run_migration(limit=args.limit, dry_run=args.dry_run))
    print(json.dumps(result, indent=2))
