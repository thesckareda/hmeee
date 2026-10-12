# VK Realty HME — Voice Latency Optimization Plan

**Project:** `vk-realty-hme`  
**Date:** July 2026  
**Goal:** Reduce perceived turn latency from **~3–4 seconds** to **~750–1,000ms p50** (voice-to-voice TTFA on real PSTN calls)

---

## Implementation Status

| Item | Status |
|------|--------|
| Soniox-only STT (`stt-rt-v5` WebSocket via LiveKit plugin) | [x] Done |
| Phase 0 TTFA measurement | [x] Done |
| Phase 1 endpointing + chunking tuning | [x] Done |
| Phase 2 preemptive generation (enabled in agent session) | [x] Done |
| Phase 2 regional co-location (Mumbai deploy) | [ ] Pending |

### Target metrics (updated)

| Metric | Baseline | After Phase 1 | After all phases |
|--------|----------|---------------|------------------|
| **p50 voice-to-voice TTFA** | ~2.5–3.5s | **~1.5–2.0s** | **~750–950ms** |
| **p95 voice-to-voice TTFA** | ~3.5–4.5s | **~2.5–3.0s** | **~1.2–1.5s** |
| **max_endpointing_delay** | 1.8s | **0.8s** | 0.6–0.8s |
| **SYSTEM_PROMPT_TOKEN_BUDGET** | 2800 | **1400** | ≤1400 |
| **MIN_CHUNK_GRAPHEMES_CLAUSE** | 20 | **16** (12 with `LIVEKIT_TTS_AGGRESSIVE_CHUNKING=1`) | 12–16 |
| **Caller perception** | Robotic / IVR | Noticeably faster | Fast human consultant |

---

## 1. Executive summary

Human conversation has a remarkably stable gap between speakers: **200–300ms**. Voice AI that exceeds **~800ms** starts to feel like a pause; past **1 second**, callers say “hello?” or talk over the agent; past **2 seconds**, the experience is indistinguishable from an IVR.

This codebase is a **pipeline voice agent** (STT → LLM → TTS) running on LiveKit with India telephony (Vobiz SIP). It already implements several latency-friendly patterns (streaming STT, streaming LLM, sentence-level TTS, barge-in, filler bridging). However, **configuration and measurement gaps** push real PSTN turns into the **2.5–4 second** range.

The largest fixable contributors today are:

1. **Silence-based endpointing** — was up to **1.8s** (Phase 1: **0.8s**); still a contributor on ambiguous turns
2. **LLM time-to-first-token (~1s)** — dominant and variable; worsened by large system prompts  
3. **Reply buffering** — waiting for speakable Indic sentence chunks before TTS starts  
4. **Incomplete measurement** — charts show partial metrics, not voice-to-voice TTFA at the caller’s ear  
5. **Multi-hop geography** — LiveKit media, Gemini inference, and Cartesia TTS are not co-located with India PSTN ingress

With a phased optimization plan, realistic targets are:

| Metric | Today | After all fixes |
|--------|-------|-----------------|
| **p50 voice-to-voice TTFA** | ~2.5–3.5s | **~750–950ms** |
| **p95 voice-to-voice TTFA** | ~3.5–4.5s | **~1.2–1.5s** |
| **Caller perception** | Robotic / IVR | Fast human consultant |

Sub-500ms conversational latency on PSTN with a pipeline architecture is **not achievable** without speech-to-speech or removing the telephone network. The realistic win is **~3× faster**, aligning with the industry “optimized pipeline” band (380–1,000ms).

---

## 2. The only honest metric: voice-to-voice TTFA

**Definition:** Elapsed time from the caller’s **last audible syllable** to the **first audible syllable** of the agent’s reply, measured at the **caller’s ear**, including PSTN/SIP transport.

Vendors often quote partial numbers (model TTFT, TTS synthesis time, API latency). None of these equal what the caller experiences.

### What this codebase measures today

| Metric | Where | Clock start | Clock end | Honest TTFA? |
|--------|-------|-------------|-----------|--------------|
| `llm_ms` | `call_quality_scorer.py` → `agent.py` `llm_node` | LLM request starts | First streamed token | **No** — partial |
| `tts_ms` | `call_quality_scorer.py` → `agent.py` `tts_node` | TTS node receives text stream | First PCM frame emitted | **No** — partial |
| `stt_ms` | `call_quality_scorer.py` | STT `is_final` event | `on_user_turn_completed` | **No** — misses pre-final silence |
| `e2e_ms` | `call_quality_scorer.py` | STT `is_final` | First TTS PCM frame | **Partial** — server-side only, no PSTN |
| `hermes_llm_ttft_ms` | `re_voice_memory/metrics.py` (Prometheus) | Same as `llm_ms` | Same | **No** — defined but partial |
| `hermes_e2e_turn_latency_ms` | `re_voice_memory/metrics.py` | Soniox STT endpoint (`is_final`) | First TTS PCM frame | **Partial** — wired; no PSTN |
| `ttfa_estimated_ms` | `call_quality_scorer.py` | Soniox STT endpoint | First TTS + 200ms PSTN estimate | **Best server-side proxy** |
| Dashboard chart | `frontend/call-logs.js` | 5-segment bars + p50/p95 | Tooltip “Total (E2E est.)” | **Accurate on new calls** |

### Why the chart showed ~1.1s but the call felt like 3–4s

The original chart (and patched call log #242) stacked only:

- **LLM first token** (~1.0s)
- **TTS first frame** (~0.07s)

It omitted:

- Silence **before** STT `is_final` (endpointing: was up to 1.8s; Phase 1: **0.8s**)
- **Reply buffering** between first token and speakable TTS chunk
- **PSTN round-trip** (LiveKit → Vobiz → handset)

---

## 3. Current architecture

### 3.1 Call path (outbound / inbound)

```
Caller handset
    ↕ PSTN / SIP (Vobiz — India)
LiveKit SFU (wss://livekit.speaksy.online)
    ↕ WebRTC audio
Agent worker (agent.py — local or GCP asia-south1)
    ├─ VAD (voice activity detection)
    ├─ STT: Soniox stt-rt-v5 WebSocket streaming (LiveKit plugin + stt_soniox.py backup path)
    ├─ Turn detection: LiveKit AgentSession, turn_detection="stt"
    ├─ LLM: Gemini 2.5 Flash (gemini_llm.py)
    ├─ TTS: Cartesia Sonic 3.5 (cartesia_tts.py) — env TTS_PROVIDER=cartesia
    └─ Post-call: quality scores → Supabase call_logs.extracted_info
```

### 3.2 Key defaults (`agent.py`, `config.json`)

| Setting | Value | Impact |
|---------|-------|--------|
| `stt_min_endpointing_delay` | **0.22s** (220ms) | Minimum silence before turn closes |
| `stt_max_endpointing_delay` | **0.8s** (was 1.8s) | Maximum wait when utterance may continue — Phase 1 tightened |
| `turn_detection` | `"stt"` | Silence/timer-based; Soniox semantic endpointing via plugin |
| `stt_provider` | `soniox` | stt-rt-v5 realtime WebSocket, `endpoint_latency_adjustment_level=2` |
| `llm_model` | `gemini-2.5-flash` | Good latency choice vs frontier models |
| `llm_max_completion_tokens` | 60–300 (clamped) | Caps generation length |
| `SYSTEM_PROMPT_TOKEN_BUDGET` | **1400 tokens** (was 2800) | Phase 1 prompt budget reduction for lower TTFT |
| `tts_provider` | Cartesia (`.env`) | Low TTFB (~50–150ms) |
| `allow_interruptions` | `true` | Barge-in enabled |
| `filler_phrases_enabled` | `true` (default) | Injects cached audio at 140ms if LLM slow — masks, does not reduce TTFA |
| `backchannel_enabled` | `false` in loader default; configurable | Partial-STT acknowledgements during long monologues |

### 3.3 Telephony (current — reverted to legacy Vobiz trunks)

| Setting | Value |
|---------|-------|
| Phone | `+918065481209` |
| Outbound trunk | `ST_GVRiRmsFYCiF` |
| Inbound trunk | `ST_ZuEUYkAinczf` |
| SIP domain | `776f26da.sip.vobiz.ai` |

---

## 4. Current latency budget

Realistic per-turn breakdown for **India PSTN + Telugu/HME preset** today:

| Stage | What happens | Typical range | Notes |
|-------|--------------|---------------|-------|
| **Pre-final endpointing** | User stops speaking → STT fires `is_final` | **300–1,800ms** | Controlled by `max_endpointing_delay`; **not in any chart** |
| **STT handoff** | `is_final` → `on_user_turn_completed` | **50–300ms** | Tracked as `stt_ms` when E2E code active |
| **LLM TTFT** | Turn handed to Gemini → first token | **800–1,100ms** | Tracked as `llm_ms`; SLA in circuit breaker: 3000ms |
| **Reply buffer** | First token → speakable Indic chunk (`indic_sentence_tokenizer.py`) | **500–1,500ms** | Gray “Reply buffer” bar in new chart |
| **TTS first audio** | Text → first PCM frame | **50–150ms** | Tracked as `tts_ms` |
| **PSTN / SIP** | LiveKit → Vobiz → handset | **200–500ms** | **Never measured** |
| **Voice-to-voice total (felt)** | | **~2,500–4,000ms** | |

### Human perception thresholds

| Latency | Perception |
|---------|------------|
| < 500ms | Genuinely conversational |
| 500–800ms | Thoughtful human pause |
| > 1,000ms | “Hello?” / overlap / hang-up risk |
| > 2,000ms | Voice-activated IVR |

**Today’s p50 is in the IVR band.**

---

## 5. What the codebase already does well

These align with industry best practices and should be **preserved**, not ripped out:

### 5.1 Streaming pipeline (partial overlap)

| Component | Implementation | File(s) |
|-----------|----------------|---------|
| STT streaming | Soniox stt-rt-v5 WebSocket, 100ms PCM chunks, partial + final events | LiveKit Soniox plugin, `stt_soniox.py` |
| LLM streaming | Token stream with TTFT measurement | `agent.py` `llm_node`, `gemini_llm.py` |
| TTS sentence streaming | Speaks first clause while LLM still generates | `agent.py` `tts_node`, `indic_sentence_tokenizer.py` |
| Filler guard | Pre-cached PCM at 140ms if LLM is slow | `filler_injection.py`, `greeting_cache.py` |

### 5.2 Turn-taking UX

| Feature | Status | File(s) |
|---------|--------|---------|
| Barge-in / interruptions | Enabled, min duration 0.35s | `agent.py` `AgentSession` |
| Silence guard | Dead-air prevention with graduated fillers | `silence_guard.py` |
| Backchannel | Optional acks during long caller monologues | `backchannel.py` |
| Circuit breaker + SLA tracking | Per-service latency with p95 in deque | `circuit_breaker.py` |

### 5.3 Model choices

- **Gemini 2.5 Flash** — appropriate speed tier for telephony (not a 4k-token frontier model for every turn)
- **Cartesia Sonic 3.5** — fast TTFB TTS with voice control (vs speech-to-speech lock-in)
- **Soniox stt-rt-v5** — Multilingual realtime STT with semantic endpointing (HME/Telugu/Hindi)

### 5.4 Observability scaffolding

- Prometheus histograms defined: `hermes_llm_ttft_ms`, `hermes_tts_first_chunk_ms`, `hermes_stt_partial_latency_ms`, `hermes_e2e_turn_latency_ms` (`re_voice_memory/metrics.py`)
- Per-call quality export: `latency_per_interaction` in `call_quality_scorer.to_dict()` → Supabase
- Dashboard chart: stacked bars in call log modal (`frontend/call-logs.js`, `frontend/styles.css`)

### 5.5 Regional deployment intent

- GCP deploy target: **`asia-south1` (Mumbai)** (`GCP_DEPLOY.md`)
- Vobiz SIP: India-local PSTN breakout
- Soniox API: Low-latency realtime STT with India language hints

---

## 6. Gaps vs. industry latency guide

| Guide principle | Status | Gap |
|-----------------|--------|-----|
| Measure voice-to-voice TTFA at caller’s ear | ❌ | Server-side partial metrics only |
| Report p50 + p95, not averages | ❌ | Per-turn bars, no distribution |
| Test on real PSTN | ✅ | Vobiz outbound/inbound |
| Stream all stages with no blocking handoffs | ⚠️ | LLM waits for full turn — no preemptive generation on stable partials |
| Co-locate media + inference | ⚠️ | LiveKit + Gemini + Cartesia likely multi-region |
| Semantic endpointing | ⚠️ | Soniox plugin enabled; LiveKit `turn_detection="stt"` still primary |
| Deliberate model + prompt sizing | ⚠️ | Good model; prompt budget reduced to 1400 tokens (Phase 1) |
| Regional media ingress | ⚠️ | Agent deployable in Mumbai; LiveKit SFU region unclear |
| Speech-to-speech option | ❌ | Pipeline only (acceptable for tool-calling + custom voice) |

### Latency-specific code gaps

| Issue | Location | Detail |
|-------|----------|--------|
| E2E Prometheus never wired | `re_voice_memory/metrics.py` | `record_e2e_turn_latency()` has zero call sites |
| E2E clock starts too late | `call_quality_scorer.record_user_final_transcript()` | Misses pre-`is_final` endpointing silence |
| `max_endpointing_delay` tuning | `agent.py`, `config.json` | Phase 1: 1.8s → **0.8s**; monitor mid-sentence cutoffs |
| No preemptive LLM | `agent.py` | `on_user_turn_completed` is the LLM entry — no partial-triggered inference |
| Indic TTS chunk gates (Phase 1 partial) | `indic_sentence_tokenizer.py` | Clause threshold 20 → 16 (12 with `LIVEKIT_TTS_AGGRESSIVE_CHUNKING=1`); reply buffer still measurable |

---

## 7. Target latency (after all fixes)

| Stage | Today | Target (optimized) | How |
|-------|-------|-------------------|-----|
| Endpointing / turn detection | 500–1,800ms | **150–400ms** | Semantic endpointing + lower max delay |
| STT finalization | 50–300ms | **50–120ms** | Streaming already; minor tuning |
| LLM TTFT | 800–1,100ms | **300–450ms** | Shorter prompt, regional Gemini, preemptive partial start |
| Reply buffer | 500–1,500ms | **150–350ms** | Aggressive first-clause TTS, lower grapheme thresholds |
| TTS first byte | 50–150ms | **50–100ms** | Already good |
| PSTN / SIP | 200–500ms | **150–300ms** | India-co-located agent + media |
| **p50 TTFA** | ~2.5–3.5s | **~750–950ms** | |
| **p95 TTFA** | ~3.5–4.5s | **~1.2–1.5s** | |

### Perception after optimization

| Threshold | Expected after fixes |
|-----------|---------------------|
| < 500ms | Occasional best turns only (PSTN floor ~550ms theoretical) |
| < 800ms | **Typical turn (p50)** |
| > 1s | **Slow turns (p95)** — load spikes, long user pauses mid-sentence |
| > 2s | Rare edge cases |

---

## 8. Optimization plan (phased)

### Phase 0 — Measure honestly (1–2 days)

**Objective:** Stop optimizing blind partial metrics.

#### 0.1 Define true TTFA clocks

| Timestamp | Source | Event |
|-----------|--------|-------|
| `t_user_stop` | `silence_guard.on_user_speech_finished()` | Best proxy for last caller syllable (VAD-based) |
| `t_stt_final` | `on_user_input_transcribed` when `is_final` | STT committed transcript |
| `t_turn_complete` | `on_user_turn_completed` | LiveKit handed turn to agent |
| `t_llm_first_token` | `llm_node` `_with_llm_latency` | First streamed token |
| `t_tts_first_frame` | `tts_node` `_yield_with_tts_latency` | First PCM to room |
| `t_pstn_estimate` | Configurable constant or SIP RTT probe | +150–300ms adjustment for phone leg |

#### 0.2 Extend `CallQualityScorer`

**File:** `call_quality_scorer.py`

Add fields:

```python
# New per-turn row shape (target)
{
  "turn": 1,
  "endpointing_ms": ...,   # t_user_stop → t_stt_final
  "stt_ms": ...,           # t_stt_final → t_turn_complete
  "llm_ms": ...,           # t_turn_complete → t_llm_first_token (or t_turn_complete if no split)
  "buffer_ms": ...,        # t_llm_first_token → t_tts_first_frame
  "tts_ms": ...,           # TTS synth only (subset of buffer)
  "ttfa_server_ms": ...,   # t_user_stop → t_tts_first_frame
  "ttfa_estimated_ms": ... # ttfa_server_ms + pstn_estimate_ms
}
```

Wire `record_e2e_turn_latency()` in `agent.py` at first TTS frame using `t_user_stop`.

#### 0.3 Dashboard: p50 / p95 rollups

**File:** `frontend/call-logs.js`

- Show per-turn stacked chart (already started)
- Add call-level summary: `p50 TTFA`, `p95 TTFA`, worst turn
- Flag calls where p95 > 1500ms

#### 0.4 External ground truth

- Record 20 outbound test calls from a real phone
- Offline analysis: waveform cross-correlation or manual annotation
- Compare against server-side `ttfa_estimated_ms` — calibrate `pstn_estimate_ms`

**Exit criteria:** New calls show full breakdown; p50/p95 visible; ±15% correlation with phone recordings.

---

### Phase 1 — Quick wins (2–3 days, ~1–1.5s savings)

Low risk, config + small code changes.

#### 1.1 Tighten endpointing

**Files:** `config.json`, `agent.py`, `config_store.py`, dashboard assistant settings

| Setting | Current | Target | Rationale |
|---------|---------|--------|-----------|
| `stt_max_endpointing_delay` | 1.8s | **0.6–0.8s** | Guide optimized band: 100–250ms ideal; 600–800ms safe for Telugu with barge-in |
| `stt_min_endpointing_delay` | 0.22s | **0.18–0.22s** | Keep — already near optimized |

**Risk:** More mid-sentence cutoffs on account numbers / addresses. **Mitigation:** barge-in + semantic endpointing (Phase 2).

**Expected savings:** **400–1,000ms** on turns where max delay was binding.

#### 1.2 Reduce reply buffer / speak sooner

**Files:** `indic_sentence_tokenizer.py`, `tts_text_transform.py`

- Lower `MIN_CHUNK_GRAPHEMES` for clause-boundary flush (comma, dash)
- Allow speak on **first stable clause** (~15–20 graphemes) for HME Telugu preset
- Add `tts_speak_on_first_clause: true` config flag per lang preset

**Expected savings:** **300–800ms**.

#### 1.3 Shrink prompt for latency-critical path

**Files:** `agent.py`, `knowledge_pack.py`, `re_voice_memory/inject.py`

- Split system prompt: **core persona** (< 800 tokens) always injected; heavy knowledge retrieved per-turn via RAG only when intent matches
- Lower `SYSTEM_PROMPT_TOKEN_BUDGET` from 2800 → **1200** for telephony profile
- Disable non-essential per-turn enrichments on first 3 turns

**Expected savings:** **200–400ms** on LLM TTFT.

#### 1.4 Enable Prometheus in production

**Files:** `re_voice_memory/metrics.py`, deploy config

- Set `METRICS_PORT` on agent worker
- Grafana dashboards: p50/p95 per stage, SLA violation rate from `circuit_breaker.py`

---

### Phase 2 — Structural improvements (1–2 weeks, ~0.5–1s additional)

#### 2.1 Semantic / adaptive endpointing

**Option A — Tune Sarvam streaming**

**Files:** `stt_provider.py`, `stt_stream.py`

- Expose Sarvam confidence + endpointing parameters in assistant settings
- A/B: 220ms vs 400ms vs semantic hybrid

**Option B — Evaluate Soniox for Telugu**

**Files:** `stt_soniox.py`, `stt_provider.py`

Soniox already implements:

```python
enable_endpoint_detection=True
endpoint_latency_adjustment_level=1  # v5 models
max_endpoint_delay_ms=...
```

Run parallel benchmark: Sarvam silence vs Soniox semantic on 50 HME calls.

**Expected savings:** **300–500ms** vs naive silence max.

#### 2.2 Preemptive LLM generation on stable partials

**Files:** `agent.py`, new `preemptive_turn.py`

Pattern:

1. On STT partial, when text matches “complete utterance” heuristic (question mark, sufficient length, stable for 200ms)
2. Start speculative `generate_reply` with partial transcript
3. On `is_final`, cancel/restart only if transcript changed materially
4. LiveKit 1.6+ may support `preemptive_generation` on `AgentSession` — evaluate native support first

**Expected savings:** **100–300ms** (overlaps STT finalization with LLM).

#### 2.3 Co-locate agent worker with media

**Files:** `GCP_DEPLOY.md`, LiveKit project settings

- Deploy agent worker in **`asia-south1`** (already documented)
- Confirm LiveKit SFU / SIP ingress region for India calls
- Prefer Gemini API endpoint with lowest RTT from Mumbai (measure, don’t assume)

**Expected savings:** **100–200ms** (2–4 fewer cross-region hops).

#### 2.4 Cartesia vs Sarvam TTS A/B

**Files:** `tts_provider.py`, `cartesia_tts.py`

- Benchmark Cartesia (current) vs Sarvam Bulbul v3 for Telugu HME
- Pick lowest **TTFB + quality** combo; Sarvam may reduce India round-trip

---

### Phase 3 — Advanced / optional (2–4 weeks)

#### 3.1 Speech-to-speech for latency-critical flows

Use pipeline (current) for tool-heavy turns (booking, CRM); route simple Q&A turns to a faster speech-to-speech model if platform adds support.

**Trade-off:** Lose Cartesia voice clone + granular tool control.

#### 3.2 Continuous latency regression CI

**New file:** `scripts/benchmark_ttfa.py`

- Synthetic audio fixtures → STT → LLM → TTS (no PSTN)
- Nightly p50/p95 vs baseline; fail CI if p95 regresses > 10%

#### 3.3 Per-caller adaptive endpointing

Short utterances (“haan”, “okay”) → low max delay  
Long explanations → higher max delay  
Use `TopicMemory` / turn length history to dynamic-adjust `max_endpointing_delay`.

---

## 9. Implementation checklist

| # | Task | Priority | Files | Est. impact |
|---|------|----------|-------|-------------|
| 1 | Start TTFA clock at `on_user_speech_finished` | P0 | `silence_guard.py`, `call_quality_scorer.py`, `agent.py` | Measurement |
| 2 | Wire `record_e2e_turn_latency()` | P0 | `agent.py`, `re_voice_memory/metrics.py` | Measurement |
| 3 | Add `endpointing_ms`, `buffer_ms`, `ttfa_estimated_ms` to scorer | P0 | `call_quality_scorer.py` | Measurement |
| 4 | Dashboard p50/p95 summary | P0 | `frontend/call-logs.js` | Visibility |
| 5 | Lower `max_endpointing_delay` to 0.7s | P1 | `config.json`, `agent.py` | −400–1000ms |
| 6 | Aggressive first-clause TTS for HME | P1 | `indic_sentence_tokenizer.py` | −300–800ms |
| 7 | Reduce system prompt budget | P1 | `agent.py`, knowledge inject | −200–400ms |
| 8 | Soniox semantic endpointing pilot | P2 | `stt_soniox.py`, `stt_provider.py` | −300–500ms |
| 9 | Preemptive LLM on partials | P2 | `agent.py` | −100–300ms |
| 10 | Mumbai agent deploy + RTT audit | P2 | `GCP_DEPLOY.md` | −100–200ms |
| 11 | Phone recording calibration | P2 | `scripts/` | Measurement accuracy |
| 12 | Latency regression CI | P3 | `scripts/benchmark_ttfa.py` | Prevent regressions |

---

## 10. Risks and tradeoffs

| Change | Risk | Mitigation |
|--------|------|------------|
| Lower `max_endpointing_delay` | Cut off mid-thought (“my budget is…”) | Semantic endpointing; barge-in; backchannel |
| Smaller system prompt | Less context → worse answers | RAG per intent; preserve full prompt for complex turns |
| Preemptive LLM | Wasted tokens if partial was wrong | Stability threshold; cancel on final mismatch |
| Aggressive TTS chunking | Unnatural mid-sentence pauses | Lang-preset tuning; clause boundaries only |
| Soniox switch | Telugu accuracy regression | A/B on real calls before cutover |

**Do not confuse filler injection with latency reduction.** `filler_injection.py` plays audio at 140ms to mask silence — it improves *perceived* responsiveness but does not change true TTFA. Keep it, but measure separately.

---

## 11. Success criteria

### Launch gates (Phase 0 + 1 complete)

- [ ] 10 consecutive real PSTN test calls with full `latency_per_interaction` rows (all fields populated)
- [ ] Server-side **p50 TTFA ≤ 1,200ms** (interim gate)
- [ ] Dashboard shows p50 + p95 per call
- [ ] Zero calls with unexplained > 3s TTFA without tagged cause (endpointing / tool call / LLM timeout)

### Target gates (Phase 1 + 2 complete)

- [ ] **p50 TTFA 750–950ms** on HME Telugu outbound calls
- [ ] **p95 TTFA ≤ 1,500ms**
- [ ] Phone recording calibration within **±15%** of server estimate
- [ ] Caller “hello?” / overlap rate down measurably vs baseline (manual review or interrupt_count trend)

### Non-goals

- Sub-500ms median on PSTN pipeline (physically unrealistic with current architecture)
- Replacing pipeline with speech-to-speech in Phase 1 (deferred to Phase 3 evaluation)

---

## 12. Reference: key source files

| Area | Path |
|------|------|
| Agent entry + session config | `agent.py` |
| Per-call latency scoring | `call_quality_scorer.py` |
| Prometheus metrics (partially wired) | `re_voice_memory/metrics.py` |
| SLA / p95 tracking | `circuit_breaker.py` |
| Soniox STT streaming (primary) | LiveKit Soniox plugin, `stt_soniox.py` |
| Sarvam STT streaming (legacy fallback) | `stt_stream.py`, `stt_provider.py` |
| Gemini LLM | `gemini_llm.py` |
| TTS factory | `tts_provider.py`, `cartesia_tts.py` |
| Indic sentence chunking (reply buffer) | `indic_sentence_tokenizer.py`, `tts_text_transform.py` |
| Filler audio bridge | `filler_injection.py`, `greeting_cache.py` |
| VAD user-stop signal | `silence_guard.py` |
| Backchannel | `backchannel.py` |
| Call log latency chart | `frontend/call-logs.js`, `frontend/styles.css` |
| Default config | `config.json` |
| GCP regional deploy | `GCP_DEPLOY.md` |

---

## 13. Appendix — latency timeline (today vs target)

### Today (typical ~3.2s turn)

```
Caller stops speaking
|-- pre-final endpointing -------- 700ms --|        ← NOT measured
|-- STT is_final → turn complete - 150ms --|        ← stt_ms
|-- LLM TTFT --------------------- 1000ms -|        ← llm_ms
|-- reply buffer ----------------- 900ms --|       ← hidden in old chart
|-- TTS first frame -------------- 80ms ----|        ← tts_ms
|-- PSTN to ear ------------------ 300ms --|        ← NOT measured
= ~3,130ms felt
```

### Target (optimized ~850ms p50)

```
Caller stops speaking
|-- semantic endpointing --------- 200ms --|
|-- STT handoff ------------------ 80ms ---|
|-- LLM TTFT (short prompt) ------ 350ms --|
|-- reply buffer (first clause) -- 120ms --|
|-- TTS first frame -------------- 70ms ---|
|-- PSTN to ear ------------------ 200ms --|
= ~1,020ms estimated (p50 target 750–950ms with overlap / best cases)
```

---

*This document should be updated after each phase with measured p50/p95 from production PSTN calls.*