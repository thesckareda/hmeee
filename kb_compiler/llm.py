"""
Compiler LLM client — Gemini 3.1, per-stage models (master plan §4.2).

Synthesis is correctness-critical (a bad pack talks garbage to real callers
for weeks), so it runs on Gemini 3.1 Pro. Extraction is high-volume chunk
work validated downstream, so it runs on Flash. The critic is a separate
pass on a different model than the generator, so its blind spots differ.

Every generation stage goes through generate_json(). JSON mode is enforced
server-side; we still parse defensively. Build-time only — latency is
irrelevant, correctness is not.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger("kb-compiler")

# Stage-model contract. Env overrides exist for emergencies / price shifts;
# the validators (validate.py) are what actually guarantee quality, so the
# pipeline stays model-agnostic by design.
SYNTH_MODEL = os.environ.get("SPEAKSY_COMPILER_MODEL", "gemini-3.1-pro")
EXTRACT_MODEL = os.environ.get("SPEAKSY_COMPILER_EXTRACT_MODEL", "gemini-3.1-flash")
CRITIC_MODEL = os.environ.get("SPEAKSY_COMPILER_CRITIC_MODEL", "gemini-3.1-flash")

# Back-compat alias — lockfiles and the dashboard treat the synthesis model
# as "the" compiler model.
GEMINI_MODEL = SYNTH_MODEL

STAGE_MODELS = {"extract": EXTRACT_MODEL, "synthesize": SYNTH_MODEL, "critic": CRITIC_MODEL}

_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
_MAX_RETRIES = 3


class CompilerLLMError(RuntimeError):
    pass


def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        try:
            from config_store import read_raw_config

            key = str(read_raw_config().get("gemini_api_key") or "").strip()
        except Exception:
            key = ""
    if not key:
        raise CompilerLLMError(
            "GEMINI_API_KEY not set (env or config.json gemini_api_key) — "
            "the KB compiler needs it for generation."
        )
    return key


def _strip_json_fences(text: str) -> str:
    text = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    return m.group(1) if m else text


def parse_json_response(text: str) -> Any:
    """Parse model output into JSON, salvaging fenced or prose-wrapped
    payloads: after a direct parse fails, scan for the first decodable
    JSON object/array in the text."""
    stripped = _strip_json_fences(text)
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for i, ch in enumerate(stripped):
        if ch in "{[":
            try:
                obj, _ = decoder.raw_decode(stripped[i:])
                return obj
            except json.JSONDecodeError:
                continue
    raise CompilerLLMError(f"Unparseable JSON response: {stripped[:200]!r}")


class _TruncatedResponse(CompilerLLMError):
    pass


async def generate_json(
    prompt: str,
    *,
    system: str = "",
    temperature: float = 0.2,
    max_output_tokens: int = 8192,
    model: str = "",
) -> Any:
    """One Gemini call → parsed JSON. Defaults to the synthesis model
    (Gemini 3.1 Pro); stages pass their own via `model=`. Retries transient
    failures with jittered backoff; a MAX_TOKENS truncation retries with a
    doubled output budget."""
    import random

    import httpx

    key = _api_key()
    use_model = model or SYNTH_MODEL
    url = f"{_API_BASE}/{use_model}:generateContent?key={key}"
    tokens = max_output_tokens

    last_err: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": tokens,
                "responseMimeType": "application/json",
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, json=body)
            if resp.status_code in (429, 500, 502, 503):
                raise CompilerLLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            resp.raise_for_status()
            data = resp.json()
            text = ""
            finish = ""
            for cand in data.get("candidates") or []:
                finish = str(cand.get("finishReason") or finish)
                for part in (cand.get("content") or {}).get("parts") or []:
                    text += str(part.get("text") or "")
            if not text:
                raise CompilerLLMError(f"Empty response: {json.dumps(data)[:300]}")
            if finish == "MAX_TOKENS":
                raise _TruncatedResponse(f"Output truncated at {tokens} tokens")
            return parse_json_response(text)
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if isinstance(exc, _TruncatedResponse):
                tokens = min(tokens * 2, 32768)
            if attempt < _MAX_RETRIES:
                wait = 2.0 * attempt + random.uniform(0, 0.75)
                logger.warning(
                    "[COMPILER-LLM] attempt %d/%d failed (%s) — retrying in %.1fs",
                    attempt, _MAX_RETRIES, exc, wait,
                )
                await asyncio.sleep(wait)
    raise CompilerLLMError(f"{use_model} call failed after {_MAX_RETRIES} attempts: {last_err}")
