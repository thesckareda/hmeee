# Providers (STT, LLM, TTS)

**Source files:** `stt_provider.py`, `gemini_llm.py`, `tts_provider.py`

Factory pattern: config selects which provider to use.

## STT (Speech-to-Text)

- **Soniox** (default) — <300ms latency, language ID
- **Google Cloud Speech** (alt)

Config: `stt_provider: soniox`, `stt_endpoint_sensitivity: high`

## LLM

- **Gemini** (default) — 2.5-flash-lite, pro, ultra variants
- **Claude** (alt) — opus, sonnet, haiku

Config: `llm_model: gemini-2.5-flash-lite`, `llm_temperature: 0.7`

## TTS (Text-to-Speech)

- **Cartesia** (default) — best prosody, ORG voices
- **Sarvam** (alt) — best for Indian languages
- **ElevenLabs** (alt) — custom voices

Config: `tts_provider: cartesia`, `tts_voice: <id>`, `cartesia_voice_speed: 1.0`

## Related
- [Audio Layer](./08-audio-layer)
- [Deployment](./13-deployment)
