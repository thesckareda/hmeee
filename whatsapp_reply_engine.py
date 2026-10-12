"""WhatsApp text-channel reply engine.

Treats an inbound WhatsApp message as another "turn" into the same
context-building pipeline the LiveKit voice agent uses — knowledge_pack,
sales_playbook, objection handling, etc. via turn_context_injection — but
generates the reply directly against Gemini's OpenAI-compatible endpoint
instead of going through LiveKit's AgentSession/llm_node, since llm_node is
tightly coupled to a live LiveKit ChatContext/session activity that doesn't
exist here.

Called by the internal FastAPI route (whatsapp_routes.py) that the Node
whatsapp-service posts inbound messages to.
"""
from __future__ import annotations

import logging
import os
from types import SimpleNamespace

import db
from re_voice_memory.kv_store import kv_get, kv_set
from turn_context_injection import compute_turn_injections

logger = logging.getLogger("whatsapp-reply-engine")

THREAD_TTL_SECONDS = 60 * 60 * 24 * 2  # 48h — matches WhatsApp session expiry
MAX_HISTORY_MESSAGES = 12
DEFAULT_MAX_TURNS = 60

_SYSTEM_PROMPT_HEADER = (
    "You are a real-estate sales assistant replying to a customer over WhatsApp. "
    "Keep replies short and natural, like real WhatsApp messages (no markdown "
    "headers, no long paragraphs). Use the context below when relevant."
)

# Deal-stage/topic-memory objects created by compute_turn_injections aren't
# JSON-serializable and are scoped to this process's lifetime — same tradeoff
# agent.py makes per-call (live_config lives only as long as the call). Only
# the plain conversation history/turn_count is persisted to Redis so a
# ui_server restart loses deal-stage tracking (like a dropped call would) but
# not the conversation itself.
_LIVE_CONFIG_CACHE: dict[str, dict] = {}


def _base_live_config() -> dict:
    try:
        from config_store import read_raw_config

        raw = read_raw_config()
        if isinstance(raw, dict) and raw:
            return dict(raw)
    except Exception:
        logger.exception("Failed to load config.json for WhatsApp reply engine")
    return {}


def _thread_key(org_id: str, wa_number: str) -> str:
    return f"whatsapp:thread:{org_id}:{wa_number}"


async def _load_thread_history(thread_key: str) -> dict:
    state = await kv_get(thread_key)
    if isinstance(state, dict) and "history" in state:
        return state
    return {"history": [], "turn_count": 0}


async def _save_thread_history(thread_key: str, history: list[dict], turn_count: int) -> None:
    await kv_set(
        thread_key,
        {"history": history, "turn_count": turn_count},
        ttl_seconds=THREAD_TTL_SECONDS,
    )


def _gemini_client():
    from openai import AsyncOpenAI

    key = os.environ.get("GEMINI_API_KEY", "") or os.environ.get("GOOGLE_API_KEY", "")
    if not key:
        raise RuntimeError("GEMINI_API_KEY/GOOGLE_API_KEY not set — cannot generate WhatsApp replies")
    return AsyncOpenAI(
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        api_key=key,
    )


async def generate_reply(
    *,
    org_id: str,
    wa_number: str,
    text: str,
    agent_id: str | None = None,
    to_number: str | None = None,
) -> str:
    """Build a reply to one inbound WhatsApp message and log the exchange.

    Returns the reply text (caller is responsible for actually sending it
    back out over WhatsApp).
    """
    from gemini_llm import resolve_dynamic_temperature, resolve_gemini_conversation_model

    thread_key = _thread_key(org_id, wa_number)
    live_config = _LIVE_CONFIG_CACHE.setdefault(thread_key, _base_live_config())
    if not live_config:
        live_config.update(_base_live_config())

    stored = await _load_thread_history(thread_key)
    history: list[dict] = stored["history"]
    turn_count = stored["turn_count"] + 1

    agent_tools = SimpleNamespace(_re_lead_phone=wa_number)

    injection_result = await compute_turn_injections(
        transcript=text,
        tc=turn_count,
        max_t=live_config.get("max_turns", DEFAULT_MAX_TURNS),
        live_config=live_config,
        agent_tools=agent_tools,
    )

    system_parts = [_SYSTEM_PROMPT_HEADER, *injection_result.messages]
    try:
        from whatsapp_db import load_whatsapp_context_for_contact

        wa_history = await load_whatsapp_context_for_contact(wa_number)
        if wa_history:
            system_parts.append(wa_history)
    except Exception:
        logger.debug("WhatsApp history context unavailable for %s", wa_number, exc_info=True)

    system_prompt = "\n\n".join(system_parts)

    history.append({"role": "user", "content": text})
    del history[:-MAX_HISTORY_MESSAGES]

    deal_stage = getattr(live_config.get("_deal_tracker"), "stage", None)
    model = resolve_gemini_conversation_model(live_config)
    temperature = resolve_dynamic_temperature(text, deal_stage=deal_stage)

    client = _gemini_client()
    completion = await client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[{"role": "system", "content": system_prompt}, *history],
    )
    reply_text = (completion.choices[0].message.content or "").strip()

    history.append({"role": "assistant", "content": reply_text})
    del history[:-MAX_HISTORY_MESSAGES]

    await _save_thread_history(thread_key, history, turn_count)

    try:
        await db.save_call_log(
            phone=wa_number,
            duration=0,
            transcript=f"[USER] {text}\n[ASSISTANT] {reply_text}",
            call_direction="inbound",
            channel_type="whatsapp",
            from_number=wa_number,
            to_number=to_number,
            agent_id=agent_id,
        )
    except Exception:
        logger.exception("Failed to log WhatsApp call_logs row for org=%s wa=%s", org_id, wa_number)

    return reply_text
