# System Architecture

**Source files:** `agent.py`, `SPEAKSY_PLATFORM_MASTER_PLAN.md` §1–2

Three layers serve all clients identically:

```
┌── LAYER 1: SHARED INFRA ─────────────────────────────┐
│  LiveKit SFU · Redis (hot cache) · Qdrant (optional) │
└──────────────────────────────────────────────────────┘
         ↓
┌── LAYER 2: CLIENT CONTAINERS (N per VM) ─────────────┐
│  speaksy-engine:<version>  (same image for every client)
│  mounts: clients/<id>/ (knowledge packs)
│  env: CLIENT_ID, REDIS_URL, provider API keys
└──────────────────────────────────────────────────────┘
         ↓
┌── LAYER 3: BUILD-TIME ───────────────────────────────┐
│  KB Compiler (Gemini 3.1 Flash)
│  IQ template generator · Cartridge baking
└──────────────────────────────────────────────────────┘
```

**The contract:** Engine code is frozen (identical bytes everywhere). All variation comes from:
- Knowledge packs (FAQ, properties, market data)
- Config (LLM model, TTS provider, language preset)
- Cartridges (compiled from KBs)

### Call Path (Per-Turn)

```
STT (Soniox)
    ↓
[WAVE 0] Identity shield / language router
    ↓
[WAVE 1] Knowledge pack lookup ∥ topic memory (parallel)
    ↓
[WAVE 2] Proactive insight
    ↓
[WAVE 3] Planner (slot VALUES) ∥ stage advance
    ↓
[WAVE 4] Objection detect + failed-script memory
    ↓
[WAVE 5] Emotion + conversation health
    ↓
[WAVE 6] IQ ASSEMBLER (≤2 rules per turn)
    ↓
LLM (client's model/temperature)
    ↓
TTS Text Transform (prosody, flatten tags)
    ↓
TTS (Cartesia/Sarvam/ElevenLabs)
    ↓
Agent PCM out
```

**Latency budget:** <5ms added by the engine. STT/LLM/TTS dominate.

## Related
- [Call Lifecycle](./02-call-lifecycle)
- [Turn Pipeline](./03-turn-pipeline)
- [Platform Master Plan](https://github.com/sushrutdeshpande0202/speaksy/blob/main/SPEAKSY_PLATFORM_MASTER_PLAN.md)
