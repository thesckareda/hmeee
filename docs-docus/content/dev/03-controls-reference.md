# Controls Reference

> Plain-language guide to every button & toggle

## The Big Ones

### Remember the Caller

**ON (default):** Agent reads every caller's prior history and adapts. Opens returning calls with *"you called yesterday…"*

**OFF:** Every call is fresh. Nothing written, nothing read. No memory cost.

Toggle: Dashboard → **Settings** → **Remember the caller**

### Conversational IQ v2

**ON (default):** All 18 IQ primitives active. Agent names callers, shows deals, handles objections intelligently, uses urgency when warranted.

**OFF:** Primitives silent. Agent falls back to base LLM replies.

Toggle: Dashboard → **Settings** → **Conversational IQ v2**

### Silence Guard

**ON (default):** 4-tier rescue when the caller goes quiet:
1. (2s silent) Backchannel: "hmm?"
2. (4s silent) Name bridge: "Ravi, andi…"
3. (6s silent) Objection rehash: "About the price…"
4. (8s silent) Transfer offer: "Let me connect you to my manager"

**OFF:** No rescue. Silence stays silent.

Toggle: Dashboard → **Settings** → **Silence Guard**

### Backchannel

**ON (tinglish default):** *"hmm"* / *"avunu"* / *"sare"* while caller monologues (PCM-cached, 0ms added latency).

**OFF:** Agent stays silent until it's time to reply.

Toggle: Dashboard → **Settings** → **Backchannel**

## Per-Client Knobs

All in `config.json`:

| Key | What | Example |
|---|---|---|
| `lang_preset` | Code-switch style | `tinglish` / `hte` / `english` |
| `agent_instructions` | System prompt | "You are Arjun, Senior Consultant at VK Realty…" |
| `first_line` | Opening greeting | "Hello andi! Nenu Arjun…" |
| `llm_model` | Which LLM | `gemini-2.5-flash-lite` |
| `llm_temperature` | Variance (0–1) | `0.7` = slightly creative |
| `tts_provider` | Voice service | `cartesia` / `sarvam` / `elevenlabs` |
| `stt_endpoint_sensitivity` | Speech detection | `low` / `medium` / `high` |
| `iq_disabled_primitives` | Kill-switch per rule | `["iq_07_same_boat"]` = disable that one |

## Related
- [Dashboard Guide](../02-dashboard-guide)
- [Config Reference (dev)](../../dev/10-config-reference)
