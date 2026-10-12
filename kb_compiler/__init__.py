"""
KB Compiler — SPEAKSY_PLATFORM_MASTER_PLAN §4, end to end.

Drop a knowledge base + one client.yaml → validated, grounded cartridge:
    ingest → extract → synthesize → validate → critic → bake → (human gate) → apply

Generation runs on Gemini 3.1 per stage: Pro for synthesis, Flash for
extraction, a separate critic pass (see kb_compiler.llm).
Nothing generated here ever runs at call time — build-time only.
"""
from kb_compiler.pipeline import run_compile, read_compile_state  # noqa: F401
from kb_compiler.bake import approve_and_apply, load_cartridge_summary  # noqa: F401
