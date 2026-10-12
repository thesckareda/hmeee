"""
Soniox STT domain context — preset-aware vocabulary for real-estate calls.

Built from docs/SONIOX_STT_CONTEXT_DRAFT.md + tinglish_inventory.json.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any

# Hyderabad western corridor — 8 active projects (tinglish_inventory.json)
_TINGLISH_PROJECTS = (
    "Godrej Madison Avenue",
    "Godrej Properties",
    "My Home Krishe",
    "My Home Constructions",
    "Rajapushpa Provincia",
    "Rajapushpa Properties",
    "Prestige Clairmont",
    "Prestige High Fields",
    "ASBL Broadway",
    "Candeur Skyline",
    "Vertex Florenza",
    "Sobha Waterfront",
)

_TINGLISH_LOCATIONS = (
    "Hyderabad",
    "Secunderabad",
    "Kokapet",
    "Neopolis",
    "Gachibowli",
    "Financial District",
    "Nanakramguda",
    "Narsingi",
    "Puppalaguda",
    "Tellapur",
    "Kollur",
    "Somajiguda",
    "HITEC City",
    "Raidurg",
    "ORR",
    "Outer Ring Road",
)

_TINGLISH_DOMAIN_TERMS = (
    "VK Realty",
    "Arjun",
    "Vikram Khanna",
    "RERA",
    "BHK",
    "2 BHK",
    "3 BHK",
    "4 BHK",
    "Crore",
    "Cr",
    "Lakh",
    "L",
    "sqft",
    "square feet",
    "super built-up",
    "carpet area",
    "UDS",
    "EMI",
    "home loan",
    "pre-approval",
    "CIBIL",
    "down payment",
    "registration",
    "stamp duty",
    "GST",
    "PLC",
    "Preferred Location Charges",
    "floor rise",
    "facing",
    "east facing",
    "ready to move",
    "under construction",
    "possession",
    "handover",
    "brokerage",
    "zero brokerage",
    "site visit",
    "sample flat",
    "model apartment",
    "clubhouse",
    "amenities",
    "rental yield",
    "appreciation",
    "investment",
    "NRI",
    "pre-launch",
    "TDR",
    "SBI",
    "HDFC",
    "ICICI",
    "Axis Bank",
)

_TINGLISH_MIXED_PHRASES = (
    "flat kavali",
    "rate entha",
    "site visit eppudu",
    "budget entha",
    "emi entha",
    "possession eppudu",
    "book cheyyali",
    "andi",
    "garu",
    "avunu",
    "sare",
    "cheppandi",
    "vinipisthundaa",
    "parledu",
    "nunchi",
    "unte",
)

_TINGLISH_GENERAL = [
    ("domain", "Real estate sales — residential apartments and villas"),
    ("company", "VK Realty — Hyderabad property consultant"),
    ("agent", "Arjun — Senior Real Estate Consultant"),
    ("market", "Hyderabad western IT corridor"),
    (
        "locations",
        "Kokapet, Neopolis, Gachibowli, Financial District, Narsingi, Tellapur",
    ),
    ("languages", "Telugu and English code-mixed (Telugish); English-only OK"),
    ("setting", "Inbound/outbound phone call — property enquiry"),
    ("topic", "Budget, BHK, location, pricing, site visit, home loan"),
    ("speakers", "2 speakers — caller (customer) and agent (Arjun)"),
    (
        "instructions",
        "Transcribe mixed Telugu-English as spoken; keep English property terms "
        "in English (BHK, Crore, site visit, EMI, RERA). Do not transliterate "
        "property jargon into Telugu script.",
    ),
]

_TINGLISH_TEXT = (
    "VK Realty is a RERA-registered Hyderabad real estate agency focused on western "
    "corridors: Kokapet, Neopolis, Gachibowli, Financial District, and Narsingi. "
    "Consultant Arjun helps IT professionals, families, and NRIs buy 2–4 BHK apartments. "
    "Typical calls cover budget in Crores or Lakhs, configuration, possession date, "
    "price per sqft, RERA, home loan rates, and booking site visits. Callers mix Telugu "
    "(andi, garu, avunu, sare) with English property terms."
)

_HME_LOCATIONS = (
    "Mumbai",
    "Thane",
    "Kanjurmarg",
    "Mulund",
    "Zirakpur",
    "Chandigarh",
)

_HME_TERMS = (
    "VK Realty",
    "Arjun",
    "BHK",
    "Crore",
    "Lakh",
    "sqft",
    "EMI",
    "RERA",
    "site visit",
    "Hinglish",
    "Lodha",
    "Godrej",
    "Prestige",
)


@lru_cache(maxsize=4)
def _tinglish_terms_tuple() -> tuple[str, ...]:
    return (
        _TINGLISH_PROJECTS
        + _TINGLISH_LOCATIONS
        + _TINGLISH_DOMAIN_TERMS
        + _TINGLISH_MIXED_PHRASES
        + (
            "DLF",
            "Sobha",
            "Brigade",
            "Aparna",
            "Candeur",
            "ASBL",
            "Rajapushpa",
        )
    )


def build_soniox_context(preset: str | None = None) -> Any | None:
    """Return Soniox ContextObject for the active language preset."""
    try:
        from livekit.plugins.soniox import ContextGeneralItem, ContextObject
    except ImportError:
        return None

    key = (preset or "tinglish").strip().lower()
    if key in ("hme", "hi-en-mr", "hinglish", "hi_en_mr"):
        return ContextObject(
            general=[
                ContextGeneralItem(key="domain", value="Real estate sales"),
                ContextGeneralItem(key="company", value="VK Realty"),
                ContextGeneralItem(
                    key="locations",
                    value="Mumbai Thane Kanjurmarg Mulund Zirakpur Chandigarh",
                ),
            ],
            terms=list(_HME_TERMS + _HME_LOCATIONS),
        )

    return ContextObject(
        general=[ContextGeneralItem(key=k, value=v) for k, v in _TINGLISH_GENERAL],
        text=_TINGLISH_TEXT,
        terms=list(_tinglish_terms_tuple()),
    )