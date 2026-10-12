"""
Trial: background next-turn prep while the agent is speaking (user listening).

Runs only when enabled, speech is long enough (~2s+), and uses a separate
low-token Gemini call so the live reply path is never blocked.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("inbound-voice-agent")

DEFAULT_MIN_SPEECH_MS = 2000
ESTIMATE_MS_PER_CHAR = 42
_START_DELAY_MS = 600
_MAX_HINT_CHARS = 480


def _enabled(live_config: dict | None) -> bool:
    return bool((live_config or {}).get("speculative_reply_enabled", False))


def _journal(live_config: dict | None, event: str, **fields: Any) -> None:
    cfg = live_config or {}
    journal = cfg.setdefault("_speculative_reply_journal", [])
    entry: dict[str, Any] = {
        "event": event,
        "at": datetime.now(timezone.utc).isoformat(),
    }
    for key, value in fields.items():
        if value is not None:
            entry[key] = value
    journal.append(entry)


def get_speculative_journal(live_config: dict | None) -> list[dict[str, Any]]:
    raw = (live_config or {}).get("_speculative_reply_journal") or []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _min_speech_ms(live_config: dict | None) -> int:
    try:
        return max(1500, min(int((live_config or {}).get("speculative_reply_min_speech_ms", DEFAULT_MIN_SPEECH_MS)), 8000))
    except (TypeError, ValueError):
        return DEFAULT_MIN_SPEECH_MS


def estimate_speech_duration_ms(text: str) -> int:
    chars = len((text or "").strip())
    if chars < 1:
        return 0
    return int(chars * ESTIMATE_MS_PER_CHAR)


def _last_assistant_text(agent: Any) -> str:
    try:
        for item in reversed(list(agent.chat_ctx.items)):
            if getattr(item, "role", None) != "assistant":
                continue
            content = getattr(item, "text_content", None) or ""
            if isinstance(content, list):
                content = " ".join(str(x) for x in content)
            text = str(content).strip()
            if text:
                return text
    except Exception:
        pass
    return ""


def cancel_speculative_prep(live_config: dict | None, *, reason: str = "speech_ended") -> None:
    cfg = live_config or {}
    task = cfg.get("_speculative_reply_task")
    if task and not task.done():
        task.cancel()
        _journal(cfg, "cancelled", reason=reason)
    cfg.pop("_speculative_reply_task", None)


def record_speculative_injected(live_config: dict | None, *, turn: int, hint: str) -> None:
    _journal(live_config, "injected", for_turn=turn, hint=hint)


def consume_speculative_hint(live_config: dict | None, *, turn: int) -> str | None:
    cfg = live_config or {}
    payload = cfg.pop("_speculative_reply_hint", None)
    if not isinstance(payload, dict):
        return None
    if int(payload.get("for_turn", -1)) != int(turn):
        return None
    hint = str(payload.get("hint") or "").strip()
    return hint[:_MAX_HINT_CHARS] if hint else None


def schedule_speculative_prep(agent: Any, agent_tools: Any, live_config: dict | None) -> None:
    cfg = live_config or {}
    if not _enabled(cfg):
        return

    spoken = _last_assistant_text(agent)
    est_ms = estimate_speech_duration_ms(spoken)
    min_ms = _min_speech_ms(cfg)
    if est_ms < min_ms:
        logger.debug(
            "[SPECULATIVE] Skipped — est speech %dms < min %dms (chars=%d)",
            est_ms,
            min_ms,
            len(spoken),
        )
        _journal(
            cfg,
            "skipped_short_speech",
            est_speech_ms=est_ms,
            min_speech_ms=min_ms,
            spoken_preview=spoken[:160],
        )
        return

    next_turn = int(getattr(agent_tools, "turn_count", 0) or 0) + 1

    cancel_speculative_prep(cfg)
    _journal(
        cfg,
        "scheduled",
        for_turn=next_turn,
        est_speech_ms=est_ms,
        min_speech_ms=min_ms,
        spoken_preview=spoken[:220],
    )

    async def _runner() -> None:
        await asyncio.sleep(_START_DELAY_MS / 1000.0)
        if getattr(agent_tools, "agent_is_speaking", False) is False:
            return
        hint = await _generate_hint(agent, spoken, next_turn)
        if hint:
            cfg["_speculative_reply_hint"] = {"for_turn": next_turn, "hint": hint, "ts": time.time()}
            _journal(cfg, "prep_ready", for_turn=next_turn, hint=hint, spoken_preview=spoken[:220])
            logger.info("[SPECULATIVE] Next-turn prep ready for turn %s (%d chars)", next_turn, len(hint))
        else:
            _journal(cfg, "prep_failed", for_turn=next_turn, spoken_preview=spoken[:160])

    task = asyncio.create_task(_runner(), name="speculative_reply_prep")
    cfg["_speculative_reply_task"] = task


async def _generate_hint(agent: Any, spoken: str, next_turn: int) -> str | None:
    key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if not key:
        return None

    try:
        from google import genai as google_genai
        from gemini_client_config import gemini_afc_for_tools
        from gemini_llm import resolve_gemini_conversation_model

        live_config = getattr(agent, "_live_config", {}) or {}
        model = resolve_gemini_conversation_model(live_config)

        recent_lines: list[str] = []
        for item in list(agent.chat_ctx.items)[-6:]:
            role = getattr(item, "role", None)
            if role not in ("user", "assistant"):
                continue
            content = getattr(item, "text_content", None) or ""
            if isinstance(content, list):
                content = " ".join(str(x) for x in content)
            text = str(content).strip()
            if text:
                recent_lines.append(f"{role}: {text[:220]}")

        context = "\n".join(recent_lines) or "(no prior turns)"
        prompt = (
            "While the caller listens to the agent's last line, prep the NEXT user turn only.\n"
            "Predict 2-3 short likely user follow-ups and one-line reply angles for each.\n"
            "Do NOT write the full spoken reply. Bullets only. Max 70 words.\n\n"
            f"Recent conversation:\n{context}\n\n"
            f"Agent is now saying:\n{spoken[:400]}\n"
        )

        client = google_genai.Client(api_key=key)

        def _call():
            return client.models.generate_content(
                model=model,
                contents=prompt,
                config={
                    "max_output_tokens": 120,
                    "temperature": 0.35,
                    "automatic_function_calling": gemini_afc_for_tools(),
                },
            )

        response = await asyncio.to_thread(_call)
        text = str(getattr(response, "text", "") or "").strip()
        if not text:
            return None
        return text[:_MAX_HINT_CHARS]
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.debug("[SPECULATIVE] Prep failed for turn %s: %s", next_turn, exc)
        return None