"""
Lightweight employee-question intent router (PLAN Phase 3.2).

Routes caller utterances to COMPANY / ROLE / POLICY / PROPERTY / SMALL_TALK
without ML dependencies — keyword heuristics only (<1ms).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class EmployeeIntent(str, Enum):
    COMPANY = "company"
    ROLE = "role"
    POLICY = "policy"
    PROPERTY = "property"
    SMALL_TALK = "small_talk"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class IntentResult:
    intent: EmployeeIntent
    confidence: float
    keywords: tuple[str, ...] = ()


_PATTERNS: list[tuple[EmployeeIntent, float, re.Pattern[str]]] = [
    (
        EmployeeIntent.SMALL_TALK,
        0.82,
        re.compile(
            r"\b(weekend|sunday|saturday|office\s*today|in\s*office|how\s*are\s*you|"
            r"weather|traffic|festival|diwali|sankranti|good\s*morning|good\s*evening)\b",
            re.I,
        ),
    ),
    (
        EmployeeIntent.ROLE,
        0.88,
        re.compile(
            r"(?:\b(?:your\s*role|what\s*do\s*you\s*do|your\s*job|who\s*is\s*your\s*boss|"
            r"reports?\s*to|manager|tenure|experience\s*here|how\s*long\s*have\s*you|"
            r"education|background|consultant|designation|"
            r"nee\s*role|meeru\s*enti|nuvvu\s*enti|role\s*enti|pani\s*enti|"
            r"cheptaru\s*enti|cheyaru\s*enti)\b|"
            r"రోల్|నీ\s*రోల్|మీ\s*రోల్|పని\s*ఏంటి|ఏంటి\s*నీ|ఏంటి\s*మీ|ఏమి\s*చేస్త|మీ\s*పని|నీ\s*పని)",
            re.I | re.UNICODE,
        ),
    ),
    (
        EmployeeIntent.POLICY,
        0.85,
        re.compile(
            r"\b(rera|refund|cancel|cancellation|brokerage|commission|token\s*amount|"
            r"payment\s*milestone|documentation|documents?\s*needed|registration|"
            r"legal|stamp\s*duty|home\s*loan\s*process|nri\s*process)\b",
            re.I,
        ),
    ),
    (
        EmployeeIntent.COMPANY,
        0.84,
        re.compile(
            r"(?:\b(?:founded|founder|owner|mission|vision|team\s*size|employees?|"
            r"how\s*many\s*people|awards?|office\s*location|office\s*hours|"
            r"working\s*hours|headquarter|company\s*age|vk\s*realty|who\s*started|"
            r"evaru\s*start|owner\s*peru|company\s*gurinchi|founder\s*evadu|"
            r"kaun\s*hai\s*owner|kisne\s*start)\b|"
            r"ఎవరు\s*స్టార్ట్|ఓనర్|ఫౌండర్|కంపెనీ\s*గురించి|స్థాపక|పేరేంటి)",
            re.I | re.UNICODE,
        ),
    ),
    (
        EmployeeIntent.PROPERTY,
        0.86,
        re.compile(
            r"\b(price|pricing|kitna|cost|bhk|sqft|project|builder|godrej|lodha|"
            r"prestige|inventory|listing|possession|rera\s*id|site\s*visit|"
            r"brochure|emi|flat|apartment|villa|compare|competitor)\b",
            re.I,
        ),
    ),
]


def classify_employee_intent(transcript: str) -> IntentResult:
    """Classify a caller utterance into an employee-knowledge intent."""
    text = (transcript or "").strip()
    if len(text) < 3:
        return IntentResult(EmployeeIntent.UNKNOWN, 0.0)

    best: IntentResult | None = None
    for intent, base_conf, pattern in _PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        kw = (match.group(0).lower(),)
        conf = min(0.98, base_conf + min(0.08, len(kw[0]) / 40))
        candidate = IntentResult(intent, conf, kw)
        if best is None or candidate.confidence > best.confidence:
            best = candidate

    if best:
        return best
    return IntentResult(EmployeeIntent.UNKNOWN, 0.35)