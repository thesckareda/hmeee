# VK Realty — Client-Centric Dashboard UX Plan

Ideas to make **dashboard.outpero.com/vk-realty** easy for **non-technical** real estate teams (sales managers, site-visit coordinators, brokers). Today the product is powerful but reads like a **developer studio** — this doc is the roadmap to make it feel like a **daily business tool**.

---

## Table of contents

1. [Who we’re designing for](#who-were-designing-for)
2. [What’s hard today](#whats-hard-today)
3. [Design principles](#design-principles)
4. [Simplified navigation (recommended)](#simplified-navigation-recommended)
5. [Rename everything (plain language)](#rename-everything-plain-language)
6. [Home page — “Morning briefing”](#home-page--morning-briefing)
7. [Core workflows (3 clicks max)](#core-workflows-3-clicks-max)
8. [Hide the technical stuff](#hide-the-technical-stuff)
9. [Onboarding for first login](#onboarding-for-first-login)
10. [Mobile & WhatsApp-first habits](#mobile--whatsapp-first-habits)
11. [Empty states & errors (human copy)](#empty-states--errors-human-copy)
12. [Role-based views (use what you already have)](#role-based-views-use-what-you-already-have)
13. [Quick wins (1–2 days each)](#quick-wins-12-days-each)
14. [Medium builds (1–2 weeks)](#medium-builds-12-weeks)
15. [Bigger bets (later)](#bigger-bets-later)
16. [Success metrics](#success-metrics)
17. [Implementation priority matrix](#implementation-priority-matrix)

---

## Who we’re designing for

| Persona | Cares about | Doesn’t care about |
|---------|-------------|-------------------|
| **Sales manager** | Hot leads, site visits booked, team follow-up | API keys, language presets, agent versions |
| **Site-visit coordinator** | Today’s appointments, caller phone numbers, project discussed | SIP trunks, LiveKit, TTS providers |
| **Broker / owner** | Conversion %, missed calls, revenue pipeline | Deploy agent, integrations, billing internals |
| **OUTPERO ops (you)** | Agent health, keys, knowledge updates | — (full studio access) |

**Goal:** A VK Realty manager opens the dashboard like they open **Google Analytics or a CRM** — not like a dev console.

---

## What’s hard today

Current sidebar has **17+ items** across Overview, Operations, and System:

- Duplicate concepts: **Leads & Calls**, **Call Logs**, **CRM**, **Analytics**
- Technical labels: **Main Agent**, **Language Presets**, **Deploy New Agent**, **Integrations**, **Knowledge Base**
- Settings exposes **API Keys**, **config.json**, agent restart — scary for non-tech users
- Errors mention **Supabase**, **RLS**, **SERVICE_ROLE_KEY** (in toasts)
- **Studio Dashboard** subtitle — wrong mental model for a property client
- First-time users don’t know: login URL, what Arjun does, or what to click after sign-in

---

## Design principles

1. **Business words, not product words** — “Site visits” not “bookings pipeline”; “Arjun” not “voice worker”.
2. **One home, three jobs** — See today’s leads → listen to a call → book / follow up.
3. **Progressive disclosure** — Simple by default; “Advanced” behind a toggle or ops-only role.
4. **No dead ends** — Every empty screen says what to do next in one sentence.
5. **Phone-first** — Tap-to-call, tap-to-WhatsApp, big touch targets.
6. **Trust cues** — “Arjun answered 42 calls today” beats “Agent process_count: 7”.

---

## Simplified navigation (recommended)

### Client view (default for VK Realty)

Reduce sidebar to **5 items** + Help:

| # | Nav label | Maps to today | Icon idea |
|---|-----------|---------------|-----------|
| 1 | **Today** | overview + hot-leads highlights | home |
| 2 | **Leads** | leads + CRM (merged) | users |
| 3 | **Calls** | call-logs (simplified) | phone |
| 4 | **Site visits** | calendar + bookings | calendar |
| 5 | **Reports** | performance + analytics (summary only) | chart |
| — | **Help** | static guide + “Call OUTPERO support” | help-circle |

**Footer only:** Arjun status pill (“Answering calls” / “Needs attention”) + Sign out + OUTPERO logo.

### Ops / OUTPERO view (hidden role)

Full current nav available via:

- `organisation` workspace slug, or
- `features` array including `settings`, `assistants`, `integrations`, etc. (already supported in `vk-realty-dashboard.js` → `applyFeatureGate`)

---

## Rename everything (plain language)

| Current UI text | Client-friendly text |
|-----------------|----------------------|
| Studio Dashboard | **VK Realty Command Center** |
| Real-time voice operations | **Arjun’s calls & leads — live** |
| Deploy New Agent | **Add new assistant** (ops only) or hide |
| Main Agent | **Arjun (your AI consultant)** |
| Language Presets | **How Arjun speaks** (ops only) |
| Agents | **AI assistants** (ops only) |
| Call Logs | **Call history** |
| Hot Leads | **Priority leads** |
| Leads & Calls | **All leads** |
| Knowledge Base | **Property information** |
| Integrations | **Connections** (ops only) |
| API Keys | **Technical setup** (ops only) |
| Analytics | **Numbers & trends** |
| Campaigns | **Outbound calling lists** |
| CRM | *(merge into Leads)* |
| Performance / Listings | *(merge into Reports)* |
| Agent online | **Arjun is answering calls** |
| Refresh | **Update** |
| Test Call | **Try a test call** |
| Run Agent | **Start Arjun** (ops only) |

### Call outcome labels (non-tech)

| System term | Show to client |
|-------------|----------------|
| `was_booked` | **Site visit booked** |
| `no_answer` | **Didn’t pick up** |
| `completed` | **Spoke with lead** |
| `failed` | **Call didn’t connect** — + plain reason (“Line busy”, not “TwirpError”) |
| `sentiment: neutral` | **Normal conversation** |

---

## Home page — “Morning briefing”

Replace generic overview with a **single-screen daily summary**:

```
┌─────────────────────────────────────────────────────────────┐
│  Good morning, VK Realty                    Wed 1 Jul 2026   │
│  Arjun answered 38 calls overnight · 5 need your attention   │
├─────────────────────────────────────────────────────────────┤
│  [ 38 Calls ]  [ 12 New leads ]  [ 3 Site visits ]  [ 2 Hot ]│
├─────────────────────────────────────────────────────────────┤
│  ⚡ Needs action (5)                                         │
│  ┌─────────────────────────────────────────────────────┐   │
│  │ Rajesh · +91 98xxx · Kokapet 3BHK · Budget 3–4 Cr   │   │
│  │ Arjun: "Wants site visit this weekend"               │   │
│  │ [ Call back ]  [ Mark visited ]  [ Listen ]         │   │
│  └─────────────────────────────────────────────────────┘   │
├─────────────────────────────────────────────────────────────┤
│  Today’s site visits (3)          │  Recent calls (play)   │
└─────────────────────────────────────────────────────────────┘
```

**Ideas:**

- **Needs action** = hot leads + booked but not confirmed + missed callback SLA
- Each card: name, phone (masked), project, **one-line Arjun summary** in Tinglish/English
- Primary buttons: **Call back**, **Listen**, **Open in WhatsApp** (if number on file)
- No charts on home — charts live under **Reports**

---

## Core workflows (3 clicks max)

### 1. “Who called yesterday about Kokapet?”

1. **Leads** → search “Kokapet” (one search bar, top of page)
2. Tap lead → see timeline: calls, summaries, site visit status
3. **Listen** to recording or read transcript (collapsed by default, “Show full conversation”)

### 2. “Book / confirm site visit”

1. **Site visits** → calendar week view (default **this week**)
2. Tap slot or lead → **Confirm visit** / **Reschedule** / **Assign to rep**
3. Optional: send WhatsApp template (“Your visit at Prestige City, Sat 11am”)

### 3. “Is Arjun working?”

1. Glance at sidebar footer: green **“Arjun is answering calls”**
2. If red: **“Something needs setup — contact OUTPERO”** (no stack traces)
3. Ops link: “Technical details” expands for support only

### 4. “Run outbound to my list”

1. **Outbound lists** (rename Campaigns) → **Upload numbers** (CSV) or paste numbers
2. Plain copy: “Arjun will call these numbers one by one”
3. Progress bar: **23 of 100 called · 5 interested**

---

## Hide the technical stuff

**Never show to client roles:**

- API Keys, JWT, Supabase, LiveKit, Cartesia, Sarvam
- Agent restart, supervisord, deploy, language preset JSON
- Raw `config.json`, env manager, circuit breaker states
- “Browser test call”, worker PIDs, `/metrics`

**Already possible:** `applyFeatureGate(features)` in `vk-realty-dashboard.js` — set org `features` to client-safe list:

```json
["overview", "leads", "hot-leads", "call-logs", "calendar", "reports"]
```

**Settings hub for clients** — only 3 cards:

| Card | Purpose |
|------|---------|
| **Business hours** | When Arjun should answer |
| **Notification number** | Manager WhatsApp/SMS for hot leads |
| **Property information** | Link to edit projects (simplified KB UI) |

---

## Onboarding for first login

**First visit wizard (4 steps, skippable):**

1. **Welcome** — “This is where you see every call Arjun handles for VK Realty.”
2. **Your login** — “Bookmark: dashboard.outpero.com/vk-realty”
3. **What Arjun does** — 30-sec loop: answers → qualifies → books site visit
4. **Try it** — “Place a test call to your mobile” (pre-filled +91 field, big button)

**Persistent help:**

- **?** button → “How to use this dashboard” (short PDF-style page in-app)
- **“Call OUTPERO support”** — `tel:` or WhatsApp link
- Tooltips on first visit only (localStorage `onboarding_v1_done`)

**Login page (already at `/vk-realty#login`):**

- Remove jargon; headline: **“Sign in to VK Realty dashboard”**
- Subtext: “See leads, calls, and site visits handled by Arjun”
- Optional: **Org code** field with helper text: “Ask your manager if you have a team code”

---

## Mobile & WhatsApp-first habits

Indian real estate teams live on **phone + WhatsApp**:

| Feature | Why |
|---------|-----|
| Bottom nav (5 icons) on mobile | Thumb reach; mirror simplified client nav |
| **Tap to call** on every lead row | `tel:+91…` |
| **Share to WhatsApp** | Pre-filled: “Lead: Rajesh, Kokapet 3BHK, budget 3Cr — summary…” |
| Large audio play button on calls | No tiny scrubber |
| Sticky “Needs action” count | Badge on **Today** tab |
| PWA “Add to Home Screen” prompt | Feels like an app; bookmark for non-tech users |

---

## Empty states & errors (human copy)

| Situation | Don’t say | Say instead |
|-----------|-----------|-------------|
| No data / 401 | (blank screen) | “Please sign in again” + button to login |
| Supabase / API error | “SUPABASE_SERVICE_ROLE_KEY” | “We couldn’t load your data. Try Refresh. If it persists, contact OUTPERO.” |
| No calls yet | “No rows” | “Arjun hasn’t received calls yet. Share your business number or run a test call.” |
| No hot leads | “No priority leads” | “No urgent follow-ups right now — check back after peak calling hours.” |
| Recording 404 | “HTTP 400” | “Recording not ready yet — try again in a few minutes.” |
| Agent down | “supervisord stopped” | “Arjun is offline. OUTPERO has been notified.” (ops alert separately) |

---

## Role-based views (use what you already have)

You already have **org workspaces** (`/vk-realty/o/{role}/dashboard`) and **feature gates**.

### Suggested role presets

| Role slug | Features enabled | Nav |
|-----------|------------------|-----|
| `vk-sales` | overview, leads, hot-leads, call-logs, calendar | Client 5-item nav |
| `vk-manager` | + reports, campaigns | + Reports + Outbound |
| `vk-owner` | + performance | + high-level KPIs only |
| `outpero-ops` | all | Full studio |

Store in org `metadata.features` or bootstrap in `org_portal.py`.

### Per-client branding

- Wordmark: **VK Realty** (done)
- Optional: client logo in sidebar (upload in Settings → Branding)
- Hide **Deploy New Agent** for all client roles

---

## Quick wins (1–2 days each)

| # | Change | Impact |
|---|--------|--------|
| 1 | **Client feature gate** on default org — hide 12 nav items | Instant simplicity |
| 2 | Rename page title/subtitle/copy per table above | Feels built for them |
| 3 | **Human error toasts** — map technical errors in `app.js` `parseApiError` | Trust |
| 4 | Default landing page = **overview** with “Needs action” section prominent | Daily habit |
| 5 | **Login helper** on dashboard when bootstrap empty: “Sign in at /vk-realty” | Fixes “no data” confusion |
| 6 | Hide **Deploy New Agent** + **Language Presets** for client org | Less fear |
| 7 | Call row: show **Listen** + **Call back** as primary actions | Action-oriented |
| 8 | Add **OUTPERO support** link in sidebar footer | Safety net |
| 9 | `.env` checklist in ops doc only — clients never see env | Already separate |
| 10 | **Telugu/Hinglish tooltips** optional on key buttons | Local comfort |

---

## Medium builds (1–2 weeks)

| # | Feature | Description |
|---|---------|-------------|
| 1 | **Merged Leads page** | Single timeline per phone: calls + CRM + site visits |
| 2 | **Morning briefing home** | KPI cards + needs-action queue (design above) |
| 3 | **First-login wizard** | 4-step overlay + bookmark reminder |
| 4 | **Simplified Call detail** | Summary first; transcript behind “Show conversation” |
| 5 | **Client Settings** (3 cards only) | Hours, notify number, property info |
| 6 | **Outbound wizard** | CSV upload → plain language → progress |
| 7 | **WhatsApp share** on lead card | One tap share summary to rep |
| 8 | **Mobile bottom nav** | Today / Leads / Calls / Visits / More |
| 9 | **Plain-language call failures** | Map `TwirpError`, `OUTBOUND_TRUNK_ID` to user text |
| 10 | **In-app Help page** | Screenshots + 2-min video embed |

---

## Bigger bets (later)

| Idea | Value |
|------|-------|
| **Daily WhatsApp digest** to manager — “5 hot leads, 2 visits today” | No login needed |
| **Rep mobile app (PWA)** — assign lead → mark visited | Field teams |
| **Hindi/Telugu UI toggle** | Broader adoption |
| **Voice note from manager** → updates Arjun’s knowledge | No typing |
| **Lead scoring in plain words** — “Very interested” / “Maybe” / “Not now” | No ML jargon |
| **Comparison reports** — “This week vs last week” one PDF | Owner-friendly |
| **Client onboarding PDF** auto-generated from their projects | Handoff from OUTPERO |
| **Read-only share link** for owner (no login, expiring) | Investor updates |

---

## Success metrics

Track after rollout:

| Metric | Target |
|--------|--------|
| Time to first meaningful action after login | < 30 seconds |
| % users only using 3 nav items | > 80% |
| Support tickets mentioning “confused” / “empty” | Down 50% |
| Mobile sessions | Up (bottom nav) |
| Site visits marked in dashboard within 24h of Arjun booking | Up |

---

## Implementation priority matrix

```
                    HIGH IMPACT
                         │
    Client feature gate  │  Morning briefing home
    Rename copy          │  Merged Leads page
    Human errors         │  First-login wizard
                         │
    ─────────────────────┼─────────────────────
                         │
    Mobile bottom nav    │  WhatsApp digest
    Help page            │  Telugu UI
                         │
                    LOW EFFORT ────────── HIGH EFFORT
```

**Recommended order:**

1. Feature gate + renames + error copy (**this week**)
2. Home “Needs action” + simplified call detail (**next**)
3. Onboarding wizard + client Settings (**then**)
4. Mobile nav + WhatsApp share (**polish**)

---

## Copy bank (ready to paste)

**Dashboard subtitle:**  
> “Every call Arjun handles — leads, recordings, and site visits in one place.”

**Empty leads:**  
> “When customers call your number, they’ll show up here automatically.”

**Hot lead card:**  
> “Wants a site visit — call within 2 hours for best conversion.”

**Arjun status (green):**  
> “Arjun is answering calls.”

**Arjun status (red):**  
> “Arjun is paused. Contact OUTPERO support.”

**Test call CTA:**  
> “Hear Arjun on your phone — enter your mobile number below.”

---

## Files to touch (when you implement)

| Area | Files |
|------|-------|
| Nav / feature gate | `frontend/vk-realty/dashboard.html`, `vk-realty-dashboard.js` |
| Copy / titles | `dashboard.html`, `dashboard.js`, `app.js` |
| Home briefing | `dashboard.js` (`loadOverview`) |
| Errors | `app.js` (`parseApiError`, `showToast`) |
| Role features | `org_portal.py`, org `metadata` |
| Help page | New `frontend/vk-realty/help.html` + route in `ui_server.py` |

---

*OUTPERO · VK Realty · Client UX plan · July 2026*