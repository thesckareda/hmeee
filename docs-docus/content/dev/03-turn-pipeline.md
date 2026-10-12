# Turn Pipeline (Waves 0–6)

**Source files:** `turn_context_injection.py`, `iq_assembler.py`, `call_planner.py`

Every turn runs six waves in sequence, <5ms total.

### Wave 0: Identity Shield
- Detect language (LID)
- Route to correct preset (tinglish/hte/english)

### Wave 1: Knowledge Retrieval (Parallel)
- FAQ lookup (token overlap, threshold 0.45)
- Topic memory (semantic if Qdrant available)

### Wave 2: Proactive Insight
- Market facts matched to caller's intent
- Competitor positioning

### Wave 3: Planner (Parallel)
- Fill slots (regex-based)
- Advance stage (discover → match → close)
- Emit IQ events (stage_change, slot_filled)

### Wave 4: Objection Detect
- Pattern match on transcript
- Recall which scripts failed before for this caller

### Wave 5: Emotion + Health
- Detect frustration (simple heuristics)
- Check conversation momentum

### Wave 6: IQ Assembler
- Drain planner's event queue
- Look up rules in registry (by event + preset + language)
- Format rules with placeholders ({name}, {value}, {delta}, etc.)
- Inject ≤2 rules (prioritized) into LLM prompt

## Related
- [Conversational IQ](./04-conversational-iq)
- [Call Lifecycle](./02-call-lifecycle)
