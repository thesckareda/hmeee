# Troubleshooting

> Symptom → Cause → Fix

## Agent doesn't respond

**Detect:** Silence, caller hears nothing.

**Likely cause:** LLM call timed out or failed.

**Fix:**
1. Check logs: `grep "LLM.*error\|timeout" logs/app.log`
2. Verify API key: `config.json` has `gemini_api_key` or env `GEMINI_API_KEY`
3. Check model: `llm_model` in `config.json` should be valid (e.g. `gemini-2.5-flash-lite`)
4. Network: curl to Gemini endpoint manually

## Caller not remembered

**Detect:** Returning caller treated as fresh, no prior history shown.

**Likely cause:** Remember button OFF or Redis down.

**Fix:**
1. Check setting: Dashboard → Settings → **Remember the caller** should be ON
2. Check Redis: `redis-cli ping` → should return "PONG"
3. If Redis down: engine falls back to SQLite (slower, but works)
4. Check directory: `~/.hermes/re-voice-memory/leads/<phone>/` exists

## Slow turn responses

**Detect:** Agent takes 3–5 sec to respond (should be <1s).

**Likely cause:** LLM slow, Redis slow, or STT endpoint overloaded.

**Fix:**
1. Check latency: Dashboard → Analytics → STT Latency (p95)
   - If >500ms: Soniox issue, try `stt_endpoint_sensitivity: low`
2. Check LLM: Change `llm_model` to faster option (`gemini-2.5-flash-lite` < `gemini-2.0-pro`)
3. Check Redis: `redis-cli info stats | grep total_commands_processed` (should be growing)
   - If stuck: Redis might be OOM (check `MEMORY STATS`)

## Bad pronunciation (TTS)

**Detect:** Agent says "Kokapet" wrong or stumbles on names.

**Fix:**
1. Check `tts_text_transform.py` — add regex rule for the word
2. Or: Switch TTS provider: `tts_provider: sarvam` (better for Telugu)
3. Cartesia prosody: add exclamation / question mark to hint emphasis

## Knowledge base incorrect

**Detect:** Agent says wrong price / info about properties.

**Likely cause:** KB not recompiled, or grounding validation missed it.

**Fix:**
1. Recompile: Dashboard → **Platform** → select client → **Compile** again
2. Check validation: Did it pass grounding (all numbers in KB)?
3. If stuck: Use **Forget** to wipe the cartridge, drop new KB, recompile

## Tests failing

**Detect:** `pytest tests/` shows failures.

**Fix:**
1. Most common: mock LLM unavailable. Set `GEMINI_API_KEY` env var
2. Check file paths: tests assume `tinglish-vk-realty/` as root
3. Run single test: `pytest tests/test_iq_foundations.py::test_wave_6_latency -v`

## Related
- [Deployment (dev)](../../dev/13-deployment) — Docker, env setup
- [Runbook (dev)](../../dev/14-runbook) — ops procedures
