"""
Multi-turn context window with sliding summarisation.

After turn 12, summarise turns 1–10 into ~150 tokens. Repeats every 10 turns.
Uses DeepSeek (secondary LLM), not the live conversation model.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("inbound-voice-agent")

SUMMARISATION_TRIGGER_TURN = 12
TURNS_TO_SUMMARISE = 10
MAX_SUMMARY_TOKENS = 150


async def call_llm_text(prompt: str, provider: str = "deepseek", max_tokens: int = 200) -> str:
    if provider == "deepseek":
        from secondary_llm import deepseek_chat, get_deepseek_api_key

        if not get_deepseek_api_key():
            raise RuntimeError("DEEPSEEK_API_KEY not set for context summarisation")
        return await deepseek_chat(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )

    raise RuntimeError(f"Unsupported secondary LLM provider: {provider}")


def _get_messages(chat_ctx) -> list:
    messages = getattr(chat_ctx, "messages", None)
    if callable(messages):
        return list(messages())
    return list(messages or [])


def _message_role(msg) -> str | None:
    return getattr(msg, "role", None)


def _message_content(msg) -> str:
    content = getattr(msg, "content", None) or getattr(msg, "text", "")
    if isinstance(content, list):
        return " ".join(str(c) for c in content if isinstance(c, str))
    return str(content or "")


async def maybe_summarise_context(chat_ctx, turn_count: int, llm_provider: str = "deepseek") -> None:
    """
    Called after every turn.
    At turn 12, summarise turns 1–10. At turn 22, summarise the next 10, etc.
    """
    if turn_count < SUMMARISATION_TRIGGER_TURN:
        return
    if (turn_count - SUMMARISATION_TRIGGER_TURN) % 10 != 0:
        return

    messages = _get_messages(chat_ctx)
    convo_msgs = [m for m in messages if _message_role(m) in ("user", "assistant")]
    if len(convo_msgs) < TURNS_TO_SUMMARISE:
        return

    to_summarise = convo_msgs[:TURNS_TO_SUMMARISE]
    transcript_snippet = "\n".join(
        f"{_message_role(m).upper()}: {_message_content(m)}" for m in to_summarise
    )

    summary_prompt = f"""
Summarise this real estate call conversation in under {MAX_SUMMARY_TOKENS} tokens.
IMPORTANT: This call is already in progress. The agent has already greeted the caller
and introduced themselves. Do NOT include greeting or self-introduction in your summary.
Preserve ALL factual information: budget, areas mentioned, properties discussed,
objections raised, decisions made, appointments scheduled.
Discard greetings, pleasantries, and filler.
Format: "[SUMMARY: budget=X, areas=Y, properties discussed=Z, objections=W, decisions=V]"

Conversation:
{transcript_snippet}
"""
    try:
        summary = await call_llm_text(summary_prompt, provider=llm_provider, max_tokens=200)
        from token_counter import count_tokens, truncate_at_boundary

        summary_count = count_tokens(summary, "hme")
        if summary_count > MAX_SUMMARY_TOKENS:
            summary = truncate_at_boundary(summary, MAX_SUMMARY_TOKENS, "hme")
        for msg in to_summarise:
            try:
                chat_ctx.messages.remove(msg)
            except Exception:
                pass
        call_state_note = "[CALL STATE: In progress. Agent already greeted caller. Continue conversation naturally without re-introducing yourself.]"
        full_summary = call_state_note + "\n" + summary
        try:
            chat_ctx.messages.insert(1, type("ChatSummary", (), {"role": "system", "content": full_summary})())
        except Exception:
            chat_ctx.append(role="system", text=full_summary)
        logger.debug("[CONTEXT] Summarised %d turns at turn %d", TURNS_TO_SUMMARISE, turn_count)
    except Exception as e:
        logger.warning("[CONTEXT] Summarisation failed: %s", e)