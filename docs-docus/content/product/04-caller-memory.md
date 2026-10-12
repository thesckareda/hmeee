# Caller Memory (Remember the Caller)

> Remember callers across calls with time-anchored greetings

## What it remembers

For each phone number:
- **Name** (captured during the call or extracted post-call)
- **Budget/location/BHK/timeline** (with exact values, not guesses)
- **Family members** mentioned ("wife Priya—schools priority")
- **Every call's summary** with timestamp
- **Objections raised** (price, location, timeline) & which scripts failed
- **Deal stage** (discover → match → close → booked)

## How it sounds when ON

| Caller returns… | Agent opens with |
|---|---|
| Same day | *"earlier today matladam kada…"* |
| Next day | *"meeru **yesterday** call chesaru — Kokapet 3BHK gurinchi…"* |
| 3 days later | *"**3 days back** call lo meeru 1.2 Cr budget annaru…"* |
| 2 weeks later | *"**2 weeks back** discuss chesam…"* |

The time deltas are computed automatically from timestamps. You never write them.

## Where it lives

Each caller's memory is **fully isolated**:

```
~/.hermes/re-voice-memory/leads/<phone>/
├── lead_profile.md          (name, budget, location, etc.)
├── call_history.md          (summaries + timestamps)
├── objection_log.md         (what scripts failed)
└── property_graph.md        (explored properties)

Redis: re_voice:lead:<phone>:*    (hot cache, <1ms reads)
```

## The Button: ON / OFF

**ON (default):** Engine reads & writes memory.

**OFF:** Zero memory. Every call is fresh. Use this for:
- Privacy testing
- Demo calls (don't pollute caller records)
- GDPR "forget" mode

Toggle: Dashboard → **Settings** → **Remember the caller**

## The Forget Button (GDPR / Delete)

Caller dials in, you want to erase everything:

Dashboard → **Leads** → (select the caller) → **⊗ Forget This Caller**

This **irreversibly** removes:
- All files under `leads/<phone>/`
- All Redis keys for that phone
- SQLite backups (if any)

**Note:** The call transcript stays in the call logs for compliance. Memory (profile, history, relations) is gone.

## How it's different from "turn off memory"

| | Remember ON | Remember OFF | After Forget |
|---|---|---|---|
| Read memory | ✅ Yes | ❌ No | ✅ No (deleted) |
| Write memory | ✅ Yes | ❌ No | ✅ No (deleted) |
| Existing memory? | Used | Ignored | Gone |
| Flip back ON? | Resumes | Cold start | Resumes from backup (if exists) |

## Related
- [Dashboard Guide](../02-dashboard-guide)
- [Memory System (dev)](../../dev/05-memory-system)
