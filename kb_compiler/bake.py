"""
Stage F + G — BAKE the cartridge, then (after the human gate) APPLY to runtime.

Bake writes everything under clients/<id>/ — nothing touches the live engine.
approve_and_apply() is the ONLY function that installs into data/ + config.json,
and it refuses to run unless validation passed and critic found no blockers.
Existing packs are backed up first; apply is reversible.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("kb-compiler")

COMPILER_VERSION = "1.0.0"

_PRESET_TO_PACK = {"tinglish": "tinglish", "telugu": "tinglish", "hte": "hte",
                   "hinglish": "hte", "hme": "hte", "english": "english"}


def clients_root(repo_root: Path) -> Path:
    return repo_root / "clients"


def client_dir(repo_root: Path, client_id: str) -> Path:
    return clients_root(repo_root) / client_id


def _source_hashes(kb_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    if kb_dir.is_dir():
        for path in sorted(kb_dir.rglob("*")):
            if path.is_file():
                hashes[str(path.relative_to(kb_dir))] = hashlib.sha1(path.read_bytes()).hexdigest()[:12]
    return hashes


def bake_cartridge(
    cdir: Path,
    manifest: dict,
    artifacts: dict,
    validation: dict,
    critic: dict,
) -> dict[str, Any]:
    """Write packs + lockfile under the cartridge dir. Never touches runtime."""
    from kb_compiler.llm import GEMINI_MODEL, STAGE_MODELS

    from kb_compiler.pipeline import write_json_atomic

    packs = cdir / "packs"
    packs.mkdir(parents=True, exist_ok=True)

    preset = str(manifest.get("lang_preset") or "tinglish")
    pack_id = _PRESET_TO_PACK.get(preset, "tinglish")

    company = dict(artifacts.get("company_knowledge") or {})
    company.setdefault("preset", pack_id)
    write_json_atomic(packs / "company_knowledge.json", company)

    inventory = dict(artifacts.get("inventory") or {})
    inventory.setdefault("preset", pack_id)
    write_json_atomic(packs / "inventory.json", inventory)

    market = dict(artifacts.get("market_competitor") or {})
    market.setdefault("preset", pack_id)
    write_json_atomic(packs / "market_competitor.json", market)

    write_json_atomic(cdir / "persona.json", artifacts.get("persona") or {})

    lock = {
        "client_id": cdir.name,
        "compiler_version": COMPILER_VERSION,
        "model": GEMINI_MODEL,
        "stage_models": dict(STAGE_MODELS),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "lang_preset": preset,
        "pack_id": pack_id,
        "industry": manifest.get("industry", "real_estate"),
        "source_hashes": _source_hashes(cdir / "kb"),
        "validation": validation,
        "critic": critic,
        "approved": False,
        "approved_at": None,
        "approved_by": None,
        "applied_at": None,
    }
    from kb_compiler.pipeline import write_json_atomic as _wja

    _wja(cdir / "compiled.lock.json", lock)
    logger.info("[BAKE] Cartridge baked at %s (validation ok=%s)", cdir, validation.get("ok"))
    return lock


def load_lock(cdir: Path) -> dict | None:
    path = cdir / "compiled.lock.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def load_cartridge_summary(cdir: Path) -> dict[str, Any]:
    """Listing card for the dashboard."""
    manifest: dict = {}
    yaml_path = cdir / "client.yaml"
    if yaml_path.is_file():
        try:
            import yaml

            manifest = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
        except Exception:
            pass
    lock = load_lock(cdir)
    kb_files = sorted(p.name for p in (cdir / "kb").glob("*") if p.is_file()) if (cdir / "kb").is_dir() else []
    return {
        "client_id": cdir.name,
        "industry": manifest.get("industry"),
        "lang_preset": manifest.get("lang_preset"),
        "persona": manifest.get("persona"),
        "kb_files": kb_files,
        "compiled": lock is not None,
        "validation_ok": bool(lock and lock.get("validation", {}).get("ok")),
        "approved": bool(lock and lock.get("approved")),
        "applied_at": lock.get("applied_at") if lock else None,
        "model": lock.get("model") if lock else None,
        "created_at": lock.get("created_at") if lock else None,
    }


def approve_and_apply(cdir: Path, repo_root: Path, approved_by: str = "") -> dict[str, Any]:
    """The human gate. Installs the cartridge into the live engine data/config.

    Refuses unless: cartridge baked, validation ok, no critic blockers.
    Backs up the packs it replaces to data/backups/<ts>/.
    """
    lock = load_lock(cdir)
    if lock is None:
        return {"success": False, "error": "Cartridge not compiled yet"}
    if not lock.get("validation", {}).get("ok"):
        return {"success": False, "error": "Validation failed — fix the KB and recompile",
                "errors": lock.get("validation", {}).get("errors")}
    blocking = (lock.get("critic") or {}).get("blocking") or []
    if blocking:
        return {"success": False, "error": "Critic found blocking issues", "blocking": blocking}

    pack_id = str(lock.get("pack_id") or "tinglish")
    data_dir = repo_root / "data"
    packs = cdir / "packs"

    targets = {
        packs / "company_knowledge.json": data_dir / "company_knowledge" / f"{pack_id}.json",
        packs / "inventory.json": data_dir / f"{pack_id}_inventory.json",
        packs / "market_competitor.json": data_dir / f"{pack_id}_market_competitor.json",
    }

    backup_dir = data_dir / "backups" / time.strftime("%Y%m%dT%H%M%S")
    installed: list[str] = []
    for src, dst in targets.items():
        if not src.is_file():
            continue
        if dst.is_file():
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, backup_dir / dst.name)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        installed.append(str(dst.relative_to(repo_root)))

    # Config render: persona + manifest knobs → config.json (merge, not replace)
    updates: dict[str, Any] = {}
    persona_path = cdir / "persona.json"
    if persona_path.is_file():
        persona = json.loads(persona_path.read_text(encoding="utf-8"))
        if persona.get("agent_instructions"):
            updates["agent_instructions"] = persona["agent_instructions"]
        if persona.get("first_line"):
            updates["first_line"] = persona["first_line"]
        if persona.get("stt_hints"):
            updates["stt_hints"] = persona["stt_hints"]

    manifest: dict = {}
    yaml_path = cdir / "client.yaml"
    if yaml_path.is_file():
        import yaml

        manifest = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    if manifest.get("lang_preset"):
        updates["lang_preset"] = manifest["lang_preset"]
    llm = manifest.get("llm") or {}
    if llm.get("model"):
        updates["llm_model"] = llm["model"]
        updates["gemini_model"] = llm["model"]
    if llm.get("temperature") is not None:
        updates["llm_temperature"] = llm["temperature"]
    if llm.get("max_tokens"):
        updates["llm_max_completion_tokens"] = llm["max_tokens"]
    voice = manifest.get("voice") or {}
    if voice.get("tts_provider"):
        updates["tts_provider"] = voice["tts_provider"]
    if voice.get("voice_id"):
        updates["tts_voice"] = voice["voice_id"]
    iq = manifest.get("iq") or {}
    if iq.get("disabled_primitives") is not None:
        updates["iq_disabled_primitives"] = iq["disabled_primitives"]

    if updates:
        from config_store import write_full_config

        write_full_config(updates)

    try:
        from assistant_settings import touch_reload_signal

        touch_reload_signal()
    except Exception:
        pass

    lock["approved"] = True
    lock["approved_at"] = datetime.now(timezone.utc).isoformat()
    lock["approved_by"] = approved_by
    lock["applied_at"] = lock["approved_at"]
    from kb_compiler.pipeline import write_json_atomic as _wja

    _wja(cdir / "compiled.lock.json", lock)

    logger.info("[APPLY] Cartridge %s installed: %s (+%d config keys)",
                cdir.name, installed, len(updates))
    return {
        "success": True,
        "installed": installed,
        "config_keys": sorted(updates.keys()),
        "backup_dir": str(backup_dir) if backup_dir.exists() else None,
        "note": "Knowledge packs load at next worker prewarm/restart; config hot-reloads.",
    }
