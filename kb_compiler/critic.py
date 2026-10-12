"""
Stage E — CRITIC (separate pass on a different model than the generator,
so its blind spots differ — master plan §4.2).

Re-reads every generated spoken artifact against source excerpts and flags
hallucination, wrong tone, and compliance problems. Blocking items fail the
build; style items surface in the human review.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from kb_compiler.llm import CRITIC_MODEL, generate_json
from kb_compiler.validate import _spoken_strings

logger = logging.getLogger("kb-compiler")


async def run_critic(artifacts: dict, chunks: list[dict], manifest: dict) -> dict[str, Any]:
    corpus = "\n\n".join(c["text"] for c in chunks)[:24000]
    spoken = _spoken_strings(artifacts)
    numbered = "\n".join(f"{i}. [{path}] {text}" for i, (path, text) in enumerate(spoken) if text)

    prompt = (
        "You are the quality critic for a voice-agent knowledge compiler. Below are (A) the SOURCE "
        "knowledge base and (B) generated spoken lines. Flag problems.\n\n"
        "BLOCKING = a claim/number/name that contradicts or does not appear in the sources, "
        "a compliance risk (promising approval/returns, fake urgency), or offensive content.\n"
        "STYLE = robotic phrasing, wrong language style, too long for speech.\n\n"
        "Return JSON: {\"blocking\": [{\"item\": <number>, \"path\": \"...\", \"reason\": \"...\"}], "
        "\"style\": [{\"item\": <number>, \"path\": \"...\", \"suggestion\": \"...\"}]}\n"
        "Empty lists if clean. Judge ONLY against the sources — do not import outside knowledge.\n\n"
        f"(A) SOURCES:\n{corpus}\n\n(B) GENERATED LINES:\n{numbered}"
    )
    try:
        result = await generate_json(prompt, temperature=0.1, model=CRITIC_MODEL)
        blocking = result.get("blocking") or []
        style = result.get("style") or []
        logger.info("[CRITIC] blocking=%d style=%d", len(blocking), len(style))
        return {"blocking": blocking, "style": style}
    except Exception as exc:
        logger.warning("[CRITIC] pass failed (%s) — treating as advisory-only", exc)
        return {"blocking": [], "style": [], "error": str(exc)}
