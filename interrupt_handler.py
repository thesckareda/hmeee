import logging

logger = logging.getLogger("inbound-voice-agent")

_current_tts_task = None
_tts_cancelled = False
interrupt_count = 0


def get_interrupt_instruction(
    lang_preset: str | None = None,
    live_config: dict | None = None,
) -> str:
    from language_router import interrupt_ack_phrases

    ack = interrupt_ack_phrases(lang_preset, live_config)
    return (
        "INTERRUPT PROTOCOL: If you see [AGENT WAS INTERRUPTED] in your context, "
        "STOP mid-sentence — do not finish the old clause. Do NOT restart or re-introduce yourself. "
        f"Acknowledge with {ack} (2 words max), then answer the caller's NEW intent only. "
        "Never resume the interrupted sentence. The call is still in progress."
    )


# Backward-compatible default (tinglish)
INTERRUPT_INSTRUCTION = get_interrupt_instruction("tinglish")


def create_interrupt_handlers(
    session,
    agent_tools,
    *,
    lang_preset: str | None = None,
    live_config: dict | None = None,
):
    global _current_tts_task, _tts_cancelled, interrupt_count

    from language_router import interrupt_ack_phrases

    ack = interrupt_ack_phrases(lang_preset, live_config)

    def _on_interrupted(ev):
        global _current_tts_task, _tts_cancelled, interrupt_count

        if _current_tts_task is not None and not _current_tts_task.done():
            _current_tts_task.cancel()
            _tts_cancelled = True

        if isinstance(ev, dict):
            interrupted_at = str(ev.get("last_phrase", ""))
        else:
            interrupted_at = str(getattr(ev, "text_content", "") or "")[-160:]

        try:
            agent = getattr(agent_tools, "_agent", None)
            chat_ctx = getattr(agent, "_chat_ctx", None) if agent else None
            if chat_ctx is not None:
                chat_ctx.add_message(
                    role="system",
                    content=(
                        "[AGENT WAS INTERRUPTED mid-response. Last phrase spoken: "
                        + interrupted_at
                        + ". The call is still in progress — do NOT re-introduce yourself or restart the conversation. "
                        + f"Acknowledge with {ack} and listen for the caller to continue.]"
                    ),
                )
        except Exception as exc:
            logger.debug("[INTERRUPT] context note skipped: %s", exc)

        agent_tools.agent_is_speaking = False
        interrupt_count += 1

        logger.info(f"[INTERRUPT] Agent interrupted. Total: {interrupt_count}")

    def _on_speech_started(ev):
        global _tts_cancelled
        agent_tools.agent_is_speaking = True
        _tts_cancelled = False

    def _on_speech_stopped(ev):
        agent_tools.agent_is_speaking = False

    return {
        "interrupted": _on_interrupted,
        "speech_started": _on_speech_started,
        "speech_stopped": _on_speech_stopped,
    }