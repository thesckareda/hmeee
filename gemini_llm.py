"""Gemini conversation LLM — patches LiveKit OpenAI stream for null chunk IDs."""
from __future__ import annotations

import asyncio
import logging
import re
import uuid
from typing import Any, cast

import httpx
import openai
from livekit.agents import llm
from livekit.agents._exceptions import APIConnectionError, APIStatusError, APITimeoutError
from livekit.agents.inference import llm as inference_llm
from livekit.agents.types import NOT_GIVEN
from livekit.plugins import openai as openai_plugin
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

logger = logging.getLogger("inbound-voice-agent")

DEFAULT_GEMINI_CONVERSATION_MODEL = "gemini-2.5-flash-lite"
_PATCHED = False

TEMP_RAPPORT = 0.65
TEMP_FACTS = 0.45
TEMP_DEFAULT = 0.55

_RAPPORT_RE = re.compile(
    r"\b(hello|hi|how\s*are\s*you|good\s*morning|good\s*evening|thanks|thank\s*you|"
    r"weekend|weather|traffic|festival|nice\s*to\s*meet|haan|ji|andi|garu|"
    r"small\s*talk|chat)\b",
    re.I,
)
_FACT_RE = re.compile(
    r"\b(price|pricing|kitna|cost|rera|policy|refund|cancel|brokerage|commission|"
    r"token|documentation|stamp\s*duty|emi|loan\s*rate|interest\s*rate|"
    r"possession|all\s*inclusive|total\s*cost|payment\s*milestone)\b",
    re.I,
)
_FACT_DEAL_STAGES = frozenset({"consideration", "purchase", "booked"})


def resolve_gemini_conversation_model(live_config: dict | None = None) -> str:
    """Conversation LLM — gemini_model / llm_model from config, else default."""
    cfg = live_config or {}
    for key in ("gemini_model", "llm_model"):
        raw = str(cfg.get(key) or "").strip()
        if raw and raw.lower().startswith("gemini"):
            return raw
    return DEFAULT_GEMINI_CONVERSATION_MODEL


def resolve_gemini_stream_chunk_id(chunk_id: str | None, last_id: str | None) -> tuple[str, str]:
    if chunk_id:
        return chunk_id, chunk_id
    if last_id:
        return last_id, last_id
    generated = f"gemini-{uuid.uuid4().hex[:12]}"
    return generated, generated


def patch_livekit_inference_llm_for_gemini() -> None:
    """Gemini's OpenAI-compat API omits chunk.id on usage-only stream events."""
    global _PATCHED
    if _PATCHED:
        return

    original_run = inference_llm.LLMStream._run
    lk_oai_debug = inference_llm.lk_oai_debug

    async def _run_with_gemini_chunk_ids(self) -> None:
        self._oai_stream = None
        self._tool_call_id = None
        self._fnc_name = None
        self._fnc_raw_arguments = None
        self._tool_extra = None
        self._tool_index = None
        retryable = True
        last_chunk_id: str | None = None

        try:
            chat_ctx, _ = self._chat_ctx.to_provider_format(format=self._provider_fmt)
            tool_schemas = cast(
                list[ChatCompletionToolParam],
                self._tool_ctx.parse_function_tools("openai", strict=self._strict_tool_schema),
            )
            if lk_oai_debug:
                tool_choice = self._extra_kwargs.get("tool_choice", NOT_GIVEN)
                inference_llm.logger.debug(
                    "chat.completions.create",
                    extra={
                        "fnc_ctx": tool_schemas,
                        "tool_choice": tool_choice,
                        "chat_ctx": chat_ctx,
                    },
                )
            if not self._tools:
                self._extra_kwargs.pop("tool_choice", None)

            if self._provider:
                headers = self._extra_kwargs.setdefault("extra_headers", {})
                headers["X-LiveKit-Inference-Provider"] = self._provider

            self._oai_stream = stream = await self._client.chat.completions.create(
                messages=cast(list[ChatCompletionMessageParam], chat_ctx),
                tools=tool_schemas or openai.omit,
                model=self._model,
                stream_options={"include_usage": True},
                stream=True,
                timeout=httpx.Timeout(self._conn_options.timeout),
                **self._extra_kwargs,
            )

            thinking = asyncio.Event()
            async with stream:
                async for chunk in stream:
                    chunk_id, last_chunk_id = resolve_gemini_stream_chunk_id(
                        chunk.id, last_chunk_id
                    )
                    for choice in chunk.choices:
                        chat_chunk = self._parse_choice(chunk_id, choice, thinking)
                        if chat_chunk is not None:
                            retryable = False
                            self._event_ch.send_nowait(chat_chunk)

                    if chunk.usage is not None:
                        retryable = False
                        tokens_details = chunk.usage.prompt_tokens_details
                        cached_tokens = tokens_details.cached_tokens if tokens_details else 0
                        usage_chunk = llm.ChatChunk(
                            id=chunk_id,
                            usage=llm.CompletionUsage(
                                completion_tokens=chunk.usage.completion_tokens,
                                prompt_tokens=chunk.usage.prompt_tokens,
                                prompt_cached_tokens=cached_tokens or 0,
                                total_tokens=chunk.usage.total_tokens,
                            ),
                        )
                        self._event_ch.send_nowait(usage_chunk)

        except openai.APITimeoutError:
            raise APITimeoutError(retryable=retryable) from None
        except openai.APIStatusError as e:
            raise APIStatusError(
                e.message,
                status_code=e.status_code,
                request_id=e.request_id,
                body=e.body,
                retryable=retryable,
            ) from None
        except Exception as e:
            raise APIConnectionError(retryable=retryable) from e

    _run_with_gemini_chunk_ids.__gemini_null_id_patch__ = True  # type: ignore[attr-defined]
    inference_llm.LLMStream._run = _run_with_gemini_chunk_ids
    _PATCHED = True
    logger.debug("[LLM] Patched LiveKit inference stream for Gemini null chunk IDs")


def resolve_dynamic_temperature(
    transcript: str,
    *,
    deal_stage: str | None = None,
    base_temperature: float = TEMP_DEFAULT,
) -> float:
    """Higher temp for rapport/small talk; lower for pricing/policy facts."""
    text = (transcript or "").strip()
    if not text:
        return max(0.0, min(float(base_temperature), 2.0))

    stage = (deal_stage or "").strip().lower()
    if _FACT_RE.search(text) or stage in _FACT_DEAL_STAGES:
        return TEMP_FACTS
    if _RAPPORT_RE.search(text) or stage in ("awareness", "interest"):
        return TEMP_RAPPORT
    return max(0.0, min(float(base_temperature), 2.0))


def apply_dynamic_temperature(
    llm_instance: Any,
    transcript: str,
    *,
    deal_stage: str | None = None,
    base_temperature: float = TEMP_DEFAULT,
) -> float:
    """Patch per-turn temperature on the LiveKit OpenAI-compat LLM instance."""
    temp = resolve_dynamic_temperature(
        transcript,
        deal_stage=deal_stage,
        base_temperature=base_temperature,
    )
    opts = getattr(llm_instance, "_opts", None)
    if opts is not None and hasattr(opts, "temperature"):
        opts.temperature = temp
    return temp


def build_gemini_conversation_llm(**kwargs: Any) -> openai_plugin.LLM:
    patch_livekit_inference_llm_for_gemini()
    return openai_plugin.LLM(**kwargs)