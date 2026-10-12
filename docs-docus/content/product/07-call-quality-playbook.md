# Call Quality Playbook

> Reading metrics & understanding what "good" looks like

## Dashboard Metrics

### Per-Call Metrics

| Metric | Good | Cause if Bad |
|---|---|---|
| **STT Latency** | <300ms | Soniox endpoint slow, noisy audio |
| **Agent Response Time** | <1s | LLM slow (check model), network lag |
| **Turn Duration** | 20–90s | Caller talking, not agent repeating |
| **IQ Fire Rate** | 30%+ | Primitives triggering (name, objection, urgency) |
| **Backchannel Rate** | 10–20% | Caller monologuing, agent saying "hmm" |

### Aggregate Metrics

| KPI | Target | How to Improve |
|---|---|---|
| **Booking Rate** | 15–25% | Better objection scripts, urgency facts |
| **Call Duration (avg)** | 4–8 min | Shorten discovery, faster close |
| **Intent Score Growth** | +5 pts/call | Better slot capture, memory reuse |
| **Memory Hit Rate** | 60%+ | Keep Remember ON, add more priors |

## IQ Fire Rate

**What:** % of turns that got a conversational-IQ rule injected.

**Why it matters:** 18 primitives make the agent sound human. High fire rate = personalization.

**Low rate?** Check:
- Config: `conversational_iq_v2_enabled: true`
- Registry: do rules match your industry/language?
- Events: are slots being filled? Is the planner triggering?

## Memory Hit Rate

**What:** % of callers where prior history was found & injected.

**Why it matters:** Returning callers convert better when the agent says *"you called 3 days ago…"*

**Low rate?** Check:
- Config: `remember_caller_enabled: true`
- Redis running: `redis-cli ping` (should return "PONG")
- Callers coming back: hit rate only counts returns

## Latency Budget (under 5ms per turn)

Wave 0 (identity): <1ms
Wave 1 (retrieval): <1ms
Wave 2 (proactive): <0.5ms
Wave 3 (planner): <1ms
Wave 4 (objection): <0.5ms
Wave 5 (emotion): <0.5ms
Wave 6 (IQ): <1ms

**Total p95:** <5ms added by the engine (STT, LLM, TTS dominate)

If waves exceed budget:
- Check: Redis vs. SQLite fallback (fallback is slower)
- Check: LLM model (larger models = slower)
- Check: Disable unused primitives via `iq_disabled_primitives`

## Related
- [Dashboard Guide](../02-dashboard-guide)
- [Turn Pipeline (dev)](../../dev/03-turn-pipeline)
