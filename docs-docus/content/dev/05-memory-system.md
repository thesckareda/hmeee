# Memory System (Tiers & Data Flow)

**Source files:** `re_voice_memory/` (all modules), `REDIS_RETRIEVAL_OPTIMIZATION.md`

Three tiers:

### Tier 1: Hot Cache (Redis)
- Lead hash: one HGETALL per call
- Caller name, budget, last-call timestamp
- <1ms latency

### Tier 2: Durable Fallback (SQLite)
- Full profiles as JSON blobs
- Call history, objection outcomes
- Used when Redis is down

### Tier 3: File Store (~/.hermes)
- Lead profiles, call summaries, property graphs
- Backups & compliance audit trail

**Lead hash schema:**
```json
{
  "profile": "{\"name\": \"Ravi\", \"budget\": \"1.2 Cr\"}",
  "name": "Ravi",
  "intent_score": "72",
  "call_count": "3",
  "last_seen": "2026-07-02T16:30:00Z",
  "relations": [["Priya", "wife"], ["Suresh", "father"]],
  "slots": "{\"location\": \"Kokapet\"}",
  "history_head": "Yesterday asked about…"
}
```

## Related
- [Memory System (Memory Admin)](./05-memory-system)
- [Redis Plan](https://github.com/sushrutdeshpande0202/speaksy/blob/main/REDIS_RETRIEVAL_OPTIMIZATION.md)
