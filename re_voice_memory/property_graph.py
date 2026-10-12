"""Property interest graph — per-lead reactions to listings."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml

from re_voice_memory.constants import MEMORY_DIR
from re_voice_memory.lead import _get_phone_lock
from voice_memory.fs import atomic_write_text


@dataclass
class PropertySignal:
    listing_id: str
    signal: Literal["liked", "rejected", "viewed", "offered", "sent", "enquired"]
    price_reaction: Literal["positive", "negative", "neutral", "no_reaction"] = "no_reaction"
    features_liked: list[str] = field(default_factory=list)
    objections_raised: list[str] = field(default_factory=list)
    call_date: str = ""
    price_at_signal: int | None = None

    def __post_init__(self):
        if not self.call_date:
            self.call_date = datetime.now(timezone.utc).isoformat()


@dataclass
class PropertyInterestGraph:
    phone: str
    signals: list[PropertySignal] = field(default_factory=list)


def _graph_path(phone: str) -> Path:
    return MEMORY_DIR / "leads" / phone / "property_interests.md"


def _parse_yaml_frontmatter(text: str) -> tuple[dict, str]:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            frontmatter_str = text[4:end]
            body = text[end + 5:]
            frontmatter = yaml.safe_load(frontmatter_str) or {}
            return frontmatter, body
    return {}, text


def _parse_signal_line(line: str) -> PropertySignal | None:
    stripped = line.strip()
    if not stripped.startswith("- "):
        return None
    content = stripped[2:]
    parts = {}
    for chunk in content.split(" | "):
        if "=" not in chunk:
            continue
        key, _, val = chunk.partition("=")
        parts[key.strip()] = val.strip()
    required = {"listing_id", "signal", "date"}
    if not required.issubset(parts.keys()):
        return None
    return PropertySignal(
        listing_id=parts["listing_id"],
        signal=parts.get("signal", "viewed"),  # type: ignore[arg-type]
        price_reaction=parts.get("price_reaction", "no_reaction"),  # type: ignore[arg-type]
        features_liked=[f.strip() for f in parts.get("features", "").split(",") if f.strip()],
        objections_raised=[o.strip() for o in parts.get("objections", "").split(",") if o.strip()],
        call_date=parts["date"],
        price_at_signal=int(parts["price"]) if parts.get("price") else None,
    )


def load_property_graph(phone: str) -> PropertyInterestGraph:
    path = _graph_path(phone)
    if not path.exists():
        return PropertyInterestGraph(phone=phone)

    text = path.read_text(encoding="utf-8")
    frontmatter, body = _parse_yaml_frontmatter(text)

    signal_section = ""
    in_signal_section = False
    for line in body.split("\n"):
        if line.startswith("## "):
            in_signal_section = True
            continue
        if in_signal_section:
            signal_section += line + "\n"

    signals: list[PropertySignal] = []
    for line in signal_section.split("\n"):
        sig = _parse_signal_line(line)
        if sig:
            signals.append(sig)

    signals.sort(key=lambda s: s.call_date)
    return PropertyInterestGraph(phone=phone, signals=signals)


def _serialize_signal(sig: PropertySignal) -> str:
    parts = [
        f"listing_id={sig.listing_id}",
        f"signal={sig.signal}",
        f"date={sig.call_date}",
    ]
    if sig.price_reaction != "no_reaction":
        parts.append(f"price_reaction={sig.price_reaction}")
    if sig.features_liked:
        parts.append(f"features={','.join(sig.features_liked)}")
    if sig.objections_raised:
        parts.append(f"objections={','.join(sig.objections_raised)}")
    if sig.price_at_signal is not None:
        parts.append(f"price={sig.price_at_signal}")
    return "- " + " | ".join(parts)


async def save_property_graph(graph: PropertyInterestGraph) -> Path:
    lock = await _get_phone_lock(graph.phone)
    async with lock:
        return _save_property_graph_nolock(graph)


def _save_property_graph_nolock(graph: PropertyInterestGraph) -> Path:
    path = _graph_path(graph.phone)
    path.parent.mkdir(parents=True, exist_ok=True)

    graph.signals.sort(key=lambda s: s.call_date)

    frontmatter = {
        "phone": graph.phone,
        "count": len(graph.signals),
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "type": "property_graph",
        "ttl_days": 365,
    }

    lines = ["---"]
    lines.append(yaml.dump(frontmatter, default_flow_style=False).rstrip())
    lines.append("---")
    lines.append("")
    lines.append("## Signal History")
    lines.append("")
    for sig in graph.signals:
        lines.append(_serialize_signal(sig))
    lines.append("")

    atomic_write_text(str(path), "\n".join(lines))
    return path


async def add_signal(
    phone: str,
    listing_id: str,
    signal: str,
    price_reaction: str = "no_reaction",
    features_liked: list[str] | None = None,
    objections_raised: list[str] | None = None,
    price_at_signal: int | None = None,
) -> PropertyInterestGraph:
    lock = await _get_phone_lock(phone)
    async with lock:
        graph = load_property_graph(phone)
        graph.signals.append(
            PropertySignal(
                listing_id=listing_id,
                signal=signal,  # type: ignore[arg-type]
                price_reaction=price_reaction,  # type: ignore[arg-type]
                features_liked=list(features_liked or []),
                objections_raised=list(objections_raised or []),
                price_at_signal=price_at_signal,
            )
        )
        _save_property_graph_nolock(graph)
        return graph


def get_rejected_listings(phone: str) -> list[str]:
    graph = load_property_graph(phone)
    return sorted({s.listing_id for s in graph.signals if s.signal == "rejected"})


def get_liked_listings(phone: str) -> list[str]:
    graph = load_property_graph(phone)
    return sorted({s.listing_id for s in graph.signals if s.signal in ("liked", "offered", "enquired")})


def get_lead_feature_preferences(phone: str) -> dict[str, int]:
    graph = load_property_graph(phone)
    counts: dict[str, int] = {}
    for sig in graph.signals:
        for feat in sig.features_liked:
            counts[feat] = counts.get(feat, 0) + 1
    return counts


def format_graph_for_prompt(phone: str, max_tokens: int = 120) -> str:
    graph = load_property_graph(phone)
    if not graph.signals:
        return "No property history."

    liked = get_liked_listings(phone)
    rejected = get_rejected_listings(phone)
    prefs = get_lead_feature_preferences(phone)
    top_features = sorted(prefs.items(), key=lambda x: x[1], reverse=True)
    top_feat_str = ", ".join(f[0] for f in top_features)

    parts = [f"Previously seen: {len(graph.signals)} properties."]
    if liked:
        parts.append(f"Liked: {', '.join(liked)}.")
    if rejected:
        parts.append(f"Rejected: {', '.join(rejected)}.")
    if top_feat_str:
        parts.append(f"Top features: {top_feat_str}.")

    result = " ".join(parts)
    from token_counter import truncate_to_token_budget_sync

    return truncate_to_token_budget_sync(result, max_tokens, "hme")
