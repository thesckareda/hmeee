"""
Twenty CRM integration — sales follow-up for Speaksy voice leads.

Upserts people, opportunities, notes, and tasks via Twenty REST API.
Caches Twenty record IDs per phone in the KV store for idempotent sync.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from urllib.parse import quote

from phone_utils import normalize_phone_number, phone_match_variants

logger = logging.getLogger(__name__)

STAGE_TO_TWENTY: dict[str, str] = {
    "cold": "NEW",
    "aware": "SCREENING",
    "interested": "SCREENING",
    "viewing": "MEETING",
    "hot": "PROPOSAL",
    "offer": "PROPOSAL",
    "closed": "CUSTOMER",
    "lost": "NEW",
    "unknown": "NEW",
    "lead": "NEW",
}

TASK_TITLES: dict[str, str] = {
    "viewing_confirm": "Confirm site visit",
    "offer_followup": "Follow up on offer",
    "nurture": "Nurture hot lead",
}

DEFAULT_TIMEOUT = 10.0
MAX_RETRIES = 2


@dataclass
class TwentyCRMConfig:
    api_url: str
    api_key: str
    ui_url: str = ""
    default_currency: str = "INR"
    enabled: bool = True

    @classmethod
    def from_env(cls) -> TwentyCRMConfig:
        api_url = (
            os.environ.get("TWENTY_API_URL")
            or os.environ.get("CRM_BASE_URL")
            or "http://localhost:3000"
        ).rstrip("/")
        api_key = os.environ.get("TWENTY_API_KEY") or os.environ.get("CRM_API_KEY") or ""
        ui_url = (os.environ.get("TWENTY_UI_URL") or api_url).rstrip("/")
        currency = os.environ.get("TWENTY_DEFAULT_CURRENCY", "INR")
        crm_type = os.environ.get("CRM_TYPE", "none").strip().lower()
        enabled = crm_type == "twenty" and bool(api_key)
        return cls(
            api_url=api_url,
            api_key=api_key,
            ui_url=ui_url,
            default_currency=currency,
            enabled=enabled,
        )


@dataclass
class TwentySyncResult:
    success: bool
    person_id: str | None = None
    opportunity_id: str | None = None
    task_id: str | None = None
    note_id: str | None = None
    message: str = ""
    errors: list[str] = field(default_factory=list)


def _phone_to_twenty(phone: str) -> dict[str, str]:
    normalized = normalize_phone_number(phone)
    digits = re.sub(r"\D", "", normalized)
    if normalized.startswith("+91") and len(digits) >= 12:
        return {
            "primaryPhoneNumber": digits[-10:],
            "primaryPhoneCallingCode": "+91",
            "primaryPhoneCountryCode": "IN",
        }
    if normalized.startswith("+"):
        for code_len in (3, 2, 1):
            prefix = normalized[1 : 1 + code_len]
            if digits.startswith(prefix):
                return {
                    "primaryPhoneNumber": digits[len(prefix) :],
                    "primaryPhoneCallingCode": f"+{prefix}",
                    "primaryPhoneCountryCode": "US" if prefix == "1" else "",
                }
    return {
        "primaryPhoneNumber": digits,
        "primaryPhoneCallingCode": "",
        "primaryPhoneCountryCode": "",
    }


def _split_name(full_name: str) -> dict[str, str]:
    text = (full_name or "").strip() or "Unknown Lead"
    parts = text.split(" ", 1)
    if len(parts) == 1:
        return {"firstName": parts[0], "lastName": ""}
    return {"firstName": parts[0], "lastName": parts[1]}


def _parse_amount_micros(amount: str | None, currency: str) -> dict[str, Any] | None:
    if not amount:
        return None
    raw = re.sub(r"[^\d.]", "", str(amount))
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    if value < 1_000_000:
        value *= 1_000_000
    return {"amountMicros": int(value), "currencyCode": currency}


def _opportunity_name(update) -> str:
    listing = getattr(update, "offer_listing_id", None) or ""
    phone = getattr(update, "phone", "") or ""
    stage = getattr(update, "deal_stage", "lead")
    base = f"Speaksy — {stage.replace('_', ' ').title()}"
    if listing:
        return f"{base} ({listing})"
    suffix = phone[-4:] if len(phone) >= 4 else phone
    return f"{base} — {suffix}"


def _resolve_followup_task(update) -> tuple[str, str]:
    if getattr(update, "viewing_scheduled", False):
        due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        return "viewing_confirm", due
    if getattr(update, "offer_intent", False):
        due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        return "offer_followup", due
    score = float(getattr(update, "intent_score", 0) or 0)
    stage = str(getattr(update, "deal_stage", "") or "")
    if score >= 7.0 or stage in {"hot", "offer", "viewing"}:
        due = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
        return "nurture", due
    return "none", ""


class TwentyCRMClient:
    def __init__(self, config: TwentyCRMConfig | None = None) -> None:
        self.config = config or TwentyCRMConfig.from_env()

    @property
    def is_configured(self) -> bool:
        return bool(self.config.enabled and self.config.api_key)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> dict[str, Any]:
        import httpx

        url = f"{self.config.api_url}/rest{path}"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                    resp = await client.request(
                        method,
                        url,
                        headers=headers,
                        json=json_body,
                        params=params,
                    )
                    resp.raise_for_status()
                    return resp.json() if resp.content else {}
            except Exception as exc:
                last_error = exc
                if attempt < MAX_RETRIES:
                    continue
                break
        raise RuntimeError(f"Twenty request failed: {last_error}")

    async def health_check(self) -> dict[str, Any]:
        import httpx

        api_ok = False
        authenticated = False
        auth_error = ""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.config.api_url}/healthz")
                api_ok = resp.status_code == 200
        except Exception as exc:
            auth_error = str(exc)
        if self.is_configured:
            try:
                await self._request("GET", "/people", params={"limit": 1})
                authenticated = True
            except Exception as exc:
                auth_error = str(exc)
        return {
            "api_ok": api_ok,
            "authenticated": authenticated,
            "auth_error": auth_error,
        }

    async def _kv_get(self, key: str) -> str | None:
        from re_voice_memory.kv_store import kv_get

        val = await kv_get(key)
        return str(val) if val else None

    async def _kv_set(self, key: str, value: str) -> None:
        from re_voice_memory.kv_store import kv_set

        await kv_set(key, value, ttl_seconds=31536000)

    def _kv_person_key(self, phone: str) -> str:
        return f"twenty:person:{normalize_phone_number(phone)}"

    def _kv_opportunity_key(self, phone: str) -> str:
        return f"twenty:opportunity:{normalize_phone_number(phone)}"

    async def find_person_id(self, phone: str) -> str | None:
        cached = await self._kv_get(self._kv_person_key(phone))
        if cached:
            return cached
        for variant in phone_match_variants(phone):
            payload = await self._graphql_search(variant)
            person_id = payload.get("person_id")
            if person_id:
                await self._kv_set(self._kv_person_key(phone), person_id)
                return person_id
        return None

    async def _graphql_search(self, query: str) -> dict[str, Any]:
        import httpx

        gql = """
        query SearchPeople($searchInput: String!, $limit: Int) {
          search(
            searchInput: $searchInput
            limit: $limit
            includedObjectNameSingulars: ["person"]
          ) {
            edges {
              node {
                recordId
                objectNameSingular
              }
            }
          }
        }
        """
        try:
            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                resp = await client.post(
                    f"{self.config.api_url}/graphql",
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "query": gql,
                        "variables": {"searchInput": query, "limit": 5},
                    },
                )
                resp.raise_for_status()
                data = resp.json().get("data", {}).get("search", {}).get("edges", [])
                for edge in data:
                    node = edge.get("node", {})
                    if node.get("objectNameSingular") == "person" and node.get("recordId"):
                        return {"person_id": node["recordId"]}
        except Exception as exc:
            logger.warning("[TWENTY] search failed for %s: %s", query, exc)
        return {}

    @staticmethod
    def _extract_record(payload: dict[str, Any], *keys: str) -> dict[str, Any] | None:
        data = payload.get("data") or payload
        for key in keys:
            record = data.get(key) if isinstance(data, dict) else None
            if isinstance(record, dict):
                return record
        if isinstance(data, dict) and data.get("id"):
            return data
        return None

    async def upsert_person(self, update) -> str:
        phone = normalize_phone_number(getattr(update, "phone", "") or "")
        if not phone:
            raise ValueError("Phone required for Twenty person sync")
        person_id = await self.find_person_id(phone)
        body = {
            "name": _split_name(getattr(update, "caller_name", "") or ""),
            "phones": _phone_to_twenty(phone),
            "phone": phone,
        }
        if person_id:
            await self._request("PATCH", f"/people/{person_id}", json_body=body)
            return person_id
        payload = await self._request("POST", "/people", json_body=body)
        record = self._extract_record(payload, "createPerson")
        person_id = (record or {}).get("id")
        if not person_id:
            raise RuntimeError("Twenty person create returned no id")
        await self._kv_set(self._kv_person_key(phone), person_id)
        return person_id

    async def upsert_opportunity(self, person_id: str, update) -> str:
        phone = normalize_phone_number(getattr(update, "phone", "") or "")
        cached = await self._kv_get(self._kv_opportunity_key(phone))
        stage = STAGE_TO_TWENTY.get(
            str(getattr(update, "deal_stage", "unknown") or "unknown"),
            "NEW",
        )
        body: dict[str, Any] = {
            "name": _opportunity_name(update),
            "stage": stage,
            "pointOfContactId": person_id,
        }
        amount = _parse_amount_micros(
            getattr(update, "offer_amount", None),
            self.config.default_currency,
        )
        if amount:
            body["amount"] = amount
        opportunity_id = cached
        if opportunity_id:
            try:
                await self._request(
                    "PATCH",
                    f"/opportunities/{opportunity_id}",
                    json_body=body,
                )
                return opportunity_id
            except Exception as exc:
                logger.warning("[TWENTY] opportunity patch failed, recreating: %s", exc)
        payload = await self._request("POST", "/opportunities", json_body=body)
        record = self._extract_record(payload, "createOpportunity")
        opportunity_id = (record or {}).get("id")
        if not opportunity_id:
            raise RuntimeError("Twenty opportunity create returned no id")
        await self._kv_set(self._kv_opportunity_key(phone), opportunity_id)
        return opportunity_id

    async def create_call_note(
        self,
        person_id: str,
        opportunity_id: str,
        update,
    ) -> str | None:
        notes = (getattr(update, "notes", "") or "").strip()
        if not notes:
            return None
        title = f"Speaksy call — {(getattr(update, 'call_date', '') or '')[:10]}"
        body = {
            "title": title[:255],
            "bodyV2": {"markdown": notes[:4000]},
        }
        try:
            payload = await self._request("POST", "/notes", json_body=body)
            record = self._extract_record(payload, "createNote")
            note_id = (record or {}).get("id")
            if note_id and opportunity_id:
                await self._request(
                    "POST",
                    "/noteTargets",
                    json_body={
                        "noteId": note_id,
                        "targetOpportunityId": opportunity_id,
                    },
                )
            return note_id
        except Exception as exc:
            logger.warning("[TWENTY] note create failed: %s", exc)
            return None

    async def create_followup_task(
        self,
        person_id: str,
        opportunity_id: str,
        update,
        task_type: str,
        due_date: str,
    ) -> str | None:
        if task_type == "none":
            return None
        title = TASK_TITLES.get(task_type, "Follow up lead")
        notes = (getattr(update, "notes", "") or "")[:200]
        body: dict[str, Any] = {
            "title": title,
            "status": "TODO",
            "dueAt": due_date,
        }
        if notes:
            body["bodyV2"] = {"markdown": notes}
        try:
            payload = await self._request("POST", "/tasks", json_body=body)
            record = self._extract_record(payload, "createTask")
            task_id = (record or {}).get("id")
            if task_id and opportunity_id:
                await self._request(
                    "POST",
                    "/taskTargets",
                    json_body={
                        "taskId": task_id,
                        "targetOpportunityId": opportunity_id,
                    },
                )
            return task_id
        except Exception as exc:
            logger.warning("[TWENTY] task create failed: %s", exc)
            return None

    async def list_open_tasks(self, limit: int = 20) -> list[dict[str, Any]]:
        try:
            payload = await self._request(
                "GET",
                "/tasks",
                params={
                    "limit": limit,
                    "filter": 'status[eq]:"TODO"',
                    "order_by": "dueAt[AscNullsLast]",
                },
            )
            tasks = (payload.get("data") or {}).get("tasks") or []
            return [
                {
                    "id": t.get("id"),
                    "title": t.get("title"),
                    "status": t.get("status"),
                    "dueAt": t.get("dueAt"),
                    "url": f"{self.config.ui_url}/object/task/{quote(str(t.get('id') or ''), safe='')}",
                }
                for t in tasks
                if t.get("id")
            ]
        except Exception as exc:
            logger.warning("[TWENTY] list tasks failed: %s", exc)
            return []

    def person_url(self, person_id: str) -> str:
        return f"{self.config.ui_url}/object/person/{quote(person_id, safe='')}"

    async def get_cached_opportunity_id(self, phone: str) -> str | None:
        return await self._kv_get(self._kv_opportunity_key(phone))

    async def sync_lead(self, update) -> TwentySyncResult:
        if not self.is_configured:
            return TwentySyncResult(success=False, message="Twenty CRM not configured")
        result = TwentySyncResult(success=False)
        try:
            person_id = await self.upsert_person(update)
            result.person_id = person_id
            opportunity_id = await self.upsert_opportunity(person_id, update)
            result.opportunity_id = opportunity_id
            result.note_id = await self.create_call_note(person_id, opportunity_id, update)
            task_type, due_date = _resolve_followup_task(update)
            if task_type != "none":
                result.task_id = await self.create_followup_task(
                    person_id,
                    opportunity_id,
                    update,
                    task_type,
                    due_date,
                )
            result.success = True
            result.message = "Synced to Twenty CRM"
            logger.info(
                "[TWENTY] Synced %s → person=%s opp=%s task=%s",
                getattr(update, "phone", ""),
                person_id,
                opportunity_id,
                result.task_id,
            )
        except Exception as exc:
            result.message = str(exc)
            result.errors.append(str(exc))
            logger.warning("[TWENTY] sync failed: %s", exc)
        return result


_client: TwentyCRMClient | None = None


def get_twenty_client() -> TwentyCRMClient:
    global _client
    if _client is None:
        _client = TwentyCRMClient()
    return _client


async def sync_lead_to_twenty(update) -> bool:
    """Push a CRMUpdate to Twenty. Never raises."""
    client = get_twenty_client()
    result = await client.sync_lead(update)
    try:
        from re_voice_memory.metrics import record_crm_sync

        record_crm_sync("twenty", result.success)
    except Exception:
        pass
    return result.success