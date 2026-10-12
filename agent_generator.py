"""Generate full Voice AI agent configs from natural language descriptions."""

from __future__ import annotations

import json
import logging
import re
import uuid
from typing import Any

import httpx
from pydantic import BaseModel, Field

from agent_models import (
    AgentCreate,
    AgentCreateFull,
    CallConfigurationBase,
    ConversationalFlowBase,
    EndCallRules,
    FlowSection,
    PostCallActionBase,
    ResponseBehavior,
    SilenceHandling,
    ExtractedVariable,
)
from secrets_manager import get_secret

logger = logging.getLogger("agent_generator")

USE_CASE_PRESETS: dict[str, dict[str, str]] = {
    "lead_generation": {"purpose": "lead_generation", "label": "Lead Generation"},
    "appointments": {"purpose": "appointments", "label": "Appointments"},
    "support": {"purpose": "support", "label": "Support"},
    "negotiation": {"purpose": "negotiation", "label": "Negotiation"},
    "collections": {"purpose": "collections", "label": "Collections"},
    "admissions": {"purpose": "admissions", "label": "Admissions"},
    "sales": {"purpose": "sales", "label": "Sales"},
    "onboarding": {"purpose": "onboarding", "label": "Onboarding"},
    "surveys": {"purpose": "surveys", "label": "Surveys"},
}


class GeneratedAgentDraft(BaseModel):
    name: str
    description: str = ""
    purpose: str = "general"
    system_prompt: str
    languages: list[str] = Field(default_factory=lambda: ["en"])
    llm_provider: str = "gemini"
    llm_model: str = "gemini-2.0-flash"
    temperature: float = 0.65
    stt_provider: str = "sarvam"
    stt_model: str | None = "saarika"
    tts_provider: str = "sarvam"
    tts_voice_id: str = "kavya"
    welcome_message: str = ""
    max_call_duration_seconds: int = 1200
    metadata: dict[str, Any] = Field(default_factory=dict)
    flow_sections: list[dict[str, Any]] = Field(default_factory=list)
    extracted_variables: list[dict[str, str]] = Field(default_factory=list)
    call_configuration: dict[str, Any] = Field(default_factory=dict)


_GENERATION_SYSTEM = (
    "You are an expert digital sales employee architect. Given a user's natural language description "
    "and optional use-case tags, produce a complete sales employee voice configuration. "
    "Respond with valid JSON only — no markdown fences, no commentary."
)

_GENERATION_USER = """Create a production-ready inbound digital sales employee from this brief.

Description:
{description}

Use-case tags (may be empty): {use_cases}

Return JSON with exactly these keys:
{{
  "name": "Official product name (3-8 words)",
  "description": "one sentence summary",
  "purpose": "snake_case purpose e.g. admissions, support, lead_generation",
  "system_prompt": "2-4 paragraph core identity and behavior (concise; details go in flow_sections)",
  "languages": ["en"] or ["en","hi","mr"] etc. ISO-ish codes,
  "llm_provider": "gemini",
  "llm_model": "gemini-2.5-flash",
  "temperature": 0.65,
  "stt_provider": "sarvam",
  "stt_model": "stt-rt-v5",
  "tts_provider": "sarvam",
  "tts_voice_id": "shreya",
  "welcome_message": "natural spoken greeting matching the org (1-2 sentences)",
  "max_call_duration_seconds": 600,
  "flow_sections": [
    {{"id": "identity", "title": "Identity & Purpose", "content": "...", "enabled": true, "order": 1}},
    {{"id": "facts", "title": "Facts", "content": "bullet facts the agent may cite", "enabled": true, "order": 2}},
    {{"id": "actions", "title": "Actions & Limits", "content": "CAN / CANNOT rules", "enabled": true, "order": 3}},
    {{"id": "flow_general", "title": "Flow: General Enquiry", "content": "step-by-step call flow", "enabled": true, "order": 4}},
    {{"id": "flow_lead", "title": "Flow: Lead Collection", "content": "fields to collect in order", "enabled": true, "order": 5}},
    {{"id": "flow_escalation", "title": "Flow: Escalation", "content": "when and how to escalate", "enabled": true, "order": 6}},
    {{"id": "guardrails", "title": "Guardrails", "content": "never guarantee, never invent data", "enabled": true, "order": 7}},
    {{"id": "faq", "title": "FAQ", "content": "User:/Agent: example Q&A pairs", "enabled": true, "order": 8}}
  ],
  "extracted_variables": [
    {{"name": "caller_name", "description": "...", "how_to_extract": "..."}}
  ],
  "call_configuration": {{
    "silence_handling": {{
      "enabled": true,
      "timeout_seconds": 10,
      "max_prompts": 2,
      "prompt_message": "Are you still there?",
      "end_call_after_max_prompts": true
    }},
    "end_call_rules": {{
      "max_duration_seconds": 600,
      "end_phrases": ["goodbye", "bye", "thank you"],
      "voicemail_detection": true,
      "voicemail_message": ""
    }},
    "response_behavior": {{
      "interruption_sensitivity": "medium",
      "max_response_sentences": 3
    }},
    "advanced_settings": {{
      "noise_cancellation": true
    }}
  }}
}}

Requirements:
- Include 8-12 flow_sections with rich, domain-specific content (facts, flows, guardrails, FAQ).
- Populate Facts with real-sounding institutional/business details inferred from the brief.
- Include 4-8 extracted_variables for lead capture.
- Prefer sarvam STT + sarvam TTS for Indian multilingual voice."""


def _slug_id(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug[:40] or f"section_{uuid.uuid4().hex[:6]}"


def _parse_json_response(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("No JSON object in LLM response")
    return json.loads(text[start : end + 1])


def _resolve_llm_credentials() -> tuple[str, str, str] | None:
    """Return (api_key, base_url, model) — DeepSeek v4 Flash first for agent generation."""
    import os

    deepseek = get_secret("DEEPSEEK_API_KEY") or get_secret("deepseek_api_key") or ""
    if deepseek:
        model = os.environ.get("DEEPSEEK_AGENT_GEN_MODEL", "deepseek-v4-flash")
        return (deepseek, "https://api.deepseek.com/v1", model)

    gemini = get_secret("gemini_api_key") or ""
    if gemini:
        return (
            gemini,
            "https://generativelanguage.googleapis.com/v1beta",
            "gemini-2.0-flash",
        )
    openai = get_secret("openai_api_key") or ""
    if openai:
        return (openai, "https://api.openai.com/v1", "gpt-4o-mini")
    groq = get_secret("groq_api_key") or ""
    if groq:
        return (groq, "https://api.groq.com/openai/v1", "llama-3.3-70b-versatile")
    return None


async def _call_llm(description: str, use_cases: list[str]) -> dict[str, Any]:
    creds = _resolve_llm_credentials()
    if not creds:
        raise RuntimeError("No LLM API key configured")

    api_key, base_url, model = creds
    user_prompt = _GENERATION_USER.format(
        description=description.strip(),
        use_cases=", ".join(use_cases) if use_cases else "none",
    )

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _GENERATION_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.35,
                "max_tokens": 8000,
            },
        )
        resp.raise_for_status()
        body = resp.json()
        raw = body["choices"][0]["message"]["content"]
        return _parse_json_response(raw)


def _normalize_use_cases(use_cases: list[str]) -> list[str]:
    out: list[str] = []
    for raw in use_cases:
        key = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
        if key and key not in out:
            out.append(key)
    return out


def _fallback_draft(description: str, use_cases: list[str]) -> GeneratedAgentDraft:
    """Heuristic config when LLM is unavailable."""
    cases = _normalize_use_cases(use_cases)
    primary = cases[0] if cases else "general"

    preset = USE_CASE_PRESETS.get(primary, {})
    purpose = preset.get("purpose", primary or "general")

    name_words = re.findall(r"[A-Za-z]{3,}", description)
    name = " ".join(name_words[:3]).title() or "Voice Assistant"
    if len(name) > 40:
        name = name[:37] + "..."

    hi_needed = any(w in description.lower() for w in ("hindi", "hinglish", "india", "namaste", "admission", "college"))
    languages = ["en", "hi"] if hi_needed else ["en"]

    sections_templates: dict[str, list[tuple[str, str]]] = {
        "admissions": [
            ("Introduction", "Greet warmly and ask if they are a student or parent."),
            ("Programs", "Explain relevant programs and ask area of interest."),
            ("Eligibility", "Clarify eligibility criteria and entrance exams."),
            ("Fees & Scholarships", "Share high-level fee info and scholarship options."),
            ("Lead Capture", "Collect name, phone, city, and program interest."),
            ("Next Steps", "Offer campus visit or callback and close positively."),
        ],
        "support": [
            ("Greeting", "Acknowledge the issue and show empathy."),
            ("Issue Discovery", "Ask clarifying questions to understand the problem."),
            ("Resolution", "Provide step-by-step help or troubleshooting."),
            ("Escalation", "Offer human handoff if unresolved."),
            ("Close", "Confirm satisfaction and summarize next steps."),
        ],
        "appointments": [
            ("Greeting", "Welcome and confirm scheduling intent."),
            ("Availability", "Ask preferred dates and times."),
            ("Details", "Collect name, phone, and appointment reason."),
            ("Confirmation", "Repeat booking details and confirm."),
            ("Close", "Thank the caller and share reminder info."),
        ],
    }

    template_key = purpose if purpose in sections_templates else "support"
    if purpose == "lead_generation":
        template_key = "appointments"

    flow_tuples = sections_templates.get(template_key, sections_templates["support"])
    flow_sections = [
        {
            "id": _slug_id(title),
            "title": title,
            "content": content,
            "enabled": True,
            "order": i + 1,
        }
        for i, (title, content) in enumerate(flow_tuples)
    ]

    system_prompt = (
        f"You are a named sales employee at this company — a real person on a live phone call.\n\n"
        f"Primary purpose: {purpose.replace('_', ' ')}.\n\n"
        f"User brief:\n{description.strip()}\n\n"
        f"Guidelines:\n"
        f"- Speak naturally in {'English and Hindi' if 'hi' in languages else 'English'}.\n"
        f"- Keep responses concise (2-3 sentences) unless more detail is requested.\n"
        f"- Confirm key details by repeating them back.\n"
        f"- If unsure, offer to connect with a human specialist.\n"
        f"- End calls warmly when the caller is satisfied."
    )

    welcome = (
        "Namaste! How can I help you today?"
        if "hi" in languages
        else "Hello! How can I help you today?"
    )

    extracted = [
        {"name": "caller_name", "description": "Caller's full name", "how_to_extract": "When they introduce themselves"},
        {"name": "primary_intent", "description": "Main reason for calling", "how_to_extract": "Infer from conversation topic"},
        {"name": "callback_requested", "description": "Wants human follow-up", "how_to_extract": "true if they ask for callback or transfer"},
    ]

    return GeneratedAgentDraft(
        name=name,
        description=description.strip()[:300],
        purpose=purpose,
        system_prompt=system_prompt,
        languages=languages,
        welcome_message=welcome,
        metadata={
            "use_cases": cases,
            "knowledge_base_files": 0,
            "search_enabled": False,
            "generated_by": "fallback",
        },
        flow_sections=flow_sections,
        extracted_variables=extracted,
    )


def _build_call_configuration(raw: dict[str, Any] | None) -> CallConfigurationBase:
    """Map LLM call_configuration blob to CallConfigurationBase."""
    if not raw:
        return CallConfigurationBase()

    sh = raw.get("silence_handling") or {}
    ec = raw.get("end_call_rules") or {}
    rb = raw.get("response_behavior") or {}
    adv = raw.get("advanced_settings") or {}

    return CallConfigurationBase(
        silence_handling=SilenceHandling(
            enabled=bool(sh.get("enabled", True)),
            timeout_seconds=int(sh.get("timeout_seconds") or 10),
            max_prompts=int(sh.get("max_prompts") or 2),
            prompt_message=str(sh.get("prompt_message") or "Are you still there?"),
            end_call_after_max_prompts=bool(sh.get("end_call_after_max_prompts", False)),
        ),
        end_call_rules=EndCallRules(
            max_duration_seconds=int(ec.get("max_duration_seconds") or 600),
            end_phrases=list(ec.get("end_phrases") or ["goodbye", "bye"]),
            voicemail_detection=bool(ec.get("voicemail_detection", True)),
            voicemail_message=str(ec.get("voicemail_message") or ""),
        ),
        response_behavior=ResponseBehavior(
            interruption_sensitivity=str(rb.get("interruption_sensitivity") or "medium"),
            max_response_sentences=int(rb.get("max_response_sentences") or 3),
        ),
        advanced_settings={
            "noise_cancellation": bool(adv.get("noise_cancellation", True)),
            **{k: v for k, v in adv.items() if k != "noise_cancellation"},
        },
    )


def _draft_to_create_full(draft: GeneratedAgentDraft) -> AgentCreateFull:
    sections = [
        FlowSection(
            id=str(s.get("id") or _slug_id(str(s.get("title", "section")))),
            title=str(s.get("title") or "Section"),
            content=str(s.get("content") or ""),
            enabled=bool(s.get("enabled", True)),
            order=int(s.get("order") or i + 1),
        )
        for i, s in enumerate(draft.flow_sections)
    ]

    extracted = [
        ExtractedVariable(
            name=str(v.get("name") or "field"),
            description=str(v.get("description") or ""),
            how_to_extract=str(v.get("how_to_extract") or ""),
        )
        for v in draft.extracted_variables
    ]

    meta = dict(draft.metadata or {})
    meta.setdefault("knowledge_base_files", 0)
    meta.setdefault("search_enabled", False)

    call_cfg = _build_call_configuration(draft.call_configuration)
    max_dur = draft.max_call_duration_seconds
    end_rules = call_cfg.end_call_rules
    if isinstance(end_rules, EndCallRules):
        max_dur = int(end_rules.max_duration_seconds)
    elif isinstance(end_rules, dict) and end_rules.get("max_duration_seconds"):
        max_dur = int(end_rules["max_duration_seconds"])

    return AgentCreateFull(
        agent=AgentCreate(
            name=draft.name,
            description=draft.description,
            purpose=draft.purpose,
            system_prompt=draft.system_prompt,
            languages=draft.languages,
            llm_provider=draft.llm_provider,
            llm_model=draft.llm_model,
            temperature=draft.temperature,
            stt_provider=draft.stt_provider,
            stt_model=draft.stt_model,
            tts_provider=draft.tts_provider,
            tts_voice_id=draft.tts_voice_id,
            welcome_message=draft.welcome_message,
            max_call_duration_seconds=max_dur,
            is_active=True,
            metadata=meta,
        ),
        conversational_flow=ConversationalFlowBase(sections=sections),
        call_configuration=call_cfg,
        post_call_action=PostCallActionBase(extracted_variables=extracted),
    )


async def generate_agent_from_description(
    description: str,
    use_cases: list[str] | None = None,
) -> AgentCreateFull:
    """Expand natural language + use cases into a full AgentCreateFull payload."""
    cases = _normalize_use_cases(use_cases or [])
    if len(description.strip()) < 10:
        raise ValueError("Description must be at least 10 characters")

    draft: GeneratedAgentDraft | None = None
    try:
        raw = await _call_llm(description, cases)
        draft = GeneratedAgentDraft.model_validate(raw)
        draft.metadata = {
            **(draft.metadata or {}),
            "use_cases": cases,
            "knowledge_base_files": 0,
            "search_enabled": False,
            "generated_by": "llm",
        }
    except Exception as exc:
        logger.warning("LLM agent generation failed, using fallback: %s", exc)
        draft = _fallback_draft(description, cases)

    return _draft_to_create_full(draft)