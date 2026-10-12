"""
DeepSeek for secondary AI tasks — everything except the live conversation LLM.

Used for: post-call sentiment, context summarisation, memory extraction,
brochure intent, etc.
"""
from __future__ import annotations

import logging
import os

from secrets_manager import get_secret

logger = logging.getLogger("inbound-voice-agent")

DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
SECONDARY_LLM_PROVIDER = "deepseek"


def get_deepseek_api_key() -> str:
    return (get_secret("DEEPSEEK_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "") or "").strip()


def get_deepseek_config() -> tuple[str, str, str]:
    """Return (api_key, base_url, model) for OpenAI-compatible DeepSeek clients."""
    api_key = get_deepseek_api_key()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY not set")
    base_url = DEEPSEEK_BASE_URL.rstrip("/")
    if not base_url.endswith("/v1"):
        base_url = f"{base_url}/v1"
    return api_key, base_url, DEEPSEEK_MODEL


async def deepseek_chat(
    messages: list[dict],
    *,
    max_tokens: int = 200,
    temperature: float = 0.0,
    response_format: dict | None = None,
) -> str:
    import openai as _oai

    api_key, base_url, model = get_deepseek_config()
    client = _oai.AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=1, timeout=15.0)
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if response_format:
        kwargs["response_format"] = response_format
    response = await client.chat.completions.create(**kwargs)
    return (response.choices[0].message.content or "").strip()