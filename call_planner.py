"""
Multi-turn call agenda planner — explicit stage machine with discovery slots.

Stages: OPEN → DISCOVER → MATCH → HANDLE_OBJECTION → CLOSE → WRAPUP
One discovery question per turn; never re-ask a filled slot.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum


class TurnStage(str, Enum):
    OPEN = "open"
    DISCOVER = "discover"
    MATCH = "match"
    HANDLE_OBJECTION = "handle_objection"
    CLOSE = "close"
    WRAPUP = "wrapup"


# Backward-compatible alias used by tests / dashboards
AgendaPhase = TurnStage

_STAGE_ORDER = (
    TurnStage.OPEN,
    TurnStage.DISCOVER,
    TurnStage.MATCH,
    TurnStage.HANDLE_OBJECTION,
    TurnStage.CLOSE,
    TurnStage.WRAPUP,
)

_DISCOVERY_SLOTS = ("budget", "location", "bhk", "timeline", "purpose")

_SLOT_PATTERNS: dict[str, re.Pattern] = {
    "budget": re.compile(
        r"\b(budget|crore|cr\b|lakh|l\b|afford|kitna|price|emi|80\s*l|90\s*l|1\.\d+\s*cr)\b",
        re.I,
    ),
    "location": re.compile(
        r"\b(kokapet|gachibowli|financial|narsingi|neopolis|tellapur|thane|zirakpur|"
        r"area|location|sector|daggara|side|corridor)\b",
        re.I,
    ),
    "bhk": re.compile(r"\b(\d\s*bhk|2bhk|3bhk|4bhk|villa|flat|apartment)\b", re.I),
    "timeline": re.compile(
        r"\b(ready|possession|202[4-9]|urgent|soon|month|year|immediately|"
        r"under\s*construction|investment|self\s*use|own\s*stay)\b",
        re.I,
    ),
    "purpose": re.compile(
        r"\b(invest|investment|rent|nri|family|school|office|commute|"
        r"self\s*use|own\s*stay|first\s*home)\b",
        re.I,
    ),
}

_OBJECTION_PATTERNS = re.compile(
    r"\b(too\s*(?:much|high|expensive)|mehnga|ekkuva|costly|compare|"
    r"alochin|think|discuss|wife|husband|parents|not\s*ready|wait|"
    r"loan|doubt|unsure|parledu)\b",
    re.I,
)

# IQ #9 — repeat-complaint detection ("cheppanu kada", "already told", "bola tha")
_REPEAT_COMPLAINT_PATTERNS = re.compile(
    r"(cheppanu\s*kada|cheppa\s*kada|already\s*told|already\s*said|"
    r"bola\s*tha|bataya\s*tha|\brepeat\b|same\s*question|"
    r"malli\s*adugutunnar|phir\s*se\s*pooch)",
    re.I,
)

# IQ 0.1 — tight capture groups for slot VALUES (confidence-gated: no match → no value)
_SLOT_VALUE_PATTERNS: dict[str, re.Pattern] = {
    "budget": re.compile(
        r"(\d+(?:\.\d+)?\s*(?:crores?|cr|lakhs?|lacs?)\b"
        r"(?:\s*(?:to|-|se|nunchi)\s*\d+(?:\.\d+)?\s*(?:crores?|cr|lakhs?|lacs?)\b)?)",
        re.I,
    ),
    "location": re.compile(
        r"\b(kokapet|gachibowli|financial\s*district|narsingi|neopolis|tellapur|"
        r"thane|zirakpur)\b",
        re.I,
    ),
    "bhk": re.compile(r"\b(\d\s*bhk|villa|apartment|flat)\b", re.I),
    "timeline": re.compile(
        r"\b(ready\s*to\s*move|under\s*construction|immediately|urgent|"
        r"202[4-9]|\d+\s*(?:months?|years?))\b",
        re.I,
    ),
    "purpose": re.compile(
        r"\b(investment|own\s*stay|self\s*use|rent(?:al)?|first\s*home|nri)\b",
        re.I,
    ),
}

_CLOSE_PATTERNS = re.compile(
    r"\b(visit|site\s*visit|book|schedule|saturday|sunday|confirm|"
    r"callback|call\s*back|fix|slot)\b",
    re.I,
)

_INSTRUCTIONS: dict[TurnStage, str] = {
    TurnStage.OPEN: (
        "[CALL AGENDA — OPEN]\n"
        "Goal: warm greeting, understand why they called.\n"
        "• ONE open question only — area or requirement.\n"
        "• Do not pitch projects yet."
    ),
    TurnStage.DISCOVER: (
        "[CALL AGENDA — DISCOVER]\n"
        "Goal: fill missing slots — budget, location, BHK, timeline, purpose.\n"
        "• Ask ONE discovery question per turn — never stack questions.\n"
        "• Never re-ask a slot already filled (see FILLED SLOTS below).\n"
        "• Use soft statements with a tail question, not interrogation."
    ),
    TurnStage.MATCH: (
        "[CALL AGENDA — MATCH]\n"
        "Goal: present ≤2 best-fit properties only.\n"
        "• One differentiator per project — not a spec sheet.\n"
        "• After matching, ask ONE reaction question."
    ),
    TurnStage.HANDLE_OBJECTION: (
        "[CALL AGENDA — HANDLE OBJECTION]\n"
        "Goal: acknowledge concern, reframe value, do not argue.\n"
        "• Validate first (ardham ayindi / nijame), then ONE clarifying question.\n"
        "• Do not counter-push in the same sentence."
    ),
    TurnStage.CLOSE: (
        "[CALL AGENDA — CLOSE]\n"
        "Goal: YOU propose and own the next step — the caller only confirms.\n"
        "• Propose a concrete slot: 'Nenu Saturday 11AM slot block cheskondam ante, "
        "mee calendar free unda?' Never ask 'when can you visit?'.\n"
        "• Risk reversal — remove the pressure: 'Nenu meeku oka 20-minute visit "
        "suggest chestanu — meere decide cheskondi fit avtundo ledo.'\n"
        "• Fallback only if the proposed slot fails: either/or close "
        "('Saturday morning a, leda Sunday better a?').\n"
        "• Summarize fit in one line, then propose the time."
    ),
    TurnStage.WRAPUP: (
        "[CALL AGENDA — WRAPUP]\n"
        "Goal: confirm the concrete step YOU booked, warm person-note, thank caller.\n"
        "• Restate the owned next step ('So Saturday 11AM ki nenu confirm chestunnanu') "
        "+ one human detail from the call."
    ),
}

# IQ #11 — one-two discovery: statement (gives value) + question (asks value)
_NEXT_SLOT_QUESTIONS: dict[str, str] = {
    "budget": (
        "Kokapet side pricing last year lo decent ga move ayindi andi — "
        "so budget range entha, rough ga cheppandi?"
    ),
    "location": (
        "Chala mandi ippudu ORR daggara connectivity chusi decide avtunnaru — "
        "meeru ye area prefer chestunnaru, Kokapet side na, Gachibowli side na?"
    ),
    "bhk": (
        "3BHK lo resale demand konchem better untundi andi — "
        "meeku 2BHK a, 3BHK a?"
    ),
    "timeline": (
        "Ready-to-move lo premium untundi, under-construction lo price advantage — "
        "meeku edi work avtundi?"
    ),
    "purpose": (
        "Own stay ki school-side areas, investment ki corridor-side better andi — "
        "meedi own stay aa, investment angle na?"
    ),
}


@dataclass
class SlotValue:
    """Value-bearing discovery slot (IQ 0.1). Truthy when filled."""
    filled: bool = False
    value: str = ""
    turn: int = 0
    ts: float = 0.0
    history: list[tuple[str, int]] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.filled

    def copy(self) -> "SlotValue":
        return SlotValue(
            filled=self.filled,
            value=self.value,
            turn=self.turn,
            ts=self.ts,
            history=list(self.history),
        )


def _coerce_slot(value) -> SlotValue:
    """Accept legacy bool inputs (clone paths / old tests) transparently."""
    if isinstance(value, SlotValue):
        return value
    return SlotValue(filled=bool(value))


@dataclass
class DiscoveryState:
    budget: SlotValue = field(default_factory=SlotValue)
    location: SlotValue = field(default_factory=SlotValue)
    bhk: SlotValue = field(default_factory=SlotValue)
    timeline: SlotValue = field(default_factory=SlotValue)
    purpose: SlotValue = field(default_factory=SlotValue)

    def __post_init__(self) -> None:
        for slot in _DISCOVERY_SLOTS:
            setattr(self, slot, _coerce_slot(getattr(self, slot)))

    def filled(self) -> list[str]:
        return [s for s in _DISCOVERY_SLOTS if getattr(self, s)]

    def missing(self) -> list[str]:
        return [s for s in _DISCOVERY_SLOTS if not getattr(self, s)]

    def filled_with_values(self) -> list[tuple[str, str]]:
        """Filled slots that carried a confidently captured value (IQ #15)."""
        return [
            (s, getattr(self, s).value)
            for s in _DISCOVERY_SLOTS
            if getattr(self, s) and getattr(self, s).value
        ]

    def value_of(self, slot: str) -> str:
        sv = getattr(self, slot, None)
        return sv.value if isinstance(sv, SlotValue) else ""

    def fill_from_text(self, text: str, turn: int = 0) -> list[dict]:
        """Fill slots; return IQ events (slot_filled / slot_delta)."""
        lowered = (text or "").lower()
        events: list[dict] = []
        for slot, pattern in _SLOT_PATTERNS.items():
            if not pattern.search(lowered):
                continue
            sv: SlotValue = getattr(self, slot)
            value_match = _SLOT_VALUE_PATTERNS[slot].search(text or "")
            new_value = " ".join(value_match.group(1).split()) if value_match else ""

            if not sv.filled:
                sv.filled = True
                sv.value = new_value
                sv.turn = turn
                sv.ts = time.time()
                events.append({"event": "slot_filled", "slot": slot, "value": new_value})
            elif new_value and sv.value and new_value.lower() != sv.value.lower():
                # Contradiction — caller changed their answer (IQ #16)
                sv.history.append((sv.value, sv.turn))
                events.append({
                    "event": "slot_delta", "slot": slot,
                    "old": sv.value, "new": new_value,
                })
                sv.value = new_value
                sv.turn = turn
                sv.ts = time.time()
            elif new_value and not sv.value:
                sv.value = new_value
        return events


class CallPlanner:
    """Tracks call stage, discovery slots, and injects stage-appropriate instructions."""

    def __init__(self) -> None:
        self._stage = TurnStage.OPEN
        self._slots = DiscoveryState()
        self._project_discussed = False
        self._objection_active = False
        self._visit_intent = False
        self._turn_count = 0
        # Legacy fields for clone_turn_state / tests
        self._budget_mentioned = False
        self._area_mentioned = False
        # IQ 0.2 — event queue drained by the Wave 6 assembler
        self._iq_events: list[dict] = []

    def pop_iq_events(self) -> list[dict]:
        events, self._iq_events = self._iq_events, []
        return events

    def next_missing_slot(self) -> str:
        missing = self._slots.missing()
        return missing[0] if missing else ""

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def phase(self) -> TurnStage:
        return self._stage

    @property
    def _phase(self) -> TurnStage:
        """Backward-compat for clone_turn_state / legacy callers."""
        return self._stage

    @_phase.setter
    def _phase(self, value: TurnStage) -> None:
        self._stage = value

    @property
    def slots(self) -> DiscoveryState:
        return self._slots

    def process_utterance(self, text: str) -> TurnStage:
        lowered = (text or "").lower().strip()
        if not lowered:
            return self._stage

        self._turn_count += 1
        slot_events = self._slots.fill_from_text(text or "", turn=self._turn_count)
        for ev in slot_events:
            ev["stage"] = self._stage.value
        self._iq_events.extend(slot_events)

        if _SLOT_PATTERNS["budget"].search(lowered):
            self._budget_mentioned = True
        if _SLOT_PATTERNS["location"].search(lowered):
            self._area_mentioned = True
        if re.search(r"\b(godrej|prestige|sobha|my\s*home|project|builder|krishe|madison)\b", lowered):
            self._project_discussed = True
        obj_match = _OBJECTION_PATTERNS.search(lowered)
        if obj_match:
            if not self._objection_active:
                self._iq_events.append({"event": "objection", "type": obj_match.group(1)})
            self._objection_active = True
        if _CLOSE_PATTERNS.search(lowered):
            if not self._visit_intent:
                self._iq_events.append({"event": "close_intent"})
            self._visit_intent = True
        if _REPEAT_COMPLAINT_PATTERNS.search(lowered):
            self._iq_events.append({"event": "repeat_complaint"})

        prev_stage = self._stage
        self._maybe_advance()
        if self._stage != prev_stage:
            self._iq_events.append({
                "event": "stage_change",
                "from": prev_stage.value,
                "to": self._stage.value,
            })
        return self._stage

    def _maybe_advance(self) -> None:
        if self._stage == TurnStage.OPEN and self._turn_count >= 1:
            self._stage = TurnStage.DISCOVER

        missing = self._slots.missing()
        if self._stage == TurnStage.DISCOVER and len(missing) <= 2:
            self._stage = TurnStage.MATCH

        if self._stage in (TurnStage.DISCOVER, TurnStage.MATCH) and self._objection_active:
            self._stage = TurnStage.HANDLE_OBJECTION

        if self._stage == TurnStage.HANDLE_OBJECTION and not self._objection_active:
            self._stage = TurnStage.MATCH if self._project_discussed else TurnStage.DISCOVER

        if self._stage in (TurnStage.MATCH, TurnStage.HANDLE_OBJECTION) and self._visit_intent:
            self._stage = TurnStage.CLOSE

        if self._stage == TurnStage.CLOSE and self._visit_intent and self._turn_count > 3:
            self._stage = TurnStage.WRAPUP

    def get_instruction(self) -> str:
        base = _INSTRUCTIONS.get(self._stage, _INSTRUCTIONS[TurnStage.OPEN])
        filled = self._slots.filled()
        parts = [base]
        if filled:
            labels = []
            for slot in filled:
                value = self._slots.value_of(slot)
                labels.append(f"{slot} ({value})" if value else slot)
            parts.append(f"FILLED SLOTS (do not re-ask): {', '.join(labels)}.")
        if self._stage == TurnStage.DISCOVER:
            missing = self._slots.missing()
            if missing:
                next_slot = missing[0]
                parts.append(
                    f"NEXT DISCOVERY (one question only): {_NEXT_SLOT_QUESTIONS.get(next_slot, next_slot)}"
                )
        return "\n".join(parts)

    def advance_for_deal_stage(self, deal_stage: str) -> None:
        """Nudge agenda forward when sales funnel stage advances."""
        mapping = {
            "awareness": TurnStage.OPEN,
            "interest": TurnStage.DISCOVER,
            "consideration": TurnStage.MATCH,
            "intent": TurnStage.CLOSE,
            "purchase": TurnStage.CLOSE,
            "booked": TurnStage.WRAPUP,
        }
        target = mapping.get((deal_stage or "").lower())
        if not target:
            return
        cur_idx = _STAGE_ORDER.index(self._stage)
        new_idx = _STAGE_ORDER.index(target)
        if new_idx > cur_idx:
            self._stage = target