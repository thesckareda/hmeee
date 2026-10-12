"""Post-call raw transcript generation — creates raw.md artifact."""

from __future__ import annotations

from pathlib import Path

from voice_memory.paths import get_calls_dir


def generate_raw_md(
    room_name: str,
    call_date: str,
    start_time: str,
    end_time: str,
    duration: int,
    caller_phone: str,
    caller_name: str,
    booking_status: str,
    sentiment: str,
    turn_count: int,
    interrupt_count: int,
    system_prompt: str,
    transcript_lines: list[dict],
) -> Path:
    """Generate a raw.md transcript file for a completed call.

    Args:
        transcript_lines: list of dicts with keys role, content, timestamp, tool_calls (optional)
    """
    output_dir = get_calls_dir()
    output_path = output_dir / room_name
    output_path.mkdir(parents=True, exist_ok=True)

    lines = [
        f"# Call Transcript — {room_name}",
        "",
        f"**Date:** {call_date}",
        f"**Time:** {start_time} – {end_time} IST",
        f"**Duration:** {duration} seconds",
        f"**Caller Phone:** {caller_phone}",
        f"**Caller Name:** {caller_name}",
        f"**Booking:** {booking_status}",
        f"**Sentiment:** {sentiment}",
        f"**Turns:** {turn_count}",
        f"**Interruptions:** {interrupt_count}",
        "",
        "---",
        "",
        "## System Context",
        "",
        "```",
        system_prompt,
        "```",
        "",
        "---",
        "",
        "## Conversation",
        "",
    ]

    for i, entry in enumerate(transcript_lines, 1):
        ts = entry.get("timestamp", "")
        role = str(entry.get("role", "")).upper()
        content = str(entry.get("content", ""))
        lines.append(f"### [TURN {i}] {ts}")
        lines.append(f"**{role}:** {content}")
        tool_calls = entry.get("tool_calls")
        if tool_calls:
            for tc in tool_calls:
                tc_name = str(tc.get("name", "unknown"))
                tc_result = str(tc.get("result", ""))
                lines.append(f"**TOOL `{tc_name}`:** {tc_result}")
        lines.append("")

    raw_md = "\n".join(lines)
    file_path = output_path / "raw.md"
    file_path.write_text(raw_md, encoding="utf-8")
    return file_path
