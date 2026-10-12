# Audio Layer (PCM, Silence Guard, Backchannel)

**Source files:** `silence_guard.py`, `backchannel.py`, `greeting_cache.py`, `tts_text_transform.py`

## Silence Guard (4-tier rescue)

Tier 1 (2s): backchannel ("hmm?")
Tier 2 (4s): name bridge ("Ravi, andi…")
Tier 3 (6s): objection rehash ("About the price…")
Tier 4 (8s): transfer ("Let me connect you…")

Config: `silence_guard_enabled: true`

## Backchannel

Pre-recorded ("hmm", "avunu", "sare") — PCM-cached, 0ms delay.

Config: `backchannel_enabled: true`

## Greeting Cache

First-line greeting stored as PCM → no TTS latency on call open.

## Prosody Transform

Add punctuation (!, ?) to hint Cartesia emphasis.
Flatten rhetorical tags ("kada?" → "kada.") for naturalness.

## Related
- [Providers](./07-providers)
- [Turn Pipeline](./03-turn-pipeline)
