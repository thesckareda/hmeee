# Conversational IQ (18 Primitives)

**Source files:** `CONVERSATIONAL_IQ_18_PLAN.md`, `data/conversational_iq/registry.json`, `iq_registry.py`, `iq_assembler.py`

18 rules that fire on specific events and placeholders to personalize replies.

Examples:
- **IQ #1:** Stage transition → greeting tailored to stage
- **IQ #4:** Social graph (family mentions) → acknowledge relations
- **IQ #7:** Same-boat disclosure → social proof ("others in your situation…")
- **IQ #17:** Time-anchored memory → "you called 2 days back"
- **IQ #24:** Honest scarcity → "14 units left" (only if real)

Each rule is a **template string with placeholders**:

\`{name}\` = caller name
\`{value}\` = captured slot value
\`{delta}\` = time since last call
\`{story}\` = objection story (grounded fact)

**Disabling a primitive:**

\`\`\`json
"iq_disabled_primitives": ["iq_07_same_boat_disclosure"]
\`\`\`

## Related
- [Turn Pipeline](./03-turn-pipeline)
- [Call Planner](./04-conversational-iq)
