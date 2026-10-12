# VK Realty HME — Feature finalization (session rollup)

Everything shipped in this release cycle, how to turn it on, and how to verify.

---

## 1. Caller history (cross-call memory)

**What:** Post-call summary saved per phone; injected on the **next** call during prewarm (parallel with other memory loads).

| Piece | Location |
|-------|----------|
| Save on shutdown | `agent.py` → `append_call_history` |
| Load at prewarm | `re_voice_memory/inject.py` → `_task_caller_history` |
| Storage | `~/.hermes/re-voice-memory/leads/{phone}/caller_history.json` |

**Verify:** Complete a call → check file exists → call same number → logs show `[INJECT]` with history block.

---

## 2. Zero-API token counter

**What:** All token budgets use local L1 cache + L2 calibrated multipliers. No `countTokens` API. Multipliers self-update from live LLM `usage_metadata.prompt_tokens` after each reply.

| Piece | Location |
|-------|----------|
| Counter | `token_counter.py` |
| Calibration | `agent.py` `llm_node` → `ingest_calibration()` |
| Budget enforcement | `inject.py`, `context_summariser.py`, `agent.py` prompt trim |

**Verify:** `python3 -m pytest tests/test_token_counter.py`

**Ops:** `python3 scripts/calibrate_token_multipliers.py` — prints current multipliers.

---

## 3. Parallel per-turn context injection

**What:** Steps 4–12 in `on_user_turn_completed` can run in dependency-safe waves instead of strictly sequential.

| Piece | Location |
|-------|----------|
| Engine | `turn_context_injection.py` |
| Hook | `agent.py` → `compute_turn_injections` + `apply_turn_injection_result` |
| Flag | `config.json` → `"parallel_turn_injectors_enabled": false` (default **off**) |

**Waves:** (4∥5) → 6 → (7∥8)+advance → 9→10 → 11–12. Identity trap stays in `agent.py` as hard gate.

**Verify:** `python3 -m pytest tests/test_turn_context_injection.py`  
**Benchmark:** `python3 scripts/benchmark_turn_injection.py`  
**Enable A/B:** Set `parallel_turn_injectors_enabled: true` in `config.json`, restart agent.

**Latency logs:** Every 20 turns + call shutdown → `[TURN-INJECT] latency stats` (p50/p95/p99).

---

## 4. Custom ambient — CICD-1

**What:** `CICD-1.ogg` bundled as `cicd_1` (default ambient).

| Piece | Location |
|-------|----------|
| File | `CICD-1.ogg` (project root) |
| Resolver | `background_audio.py` → `_CUSTOM_SOUND_FILES` |
| Config | `ambient_sound.sound_type: "cicd_1"` |

**Verify:** Log line `[AMBIENT] Started — type=cicd_1 volume=0.12`

---

## 5. Organisations & tenancy

**What:** Multi-workspace dashboard auth (local SQLite) + Supabase `org_id` on agents/calls when v6 migration applied.

| Piece | Location |
|-------|----------|
| Local users/orgs | `org_store.py` → `data/tenancy.db` |
| Request scoping | `tenancy.py` |
| API | `org_routes.py` → `/api/organizations/...` |
| Supabase | `sql/supabase_migration_v6_orgs.sql` |

**Verify:** Dashboard org switcher; `GET /api/organizations` returns workspaces.

---

## 6. Latency instrumentation

| Signal | Where |
|--------|-------|
| Turn inject p50/p95/p99 | `turn_context_injection.py` → shutdown log |
| LLM TTFT | `agent.py` `llm_node` → `record_llm_ttft` |
| STT / E2E | `call_quality_scorer.py` |
| Reference doc | `LATENCY_OPTIMIZATION.md` |
| Context engineering | `CONTEXT_ENGINEERING.md` |

---

## 7. Config flags (master list)

| Flag | Default | File |
|------|---------|------|
| `parallel_turn_injectors_enabled` | `false` | `config.json` |
| `speculative_reply_enabled` | `false` | `config.json` |
| `llm_context_prewarm` | `true` | `config.json` |
| `system_prompt_token_budget` | `1000` | `config.json` |
| `ambient_sound.enabled` | `true` | `config.json` |
| `ambient_sound.sound_type` | `cicd_1` | `config.json` |

Secrets stay in **`.env` only** (Supabase, LiveKit, API keys).

---

## 8. Tests

```bash
cd vk-realty-hme
python3 -m pytest tests/ -q
```

| Suite | Covers |
|-------|--------|
| `test_token_counter.py` | Local counter, cache, calibration, truncate |
| `test_turn_context_injection.py` | Sequential vs parallel equivalence |

---

## 9. Quick ops commands

```bash
# Health + env check
python3 scripts/verify_setup.py

# Agent status
python3 agent_process.py status

# Fresh restart (agent + caches)
python3 -c "from agent_process import restart_agent_fresh; restart_agent_fresh()"

# Dashboard (local dev)
DISABLE_AUTH=true python3 -m uvicorn ui_server:app --host 0.0.0.0 --port 8000

# Demo call
open http://127.0.0.1:8000/demo

# Outbound (auth required unless DISABLE_AUTH=true)
curl -X POST http://127.0.0.1:8000/api/call/single -H 'Content-Type: application/json' -d '{"phone":"+91XXXXXXXXXX"}'
```

---

## 10. Pre-production checklist

- [ ] `sql/` migrations applied on Supabase
- [ ] `SUPABASE_SERVICE_ROLE_KEY` set (recommended)
- [ ] `SUPABASE_RECORDINGS_BUCKET=call-recordings`
- [ ] `DEFAULT_AGENT_ID` matches active agent in DB
- [ ] `DISABLE_AUTH=false` + strong dashboard password
- [ ] `parallel_turn_injectors_enabled` A/B tested before enabling in prod
- [ ] `pytest tests/` green
- [ ] Test inbound + outbound call with CICD ambient
- [ ] Caller history file written after hangup