"""Canonical call transcript artifact generation — raw.md writer."""

import asyncio
from pathlib import Path

from re_voice_memory.constants import MEMORY_DIR
from re_voice_memory.lead import _get_phone_lock
from voice_memory.fs import atomic_write_text


async def generate_raw_md(
    room_name: str,
    call_date: str,
    start_time: str,
    end_time: str,
    duration: int,
    caller_phone: str,
    caller_name: str,
    lead_role: str,
    deal_stage: str,
    campaign_id: str | None,
    listings_discussed: list[str],
    viewing_requests: list[str],
    offer_intent: dict | None,
    intent_score_before: float | None,
    booking_status: str,
    sentiment: str,
    turn_count: int,
    interrupt_count: int,
    system_prompt: str,
    transcript_lines: list[dict],
    output_dir: Path,
) -> Path:
    lock = await _get_phone_lock(caller_phone)
    async with lock:
        return _write_raw_md_nolock(
            room_name=room_name,
            call_date=call_date,
            start_time=start_time,
            end_time=end_time,
            duration=duration,
            caller_phone=caller_phone,
            caller_name=caller_name,
            lead_role=lead_role,
            deal_stage=deal_stage,
            campaign_id=campaign_id,
            listings_discussed=listings_discussed,
            viewing_requests=viewing_requests,
            offer_intent=offer_intent,
            intent_score_before=intent_score_before,
            booking_status=booking_status,
            sentiment=sentiment,
            turn_count=turn_count,
            interrupt_count=interrupt_count,
            system_prompt=system_prompt,
            transcript_lines=transcript_lines,
            output_dir=output_dir,
        )


def _write_raw_md_nolock(
    room_name: str,
    call_date: str,
    start_time: str,
    end_time: str,
    duration: int,
    caller_phone: str,
    caller_name: str,
    lead_role: str,
    deal_stage: str,
    campaign_id: str | None,
    listings_discussed: list[str],
    viewing_requests: list[str],
    offer_intent: dict | None,
    intent_score_before: float | None,
    booking_status: str,
    sentiment: str,
    turn_count: int,
    interrupt_count: int,
    system_prompt: str,
    transcript_lines: list[dict],
    output_dir: Path,
) -> Path:
    out_path = output_dir / "calls" / room_name / "raw.md"

    listings_str = ", ".join(listings_discussed) if listings_discussed else "none"
    viewing_str = ", ".join(viewing_requests) if viewing_requests else "none"

    if offer_intent is not None:
        lid = offer_intent.get("listing_id", "?")
        amt = offer_intent.get("amount", "?")
        offer_str = f"{lid}: ${amt}"
    else:
        offer_str = "none"

    if intent_score_before is not None:
        score_str = str(intent_score_before)
    else:
        score_str = "first call"

    campaign_str = campaign_id if campaign_id else "direct"

    lines: list[str] = []
    lines.append(f"# Call Transcript - {room_name}")
    lines.append("")
    lines.append(f"**Date:** {call_date}")
    lines.append(f"**Time:** {start_time} - {end_time} IST")
    lines.append(f"**Duration:** {duration} s")
    lines.append(f"**Lead:** {caller_name} ({caller_phone})")
    lines.append(f"**Role:** {lead_role}")
    lines.append(f"**Stage before call:** {deal_stage}")
    lines.append(f"**Campaign:** {campaign_str}")
    lines.append(f"**Listings discussed:** {listings_str}")
    lines.append(f"**Viewing requests:** {viewing_str}")
    lines.append(f"**Offer intent:** {offer_str}")
    lines.append(f"**Intent score before:** {score_str}")
    lines.append(f"**Outcome:** {booking_status}")
    lines.append(f"**Sentiment:** {sentiment}")
    lines.append(f"**Turns:** {turn_count}")
    lines.append(f"**Interrupts:** {interrupt_count}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## System Context")
    lines.append("")
    lines.append("```")
    lines.append(system_prompt)
    lines.append("```")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Conversation")
    lines.append("")

    for i, turn in enumerate(transcript_lines):
        role = turn.get("role", "unknown")
        content = turn.get("content", "")
        ts = turn.get("timestamp", "")
        tool_calls = turn.get("tool_calls")

        lines.append(f"**Turn {i + 1}** — {ts} | **{role}**")
        if content:
            lines.append(content)

        if tool_calls:
            for tc in tool_calls:
                name = tc.get("name", "tool")
                result = tc.get("result", "")
                lines.append(f"TOOL {name}: {result}")

        lines.append("")

    atomic_write_text(out_path, "\n".join(lines))
    return out_path
