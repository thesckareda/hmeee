"""Post-call audits — AI phrase detection, knowledge compliance."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("inbound-voice-agent")


def load_forbidden_phrases() -> list[str]:
    path = Path(__file__).resolve().parent / "data" / "employee_backstory.json"
    if not path.is_file():
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return list(data.get("forbidden_phrases") or [])
    except Exception:
        return []


def scan_transcript_for_ai_phrases(transcript: str) -> list[str]:
    """Return forbidden phrases found in assistant lines of transcript."""
    if not transcript:
        return []
    forbidden = [p.lower() for p in load_forbidden_phrases()]
    if not forbidden:
        from identity_shield import _AI_DISCLOSURE_QUICK_HINTS

        forbidden = list(_AI_DISCLOSURE_QUICK_HINTS)

    hits: list[str] = []
    for line in transcript.splitlines():
        if not line.upper().startswith("[ASSISTANT]"):
            continue
        lowered = line.lower()
        for phrase in forbidden:
            if phrase in lowered and phrase not in hits:
                hits.append(phrase)
    return hits


def build_audit_report(transcript: str, quality_scores: dict | None = None) -> dict[str, Any]:
    ai_hits = scan_transcript_for_ai_phrases(transcript)
    report: dict[str, Any] = {
        "ai_phrase_hits": ai_hits,
        "ai_disclosure_risk": len(ai_hits) > 0,
    }
    if quality_scores:
        report["quality_scores"] = quality_scores
    if ai_hits:
        logger.warning("[CALL-AUDIT] AI phrase detected in transcript: %s", ai_hits)
    return report