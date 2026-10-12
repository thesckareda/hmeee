"""Warm the conversation LLM with the live system prompt, tools, and greeting context."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from livekit.agents import llm
from livekit.agents.types import APIConnectOptions

logger = logging.getLogger("inbound-voice-agent")

_WARMUP_USER = "."
_PREWARM_TIMEOUT_SEC = 12.0


async def prewarm_llm_context(
    llm_instance: Any,
    agent: Any,
    tools: list[llm.Tool] | None,
    *,
    greeting: str = "",
    live_config: dict | None = None,
) -> float | None:
    """
    Stream a minimal completion through the same LLM + system prompt + tool schemas
    the first real user turn will use. Returns prewarm TTFT ms, or None on skip/fail.
    """
    if llm_instance is None:
        return None

    cfg = live_config or {}
    if not bool(cfg.get("llm_context_prewarm", True)):
        logger.debug("[LLM-PREWARM] Disabled by config")
        return None

    prewarm_fn = getattr(llm_instance, "prewarm", None)
    if callable(prewarm_fn):
        try:
            prewarm_fn()
        except Exception as exc:
            logger.debug("[LLM-PREWARM] Connection prewarm skipped: %s", exc)

    try:
        warm_ctx = agent.chat_ctx.copy()
    except Exception as exc:
        logger.warning("[LLM-PREWARM] chat_ctx copy failed: %s", exc)
        return None

    greeting_text = (greeting or "").strip()
    if greeting_text:
        try:
            has_greeting = any(
                getattr(item, "role", None) == "assistant"
                and greeting_text in (getattr(item, "text_content", "") or "")
                for item in warm_ctx.items
            )
        except Exception:
            has_greeting = False
        if not has_greeting:
            warm_ctx.add_message(role="assistant", content=greeting_text)

    warm_ctx.add_message(role="user", content=_WARMUP_USER)

    tool_list = tools or []
    conn_options = APIConnectOptions(max_retry=1, retry_interval=1.0, timeout=_PREWARM_TIMEOUT_SEC)
    extra_kwargs: dict[str, Any] = {"max_completion_tokens": 16}

    start = time.perf_counter()
    first_token_ms: float | None = None

    try:
        stream = llm_instance.chat(
            chat_ctx=warm_ctx,
            tools=tool_list or None,
            conn_options=conn_options,
            extra_kwargs=extra_kwargs,
        )
        async with stream:
            async for chunk in stream:
                delta = getattr(chunk, "delta", None)
                content = getattr(delta, "content", None) if delta else None
                if content:
                    first_token_ms = (time.perf_counter() - start) * 1000
                    break
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "[LLM-PREWARM] Context ready — ttft=%.0fms total=%.0fms tools=%d prompt_msgs=%d",
            first_token_ms or elapsed_ms,
            elapsed_ms,
            len(tool_list),
            len(list(warm_ctx.items)),
        )
        return first_token_ms or elapsed_ms
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("[LLM-PREWARM] Failed (call continues): %s", exc)
        return None


def schedule_llm_context_prewarm(
    llm_instance: Any,
    agent: Any,
    tools: list[llm.Tool] | None,
    *,
    greeting: str = "",
    live_config: dict | None = None,
) -> asyncio.Task | None:
    """Fire-and-forget prewarm task; store on live_config for optional await."""
    cfg = live_config or {}
    if not bool(cfg.get("llm_context_prewarm", True)) or llm_instance is None:
        return None

    task = asyncio.create_task(
        prewarm_llm_context(
            llm_instance,
            agent,
            tools,
            greeting=greeting,
            live_config=cfg,
        ),
        name="llm_context_prewarm",
    )
    cfg["_llm_prewarm_task"] = task
    return task


async def await_llm_context_prewarm(
    live_config: dict | None,
    *,
    timeout_sec: float = _PREWARM_TIMEOUT_SEC,
) -> float | None:
    """Wait for an in-flight prewarm started during greeting (best-effort)."""
    cfg = live_config or {}
    task = cfg.get("_llm_prewarm_task")
    if task is None or task.done():
        return None
    try:
        return await asyncio.wait_for(task, timeout=timeout_sec)
    except asyncio.TimeoutError:
        logger.warning("[LLM-PREWARM] Still running after %.0fs — continuing call setup", timeout_sec)
        return None
    except Exception as exc:
        logger.debug("[LLM-PREWARM] await failed: %s", exc)
        return None