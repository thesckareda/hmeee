"""
TTS pronunciation normalizer — spoken-form numbers, BHK, phone digits.

Hook before synthesis so Cartesia/Gemini speak property terms naturally.
"""
from __future__ import annotations

import re

# ₹1.2 Cr / 1.2 Crore / 85 L / 85 Lakhs
_CR_RE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:Cr|Crore|Crores|crore|crores)\b",
    re.I,
)
_L_RE = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:L|Lakh|Lakhs|lakh|lakhs)\b",
    re.I,
)
_BHK_RE = re.compile(r"\b(\d)\s*BHK\b", re.I)
_SQFT_RE = re.compile(r"\b(\d[\d,]*)\s*(?:sqft|sq\s*ft|square\s*feet)\b", re.I)
_PHONE_RE = re.compile(r"\b(\+?91[\s-]?)?(\d{10})\b")
_PER_SQFT_RE = re.compile(
    r"(?<![\w.])(\d[\d,]*)\s+per\s+sqft\b",
    re.I,
)

_ONES = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four",
    5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
    10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen",
    14: "fourteen", 15: "fifteen", 16: "sixteen", 17: "seventeen",
    18: "eighteen", 19: "nineteen",
}
_TENS = {
    2: "twenty", 3: "thirty", 4: "forty", 5: "fifty",
    6: "sixty", 7: "seventy", 8: "eighty", 9: "ninety",
}


def _int_to_words(n: int) -> str:
    if n < 0:
        return str(n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        t, o = divmod(n, 10)
        return _TENS[t] if o == 0 else f"{_TENS[t]} {_ONES[o]}"
    if n < 1000:
        h, rem = divmod(n, 100)
        head = f"{_ONES[h]} hundred"
        return head if rem == 0 else f"{head} {_int_to_words(rem)}"
    if n < 100_000:
        th, rem = divmod(n, 1000)
        head = f"{_int_to_words(th)} thousand"
        return head if rem == 0 else f"{head} {_int_to_words(rem)}"
    return str(n)


def _decimal_to_words(num_str: str) -> str:
    if "." not in num_str:
        return _int_to_words(int(num_str))
    whole, frac = num_str.split(".", 1)
    whole_w = _int_to_words(int(whole)) if whole else "zero"
    frac_digits = " ".join(_ONES[int(d)] for d in frac if d.isdigit())
    return f"{whole_w} point {frac_digits}".strip()


def _normalize_cr(match: re.Match) -> str:
    return f"{_decimal_to_words(match.group(1))} crores"


def _normalize_lakh(match: re.Match) -> str:
    return f"{_decimal_to_words(match.group(1))} lakhs"


def _normalize_bhk(match: re.Match) -> str:
    digit = match.group(1)
    word = _ONES.get(int(digit), digit)
    return f"{word} B H K"


def _normalize_sqft(match: re.Match) -> str:
    raw = match.group(1).replace(",", "")
    return f"{_int_to_words(int(raw))} square feet"


def _normalize_per_sqft(match: re.Match) -> str:
    raw = match.group(1).replace(",", "")
    return f"{_int_to_words(int(raw))} per square foot"


def _normalize_phone(match: re.Match) -> str:
    prefix = match.group(1) or ""
    digits = match.group(2)
    spoken = " ".join(_ONES[int(d)] for d in digits)
    if prefix:
        return f"plus nine one {spoken}"
    return spoken


def normalize_for_tts(text: str, *, lang_preset: str | None = None) -> str:
    """
    Convert written property shorthand to spoken form for TTS.

    Preserves Telugu script and English property terms; only normalizes
    numbers, currency, BHK, sqft, and phone numbers.
    """
    if not text or not text.strip():
        return text

    out = text
    preset = (lang_preset or "tinglish").strip().lower()

    out = _PHONE_RE.sub(_normalize_phone, out)
    out = _CR_RE.sub(_normalize_cr, out)
    out = _L_RE.sub(_normalize_lakh, out)
    out = _BHK_RE.sub(_normalize_bhk, out)
    out = _PER_SQFT_RE.sub(_normalize_per_sqft, out)
    out = _SQFT_RE.sub(_normalize_sqft, out)

    if preset == "tinglish":
        # Keep English words in Latin script; collapse double spaces
        out = re.sub(r"\s{2,}", " ", out)

    return out.strip() if out != text else text