"""Execute post-call actions configured per Voice AI agent."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from agent_models import PostCallAction

logger = logging.getLogger("post_call_handler")


def _as_post_call(config: Any) -> PostCallAction | None:
    if config is None:
        return None
    if isinstance(config, PostCallAction):
        return config
    if isinstance(config, dict):
        try:
            return PostCallAction.model_validate(config)
        except Exception:
            return None
    return None


def _build_payload(
    action: PostCallAction,
    call_data: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event": "post_call",
        "agent_id": call_data.get("agent_id"),
        "call_status": call_data.get("call_status", "completed"),
        "phone": call_data.get("phone"),
        "caller_name": call_data.get("caller_name"),
        "duration_seconds": call_data.get("duration_seconds"),
        "room_id": call_data.get("room_id"),
    }

    if action.include_summary:
        payload["summary"] = call_data.get("summary") or call_data.get("call_summary")
    if action.include_full_transcript:
        payload["transcript"] = call_data.get("transcript")
    if action.include_sentiment:
        payload["sentiment"] = call_data.get("sentiment")
    if action.include_extracted_info:
        payload["extracted_info"] = call_data.get("extracted_info") or {}
        payload["extracted_variables"] = [
            v.model_dump() if hasattr(v, "model_dump") else v
            for v in (action.extracted_variables or [])
        ]

    if call_data.get("recording_url"):
        payload["recording_url"] = call_data["recording_url"]

    return payload


async def execute_post_call_actions(
    post_call_config: Any,
    *,
    call_status: str,
    call_data: dict[str, Any],
) -> None:
    """Deliver post-call payload via webhook/email per agent configuration."""
    action = _as_post_call(post_call_config)
    if not action:
        return

    method = str(action.delivery_method or "webhook").lower()
    if method == "none":
        return

    triggers = [str(s).lower() for s in (action.trigger_statuses or ["completed"])]
    normalized_status = str(call_status or "completed").lower()
    if triggers and normalized_status not in triggers:
        logger.info(
            "[POST-CALL] Skipping delivery — status '%s' not in triggers %s",
            normalized_status,
            triggers,
        )
        return

    payload = _build_payload(action, {**call_data, "call_status": normalized_status})

    if method in ("webhook", "both") and action.webhook_url:
        headers = dict(action.webhook_headers or {})
        headers.setdefault("Content-Type", "application/json")
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(action.webhook_url, json=payload, headers=headers)
                resp.raise_for_status()
            logger.info("[POST-CALL] Webhook delivered to %s", action.webhook_url)
        except Exception as exc:
            logger.warning("[POST-CALL] Webhook failed: %s", exc)

    if method in ("email", "both") and action.email_recipients:
        logger.info(
            "[POST-CALL] Email delivery queued for %s (SMTP integration pending)",
            ", ".join(action.email_recipients),
        )