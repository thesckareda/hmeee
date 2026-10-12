"""Pre-call context injection — enrich system prompt with caller memories."""

from __future__ import annotations

from voice_memory.paths import get_project_memory_dir
from voice_memory.relevance import format_relevant_memories, select_relevant_memories
from voice_memory.token_utils import estimate_tokens


def inject_caller_context(
    caller_phone: str,
    base_instructions: str,
    *,
    max_memories: int = 3,
    max_memory_tokens: int = 150,
) -> str:
    """Enrich system prompt with relevant memories for this caller.

    Runs BEFORE agent init — file reads are <50ms.
    """
    if caller_phone == "unknown":
        return base_instructions

    memory_dir = get_project_memory_dir()

    caller_query = f"caller {caller_phone} preferences history appointments"
    relevant = select_relevant_memories(
        query=caller_query,
        max_results=max_memories,
        memory_dir=memory_dir,
    )

    if not relevant:
        return base_instructions

    memory_context = format_relevant_memories(relevant, max_chars=max_memory_tokens * 4)

    token_count = estimate_tokens(memory_context)
    if token_count > max_memory_tokens:
        memory_context = memory_context[: max_memory_tokens * 4]

    return (
        base_instructions
        + "\n\n[RETURNING CALLER CONTEXT — from previous interactions]\n"
        + memory_context
        + "\nUse this context to personalize responses. "
        + "Do not explicitly mention you are reading past records unless relevant."
    )
