import json
import os
import logging
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("calendar-tools")

IST = timezone(timedelta(hours=5, minutes=30))
BOOKING_START_HOUR_IST = 9
BOOKING_END_HOUR_IST = 21
MIN_BOOKING_LEAD_MINUTES = 1
APPOINTMENT_DURATION_MINUTES = 30


def _format_ampm(dt: datetime) -> str:
    return dt.strftime("%I:%M %p").lstrip("0")


def _to_ist_datetime(value: str | datetime) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        normalized = raw.replace(" ", "T")
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(normalized)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(IST)


def is_within_booking_hours_ist(value: str | datetime) -> bool:
    dt_ist = _to_ist_datetime(value)
    if not dt_ist:
        return False
    start_minutes = BOOKING_START_HOUR_IST * 60
    end_minutes = BOOKING_END_HOUR_IST * 60
    appt_minutes = dt_ist.hour * 60 + dt_ist.minute
    return start_minutes <= appt_minutes < end_minutes


def _slot_key_ist(value: str | datetime) -> str:
    dt_ist = _to_ist_datetime(value)
    if not dt_ist:
        return ""
    return dt_ist.strftime("%Y-%m-%dT%H:%M")


def _is_bookable_slot_ist(slot_dt_ist: datetime) -> bool:
    if not slot_dt_ist:
        return False
    minimum_allowed = datetime.now(IST) + timedelta(minutes=MIN_BOOKING_LEAD_MINUTES)
    if slot_dt_ist < minimum_allowed:
        return False
    return is_within_booking_hours_ist(slot_dt_ist)


def get_calendar_setup_error() -> str:
    """Cal.com is disabled — local slots always available."""
    return ""


def _gcal_configured() -> bool:
    gcal_id = os.environ.get("GOOGLE_CALENDAR_ID", "")
    gcal_creds = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "google_creds.json")
    return bool(gcal_id and os.path.exists(gcal_creds))


def get_available_slots(date_str: str) -> list:
    """
    Return open slots for a date.
    Uses Google Calendar free/busy when configured; otherwise local generated slots.
    Cal.com is not used.
    """
    if _gcal_configured():
        try:
            return _get_slots_gcal(
                date_str,
                os.environ["GOOGLE_CALENDAR_ID"],
                os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "google_creds.json"),
            )
        except Exception as e:
            logger.warning(f"[GCAL] Falling back to local slots: {e}")

    return _get_slots_local(date_str)


def _get_reserved_slot_keys() -> set[str]:
    keys: set[str] = set()
    base = Path(__file__).resolve().parent
    for filename in ("appointments.json", "site_visits.json"):
        path = base / filename
        try:
            if not path.exists():
                continue
            records = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(records, list):
                continue
            for record in records:
                if str(record.get("status", "confirmed")) == "cancelled":
                    continue
                raw = record.get("scheduled_at") or ""
                dt = _to_ist_datetime(raw)
                if dt:
                    keys.add(dt.strftime("%Y-%m-%dT%H:%M"))
        except Exception as e:
            logger.warning(f"[BOOKING] Could not read {filename}: {e}")
    return keys


def _get_slots_local(date_str: str) -> list:
    """Generate 30-minute slots between 9 AM and 9 PM IST, excluding booked slots."""
    import pytz

    reserved = _get_reserved_slot_keys()
    ist = pytz.timezone("Asia/Kolkata")
    try:
        day_start = ist.localize(datetime.strptime(f"{date_str} 09:00", "%Y-%m-%d %H:%M"))
        day_end = ist.localize(datetime.strptime(f"{date_str} 21:00", "%Y-%m-%d %H:%M"))
    except ValueError:
        logger.error(f"[BOOKING] Invalid date for slots: {date_str}")
        return []

    slots = []
    slot = day_start
    while slot < day_end:
        if _is_bookable_slot_ist(slot.astimezone(IST)):
            key = slot.strftime("%Y-%m-%dT%H:%M")
            if key not in reserved:
                slots.append({"time": slot.isoformat(), "label": _format_ampm(slot)})
        slot += timedelta(minutes=30)

    logger.info(f"[BOOKING] {len(slots)} local slots for {date_str}")
    return slots


def _get_slots_gcal(date_str: str, calendar_id: str, creds_file: str) -> list:
    from googleapiclient.discovery import build
    from google.oauth2 import service_account
    import pytz

    creds = service_account.Credentials.from_service_account_file(
        creds_file,
        scopes=["https://www.googleapis.com/auth/calendar.readonly"],
    )
    service = build("calendar", "v3", credentials=creds)

    start = f"{date_str}T00:00:00+05:30"
    end = f"{date_str}T23:59:59+05:30"

    result = service.freebusy().query(body={
        "timeMin": start,
        "timeMax": end,
        "items": [{"id": calendar_id}],
    }).execute()

    busy_slots = result.get("calendars", {}).get(calendar_id, {}).get("busy", [])

    ist = pytz.timezone("Asia/Kolkata")
    day_start = ist.localize(datetime.strptime(f"{date_str} 09:00", "%Y-%m-%d %H:%M"))
    day_end = ist.localize(datetime.strptime(f"{date_str} 21:00", "%Y-%m-%d %H:%M"))

    busy_ranges = []
    for b in busy_slots:
        bs = datetime.fromisoformat(b["start"]).astimezone(ist)
        be = datetime.fromisoformat(b["end"]).astimezone(ist)
        busy_ranges.append((bs, be))

    reserved = _get_reserved_slot_keys()
    free_slots = []
    slot = day_start
    while slot < day_end:
        slot_end = slot + timedelta(minutes=30)
        is_busy = any(bs <= slot < be for bs, be in busy_ranges)
        key = slot.strftime("%Y-%m-%dT%H:%M")
        if (not is_busy) and _is_bookable_slot_ist(slot) and key not in reserved:
            free_slots.append({"time": slot.isoformat(), "label": _format_ampm(slot)})
        slot = slot_end

    logger.info(f"[GCAL] {len(free_slots)} free slots for {date_str}")
    return free_slots


def create_booking(
    start_time: str,
    caller_name: str,
    caller_phone: str,
    notes: str = "",
) -> dict:
    import asyncio
    try:
        return asyncio.get_event_loop().run_until_complete(
            async_create_booking(start_time, caller_name, caller_phone, notes)
        )
    except RuntimeError:
        return asyncio.run(async_create_booking(start_time, caller_name, caller_phone, notes))


async def async_create_booking(
    start_time: str,
    caller_name: str,
    caller_phone: str,
    notes: str = "",
) -> dict:
    """
    Book a slot locally (WhatsApp + dashboard). Google Calendar optional.
    Cal.com is not used.
    """
    requested_dt_ist = _to_ist_datetime(start_time)
    if not requested_dt_ist:
        return {"success": False, "booking_id": None, "message": "Invalid appointment time format."}

    now_ist = datetime.now(IST)
    minimum_allowed = now_ist + timedelta(minutes=MIN_BOOKING_LEAD_MINUTES)
    if requested_dt_ist < minimum_allowed:
        return {
            "success": False,
            "booking_id": None,
            "message": f"Appointments can be booked as early as {MIN_BOOKING_LEAD_MINUTES} minute from now.",
        }

    if not is_within_booking_hours_ist(requested_dt_ist):
        return {
            "success": False,
            "booking_id": None,
            "message": "Appointments are available only between 9:00 AM and 9:00 PM IST.",
        }

    slot_key = _slot_key_ist(requested_dt_ist)
    if slot_key in _get_reserved_slot_keys():
        return {
            "success": False,
            "booking_id": None,
            "message": "That time slot is already booked. Please choose another time.",
        }

    if _gcal_configured():
        gcal_result = await _create_booking_gcal(
            requested_dt_ist.isoformat(),
            caller_name,
            caller_phone,
            notes,
            os.environ["GOOGLE_CALENDAR_ID"],
            os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "google_creds.json"),
        )
        if gcal_result.get("success"):
            return gcal_result
        logger.warning(f"[GCAL] Event create failed, saving locally: {gcal_result.get('message')}")

    return await _create_booking_local(
        requested_dt_ist.isoformat(), caller_name, caller_phone, notes
    )


async def _create_booking_local(
    start_time: str,
    caller_name: str,
    caller_phone: str,
    notes: str,
) -> dict:
    import db

    try:
        result = await db.save_local_appointment(
            phone=caller_phone,
            caller_name=caller_name,
            scheduled_at=start_time,
            notes=notes,
        )
        booking_id = result.get("booking_id") or str(uuid.uuid4())
        logger.info(f"[BOOKING] Local appointment saved: id={booking_id}")
        return {"success": True, "booking_id": booking_id, "message": "Booking confirmed"}
    except Exception as e:
        logger.error(f"[BOOKING] Local save failed: {e}")
        return {"success": False, "booking_id": None, "message": str(e)}


async def _create_booking_gcal(
    start_time: str,
    caller_name: str,
    caller_phone: str,
    notes: str,
    calendar_id: str,
    creds_file: str,
) -> dict:
    try:
        from googleapiclient.discovery import build
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(
            creds_file,
            scopes=["https://www.googleapis.com/auth/calendar"],
        )
        service = build("calendar", "v3", credentials=creds)

        dt_start = datetime.fromisoformat(start_time)
        dt_end = dt_start + timedelta(minutes=APPOINTMENT_DURATION_MINUTES)

        event = {
            "summary": f"Appointment — {caller_name}",
            "description": f"Phone: {caller_phone}\nNotes: {notes}\nBooked via VK Realty AI Voice Agent",
            "start": {"dateTime": dt_start.isoformat(), "timeZone": "Asia/Kolkata"},
            "end": {"dateTime": dt_end.isoformat(), "timeZone": "Asia/Kolkata"},
            "attendees": [{"displayName": caller_name, "comment": caller_phone}],
        }

        created = service.events().insert(calendarId=calendar_id, body=event).execute()
        event_id = created.get("id", "unknown")
        logger.info(f"[GCAL] Event created: id={event_id}")
        return {"success": True, "booking_id": event_id, "message": "Google Calendar event created"}
    except Exception as e:
        logger.error(f"[GCAL] Create booking failed: {e}")
        return {"success": False, "booking_id": None, "message": str(e)}


def cancel_booking(booking_id: str, reason: str = "Cancelled by caller") -> dict:
    """Cancel a locally stored appointment."""
    import db

    try:
        records = db._load_local_appointments()
        updated = False
        for record in records:
            if str(record.get("id")) == str(booking_id):
                record["status"] = "cancelled"
                record["cancel_reason"] = reason
                updated = True
                break
        if updated:
            db._save_local_appointments(records)
            logger.info(f"[BOOKING] Local appointment cancelled: {booking_id}")
            return {"success": True, "message": "Cancelled successfully"}
        return {"success": False, "message": f"Booking {booking_id} not found"}
    except Exception as e:
        logger.error(f"[BOOKING] cancel_booking error: {e}")
        return {"success": False, "message": str(e)}