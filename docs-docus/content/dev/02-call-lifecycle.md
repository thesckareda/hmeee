# Call Lifecycle

**Source files:** `agent.py` (entrypoint, `unified_shutdown_hook`)

```
INBOUND CALL
    ↓
[setup] Create JobContext, identity shield (language detect)
    ↓
[prewarm] Load packs, config, knowledge from disk (cached)
    ↓
[inject] Fetch caller memory from Redis/SQLite, render system prompt
    ↓
[waves 0-6] Per-turn pipeline (repeats until end-of-call)
    ↓
[post-call] Extract memories (LLM), append to history, emit IQ events
    ↓
[backup] Write lead hash to Redis, persist to SQLite
    ↓
CALL ENDS
```

## Related
- [Turn Pipeline](./03-turn-pipeline)
- [Memory System](./05-memory-system)
