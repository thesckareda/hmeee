# Dashboard Guide

> **Who:** operators & client managers

The dashboard is split into **three areas**: Live Calls, Leads, and Analytics.

## Live Calls

**URL:** Dashboard → **Live**

Shows all active calls in real-time:
- Caller name & phone
- Current stage (discovery / match / close)
- Duration, STT latency, agent response time
- Transcript scroll (auto-updates as they talk)

**Controls:**
- **End Call** — hang up immediately
- **Transfer** — route to a human agent
- **Hold/Resume** — pause the agent

## Leads

**URL:** Dashboard → **Leads**

Call history, profiles, and memory for every phone number.

| Column | Shows |
|---|---|
| Phone | Normalized number (lookup key) |
| Name | Captured from call or memory |
| Last Call | When they last dialed (time delta: "2 days ago") |
| Intent Score | 0–100, updated post-call |
| Deal Stage | discover / match / close / booked |
| Actions | View full memory, forget, resend offer |

**Click a lead** to see:
- Full profile (name, budget, location, family members mentioned)
- Complete call history with summaries
- Objections raised & outcomes
- Property matches offered
- Next steps

## Analytics

**URL:** Dashboard → **Analytics**

Platform-wide metrics & KPIs.

| Metric | What it measures |
|---|---|
| **Calls Today** | Inbound + outbound |
| **Avg Duration** | Minutes per call |
| **IQ Fire Rate** | % of turns that got a conversational-IQ rule |
| **Memory Hit Rate** | % of calls that found prior history |
| **STT Latency (p95)** | How fast speech-to-text responds |
| **Booking Rate** | % of calls that converted |

## Settings

**URL:** Dashboard → **Settings**

### Buttons
- **Remember the caller** — ON/OFF toggle
- **Conversational IQ v2** — enable all 18 primitives
- **Silence Guard** — 4-tier rescue phrases
- **Backchannel** — "hmm" / "avunu" while caller talks

### Config Keys
Edit any key in the table. Changes hot-reload (no restart needed).

## Related
- [Controls Reference](../03-controls-reference)
- [Caller Memory](../04-caller-memory)
