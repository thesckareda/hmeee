"""
Post-call summary — uses unified analyze_post_call() (single LLM call).

Called at:
  1. agent.py shutdown (via analyze_post_call directly)
  2. On-demand via POST /api/calls/{call_id}/summarise
"""

import logging

logger = logging.getLogger("inbound-voice-agent")


async def generate_call_summary(transcript_text: str) -> dict:
    """
    Return dashboard summary fields from the unified post-call analysis.

    Fields: short_summary, call_purpose, outcome, lead_score,
            next_action, key_points, objections, action_items.
    """
    from re_voice_memory.extract import analyze_post_call

    analysis = await analyze_post_call(transcript_text)
    return dict(analysis.summary)


def format_summary_for_db(summary: dict) -> tuple[str, str]:
    """Convert summary dict into (call_summary, call_purpose) for Supabase."""
    return (
        summary.get("short_summary", "Call completed."),
        summary.get("call_purpose", "general_inquiry"),
    )