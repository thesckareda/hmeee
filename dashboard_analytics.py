"""Analytics, leads, and reporting helpers for the Speaksy dashboard."""

from __future__ import annotations

import csv
import io
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from phone_utils import is_demo_phone, normalize_phone_number

logger = logging.getLogger("dashboard-analytics")

IST = timezone(timedelta(hours=5, minutes=30))
AVG_DEAL_VALUE_INR = 250_000

SUCCESS_KEYWORDS = (
    "interested",
    "site visit",
    "offer",
    "booking",
    "booked",
    "confirm",
    "appointment",
)

OUTCOME_LABELS = {
    "booked": "Booked",
    "interested": "Interested",
    "follow_up": "Follow-up needed",
    "not_interested": "Not interested",
    "unknown": "Unknown",
}

AREA_KEYWORDS = [
    "Bandra", "Andheri", "Powai", "Juhu", "Worli", "Lower Parel", "Colaba",
    "Malad", "Goregaon", "Thane", "Navi Mumbai", "BKC", "Chembur", "Vashi",
    "Indiranagar", "Koramangala", "Whitefield", "HSR", "Jayanagar", "MG Road",
    "Gurgaon", "Noida", "Dwarka", "Saket", "Connaught Place", "Greater Kailash",
    "Hauz Khas", "Vasant Kunj", "Cyber City", "Sector 62",
]

TONE_PRESETS = {
    "professional": {
        "label": "Professional",
        "description": "Formal, trust-building tone",
        "instruction": "Use a formal, professional tone. Build trust through expertise.",
    },
    "friendly": {
        "label": "Friendly",
        "description": "Warm and conversational",
        "instruction": "Be warm, conversational, and personable. Use the caller's name naturally.",
    },
    "aggressive_closer": {
        "label": "Aggressive Closer",
        "description": "High-urgency sales approach",
        "instruction": "Be high-energy and create urgency. Use time-limited offers and social proof.",
    },
}

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def now_ist() -> datetime:
    return datetime.now(IST)


def parse_row_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        return dt.astimezone(IST)
    except Exception:
        if len(raw) >= 10:
            try:
                return datetime.strptime(raw[:10], "%Y-%m-%d").replace(tzinfo=IST)
            except Exception:
                return None
    return None


def ist_day_start(dt: datetime | None = None) -> datetime:
    ref = (dt or now_ist()).astimezone(IST)
    return ref.replace(hour=0, minute=0, second=0, microsecond=0)


def filter_valid_rows(rows: list[dict]) -> list[dict]:
    out = []
    for row in rows or []:
        phone = row.get("phone_number") or row.get("from_number") or ""
        if is_demo_phone(phone):
            continue
        out.append(row)
    return out


def row_summary_text(row: dict) -> str:
    return str(row.get("call_summary") or row.get("summary") or "").strip()


def row_summary_lower(row: dict) -> str:
    return row_summary_text(row).lower()


def is_successful_conversation(row: dict) -> bool:
    if bool(row.get("was_booked")):
        return True
    summary = row_summary_lower(row)
    purpose = str(row.get("call_purpose") or "").lower()
    if any(k in summary for k in SUCCESS_KEYWORDS):
        return True
    if purpose in {"site_visit", "offer", "booking", "appointment"}:
        return True
    return False


def classify_outcome(row: dict) -> str:
    if bool(row.get("was_booked")):
        return "booked"
    summary = row_summary_lower(row)
    duration = int(row.get("duration_seconds") or 0)
    if "not interested" in summary or "hang up" in summary or "hung up" in summary:
        return "not_interested"
    if duration < 20 and not summary:
        return "not_interested"
    if any(k in summary for k in ("follow-up", "follow up", "callback", "call back", "will call back")):
        return "follow_up"
    if duration >= 30 and summary:
        return "interested"
    if duration > 0:
        return "interested"
    return "unknown"


def next_action_for_outcome(outcome: str) -> str:
    return {
        "booked": "Confirm appointment details",
        "interested": "Send WhatsApp listing",
        "follow_up": "Schedule callback",
        "not_interested": "Archive lead",
    }.get(outcome, "Review call transcript")


def extract_what_they_want(row: dict) -> str:
    text = row_summary_text(row) or str(row.get("call_purpose") or "").strip()
    if not text:
        return "General enquiry"
    first_sentence = re.split(r"[.!?\n]", text)[0].strip()
    snippet = first_sentence or text
    return snippet[:120] + ("…" if len(snippet) > 120 else "")


def compute_urgency_score(row: dict, ref: datetime | None = None) -> int:
    summary = row_summary_lower(row)
    duration = int(row.get("duration_seconds") or 0)
    score = 50
    if any(k in summary for k in ("offer", "ready to buy", "finalize", "finalise")):
        score += 30
    if any(k in summary for k in ("site visit", "want to see", "visit property")):
        score += 20
    if any(k in summary for k in ("interested", "budget")):
        score += 15
    if bool(row.get("was_booked")):
        score += 10
    if "not interested" in summary:
        score -= 20
    if duration < 30:
        score -= 10

    created = parse_row_datetime(row.get("created_at"))
    if created:
        hours = (ref or now_ist()) - created
        days_old = max(0, hours.total_seconds() / 86400)
        score -= min(40, int(days_old * 5))
    return max(0, min(100, score))


def urgency_label(score: int) -> str:
    if score >= 80:
        return "High intent"
    if score >= 60:
        return "Warm"
    return "Watching"


def suggested_hot_action(row: dict) -> str:
    summary = row_summary_lower(row)
    if "site visit" in summary:
        return "Schedule Site Visit"
    if "offer" in summary or "ready to buy" in summary:
        return "Mark as Booked"
    if bool(row.get("was_booked")):
        return "Confirm Booking"
    return "Send WhatsApp Listing"


def extract_listing_ids(text: str) -> list[str]:
    if not text:
        return []
    return re.findall(r"[A-Z]{2,}-\d{3,}", text.upper())


def extract_areas(summary: str) -> list[str]:
    found = []
    for area in AREA_KEYWORDS:
        if area.lower() in summary.lower():
            found.append(area)
    return found


def dedupe_by_phone(rows: list[dict]) -> tuple[list[dict], dict[str, int]]:
    """Keep most recent row per phone; return call counts per phone."""
    by_phone: dict[str, dict] = {}
    counts: dict[str, int] = {}
    for row in rows:
        phone = normalize_phone_number(row.get("phone_number") or row.get("from_number") or "")
        if not phone:
            phone = f"unknown-{row.get('id', '')}"
        counts[phone] = counts.get(phone, 0) + 1
        existing = by_phone.get(phone)
        if not existing:
            by_phone[phone] = row
            continue
        existing_dt = parse_row_datetime(existing.get("created_at"))
        row_dt = parse_row_datetime(row.get("created_at"))
        if row_dt and (not existing_dt or row_dt > existing_dt):
            by_phone[phone] = row
    return list(by_phone.values()), counts


def agent_name_map(agents: list[Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for agent in agents or []:
        agent_id = str(getattr(agent, "id", "") or (agent.get("id") if isinstance(agent, dict) else ""))
        name = getattr(agent, "name", None) or (agent.get("name") if isinstance(agent, dict) else None)
        if agent_id:
            mapping[agent_id] = name or "Agent"
    return mapping


def build_lead_record(row: dict, call_count: int, agents: dict[str, str]) -> dict:
    outcome = classify_outcome(row)
    agent_id = str(row.get("agent_id") or "")
    return {
        "id": str(row.get("id") or ""),
        "caller_name": row.get("caller_name") or "Unknown Caller",
        "phone_number": row.get("phone_number") or row.get("from_number") or "",
        "last_call_date": row.get("created_at") or "",
        "what_they_want": extract_what_they_want(row),
        "outcome": outcome,
        "outcome_label": OUTCOME_LABELS.get(outcome, outcome.title()),
        "next_action": next_action_for_outcome(outcome),
        "call_count": call_count,
        "recording_url": row.get("recording_url") or "",
        "has_transcript": bool(row.get("transcript")),
        "agent_name": agents.get(agent_id, "Default Agent"),
        "duration_seconds": int(row.get("duration_seconds") or 0),
    }


LIVE_SALES_URGENCY_THRESHOLD = 60


def _profile_to_call_row(phone: str, profile: Any) -> dict:
    """Synthesize a dashboard call row from a lead profile when logs are unavailable."""
    areas = ", ".join(getattr(profile, "preferred_areas", []) or []) or "property"
    budget_min = getattr(profile, "budget_min", None)
    budget_max = getattr(profile, "budget_max", None)
    budget_bits = []
    if budget_min:
        budget_bits.append(f"min Rs{budget_min:,}")
    if budget_max:
        budget_bits.append(f"max Rs{budget_max:,}")
    budget_str = " ".join(budget_bits) if budget_bits else ""
    stage = getattr(profile, "deal_stage", "unknown")
    summary_parts = [f"Interested in {areas}"]
    if budget_str:
        summary_parts.append(f"budget {budget_str}")
    summary_parts.append(f"stage {stage}")
    if stage in ("intent", "purchase", "booked"):
        summary_parts.append("site visit interest")
    return {
        "phone_number": phone,
        "from_number": phone,
        "call_summary": ". ".join(summary_parts),
        "duration_seconds": 90,
        "was_booked": stage == "booked",
        "created_at": getattr(profile, "last_contact_date", None) or now_ist().isoformat(),
        "call_purpose": "site_visit" if stage in ("intent", "booked") else "",
    }


def _latest_row_for_phone(phone: str, call_rows: list[dict] | None) -> dict | None:
    if not call_rows or not phone:
        return None
    normalized = normalize_phone_number(phone)
    if not normalized:
        return None
    latest: dict | None = None
    latest_dt: datetime | None = None
    for row in call_rows:
        row_phone = normalize_phone_number(row.get("phone_number") or row.get("from_number") or "")
        if row_phone != normalized:
            continue
        row_dt = parse_row_datetime(row.get("created_at"))
        if latest is None or (row_dt and (not latest_dt or row_dt > latest_dt)):
            latest = row
            latest_dt = row_dt
    return latest


def build_live_sales_context(
    phone: str,
    profile: Any | None,
    call_rows: list[dict] | None = None,
) -> str:
    """
    Build a live prompt block from dashboard urgency analytics.

    Uses compute_urgency_score / build_hot_lead_record logic so returning HOT
    leads get proactive-close instructions even before intent_score is refreshed.
    """
    if not phone or phone == "unknown":
        return ""
    if profile is None:
        return ""

    call_count = int(getattr(profile, "call_count", 0) or 0)
    if call_count <= 0:
        return ""

    row = _latest_row_for_phone(phone, call_rows) or _profile_to_call_row(phone, profile)
    ref = now_ist()
    hot = build_hot_lead_record(row, agents={}, ref=ref)
    score = int(hot.get("urgency_score") or 0)
    if score < LIVE_SALES_URGENCY_THRESHOLD:
        return ""

    intent_summary = hot.get("intent_summary") or "Prior interest noted"
    u_label = hot.get("urgency_label") or urgency_label(score)
    suggested = hot.get("suggested_action") or suggested_hot_action(row)
    hours_since = hot.get("hours_since_call")
    recency = f" ({hours_since}h since last call)" if hours_since is not None else ""

    return (
        f"[LIVE SALES CONTEXT — {u_label.upper()} (urgency {score}/100){recency}]\n"
        f"Lead intent: {intent_summary}\n"
        f"Recommended action: {suggested}\n"
        "Lead with urgency, not discovery. Skip generic qualifying — reference prior interest "
        "and propose a concrete site visit slot in your first substantive reply."
    )


def build_hot_lead_record(row: dict, agents: dict[str, str], ref: datetime | None = None) -> dict:
    ref = ref or now_ist()
    score = compute_urgency_score(row, ref)
    created = parse_row_datetime(row.get("created_at"))
    hours_since = 0.0
    if created:
        hours_since = round((ref - created).total_seconds() / 3600, 1)
    agent_id = str(row.get("agent_id") or "")
    return {
        "id": str(row.get("id") or ""),
        "caller_name": row.get("caller_name") or "Unknown Caller",
        "phone_number": row.get("phone_number") or row.get("from_number") or "",
        "intent_summary": extract_what_they_want(row),
        "urgency_score": score,
        "urgency_label": urgency_label(score),
        "hours_since_call": hours_since,
        "suggested_action": suggested_hot_action(row),
        "recording_url": row.get("recording_url") or "",
        "agent_name": agents.get(agent_id, "Default Agent"),
    }


def conversion_rate(successful: int, total: int) -> float:
    if not total:
        return 0.0
    return round((successful / total) * 100, 1)


def rows_in_range(rows: list[dict], start: datetime, end: datetime) -> list[dict]:
    filtered = []
    for row in rows:
        dt = parse_row_datetime(row.get("created_at"))
        if dt and start <= dt < end:
            filtered.append(row)
    return filtered


def summarize_today(rows: list[dict], agents: list[Any]) -> dict:
    ref = now_ist()
    today_start = ist_day_start(ref)
    week_start = today_start - timedelta(days=ref.weekday())
    month_start = today_start.replace(day=1)

    today_rows = rows_in_range(rows, today_start, ref + timedelta(seconds=1))
    week_rows = rows_in_range(rows, week_start, ref + timedelta(seconds=1))

    today_success = sum(1 for r in today_rows if is_successful_conversation(r))
    today_total = len(today_rows)

    agent_bookings: dict[str, int] = {}
    for row in today_rows:
        if bool(row.get("was_booked")):
            aid = str(row.get("agent_id") or "default")
            agent_bookings[aid] = agent_bookings.get(aid, 0) + 1

    names = agent_name_map(agents)
    top_agent = None
    if agent_bookings:
        best_id = max(agent_bookings, key=agent_bookings.get)
        top_agent = {
            "name": names.get(best_id, "Default Agent"),
            "agent_id": best_id,
            "conversions": agent_bookings[best_id],
        }

    followup_cutoff = ref - timedelta(hours=48)
    hot_waiting = 0
    for row in rows:
        dt = parse_row_datetime(row.get("created_at"))
        if not dt or dt < followup_cutoff:
            continue
        summary = row_summary_lower(row)
        if bool(row.get("was_booked")):
            continue
        if any(k in summary for k in ("follow-up", "follow up", "callback", "high intent", "interested")):
            hot_waiting += 1

    alerts = []
    if hot_waiting:
        alerts.append({
            "type": "hot_lead",
            "message": f"{hot_waiting} high-intent leads waiting for follow-up",
            "count": hot_waiting,
        })

    return {
        "today": {
            "total_calls": today_total,
            "week_calls": len(week_rows),
            "successful_conversations": today_success,
            "conversion_rate": conversion_rate(today_success, today_total),
            "money_impact_inr": today_success * AVG_DEAL_VALUE_INR,
            "top_agent": top_agent,
        },
        "alerts": alerts,
        "month_start": month_start.isoformat(),
    }


def summarize_performance(rows: list[dict]) -> dict:
    ref = now_ist()
    today_start = ist_day_start(ref)
    month_start = today_start.replace(day=1)
    chart_start = today_start - timedelta(days=13)

    chart_days: dict[str, dict] = {}
    for i in range(14):
        day = (chart_start + timedelta(days=i)).strftime("%Y-%m-%d")
        chart_days[day] = {"date": day, "calls": 0, "converted": 0}

    site_visits = 0
    offers = 0
    total_duration = 0
    connected = 0
    successful = 0
    day_bookings: dict[str, int] = {}
    hour_bookings: dict[int, int] = {}

    for row in rows:
        dt = parse_row_datetime(row.get("created_at"))
        if not dt:
            continue
        day_key = dt.strftime("%Y-%m-%d")
        if day_key in chart_days:
            chart_days[day_key]["calls"] += 1
            if is_successful_conversation(row):
                chart_days[day_key]["converted"] += 1

        if dt >= month_start:
            summary = row_summary_lower(row)
            purpose = str(row.get("call_purpose") or "").lower()
            if "site visit" in summary or purpose == "site_visit":
                site_visits += 1
            if "offer" in summary or purpose == "offer":
                offers += 1

        duration = int(row.get("duration_seconds") or 0)
        if duration > 0:
            total_duration += duration
            connected += 1
        if is_successful_conversation(row):
            successful += 1

        if bool(row.get("was_booked")):
            dow = row.get("call_day_of_week")
            if dow is None:
                dow = DAY_NAMES[dt.weekday()]
            day_bookings[str(dow)] = day_bookings.get(str(dow), 0) + 1
            hour = row.get("call_hour")
            if hour is None:
                hour = dt.hour
            try:
                hour_bookings[int(hour)] = hour_bookings.get(int(hour), 0) + 1
            except Exception:
                pass

    total = len(rows)
    best_days = sorted(day_bookings, key=day_bookings.get, reverse=True)[:3]
    best_hours = sorted(hour_bookings, key=hour_bookings.get, reverse=True)[:3]

    return {
        "weekly_conversion_chart": list(chart_days.values()),
        "site_visits_this_month": site_visits,
        "offers_received_this_month": offers,
        "avg_call_duration_seconds": round(total_duration / connected) if connected else 0,
        "overall_success_rate": conversion_rate(successful, total),
        "best_days": best_days,
        "best_hours": best_hours,
    }


def summarize_campaigns(rows: list[dict], agents: list[Any]) -> dict:
    listing_counts: dict[str, int] = {}
    area_counts: dict[str, int] = {}

    for row in rows:
        transcript = str(row.get("transcript") or "")
        summary = row_summary_text(row)
        for lid in extract_listing_ids(transcript):
            listing_counts[lid] = listing_counts.get(lid, 0) + 1
        for area in extract_areas(summary):
            area_counts[area] = area_counts.get(area, 0) + 1

    top_listings = sorted(
        [{"listing_id": k, "mentions": v, "interest_score": min(100, v * 7 + 10)} for k, v in listing_counts.items()],
        key=lambda x: x["mentions"],
        reverse=True,
    )[:10]

    top_areas = sorted(
        [{"area": k, "mentions": v} for k, v in area_counts.items()],
        key=lambda x: x["mentions"],
        reverse=True,
    )[:10]

    names = agent_name_map(agents)
    agent_stats: dict[str, dict] = {}
    for row in rows:
        aid = str(row.get("agent_id") or "default")
        bucket = agent_stats.setdefault(aid, {"calls": 0, "converted": 0, "name": names.get(aid, "Agent")})
        bucket["calls"] += 1
        if is_successful_conversation(row):
            bucket["converted"] += 1

    ranked = sorted(
        [
            {
                "agent_id": aid,
                "name": data["name"],
                "conversion_rate": conversion_rate(data["converted"], data["calls"]),
                "calls": data["calls"],
            }
            for aid, data in agent_stats.items()
            if data["calls"] >= 3
        ],
        key=lambda x: x["conversion_rate"],
        reverse=True,
    )

    ab_test = {"tone_a": None, "tone_b": None, "winner": None, "delta_pct": 0.0}
    if len(ranked) >= 2:
        a, b = ranked[0], ranked[1]
        ab_test = {
            "tone_a": {"name": a["name"], "conversion_rate": a["conversion_rate"], "calls": a["calls"]},
            "tone_b": {"name": b["name"], "conversion_rate": b["conversion_rate"], "calls": b["calls"]},
            "winner": a["name"],
            "delta_pct": round(a["conversion_rate"] - b["conversion_rate"], 1),
        }
    elif len(ranked) == 1:
        a = ranked[0]
        ab_test["tone_a"] = {"name": a["name"], "conversion_rate": a["conversion_rate"], "calls": a["calls"]}

    return {
        "top_listings": top_listings,
        "top_areas": top_areas,
        "ab_test": ab_test,
    }


def weekly_report(rows: list[dict]) -> dict:
    ref = now_ist()
    end = ist_day_start(ref) + timedelta(days=1)
    start = end - timedelta(days=7)
    prev_start = start - timedelta(days=7)

    week_rows = rows_in_range(rows, start, end)
    prev_rows = rows_in_range(rows, prev_start, start)

    week_success = sum(1 for r in week_rows if is_successful_conversation(r))
    prev_success = sum(1 for r in prev_rows if is_successful_conversation(r))
    week_total = len(week_rows)
    prev_total = len(prev_rows)

    site_visits = sum(
        1 for r in week_rows
        if "site visit" in row_summary_lower(r) or str(r.get("call_purpose") or "").lower() == "site_visit"
    )
    offers = sum(
        1 for r in week_rows
        if "offer" in row_summary_lower(r) or str(r.get("call_purpose") or "").lower() == "offer"
    )

    property_counts: dict[str, int] = {}
    for row in week_rows:
        for lid in extract_listing_ids(str(row.get("transcript") or "")):
            property_counts[lid] = property_counts.get(lid, 0) + 1
    top_properties = [k for k, _ in sorted(property_counts.items(), key=property_counts.get, reverse=True)[:2]]

    pending = sum(
        1 for r in week_rows
        if not bool(r.get("was_booked")) and any(
            k in row_summary_lower(r) for k in ("follow-up", "follow up", "callback")
        )
    )

    period = f"{start.strftime('%B %d')} – {(end - timedelta(days=1)).strftime('%B %d, %Y')}"
    conv = conversion_rate(week_success, week_total)
    prev_conv = conversion_rate(prev_success, prev_total)

    summary_text = (
        f"This week, your AI agent handled {week_total} calls. "
        f"{week_success} leads showed strong interest — {site_visits} booked site visits "
        f"and {offers} made formal offers. "
        f"Your conversion rate was {conv}%"
        + (f", up from {prev_conv}% last week" if prev_total else "")
        + ". "
    )
    if top_properties:
        summary_text += f"Top performing property: {top_properties[0]} with {property_counts[top_properties[0]]} enquiries. "
    if pending:
        summary_text += f"{pending} leads are still awaiting follow-up."

    return {
        "period": period,
        "generated_at": ref.isoformat(),
        "summary_text": summary_text.strip(),
        "kpis": {
            "total_calls": week_total,
            "successful": week_success,
            "site_visits_booked": site_visits,
            "offers_received": offers,
            "conversion_rate": conv,
            "prev_week_conversion_rate": prev_conv,
        },
        "top_properties": top_properties,
        "pending_followups": pending,
    }


def monthly_report(rows: list[dict], agents: list[Any]) -> dict:
    ref = now_ist()
    month_start = ist_day_start(ref).replace(day=1)
    if month_start.month == 12:
        month_end = month_start.replace(year=month_start.year + 1, month=1)
    else:
        month_end = month_start.replace(month=month_start.month + 1)

    month_rows = rows_in_range(rows, month_start, month_end)
    successful = sum(1 for r in month_rows if is_successful_conversation(r))
    site_visits = sum(
        1 for r in month_rows
        if "site visit" in row_summary_lower(r) or str(r.get("call_purpose") or "").lower() == "site_visit"
    )
    offers = sum(
        1 for r in month_rows
        if "offer" in row_summary_lower(r) or str(r.get("call_purpose") or "").lower() == "offer"
    )
    total_duration = sum(int(r.get("duration_seconds") or 0) for r in month_rows if int(r.get("duration_seconds") or 0) > 0)
    connected = sum(1 for r in month_rows if int(r.get("duration_seconds") or 0) > 0)

    week_buckets: dict[str, dict] = {}
    for row in month_rows:
        dt = parse_row_datetime(row.get("created_at"))
        if not dt:
            continue
        week_num = (dt.day - 1) // 7 + 1
        week_end_day = min(dt.day + (6 - (dt.day - 1) % 7), (month_end - timedelta(days=1)).day)
        label = f"{dt.strftime('%B')} {(week_num - 1) * 7 + 1}-{week_end_day}"
        bucket = week_buckets.setdefault(label, {"week": label, "calls": 0, "converted": 0})
        bucket["calls"] += 1
        if is_successful_conversation(row):
            bucket["converted"] += 1

    names = agent_name_map(agents)
    agent_stats: dict[str, dict] = {}
    for row in month_rows:
        aid = str(row.get("agent_id") or "default")
        bucket = agent_stats.setdefault(aid, {"agent_name": names.get(aid, "Agent"), "calls": 0, "conversions": 0})
        bucket["calls"] += 1
        if is_successful_conversation(row):
            bucket["conversions"] += 1

    top_agents = sorted(agent_stats.values(), key=lambda x: x["conversions"], reverse=True)[:5]
    total = len(month_rows)
    conv = conversion_rate(successful, total)
    period = month_start.strftime("%B %Y")

    summary_text = (
        f"In {period}, your AI team handled {total} calls with a {conv}% conversion rate. "
        f"{site_visits} site visits were scheduled and {offers} offers were received. "
        f"Estimated pipeline value: ₹{successful * AVG_DEAL_VALUE_INR:,}."
    )

    return {
        "period": period,
        "generated_at": ref.isoformat(),
        "kpis": {
            "total_calls": total,
            "successful": successful,
            "site_visits": site_visits,
            "offers": offers,
            "conversion_rate": conv,
            "avg_call_duration_seconds": round(total_duration / connected) if connected else 0,
            "estimated_revenue_inr": successful * AVG_DEAL_VALUE_INR,
        },
        "week_by_week": list(week_buckets.values()),
        "top_agents": top_agents,
        "summary_text": summary_text,
    }


def leads_to_csv(leads: list[dict], include_recording: bool = True) -> str:
    output = io.StringIO()
    fields = ["Name", "Phone", "Last Call Date", "What They Want", "Outcome", "Next Action", "Call Count", "Agent"]
    if include_recording:
        fields.append("Recording URL")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for lead in leads:
        row = {
            "Name": lead.get("caller_name", ""),
            "Phone": lead.get("phone_number", ""),
            "Last Call Date": lead.get("last_call_date", ""),
            "What They Want": lead.get("what_they_want", ""),
            "Outcome": lead.get("outcome_label", lead.get("outcome", "")),
            "Next Action": lead.get("next_action", ""),
            "Call Count": lead.get("call_count", 1),
            "Agent": lead.get("agent_name", ""),
        }
        if include_recording:
            row["Recording URL"] = lead.get("recording_url", "")
        writer.writerow(row)
    return output.getvalue()