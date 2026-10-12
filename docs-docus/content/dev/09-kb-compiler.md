# KB Compiler (Pipeline & Validators)

**Source files:** `kb_compiler/*.py`, tests in `tests/test_kb_compiler.py`

## Stages

**A. Ingest** → multi-format source to chunks
**B. Extract** → chunks to grounded facts (Gemini)
**C. Synthesize** → facts to containers (Gemini)
**D. Validate** → schema checks + grounding (deterministic)
**E. Critic** → hallucination detection (Gemini)
**F. Bake** → write cartridge + lockfile

All Gemini calls use **3.1 Flash** exclusively.

## Grounding (The Trust Layer)

Every number in spoken output must exist in the KB sources.

Violated? Build is **BLOCKED** — ungrounded facts never reach callers.

Example failure:
- KB says: "Kokapet 42% appreciation"
- Generated story: "73% appreciation" (invented)
- Validator: ✗ BLOCKED

## Config Rendering

post-compile, `approve_and_apply()` merges persona + manifest into `config.json`:
- `agent_instructions`, `first_line` from generated persona
- `lang_preset`, `llm_model`, `tts_provider` from client.yaml

## Related
- [Platform Compiler (product)](../product/06-platform-compiler)
- [DOCUMENTATION_MASTER_PLAN](https://github.com/sushrutdeshpande0202/speaksy/blob/main/DOCUMENTATION_MASTER_PLAN.md)
