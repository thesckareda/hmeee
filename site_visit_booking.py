"""
Site visit booking — WhatsApp confirmation + dashboard persistence.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime

from calendar_tools import _to_ist_datetime
from phone_utils import is_valid_phone_number, normalize_phone_number
from send_whatsapp_media import BROCHURE_ALIASES, BROCHURES, resolve_property

logger = logging.getLogger("site-visit-booking")

SITE_VISIT_TEXT_SIGNALS = (
    "site visit", "sitevisit", "property visit", "visit the property",
    "visit property", "see the property", "book a visit", "schedule a visit",
    "visit godrej", "visit trilight", "visit signature", "visit my home",
)

PROPERTY_PATTERNS: dict[str, list[str]] = {
    "godrej": [r"godrej", r"regal\s*pavilion"],
    "myhome": [r"my[\s-]*home", r"myhome", r"udyan"],
    "signature": [r"signature"],
    "housing": [r"housing"],
    "indroyal": [r"ind[\s-]*royal", r"indroyal"],
    "tricolt": [r"tricolt", r"tri[\s-]*colt"],
    "trilight": [r"trilight", r"tri[\s-]*light", r"thetrilight"],
    "trident": [r"trident", r"triednt"],
}

OFFICE_ADDRESS = (
    "VK Realty, Plot No 42, 2nd Floor, Jayabheri Enclave, "
    "Financial District, Gachibowli, Hyderabad 500032"
)
SITE_VISIT_HOURS = "10:00 AM – 6:00 PM, Monday to Saturday"


def normalize_site_visit_datetime(requested_datetime: str) -> str | None:
    """Normalize free-form datetime to ISO 8601 with IST offset."""
    dt = _to_ist_datetime(requested_datetime)
    return dt.isoformat() if dt else None


def format_readable_datetime(scheduled_at_iso: str) -> str:
    try:
        dt = datetime.fromisoformat(scheduled_at_iso)
        return dt.strftime("%A, %d %B %Y at %I:%M %p IST")
    except Exception:
        return scheduled_at_iso


def detect_property_in_text(text: str) -> str | None:
    lowered = (text or "").lower()
    for key, patterns in PROPERTY_PATTERNS.items():
        if any(re.search(pattern, lowered) for pattern in patterns):
            return key
    for alias, canonical in BROCHURE_ALIASES.items():
        if alias in lowered and canonical in BROCHURES:
            return canonical
    return None


def looks_like_site_visit(text: str) -> bool:
    lowered = (text or "").lower()
    if any(signal in lowered for signal in SITE_VISIT_TEXT_SIGNALS):
        return True
    return detect_property_in_text(lowered) is not None


def booking_intent_datetime_str(start_time_iso: str) -> str:
    dt = _to_ist_datetime(start_time_iso)
    if dt:
        return dt.strftime("%Y-%m-%d %H:%M")
    return start_time_iso


def resolve_visit_property(listing_id: str, property_name: str = "") -> dict:
    """Match listing/property name to catalog entry with maps link and brochure."""
    for candidate in (listing_id, property_name):
        if not candidate:
            continue
        resolved = resolve_property(candidate)
        if resolved:
            return resolved
    label = (property_name or listing_id or "Property").strip()
    return {
        "key": (listing_id or property_name or "property").strip().lower(),
        "label": label,
        "maps_url": "",
        "brochure_url": "",
        "has_brochure": False,
    }


def build_site_visit_whatsapp_message(
    caller_name: str,
    property_name: str,
    location: str,
    scheduled_at_iso: str,
    listing_id: str = "",
    maps_url: str = "",
) -> str:
    readable = format_readable_datetime(scheduled_at_iso)
    property_label = property_name or listing_id or "your selected property"
    location_line = f"\n📍 *Area:* {location}" if location else ""
    maps_line = f"\n🗺️ *Directions:* {maps_url}" if maps_url else ""

    return (
        f"✅ Hi {caller_name or 'there'}! Your *site visit is confirmed*.\n\n"
        f"🏠 *Property:* {property_label}{location_line}\n"
        f"📅 *Date & Time:* {readable}\n"
        f"🕐 *Visit hours:* {SITE_VISIT_HOURS}{maps_line}\n\n"
        f"A VK Realty consultant will meet you at the property.\n"
        f"Office: {OFFICE_ADDRESS}\n\n"
        f"We are also sending the property brochure separately if available.\n"
        f"Need to reschedule? Just call us back.\n\n"
        f"— VK Realty 🤖"
    )


def _dedupe_key(listing_id: str, scheduled_at_iso: str) -> str:
    return f"{listing_id.strip().lower()}:{scheduled_at_iso}"


async def _send_property_brochure(caller_phone: str, property_key: str) -> dict | None:
    if not property_key or not resolve_property(property_key):
        return None
    resolved = resolve_property(property_key)
    if not resolved or not resolved.get("has_brochure"):
        return None
    from send_whatsapp_media import send_brochure_via_whatsapp

    try:
        return await asyncio.to_thread(send_brochure_via_whatsapp, caller_phone, property_key)
    except Exception as exc:
        logger.warning("[SITE-VISIT] Brochure send failed for %s: %s", property_key, exc)
        return None


async def confirm_site_visit(
    caller_phone: str,
    caller_name: str,
    listing_id: str,
    requested_datetime: str,
    property_name: str = "",
    location: str = "",
    notes: str = "",
    room_id: str | None = None,
    scheduled_keys: set[str] | None = None,
) -> dict:
    """
    Send WhatsApp confirmation and persist site visit for the dashboard.
    Returns: {success, whatsapp_sent, brochure_sent, record, message}
    """
    normalized_phone = normalize_phone_number(caller_phone)
    if not is_valid_phone_number(normalized_phone):
        return {
            "success": False,
            "whatsapp_sent": False,
            "brochure_sent": False,
            "record": None,
            "message": "Invalid caller phone number for site visit confirmation.",
        }

    scheduled_at_iso = normalize_site_visit_datetime(requested_datetime)
    if not scheduled_at_iso:
        return {
            "success": False,
            "whatsapp_sent": False,
            "brochure_sent": False,
            "record": None,
            "message": f"Could not parse site visit datetime: {requested_datetime}",
        }

    property_info = resolve_visit_property(listing_id, property_name)
    listing_key = property_info["key"]
    dedupe = _dedupe_key(listing_key, scheduled_at_iso)
    if scheduled_keys is not None:
        if dedupe in scheduled_keys:
            return {
                "success": True,
                "whatsapp_sent": False,
                "brochure_sent": False,
                "record": None,
                "message": "Site visit already confirmed for this property and time.",
                "duplicate": True,
            }
        scheduled_keys.add(dedupe)

    effective_property = property_info["label"]
    maps_url = property_info.get("maps_url") or ""
    effective_location = (location or "").strip()
    effective_name = (caller_name or "").strip() or normalized_phone
    canonical_listing_id = property_info["key"]

    from notify import send_whatsapp

    whatsapp_body = build_site_visit_whatsapp_message(
        caller_name=effective_name,
        property_name=effective_property,
        location=effective_location,
        scheduled_at_iso=scheduled_at_iso,
        listing_id=canonical_listing_id,
        maps_url=maps_url,
    )
    whatsapp_sent = await asyncio.to_thread(send_whatsapp, normalized_phone, whatsapp_body)

    brochure_result = None
    brochure_sent = False
    if property_info.get("has_brochure"):
        brochure_result = await _send_property_brochure(normalized_phone, canonical_listing_id)
        brochure_sent = brochure_result is not None

    import db

    save_result = await db.save_site_visit(
        phone=normalized_phone,
        caller_name=effective_name,
        listing_id=canonical_listing_id,
        property_name=effective_property,
        location=effective_location,
        scheduled_at=scheduled_at_iso,
        room_id=room_id,
        notes=notes,
        whatsapp_sent=whatsapp_sent,
        maps_url=maps_url,
        brochure_sent=brochure_sent,
    )

    record = save_result.get("record")
    if not save_result.get("success"):
        if whatsapp_sent:
            return {
                "success": True,
                "whatsapp_sent": True,
                "brochure_sent": brochure_sent,
                "record": None,
                "message": "WhatsApp sent but dashboard save failed.",
            }
        return {
            "success": False,
            "whatsapp_sent": False,
            "brochure_sent": False,
            "record": None,
            "message": save_result.get("message", "Failed to save site visit."),
        }

    readable = format_readable_datetime(scheduled_at_iso)
    extras = []
    if maps_url:
        extras.append("directions link")
    if brochure_sent:
        extras.append("brochure")
    extras_text = f" with {' and '.join(extras)}" if extras else ""

    if whatsapp_sent:
        message = (
            f"Site visit confirmed for {effective_property} on {readable}. "
            f"Confirmation{extras_text} sent to WhatsApp at {normalized_phone}."
        )
    else:
        message = (
            f"Site visit saved for {effective_property} on {readable}, "
            "but WhatsApp confirmation could not be delivered."
        )

    logger.info(
        "[SITE-VISIT] Confirmed %s at %s for %s (whatsapp=%s brochure=%s maps=%s)",
        effective_property,
        scheduled_at_iso,
        normalized_phone,
        whatsapp_sent,
        brochure_sent,
        bool(maps_url),
    )
    return {
        "success": True,
        "whatsapp_sent": whatsapp_sent,
        "brochure_sent": brochure_sent,
        "maps_url": maps_url,
        "property_key": canonical_listing_id,
        "record": record,
        "message": message,
    }


async def confirm_site_visit_from_booking_intent(
    booking_intent: dict,
    scheduled_keys: set[str] | None = None,
    room_id: str | None = None,
    listing_id: str = "",
    property_name: str = "",
) -> dict | None:
    """Send site-visit WhatsApp from a save_booking_intent payload (Cal.com-independent)."""
    notes = str(booking_intent.get("notes") or "")
    detected = detect_property_in_text(notes) or detect_property_in_text(property_name)
    effective_listing = listing_id or detected or ""
    effective_property = property_name or notes or effective_listing

    if not effective_listing and not looks_like_site_visit(notes) and not property_name:
        return None

    return await confirm_site_visit(
        caller_phone=booking_intent.get("caller_phone") or "",
        caller_name=booking_intent.get("caller_name") or "",
        listing_id=effective_listing or effective_property,
        requested_datetime=booking_intent_datetime_str(booking_intent.get("start_time") or ""),
        property_name=effective_property if effective_property != effective_listing else "",
        location="",
        notes=notes,
        room_id=room_id,
        scheduled_keys=scheduled_keys,
    )


def list_bookable_properties() -> list[dict]:
    """Properties available for CRM / dashboard site visit scheduling."""
    from send_whatsapp_media import BROCHURES, SITE_ONLY_PROPERTIES

    items: list[dict] = []
    for key, info in {**BROCHURES, **SITE_ONLY_PROPERTIES}.items():
        items.append({
            "key": key,
            "label": info.get("label") or key.replace("_", " ").title(),
            "has_brochure": bool(info.get("url")),
            "has_maps": bool(info.get("maps_url")),
        })
    items.sort(key=lambda x: str(x.get("label") or ""))
    return items


def site_visit_summary_line(record: dict) -> str:
    property_name = record.get("property_name") or record.get("listing_id") or "property"
    when = format_readable_datetime(record.get("appointment_time") or record.get("scheduled_at") or "")
    location = record.get("location") or ""
    if location:
        return f"Site visit booked: {property_name} at {location} on {when}"
    return f"Site visit booked: {property_name} on {when}"