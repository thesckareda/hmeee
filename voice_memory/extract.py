"""Post-call memory extraction — durable fact extraction from transcripts.

Rule-based extraction (fast, no API cost) as primary mode.
Optional LLM-powered extraction for richer summaries when an LLM function is provided.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from voice_memory.manager import add_memory_entry
from voice_memory.paths import get_caller_memory_dir

logger = logging.getLogger(__name__)

# LLM summarizer type: takes (transcript_text, caller_name) -> returns summary string
LlmSummarizer = Callable[[str, str], str]


def extract_call_memories(
    caller_phone: str,
    caller_name: str,
    booking_status: str,
    transcript_text: str,
    buffered_context: list[dict],
    *,
    memory_dir: str | Path | None = None,
) -> list[Path]:
    """Extract durable memories from a completed call transcript.

    Creates/updates:
      - {phone}/caller_profile.md — caller info, preferences, call count
      - {phone}/call_{date}.md — call summary
      - {phone}/appointment_{date}.md — if booking detected

    Returns list of created/updated file paths.
    """
    if caller_phone == "unknown":
        return []

    caller_dir = get_caller_memory_dir(caller_phone, memory_dir=memory_dir)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    created: list[Path] = []

    # ── 1. Update caller profile ──
    profile_content = _build_profile_content(caller_name, caller_phone, booking_status, transcript_text, buffered_context)
    profile_path = add_memory_entry(
        title=f"Caller Profile — {caller_name or caller_phone}",
        content=profile_content,
        memory_type="user",
        description=f"Profile for caller {caller_phone}",
        memory_dir=caller_dir,
    )
    created.append(profile_path)

    # ── 2. Save call summary ──
    topics = _extract_topics(transcript_text)
    call_summary_content = _build_call_summary(
        caller_name, caller_phone, today, booking_status, topics, buffered_context
    )
    call_path = add_memory_entry(
        title=f"Call {today} — {caller_name or caller_phone}",
        content=call_summary_content,
        memory_type="project",
        description=f"Call summary for {caller_phone} on {today}",
        memory_dir=caller_dir,
    )
    created.append(call_path)

    # ── 3. Save appointment if booking confirmed ──
    booking_info = _extract_booking_info(buffered_context)
    if booking_info:
        appointment_path = add_memory_entry(
            title=f"Appointment {today} — {caller_name or caller_phone}",
            content=_build_appointment_summary(caller_name, caller_phone, booking_info),
            memory_type="project",
            description=f"Appointment for {caller_phone}",
            memory_dir=caller_dir,
        )
        created.append(appointment_path)

    return created


def extract_call_memories_llm(
    caller_phone: str,
    caller_name: str,
    booking_status: str,
    transcript_text: str,
    buffered_context: list[dict],
    *,
    llm_summarize: LlmSummarizer | None = None,
    memory_dir: str | Path | None = None,
) -> list[Path]:
    """Extract memories using LLM-powered summarization for richer summaries.

    Falls back to rule-based extraction if no llm_summarize function provided.

    Args:
        llm_summarize: Function that takes (transcript_text, caller_name) and returns
                       a concise 2-3 sentence summary of the call. If None, uses
                       rule-based summary generation.
    """
    if caller_phone == "unknown":
        return []

    caller_dir = get_caller_memory_dir(caller_phone, memory_dir=memory_dir)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    created: list[Path] = []

    # ── 1. Update caller profile (same as rule-based) ──
    profile_content = _build_profile_content(caller_name, caller_phone, booking_status, transcript_text, buffered_context)
    created.append(
        add_memory_entry(
            title=f"Caller Profile — {caller_name or caller_phone}",
            content=profile_content,
            memory_type="user",
            description=f"Profile for caller {caller_phone}",
            memory_dir=caller_dir,
        )
    )

    # ── 2. LLM-generated call summary ──
    if llm_summarize and transcript_text and transcript_text != "unavailable":
        try:
            llm_summary = llm_summarize(transcript_text, caller_name)
            summary = f"# Call Summary — {today}\n\n**LLM-generated summary:**\n\n{llm_summary}\n\n"
        except Exception as e:
            logger.warning("LLM summarization failed: %s", e, exc_info=True)
            summary = f"# Call Summary — {today}\n\n(LLM summarization failed — using rule-based fallback)\n\n"
    else:
        summary = f"# Call Summary — {today}\n\n"

    # Add metadata
    topics = _extract_topics(transcript_text)
    summary += f"- **Caller:** {caller_name or caller_phone}\n"
    summary += f"- **Phone:** {caller_phone}\n"
    summary += f"- **Date:** {today}\n"
    summary += f"- **Booking Status:** {booking_status}\n"
    summary += "\n## Topics\n\n"
    for t in topics or ["General inquiry"]:
        summary += f"- {t}\n"
    summary += f"\n*Recorded at: {datetime.now(timezone.utc).isoformat()}*\n"

    created.append(
        add_memory_entry(
            title=f"Call {today} — {caller_name or caller_phone}",
            content=summary,
            memory_type="project",
            description=f"Call summary for {caller_phone} on {today}",
            memory_dir=caller_dir,
        )
    )

    # ── 3. Save appointment if booking confirmed ──
    booking_info = _extract_booking_info(buffered_context)
    if booking_info:
        created.append(
            add_memory_entry(
                title=f"Appointment {today} — {caller_name or caller_phone}",
                content=_build_appointment_summary(caller_name, caller_phone, booking_info),
                memory_type="project",
                description=f"Appointment for {caller_phone}",
                memory_dir=caller_dir,
            )
        )

    return created


def _build_profile_content(
    caller_name: str,
    caller_phone: str,
    booking_status: str,
    transcript_text: str,
    buffered_context: list[dict],
) -> str:
    lang = _detect_language(transcript_text)
    booking_count = sum(1 for ctx in buffered_context if ctx.get("key", "").startswith("booking:"))
    lines = [
        f"# Caller Profile: {caller_name or caller_phone}",
        "",
        f"- **Phone:** {caller_phone}",
        f"- **Name:** {caller_name or 'Unknown'}",
        f"- **Language Preference:** {lang}",
        f"- **Total Bookings:** {booking_count}",
        f"- **Last Booking Status:** {booking_status}",
        f"- **Last Updated:** {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Preferences",
        "",
        _extract_preferences(transcript_text, buffered_context),
    ]
    return "\n".join(lines)


def _build_call_summary(
    caller_name: str,
    caller_phone: str,
    date: str,
    booking_status: str,
    topics: list[str],
    buffered_context: list[dict],
) -> str:
    lines = [
        f"# Call Summary — {date}",
        "",
        f"- **Caller:** {caller_name or caller_phone}",
        f"- **Phone:** {caller_phone}",
        f"- **Date:** {date}",
        f"- **Booking Status:** {booking_status}",
        "",
        "## Topics Discussed",
        "",
    ]
    if topics:
        for t in topics:
            lines.append(f"- {t}")
    else:
        lines.append("- General inquiry")

    if buffered_context:
        lines.extend(["", "## Context Updates", ""])
        for ctx in buffered_context:
            lines.append(f"- **{ctx.get('key', '')}:** {ctx.get('value', '')}")

    lines.extend(["", f"*Recorded at: {datetime.now(timezone.utc).isoformat()}*"])
    return "\n".join(lines)


def _build_appointment_summary(
    caller_name: str,
    caller_phone: str,
    booking_info: dict,
) -> str:
    lines = [
        f"# Appointment — {booking_info.get('time', 'Unknown')}",
        "",
        f"- **Caller:** {caller_name or caller_phone}",
        f"- **Phone:** {caller_phone}",
        f"- **Scheduled Time:** {booking_info.get('time', 'Unknown')}",
        f"- **Notes:** {booking_info.get('notes', 'None')}",
        f"- **Created:** {datetime.now(timezone.utc).isoformat()}",
    ]
    return "\n".join(lines)


def _extract_topics(transcript_text: str) -> list[str]:
    """Simple keyword-based topic extraction."""
    if not transcript_text or transcript_text == "unavailable":
        return []
    lower = transcript_text.lower()
    topics = []
    keyword_map = {
        "appointment": "Appointment booking",
        "booking": "Appointment booking",
        "hours": "Business hours inquiry",
        "pricing": "Pricing inquiry",
        "price": "Pricing inquiry",
        "cost": "Pricing inquiry",
        "service": "Service inquiry",
        "dental": "Dental services",
        "x-ray": "Dental X-ray",
        "aligner": "Clear aligners",
        "implant": "Dental implants",
        "cleaning": "Teeth cleaning",
        "transfer": "Transfer requested",
        "complaint": "Complaint/Issue",
        "emergency": "Emergency",
    }
    seen = set()
    for keyword, topic in keyword_map.items():
        if keyword in lower and topic not in seen:
            topics.append(topic)
            seen.add(topic)
    return topics[:5]


def _extract_booking_info(buffered_context: list[dict]) -> dict | None:
    for ctx in buffered_context:
        key = ctx.get("key", "")
        if key.startswith("booking:"):
            time_val = key[len("booking:"):]
            return {
                "time": time_val,
                "notes": ctx.get("value", ""),
            }
    return None


def _detect_language(transcript_text: str) -> str:
    if not transcript_text or transcript_text == "unavailable":
        return "unknown"
    lower = transcript_text.lower()
    hindi_markers = ["namaste", "ji", "haan", "nahi", "theek", "acha", "kya", "mein", "aap"]
    hinglish_markers = ["namaste", "ji"]
    hindi_count = sum(1 for m in hindi_markers if m in lower)
    hinglish_count = sum(1 for m in hinglish_markers if m in lower)
    if hindi_count >= 3:
        return "Hindi"
    if hinglish_count >= 1:
        return "Hinglish"
    return "English"


def _extract_preferences(transcript_text: str, buffered_context: list[dict]) -> str:
    lower = (transcript_text or "").lower()
    prefs = []
    if "morning" in lower:
        prefs.append("- Prefers morning appointments")
    if "evening" in lower:
        prefs.append("- Prefers evening appointments")
    if "weekend" in lower:
        prefs.append("- Prefers weekend appointments")
    for ctx in buffered_context:
        key = ctx.get("key", "")
        if key == "preferred_time":
            prefs.append(f"- Preferred time: {ctx.get('value', '')}")
    lang = _detect_language(transcript_text)
    if lang != "English":
        prefs.append(f"- Language: {lang}")
    return "\n".join(prefs) if prefs else "- No specific preferences recorded"
