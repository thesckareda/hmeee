"""
Dream pipeline — background consolidation after every call.
Merges memories, syncs market context, flags stale leads.
"""
import asyncio
import hashlib
import logging
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

from re_voice_memory.constants import MEMORY_DIR


async def run_dream_pipeline(
    phone: str,
    output_dir: Path = MEMORY_DIR,
    market_api_url: str | None = None,
) -> None:
    """Background consolidation — fires after every call. Never raises."""
    lead_dir = output_dir / "leads" / phone

    # ── Sub-task 1: Memory consolidation ──
    try:
        await _consolidate_memories(phone, output_dir)
    except Exception as e:
        logger.warning("[DREAM] Consolidation failed: %s", e)

    # ── Sub-task 2: Property graph pruning ──
    try:
        await _prune_property_graph(phone, output_dir)
    except Exception as e:
        logger.warning("[DREAM] Graph pruning failed: %s", e)

    # ── Sub-task 3: Re-engagement scoring ──
    try:
        await _score_re_engagement(phone, output_dir)
    except Exception as e:
        logger.warning("[DREAM] Re-engagement scoring failed: %s", e)

    logger.info("[DREAM] Pipeline complete for %s", phone)


async def _consolidate_memories(phone: str, output_dir: Path) -> None:
    """Merge call summaries into consolidated profile after 3+ calls."""
    from re_voice_memory.lead import load_lead_profile

    profile = await asyncio.to_thread(load_lead_profile, phone)
    if not profile or profile.call_count < 3:
        return

    cons_path = output_dir / "leads" / phone / "consolidated_profile.md"
    if cons_path.exists():
        return  # already consolidated

    cons_path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        f"# Consolidated Lead Profile — {profile.stable_name or phone}\n\n"
        f"**Role:** {profile.role}\n"
        f"**Budget:** Rs{getattr(profile, 'budget_min', '?')}–{getattr(profile, 'budget_max', '?')}\n"
        f"**Preferred Areas:** {', '.join(profile.preferred_areas) or 'none'}\n"
        f"**Excluded Areas:** {', '.join(profile.excluded_areas) or 'none'}\n"
        f"**Deal Stage:** {profile.deal_stage}\n"
        f"**Intent Score:** {profile.intent_score}\n"
        f"**Total Calls:** {profile.call_count}\n"
        f"**Last Contact:** {profile.last_contact_date or 'never'}\n"
        f"**Loan Status:** {profile.loan_status}\n"
        f"\n**Notes:** {profile.notes or 'none'}\n"
    )
    cons_path.write_text(content)
    logger.info("[DREAM] Consolidated profile created for %s", phone)


async def _prune_property_graph(phone: str, output_dir: Path) -> None:
    """Remove property signals older than 365 days."""
    from re_voice_memory.property_graph import load_property_graph, save_property_graph

    graph = await asyncio.to_thread(load_property_graph, phone)
    if not graph.signals:
        return

    cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    old_count = len(graph.signals)

    # Remove signals older than 365 days
    graph.signals = [s for s in graph.signals if s.call_date >= cutoff]

    # For listings with both liked and rejected, keep only the most recent
    seen: dict[str, list] = {}
    for s in graph.signals:
        seen.setdefault(s.listing_id, []).append(s)
    keep_ids: set = set()
    for lid, sigs in seen.items():
        if len(sigs) > 1:
            sigs.sort(key=lambda s: s.call_date, reverse=True)
            keep_ids.add(id(sigs[0]))  # keep most recent by object identity
        else:
            keep_ids.add(id(sigs[0]))
    graph.signals = [s for s in graph.signals if id(s) in keep_ids]

    if len(graph.signals) < old_count:
        await asyncio.to_thread(save_property_graph, graph)
        logger.info("[DREAM] Pruned %d stale signals for %s", old_count - len(graph.signals), phone)


async def _score_re_engagement(phone: str, output_dir: Path) -> None:
    """Flag leads that need re-engagement callback."""
    from re_voice_memory.lead import load_lead_profile

    profile = await asyncio.to_thread(load_lead_profile, phone)
    if not profile or not profile.last_contact_date:
        return

    if profile.deal_stage not in ("aware", "interested", "viewing"):
        return

    try:
        last_contact = datetime.fromisoformat(profile.last_contact_date.replace("Z", "+00:00"))
        days_since = (datetime.now(timezone.utc) - last_contact).days
    except (ValueError, TypeError):
        return

    if days_since <= 14:
        return

    # re_engagement_score = intent_score * (1 / log(days_since + 1))
    raw_score = profile.intent_score * (1.0 / math.log(days_since + 1))
    if raw_score > 4.0:
        flag_path = output_dir / "leads" / phone / "re_engagement_flag.md"
        flag_path.parent.mkdir(parents=True, exist_ok=True)
        flag_path.write_text(
            f"# Re-engagement Flag\n\n"
            f"**Score:** {raw_score:.1f}\n"
            f"**Days Since Last Contact:** {days_since}\n"
            f"**Deal Stage:** {profile.deal_stage}\n"
            f"**Intent Score:** {profile.intent_score}\n"
        )
        logger.info("[DREAM] Re-engagement flagged for %s (score: %.1f)", phone, raw_score)


async def run_ttl_reaper(output_dir: Path = MEMORY_DIR) -> None:
    """Scan .md files and disable expired entries. Run weekly."""
    import yaml
    reaped = 0
    now = datetime.now(timezone.utc)

    for md_file in output_dir.rglob("*.md"):
        try:
            content = md_file.read_text()
            if content.startswith("---"):
                end = content.find("\n---\n", 4)
                if end != -1:
                    fm = yaml.safe_load(content[4:end]) or {}
                    ttl_days = fm.get("ttl_days")
                    created = fm.get("call_date") or fm.get("last_updated")
                    if ttl_days and created:
                        created_dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                        if (now - created_dt).days > int(ttl_days):
                            # Soft-delete: prepend disabled marker
                            md_file.write_text(f"---\ndisabled: true\n---\n{content}")
                            reaped += 1
        except Exception:
            pass

    if reaped:
        logger.info("[DREAM-REAPER] Disabled %d expired entries", reaped)
