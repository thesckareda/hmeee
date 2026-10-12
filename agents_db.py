"""Database operations for Voice AI agents (migration v4)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from agent_models import (
    Agent,
    AgentCreate,
    AgentCreateFull,
    AgentUpdate,
    CallConfiguration,
    CallConfigurationBase,
    CallConfigurationUpdate,
    ConversationalFlow,
    ConversationalFlowBase,
    ConversationalFlowUpdate,
    FullAgentConfig,
    PostCallAction,
    PostCallActionBase,
    PostCallActionUpdate,
)

import db
from tenancy import apply_org_filter, inject_org_id

logger = logging.getLogger("agents_db")

DEFAULT_AGENT_ID = "a0000000-0000-4000-8000-000000000001"


class AgentsDbError(Exception):
    def __init__(self, message: str, *, code: str = "db_error"):
        super().__init__(message)
        self.code = code


def _is_missing_table_error(err: str) -> bool:
    lowered = err.lower()
    return (
        "pgrst205" in lowered
        or "42p01" in lowered
        or "does not exist" in lowered
        or "schema cache" in lowered and "agents" in lowered
    )


def _serialize_sections(sections: list[Any]) -> list[dict]:
    out: list[dict] = []
    for section in sections:
        if hasattr(section, "model_dump"):
            out.append(section.model_dump())
        elif isinstance(section, dict):
            out.append(section)
    return out


def _serialize_json_value(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return value


def _row_to_agent(row: dict) -> Agent:
    return Agent.model_validate(row)


def _row_to_flow(row: dict | None) -> ConversationalFlow | None:
    if not row:
        return None
    sections = row.get("sections") or []
    if sections and isinstance(sections[0], dict):
        from agent_models import FlowSection

        row = dict(row)
        row["sections"] = [FlowSection.model_validate(s) for s in sections]
    return ConversationalFlow.model_validate(row)


def _row_to_call_config(row: dict | None) -> CallConfiguration | None:
    return CallConfiguration.model_validate(row) if row else None


def _row_to_post_call(row: dict | None) -> PostCallAction | None:
    return PostCallAction.model_validate(row) if row else None


def _is_rls_error(err: str) -> bool:
    lowered = err.lower()
    return "row-level security" in lowered or "42501" in lowered


def _rls_error() -> AgentsDbError:
    return AgentsDbError(
        "Database permission denied. Set SUPABASE_SERVICE_ROLE_KEY in .env "
        "(Supabase → Project Settings → API → service_role secret), "
        "or run supabase_fix_rls.sql in the Supabase SQL Editor.",
        code="rls_denied",
    )


async def _supabase():
    client = await db.get_supabase()
    if not client:
        raise AgentsDbError(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.",
            code="not_configured",
        )
    return client


async def _supabase_write():
    """Prefer service-role key for inserts/updates/deletes (bypasses RLS)."""
    client = await db.get_supabase_admin()
    if client:
        return client
    client = await db.get_supabase()
    if not client:
        raise AgentsDbError(
            "Supabase is not configured. Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.",
            code="not_configured",
        )
    return client


async def list_agents(
    *,
    is_active: bool | None = None,
    purpose: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    sb = await _supabase()
    try:
        query = sb.table("agents").select("*").order("created_at", desc=True)
        query = apply_org_filter(query)
        if is_active is not None:
            query = query.eq("is_active", is_active)
        if purpose:
            query = query.eq("purpose", purpose)
        res = query.range(offset, offset + max(limit, 1) - 1).execute()
        return res.data or []
    except Exception as exc:
        err = str(exc)
        if _is_missing_table_error(err):
            raise AgentsDbError(
                "Agents tables not found. Run supabase_migration_v4_agents.sql first.",
                code="migration_required",
            ) from exc
        logger.error("list_agents failed: %s", exc)
        raise AgentsDbError(err) from exc


async def get_agent_row(agent_id: str | UUID) -> dict | None:
    sb = await _supabase()
    try:
        query = sb.table("agents").select("*").eq("id", str(agent_id))
        query = apply_org_filter(query)
        res = query.maybe_single().execute()
        return res.data
    except Exception as exc:
        err = str(exc)
        if _is_missing_table_error(err):
            raise AgentsDbError(
                "Agents tables not found. Run supabase_migration_v4_agents.sql first.",
                code="migration_required",
            ) from exc
        logger.error("get_agent_row failed: %s", exc)
        raise AgentsDbError(err) from exc


async def _fetch_related(agent_id: str | UUID) -> tuple[dict | None, dict | None, dict | None]:
    sb = await _supabase()
    agent_key = str(agent_id)
    flow = (
        sb.table("conversational_flows")
        .select("*")
        .eq("agent_id", agent_key)
        .maybe_single()
        .execute()
        .data
    )
    call_cfg = (
        sb.table("call_configurations")
        .select("*")
        .eq("agent_id", agent_key)
        .maybe_single()
        .execute()
        .data
    )
    post_call = (
        sb.table("post_call_actions")
        .select("*")
        .eq("agent_id", agent_key)
        .maybe_single()
        .execute()
        .data
    )
    return flow, call_cfg, post_call


async def get_agent_config(agent_id: str | UUID) -> FullAgentConfig | None:
    """Fetch merged configuration for an agent (used by API and LiveKit worker)."""
    row = await get_agent_row(agent_id)
    if not row:
        return None

    flow_row, call_row, post_row = await _fetch_related(agent_id)
    config = FullAgentConfig(
        agent=_row_to_agent(row),
        conversational_flow=_row_to_flow(flow_row),
        call_configuration=_row_to_call_config(call_row),
        post_call_action=_row_to_post_call(post_row),
    )
    return config


async def get_agent_config_dict(agent_id: str | UUID) -> dict | None:
    config = await get_agent_config(agent_id)
    if not config:
        return None
    payload = config.model_dump(mode="json")
    payload["merged_system_prompt"] = config.merged_system_prompt()
    return payload


async def create_default_related(agent_id: str | UUID) -> None:
    sb = await _supabase_write()
    key = str(agent_id)
    sb.table("conversational_flows").insert({"agent_id": key, "sections": [], "version": 1}).execute()
    sb.table("call_configurations").insert({"agent_id": key}).execute()
    sb.table("post_call_actions").insert({"agent_id": key}).execute()


async def create_agent(payload: AgentCreateFull) -> FullAgentConfig:
    sb = await _supabase_write()
    agent_data = inject_org_id(payload.agent.model_dump())
    try:
        inserted = sb.table("agents").insert(agent_data).execute()
        row = (inserted.data or [None])[0]
        if not row:
            raise AgentsDbError("Agent insert returned no data")
        agent_id = row["id"]

        flow_base = payload.conversational_flow or ConversationalFlowBase()
        call_base = payload.call_configuration or CallConfigurationBase()
        post_base = payload.post_call_action or PostCallActionBase()

        sb.table("conversational_flows").insert(
            {
                "agent_id": agent_id,
                "sections": _serialize_sections(flow_base.sections),
                "version": flow_base.version,
            }
        ).execute()
        sb.table("call_configurations").insert(
            {
                "agent_id": agent_id,
                "silence_handling": _serialize_json_value(call_base.silence_handling),
                "end_call_rules": _serialize_json_value(call_base.end_call_rules),
                "transfer_routing": _serialize_json_value(call_base.transfer_routing),
                "response_behavior": _serialize_json_value(call_base.response_behavior),
                "ambient_sound": _serialize_json_value(call_base.ambient_sound),
                "advanced_settings": call_base.advanced_settings,
            }
        ).execute()
        sb.table("post_call_actions").insert(
            {
                "agent_id": agent_id,
                "delivery_method": (
                    post_base.delivery_method.value
                    if hasattr(post_base.delivery_method, "value")
                    else post_base.delivery_method
                ),
                "trigger_statuses": post_base.trigger_statuses,
                "include_summary": post_base.include_summary,
                "include_full_transcript": post_base.include_full_transcript,
                "include_sentiment": post_base.include_sentiment,
                "include_extracted_info": post_base.include_extracted_info,
                "extracted_variables": _serialize_sections(post_base.extracted_variables),
                "webhook_url": post_base.webhook_url,
                "webhook_headers": post_base.webhook_headers,
                "email_recipients": post_base.email_recipients,
            }
        ).execute()

        config = await get_agent_config(agent_id)
        if not config:
            raise AgentsDbError("Failed to load agent after create")
        return config
    except AgentsDbError:
        raise
    except Exception as exc:
        err = str(exc)
        if _is_missing_table_error(err):
            raise AgentsDbError(
                "Agents tables not found. Run supabase_migration_v4_agents.sql first.",
                code="migration_required",
            ) from exc
        if _is_rls_error(err):
            raise _rls_error() from exc
        logger.error("create_agent failed: %s", exc)
        raise AgentsDbError(err) from exc


async def activate_agent(agent_id: str | UUID) -> Agent:
    """
    Set one agent as the live default: activate it and pause all others.
    Used when dashboard user clicks 'Make Agent Active'.
    """
    sb = await _supabase_write()
    key = str(agent_id)
    row = await get_agent_row(agent_id)
    if not row:
        raise AgentsDbError("Agent not found", code="not_found")

    try:
        sb.table("agents").update({"is_active": False}).neq("id", "00000000-0000-0000-0000-000000000000").execute()
        res = sb.table("agents").update({"is_active": True}).eq("id", key).execute()
        updated = (res.data or [None])[0]
        if not updated:
            raise AgentsDbError("Failed to activate agent")
        return _row_to_agent(updated)
    except AgentsDbError:
        raise
    except Exception as exc:
        err = str(exc)
        if _is_rls_error(err):
            raise _rls_error() from exc
        logger.error("activate_agent failed: %s", exc)
        raise AgentsDbError(err) from exc


async def update_agent(agent_id: str | UUID, payload: AgentUpdate) -> Agent:
    sb = await _supabase_write()
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        row = await get_agent_row(agent_id)
        if not row:
            raise AgentsDbError("Agent not found", code="not_found")
        return _row_to_agent(row)

    try:
        res = sb.table("agents").update(updates).eq("id", str(agent_id)).execute()
        row = (res.data or [None])[0]
        if not row:
            raise AgentsDbError("Agent not found", code="not_found")
        return _row_to_agent(row)
    except AgentsDbError:
        raise
    except Exception as exc:
        err = str(exc)
        if _is_rls_error(err):
            raise _rls_error() from exc
        logger.error("update_agent failed: %s", exc)
        raise AgentsDbError(err) from exc


async def delete_agent(agent_id: str | UUID) -> bool:
    sb = await _supabase_write()
    try:
        existing = await get_agent_row(agent_id)
        if not existing:
            return False
        sb.table("agents").delete().eq("id", str(agent_id)).execute()
        return True
    except Exception as exc:
        err = str(exc)
        if _is_rls_error(err):
            raise _rls_error() from exc
        logger.error("delete_agent failed: %s", exc)
        raise AgentsDbError(err) from exc


async def update_conversational_flow(
    agent_id: str | UUID,
    payload: ConversationalFlowUpdate,
) -> ConversationalFlow:
    sb = await _supabase_write()
    if not await get_agent_row(agent_id):
        raise AgentsDbError("Agent not found", code="not_found")

    flow_row, _, _ = await _fetch_related(agent_id)
    updates: dict[str, Any] = {}
    if payload.sections is not None:
        updates["sections"] = _serialize_sections(payload.sections)
    if payload.version is not None:
        updates["version"] = payload.version
    elif payload.sections is not None and flow_row:
        updates["version"] = int(flow_row.get("version") or 1) + 1

    try:
        if flow_row:
            res = (
                sb.table("conversational_flows")
                .update(updates)
                .eq("agent_id", str(agent_id))
                .execute()
            )
            row = (res.data or [None])[0]
        else:
            res = sb.table("conversational_flows").insert(
                {
                    "agent_id": str(agent_id),
                    "sections": updates.get("sections", []),
                    "version": updates.get("version", 1),
                }
            ).execute()
            row = (res.data or [None])[0]
        if not row:
            raise AgentsDbError("Failed to update conversational flow")
        return _row_to_flow(row)  # type: ignore[return-value]
    except AgentsDbError:
        raise
    except Exception as exc:
        err = str(exc)
        if _is_rls_error(err):
            raise _rls_error() from exc
        logger.error("update_conversational_flow failed: %s", exc)
        raise AgentsDbError(err) from exc


async def update_call_configuration(
    agent_id: str | UUID,
    payload: CallConfigurationUpdate,
) -> CallConfiguration:
    sb = await _supabase_write()
    if not await get_agent_row(agent_id):
        raise AgentsDbError("Agent not found", code="not_found")

    _, call_row, _ = await _fetch_related(agent_id)
    raw = payload.model_dump(exclude_unset=True)
    updates = {k: _serialize_json_value(v) for k, v in raw.items()}

    try:
        if call_row:
            res = (
                sb.table("call_configurations")
                .update(updates)
                .eq("agent_id", str(agent_id))
                .execute()
            )
            row = (res.data or [None])[0]
        else:
            data = {"agent_id": str(agent_id), **updates}
            res = sb.table("call_configurations").insert(data).execute()
            row = (res.data or [None])[0]
        if not row:
            raise AgentsDbError("Failed to update call configuration")
        return _row_to_call_config(row)  # type: ignore[return-value]
    except AgentsDbError:
        raise
    except Exception as exc:
        err = str(exc)
        if _is_rls_error(err):
            raise _rls_error() from exc
        logger.error("update_call_configuration failed: %s", exc)
        raise AgentsDbError(err) from exc


async def update_post_call_actions(
    agent_id: str | UUID,
    payload: PostCallActionUpdate,
) -> PostCallAction:
    sb = await _supabase_write()
    if not await get_agent_row(agent_id):
        raise AgentsDbError("Agent not found", code="not_found")

    _, _, post_row = await _fetch_related(agent_id)
    raw = payload.model_dump(exclude_unset=True)
    if "extracted_variables" in raw and raw["extracted_variables"] is not None:
        raw["extracted_variables"] = _serialize_sections(raw["extracted_variables"])
    if "delivery_method" in raw and raw["delivery_method"] is not None:
        dm = raw["delivery_method"]
        raw["delivery_method"] = dm.value if hasattr(dm, "value") else dm

    try:
        if post_row:
            res = (
                sb.table("post_call_actions")
                .update(raw)
                .eq("agent_id", str(agent_id))
                .execute()
            )
            row = (res.data or [None])[0]
        else:
            data = {"agent_id": str(agent_id), **raw}
            res = sb.table("post_call_actions").insert(data).execute()
            row = (res.data or [None])[0]
        if not row:
            raise AgentsDbError("Failed to update post-call actions")
        return _row_to_post_call(row)  # type: ignore[return-value]
    except AgentsDbError:
        raise
    except Exception as exc:
        err = str(exc)
        if _is_rls_error(err):
            raise _rls_error() from exc
        logger.error("update_post_call_actions failed: %s", exc)
        raise AgentsDbError(err) from exc


def _is_successful_call(row: dict) -> bool:
    status = str(row.get("call_status") or "completed").lower()
    if status in {"completed", "answered", "success"}:
        return True
    summary = str(row.get("summary") or "").lower()
    duration = int(row.get("duration_seconds") or 0)
    return duration > 0 and status not in {"failed", "no_answer", "busy", "cancelled"}


async def get_agent_stats(agent_id: str | UUID) -> dict:
    sb = await _supabase()
    if not await get_agent_row(agent_id):
        raise AgentsDbError("Agent not found", code="not_found")

    try:
        res = (
            sb.table("call_logs")
            .select(
                "id, duration_seconds, call_status, was_booked, estimated_cost_usd, "
                "created_at, sentiment, summary"
            )
            .eq("agent_id", str(agent_id))
            .order("created_at", desc=True)
            .limit(5000)
            .execute()
        )
        rows = res.data or []
    except Exception as exc:
        err = str(exc)
        if "agent_id" in err.lower() and ("pgrst204" in err or "schema cache" in err.lower()):
            return {
                "agent_id": str(agent_id),
                "total_calls": 0,
                "successful_calls": 0,
                "success_rate": 0.0,
                "avg_duration_seconds": 0,
                "total_cost_usd": 0.0,
                "bookings": 0,
                "last_call_at": None,
                "note": "agent_id column missing on call_logs — run migration v4",
            }
        logger.error("get_agent_stats failed: %s", exc)
        raise AgentsDbError(err) from exc

    total = len(rows)
    successful = sum(1 for r in rows if _is_successful_call(r))
    durations = [int(r.get("duration_seconds") or 0) for r in rows if int(r.get("duration_seconds") or 0) > 0]
    costs = [float(r.get("estimated_cost_usd") or 0) for r in rows if r.get("estimated_cost_usd") is not None]
    bookings = sum(
        1
        for r in rows
        if bool(r.get("was_booked"))
        or "confirm" in str(r.get("summary") or "").lower()
    )

    return {
        "agent_id": str(agent_id),
        "total_calls": total,
        "successful_calls": successful,
        "success_rate": round((successful / total) * 100, 1) if total else 0.0,
        "avg_duration_seconds": round(sum(durations) / len(durations)) if durations else 0,
        "total_cost_usd": round(sum(costs), 4),
        "bookings": bookings,
        "last_call_at": rows[0].get("created_at") if rows else None,
    }


async def apply_agent_patch(agent_id: str | UUID, updates: dict[str, Any]) -> FullAgentConfig:
    """Apply partial updates across agent, flow, call config, and post-call tables."""
    if not await get_agent_row(agent_id):
        raise AgentsDbError("Agent not found", code="not_found")

    if updates.get("agent"):
        await update_agent(agent_id, updates["agent"])
    if updates.get("conversational_flow"):
        await update_conversational_flow(agent_id, updates["conversational_flow"])
    if updates.get("call_configuration"):
        await update_call_configuration(agent_id, updates["call_configuration"])
    if updates.get("post_call_action"):
        await update_post_call_actions(agent_id, updates["post_call_action"])

    config = await get_agent_config(agent_id)
    if not config:
        raise AgentsDbError("Agent not found", code="not_found")
    return config


async def fetch_agent_calls(agent_id: str | UUID, limit: int = 50) -> list[dict]:
    """Return recent call_logs for a specific agent."""
    sb = await _supabase()
    if not await get_agent_row(agent_id):
        raise AgentsDbError("Agent not found", code="not_found")

    try:
        res = (
            sb.table("call_logs")
            .select("*")
            .eq("agent_id", str(agent_id))
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data or []
    except Exception as exc:
        err = str(exc)
        if "agent_id" in err.lower() and ("pgrst204" in err or "schema cache" in err.lower()):
            return []
        logger.error("fetch_agent_calls failed: %s", exc)
        raise AgentsDbError(err) from exc