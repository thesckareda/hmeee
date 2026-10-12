"""Intent scoring engine — LLM-driven post-call scoring."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

_SCORING_RUBRIC = """
Scoring rubrics (integer 1-10):

timeline_urgency:
  10 = explicit deadline named (date, month, or event anchoring the move)
  8-9 = need to move in 2-3 months
  5-7 = within 6 months
  3-4 = sometime next year
  1-2 = just exploring, no rush mentioned

budget_seriousness:
  10 = pre-approved + exact amount stated + ready to make an offer
  8-9 = specific number given + pre-approval mentioned
  6-7 = budget range given, seems firm
  4-5 = range given but flexible / "depends"
  2-3 = depends on what we find, no number
  1 = just checking prices / asking what's available

area_commitment:
  10 = named 2+ specific micro-markets + firm exclusions + reason given
  7-9 = named specific localities + one firm constraint (e.g. "only Koramangala")
  5-6 = named area with some flexibility
  3-4 = named city zone only (e.g. "somewhere in South Bangalore")
  1-2 = fully open, no area preference expressed
"""

_SYSTEM_PROMPT = (
    "You are a real estate lead intelligence system. "
    "Extract intent scores from call transcripts. "
    "Always respond in JSON only. No preamble. No markdown."
)

_USER_PROMPT_TEMPLATE = """Analyze this real estate call transcript and extract lead intent scores.

{scoring_rubric}

Transcript:
---
{transcript}
---

Return a single JSON object with these exact keys:
  timeline_urgency (integer 1-10)
  timeline_rationale (one sentence explaining the score)
  budget_seriousness (integer 1-10)
  budget_rationale (one sentence explaining the score)
  area_commitment (integer 1-10)
  area_rationale (one sentence explaining the score)"""


@dataclass
class IntentScores:
    timeline_urgency: float
    budget_seriousness: float
    area_commitment: float
    composite: float = field(init=False)
    rationale: str = field(init=False)
    prior_score: float | None = None

    def __post_init__(self):
        self.composite = round(
            self.timeline_urgency * 0.4
            + self.budget_seriousness * 0.35
            + self.area_commitment * 0.25,
            2,
        )
        self.rationale = ""  # set by score_call_intent


# DEPRECATED: Use extract_all_call_data() from extract.py instead (single combined LLM call)
async def score_call_intent(
    transcript_text: str,
    prior_score: float | None = None,
    llm_provider: str = "deepseek",
) -> IntentScores:
    api_key = os.environ.get("OPENAI_API_KEY", "")
    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

    # Support provider-specific env vars
    if llm_provider == "gemini":
        api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        base_url = os.environ.get("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
    elif llm_provider == "groq":
        api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        base_url = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
    elif llm_provider == "deepseek":
        api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

    model = "gpt-4o-mini"
    if llm_provider == "gemini":
        model = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
    elif llm_provider == "groq":
        model = "llama-3.3-70b-versatile"
    elif llm_provider == "deepseek":
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")

    user_prompt = _USER_PROMPT_TEMPLATE.format(
        scoring_rubric=_SCORING_RUBRIC,
        transcript=transcript_text,
    )
    try:
        import httpx
        # Strip trailing slash to avoid double-slash
        url = base_url.rstrip("/") + "/chat/completions"
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.0,
                    "max_tokens": 300,
                },
            )
            resp.raise_for_status()
            body = resp.json()
            raw = body["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        logger.warning(
            "LLM intent scoring failed, returning defaults: %s", exc
        )
        fallback = prior_score if prior_score is not None else 3.0
        scores = IntentScores(
            timeline_urgency=fallback,
            budget_seriousness=fallback,
            area_commitment=fallback,
            prior_score=prior_score,
        )
        scores.rationale = "llm_error: fallback scores used"
        return scores

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("LLM returned non-JSON: %s", raw[:200])
        fallback = prior_score if prior_score is not None else 3.0
        scores = IntentScores(
            timeline_urgency=fallback,
            budget_seriousness=fallback,
            area_commitment=fallback,
            prior_score=prior_score,
        )
        scores.rationale = "llm_error: invalid JSON response"
        return scores

    scores = IntentScores(
        timeline_urgency=float(parsed.get("timeline_urgency", 3)),
        budget_seriousness=float(parsed.get("budget_seriousness", 3)),
        area_commitment=float(parsed.get("area_commitment", 3)),
        prior_score=prior_score,
    )
    scores.rationale = (
        f"timeline: {parsed.get('timeline_rationale', '')}; "
        f"budget: {parsed.get('budget_rationale', '')}; "
        f"area: {parsed.get('area_rationale', '')}"
    )
    return scores


def stage_from_intent(scores: IntentScores, call_count: int) -> str:
    if scores.composite >= 8.0:
        return "hot"
    if scores.composite >= 6.0 and call_count >= 2:
        return "interested"
    if scores.composite >= 6.0 and call_count == 1:
        return "aware"
    if scores.composite >= 4.0:
        return "aware"
    return "cold"
