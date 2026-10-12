"""Apply natural-language edits to full agent configuration."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
from pydantic import BaseModel, Field

from agent_generator import _build_call_configuration, _parse_json_response, _resolve_llm_credentials, _slug_id
from agent_models import (
    AgentUpdate,
    CallConfigurationUpdate,
    ConversationalFlowUpdate,
    FlowSection,
    PostCallActionUpdate,
)
logger = logging.getLogger("agent_ai_edit")

_EDIT_SYSTEM = (
    "You are a production Voice AI configuration editor. "
    "Given the current agent configuration and a user instruction, produce a minimal JSON patch. "
    "Respond with valid JSON only — no markdown fences, no commentary."
)

_EDIT_USER = """Current agent configuration (JSON):
{current_config}

User instruction (may be natural language, bullet facts, or pasted config data):
{instruction}

Return JSON with exactly these keys:
{{
  "summary": "One sentence describing what changed",
  "agent": {{ optional partial agent fields: name, description, purpose, system_prompt, languages, llm_provider, llm_model, temperature, stt_provider, stt_model, tts_provider, tts_voice_id, welcome_message, max_call_duration_seconds, metadata }},
  "conversational_flow": {{ optional "sections": [{{"id","title","content","enabled","order"}}] }},
  "call_configuration": {{ optional silence_handling, end_call_rules, transfer_routing, response_behavior, ambient_sound, advanced_settings }},
  "post_call_action": {{ optional delivery_method, trigger_statuses, extracted_variables, webhook_url, email_recipients, include_* booleans }}
}}

Rules:
- Only include keys you are changing; omit unchanged sections entirely (use {{}} for agent if nothing changes).
- Preserve existing section ids when editing flow content; add new sections with snake_case ids.
- When user pastes facts, pricing, FAQs, or policies: merge into system_prompt and/or relevant flow sections — do not wipe unrelated content.
- When user pastes JSON-like data, interpret and merge gracefully into the right fields.
- Keep languages as ISO codes. Prefer sarvam STT + sarvam TTS for Indian voice unless user specifies otherwise.
- Never remove required agent name. Never return empty system_prompt if one already exists unless user explicitly asks to clear it."""


class AgentAiEditResult(BaseModel):
    summary: str = ""
    agent: dict[str, Any] = Field(default_factory=dict)
    conversational_flow: dict[str, Any] = Field(default_factory=dict)
    call_configuration: dict[str, Any] = Field(default_factory=dict)
    post_call_action: dict[str, Any] = Field(default_factory=dict)
    applied_by: str = "llm"


def _compact_config_for_llm(config: dict[str, Any]) -> dict[str, Any]:
    """Trim very large fields while keeping structure for the LLM."""
    out = json.loads(json.dumps(config))
    out.pop("merged_system_prompt", None)

    agent = out.get("agent") or {}
    sp = str(agent.get("system_prompt") or "")
    if len(sp) > 6000:
        agent["system_prompt"] = sp[:5500] + "\n\n[... truncated for edit context ...]"
        out["agent"] = agent

    flow = out.get("conversational_flow") or {}
    sections = flow.get("sections") or []
    for section in sections:
        content = str(section.get("content") or "")
        if len(content) > 2500:
            section["content"] = content[:2200] + "\n[... truncated ...]"
    return out


def _try_parse_embedded_json(text: str) -> dict[str, Any] | None:
    stripped = text.strip()
    if not stripped:
        return None
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", stripped)
    if fence:
        try:
            parsed = json.loads(fence.group(1))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            pass
    return None


def _normalize_flow_sections(raw_sections: list[Any], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    existing_by_id = {str(s.get("id")): s for s in existing if s.get("id")}
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw_sections):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or f"Section {i + 1}")
        sid = str(item.get("id") or existing_by_id.get(title, {}).get("id") or _slug_id(title))
        out.append(
            {
                "id": sid,
                "title": title,
                "content": str(item.get("content") or ""),
                "enabled": bool(item.get("enabled", True)),
                "order": int(item.get("order") or i + 1),
            }
        )
    return out


def _merge_flow_sections(existing: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not incoming:
        return existing
    by_id = {str(s.get("id")): dict(s) for s in existing}
    for section in incoming:
        sid = str(section.get("id") or _slug_id(str(section.get("title", "section"))))
        if sid in by_id:
            by_id[sid].update(section)
            by_id[sid]["id"] = sid
        else:
            by_id[sid] = section
    merged = list(by_id.values())
    merged.sort(key=lambda s: int(s.get("order") or 0))
    for i, section in enumerate(merged):
        section["order"] = i + 1
    return merged


def _fallback_edit(config: dict[str, Any], instruction: str) -> AgentAiEditResult:
    """Heuristic merge when LLM is unavailable."""
    instruction = instruction.strip()
    if not instruction:
        raise ValueError("Instruction cannot be empty")

    agent = dict(config.get("agent") or {})
    flow = dict(config.get("conversational_flow") or {})
    existing_sections = list(flow.get("sections") or [])
    call_cfg = dict(config.get("call_configuration") or {})
    post_call = dict(config.get("post_call_action") or {})

    embedded = _try_parse_embedded_json(instruction)
    if embedded:
        patch_agent = embedded.get("agent") if isinstance(embedded.get("agent"), dict) else embedded
        result_agent: dict[str, Any] = {}
        flow_patch: dict[str, Any] = {}
        call_patch: dict[str, Any] = {}
        post_patch: dict[str, Any] = {}

        allowed_agent = {
            "name", "description", "purpose", "system_prompt", "languages",
            "llm_provider", "llm_model", "temperature", "stt_provider", "stt_model",
            "tts_provider", "tts_voice_id", "welcome_message", "max_call_duration_seconds", "metadata",
        }
        for key, value in patch_agent.items():
            if key in allowed_agent and value is not None:
                result_agent[key] = value

        if isinstance(embedded.get("conversational_flow"), dict):
            secs = embedded["conversational_flow"].get("sections")
            if isinstance(secs, list):
                flow_patch["sections"] = _merge_flow_sections(
                    existing_sections,
                    _normalize_flow_sections(secs, existing_sections),
                )
        elif isinstance(embedded.get("sections"), list):
            flow_patch["sections"] = _merge_flow_sections(
                existing_sections,
                _normalize_flow_sections(embedded["sections"], existing_sections),
            )

        if isinstance(embedded.get("call_configuration"), dict):
            call_patch = embedded["call_configuration"]
        if isinstance(embedded.get("post_call_action"), dict):
            post_patch = embedded["post_call_action"]

        return AgentAiEditResult(
            summary="Merged pasted configuration data",
            agent=result_agent,
            conversational_flow=flow_patch,
            call_configuration=call_patch,
            post_call_action=post_patch,
            applied_by="fallback_json",
        )

    summary = "Updated configuration from instruction"
    result_agent: dict[str, Any] = {}
    flow_patch: dict[str, Any] = {}

    lower = instruction.lower()
    if any(w in lower for w in ("welcome", "greeting", "first line")):
        result_agent["welcome_message"] = instruction.split("\n", 1)[0].strip()[:500]
        summary = "Updated welcome message"

    if any(w in lower for w in ("faq", "facts", "pricing", "policy", "project", "property", "brochure")):
        new_section = {
            "id": _slug_id("user_provided_facts"),
            "title": "User Provided Facts",
            "content": instruction,
            "enabled": True,
            "order": len(existing_sections) + 1,
        }
        flow_patch["sections"] = _merge_flow_sections(existing_sections, [new_section])
        summary = "Added user-provided facts to conversational flow"
    else:
        base_prompt = str(agent.get("system_prompt") or "").strip()
        addition = f"\n\n--- Additional instructions ---\n{instruction}"
        result_agent["system_prompt"] = (base_prompt + addition).strip()
        summary = "Appended instruction to system prompt"

    return AgentAiEditResult(
        summary=summary,
        agent=result_agent,
        conversational_flow=flow_patch,
        call_configuration={},
        post_call_action={},
        applied_by="fallback_rules",
    )


async def _call_edit_llm(config: dict[str, Any], instruction: str) -> dict[str, Any]:
    creds = _resolve_llm_credentials()
    if not creds:
        raise RuntimeError("No LLM API key configured")

    api_key, base_url, model = creds
    compact = _compact_config_for_llm(config)
    user_prompt = _EDIT_USER.format(
        current_config=json.dumps(compact, ensure_ascii=False, indent=2),
        instruction=instruction.strip(),
    )

    async with httpx.AsyncClient(timeout=90.0) as client:
        resp = await client.post(
            f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": _EDIT_SYSTEM},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.25,
                "max_tokens": 8000,
            },
        )
        resp.raise_for_status()
        body = resp.json()
        raw = body["choices"][0]["message"]["content"]
        return _parse_json_response(raw)


async def generate_ai_edit_patch(config: dict[str, Any], instruction: str) -> AgentAiEditResult:
    if len(instruction.strip()) < 3:
        raise ValueError("Instruction must be at least 3 characters")

    try:
        raw = await _call_edit_llm(config, instruction)
        result = AgentAiEditResult.model_validate(raw)
        result.applied_by = "llm"
        existing_sections = list((config.get("conversational_flow") or {}).get("sections") or [])
        if result.conversational_flow.get("sections"):
            result.conversational_flow["sections"] = _merge_flow_sections(
                existing_sections,
                _normalize_flow_sections(result.conversational_flow["sections"], existing_sections),
            )
        return result
    except Exception as exc:
        logger.warning("LLM agent edit failed, using fallback: %s", exc)
        return _fallback_edit(config, instruction)


def patch_to_updates(patch: AgentAiEditResult) -> dict[str, Any]:
    """Convert AI edit patch into pydantic update payloads."""
    updates: dict[str, Any] = {}

    if patch.agent:
        updates["agent"] = AgentUpdate.model_validate(patch.agent)

    if patch.conversational_flow:
        flow_data = dict(patch.conversational_flow)
        if flow_data.get("sections"):
            flow_data["sections"] = [
                FlowSection.model_validate(s) if isinstance(s, dict) else s
                for s in flow_data["sections"]
            ]
        updates["conversational_flow"] = ConversationalFlowUpdate.model_validate(flow_data)

    if patch.call_configuration:
        cc_raw = dict(patch.call_configuration)
        built = _build_call_configuration(cc_raw)
        updates["call_configuration"] = CallConfigurationUpdate.model_validate(
            built.model_dump(exclude_unset=True)
        )

    if patch.post_call_action:
        updates["post_call_action"] = PostCallActionUpdate.model_validate(patch.post_call_action)

    return updates