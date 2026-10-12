# Soniox STT + Latency Optimization — Step-by-Step Runbook

**Project:** `vk-realty-hme`  
**Status:** Phase 0 + Phase 1 implemented (July 2026)

---

## What changed (summary)

| Area | Before | After |
|------|--------|-------|
| **STT** | Sarvam Saaras v3 (silence endpointing, max 1.8s) | **Soniox stt-rt-v5 WebSocket** via `livekit.plugins.soniox` |
| **Turn detection** | STT silence timers | **Soniox semantic endpoint detection** (`turn_detection=stt`) |
| **Interruption** | None (VAD off) | **Silero VAD** (`interruption.mode=vad`) |
| **Preemptive LLM** | Off | **On** (`PreemptiveGenerationOptions(enabled=True)`) |
| **Max endpointing** | 1.8s | **0.5s** (500ms Soniox `max_endpoint_delay_ms` — minimum for fastest time-to-final) |
| **Prompt budget** | 2800 tokens | **1400 tokens** default |
| **Measurement** | LLM TTFT + TTS only | **Full TTFA** (endpointing, STT, LLM, buffer, TTS, PSTN estimate) |
| **Dashboard** | ~1.1s misleading chart | **p50/p95 TTFA** + 5-segment stacked bars |

---

## Prerequisites

1. **Soniox API key** in `.env`:
   ```bash
   SONIOX_API_KEY=your_key_here
   ```
   Create at [Soniox Console](https://console.soniox.com/).

2. **Install updated deps:**
   ```bash
   cd vk-realty-hme
   pip install -r requirements.txt
   ```
   Key new package: `livekit-plugins-soniox>=1.6.4`  
   Bumped: `livekit-agents>=1.6.4`

3. **Optional tuning env vars:**
   ```bash
   PSTN_LATENCY_ESTIMATE_MS=200          # added to server TTFA for phone estimate
   LIVEKIT_TTS_AGGRESSIVE_CHUNKING=1   # faster first TTS clause (Telugu/HME)
   SYSTEM_PROMPT_TOKEN_BUDGET=1400       # override prompt size cap
   ```

---

## Step 1 — Restart the agent (required)

Old worker processes do **not** hot-reload STT changes.

```bash
pkill -f "vk-realty-hme.*agent\.py" 2>/dev/null
pkill -f "Python agent\.py dev" 2>/dev/null
sleep 2
cd vk-realty-hme
python3 agent.py dev
```

**Confirm in logs:**
```
[STT] Soniox stt-rt-v5 WebSocket — hints=['te', 'en'] ...
[VAD] Silero loaded — interruption detection only (turn_detection=stt)
```

If you see `SONIOX_API_KEY missing`, fix `.env` and restart.

---

## Step 2 — Place a real PSTN test call

WebRTC-only tests skip ~200ms PSTN — always test on a **real phone**.

```bash
# Via dashboard: Outbound Calls → dial test number
# Or use your existing outbound dispatch API
```

Speak 2–3 short turns. Hang up. Wait for call log to sync (~10–30s).

---

## Step 3 — Verify latency on dashboard

1. Hard refresh: `Cmd+Shift+R`
2. Open **Call Logs** → latest call
3. Check **Latency per Interaction** section:

| What to see | Good sign |
|-------------|-----------|
| **p50 TTFA (est.)** | **< 1.0s** (target 750–950ms) |
| **p95 TTFA (est.)** | **< 1.5s** |
| 5 stacked segments | Endpointing, STT, LLM, Reply buffer, Audio start |
| Tooltip **Total (E2E est.)** | Close to what you felt on the phone |

**Old calls** (before this deploy) will still show partial data — only **new calls** after agent restart count.

---

## Step 4 — Read the latency breakdown

```
Soniox endpoint (user stopped)
  → endpointing_ms   (usually ~0 — clock starts at Soniox endpoint)
  → stt_ms           (handoff to LLM)
  → llm_ms           (first token)
  → buffer_ms        (wait for speakable sentence)
  → tts_ms           (first audio frame)
  → +200ms PSTN est. = ttfa_estimated_ms
```

If **buffer_ms** is large (>500ms): enable aggressive chunking:
```bash
export LIVEKIT_TTS_AGGRESSIVE_CHUNKING=1
```

If turns feel **cut off** mid-sentence: raise endpointing in `config.json`:
```json
"max_endpointing_delay": 1.0
```

If **LLM ms** is large (>600ms): check prompt size in logs `[TOKEN-BUDGET]`.

---

## Step 5 — Tune for your callers (production)

### Endpointing (biggest lever)

| Setting | File | Latency-first | Accuracy-first |
|---------|------|---------------|----------------|
| `stt_max_endpointing_delay` | `config.json` | **0.7–0.8** | 1.0–1.2 |
| Soniox `endpoint_sensitivity` | `stt_provider.py` | **0.35** | 0.0–0.2 |
| `max_endpoint_delay_ms` | auto from config | 700–800 | 1000+ |

Soniox API minimum `max_endpoint_delay_ms` is **500ms**.

### TTS first audio

| Setting | Effect |
|---------|--------|
| `LIVEKIT_TTS_AGGRESSIVE_CHUNKING=1` | Speak at shorter Telugu clauses (~12 graphemes) |
| Cartesia TTS (current default) | Fast TTFB — keep unless quality issues |

### LLM TTFT

| Setting | Effect |
|---------|--------|
| `SYSTEM_PROMPT_TOKEN_BUDGET=1400` | Smaller prompt → faster first token |
| `gemini-2.5-flash` | Already optimal for latency |
| `preemptive_generation=True` | LLM starts before full turn close |

### Co-location (Phase 2)

Deploy agent worker in **`asia-south1`** (Mumbai) per `GCP_DEPLOY.md` to shave ~100–200ms on Gemini + media hops.

---

## Step 6 — Prometheus (optional, production)

```bash
export METRICS_PORT=9090
```

Histograms wired:
- `hermes_llm_ttft_ms`
- `hermes_tts_first_chunk_ms`
- `hermes_e2e_turn_latency_ms` (now called from agent)

---

## Soniox STT config (matches LiveKit integration guide)

```python
from livekit.plugins import soniox
from stt_provider import build_soniox_stt, SONIOX_RT_WSS_URL

# Built per-call in agent.py (http_session reused from worker prewarm):
stt = build_soniox_stt(live_config, call_cfg, get_secret, http_session=soniox_http)

# Equivalent raw construction:
stt = soniox.STT(
    api_key=None,  # reads SONIOX_API_KEY from env
    base_url="wss://stt-rt.soniox.com/transcribe-websocket",
    params=soniox.STTOptions(
        model="stt-rt-v5",
        language_hints=["te", "en"],
        max_endpoint_delay_ms=500,
        enable_language_identification=True,
        endpoint_sensitivity=0.35,
        context=...,  # VK Realty terms in stt_provider.py
    ),
    http_session=reused_aiohttp_session,
)

# AgentSession (agent.py):
TurnHandlingOptions(
    turn_detection="stt",           # Soniox endpointing owns turn ends
    interruption=InterruptionOptions(mode="vad", ...),  # Silero for barge-in
    preemptive_generation=PreemptiveGenerationOptions(enabled=True),
)
```

**Interim results:** Soniox streams non-final + final tokens by default.  
`session.on("user_input_transcribed")` — check `ev.is_final` for partial vs final.

**Tuning `max_endpoint_delay_ms` (dashboard → STT → Soniox endpoint detection):**

| Value | Effect |
|-------|--------|
| **500** (current) | Fastest time-to-final; may cut off mid-thought |
| 800–1000 | Balanced for Telugu/Hinglish |
| 2000–3000 | Patient; feels slower |

**WebSocket API fields (efaf.md) — wired in dashboard + `stt_provider.py`:**

| Parameter | Dashboard / config key | Default |
|-----------|------------------------|---------|
| `enable_endpoint_detection` | Always on (Soniox plugin) | `true` |
| `max_endpoint_delay_ms` | `stt_max_endpoint_delay_ms` | **500** |
| `endpoint_sensitivity` | `stt_endpoint_sensitivity` | **0.35** (v5 only, -1…1) |
| `enable_language_identification` | `stt_enable_language_identification` | `true` |
| `language_hints` | Auto from `stt_language` / preset | `["te","en"]` etc. |

---

## Architecture (current)

```
Phone (PSTN)
  ↕ Vobiz SIP
LiveKit SFU (speaksy.online)
  ↕
Agent worker
  ├─ Silero VAD          → interruption only
  ├─ Soniox STT (WSS)    → turn end detection (stt-rt-v5)
  ├─ Gemini 2.5 Flash    → LLM (preemptive generation)
  ├─ Cartesia Sonic 3.5  → TTS
  └─ CallQualityScorer   → TTFA → Supabase call_logs
```

**Removed from production path:** Sarvam STT (`stt_stream.py`, Sarvam plugin).  
**Kept:** Sarvam TTS as emergency fallback only.

---

## Key files

| File | Role |
|------|------|
| `stt_provider.py` | `build_soniox_stt()` — only STT entry point |
| `stt_soniox.py` | Custom WebSocket SDK (backup; not used when LiveKit plugin active) |
| `agent.py` | Session: Soniox STT + Silero VAD + TurnHandlingOptions |
| `call_quality_scorer.py` | TTFA measurement |
| `frontend/call-logs.js` | p50/p95 chart |
| `LATENCY_OPTIMIZATION.md` | Full technical plan + targets |

---

## Target latency (after this deploy)

| Metric | Target |
|--------|--------|
| p50 TTFA (estimated) | **750–950ms** |
| p95 TTFA (estimated) | **1.2–1.5s** |
| Perception | Fast human consultant (not IVR) |

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Agent won't start — `SONIOX_API_KEY missing` | Add key to `.env` |
| `stt end of speech while user is speaking` warnings | Raise `max_endpointing_delay` to 1.0s |
| Chart empty on new calls | Kill all agent workers; confirm `latency_per_interaction` in Supabase |
| Telugu accuracy worse | Add terms to `_vk_realty_context()` in `stt_provider.py` |
| Still feels 3s+ | Check `buffer_ms` + `llm_ms` in chart; enable aggressive chunking |

---

## Next: Phase 2 (optional)

- [ ] Mumbai agent deploy + RTT audit
- [ ] Phone recording calibration vs server TTFA
- [ ] Nightly `scripts/benchmark_ttfa.py` regression CI
- [ ] Dynamic endpointing per turn length

See `LATENCY_OPTIMIZATION.md` for full Phase 2/3 plan.