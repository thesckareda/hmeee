"""
Pipeline orchestrator — one call runs the whole compiler with progress state
the dashboard can poll (clients/<id>/compile_state.json).
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("kb-compiler")

_STAGES = [
    ("ingest", 10), ("extract", 30), ("synthesize", 65),
    ("validate", 75), ("critic", 90), ("bake", 100),
]


def load_manifest(cdir: Path) -> dict:
    import yaml

    path = cdir / "client.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"client.yaml missing in {cdir}")
    manifest = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    manifest.setdefault("client_id", cdir.name)
    manifest.setdefault("industry", "real_estate")
    manifest.setdefault("lang_preset", "tinglish")
    return manifest


def _state_path(cdir: Path) -> Path:
    return cdir / "compile_state.json"


def write_json_atomic(path: Path, obj: Any) -> None:
    """tmp + rename so a crash mid-write never leaves corrupt JSON behind."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _write_state(cdir: Path, **fields: Any) -> None:
    state = read_compile_state(cdir)
    state.update(fields)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    write_json_atomic(_state_path(cdir), state)


def read_compile_state(cdir: Path) -> dict[str, Any]:
    path = _state_path(cdir)
    if not path.is_file():
        return {"stage": "idle", "pct": 0, "running": False}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"stage": "idle", "pct": 0, "running": False}


def reconcile_state(cdir: Path, task_alive: bool) -> dict[str, Any]:
    """State says running but no live task = the process died mid-compile
    (restart/crash). Convert the orphan into a clear, recoverable error so
    the dashboard never spins forever."""
    state = read_compile_state(cdir)
    if state.get("running") and not task_alive:
        _write_state(
            cdir, running=False, stage="error",
            error="Compile was interrupted (server restarted) — run it again.",
            message="interrupted — recompile to continue",
        )
        state = read_compile_state(cdir)
    return state


async def run_compile(client_id: str, repo_root: Path) -> dict[str, Any]:
    """Ingest → extract → synthesize → validate → critic → bake.

    Extraction on Gemini 3.1 Flash, synthesis on Gemini 3.1 Pro, critic on a
    separate model (kb_compiler.llm stage contract). Progress + errors land
    in compile_state.json; artifacts + lock land in the cartridge dir.
    """
    from kb_compiler.bake import bake_cartridge, client_dir
    from kb_compiler.critic import run_critic
    from kb_compiler.ingest import ingest_sources
    from kb_compiler.synthesize import extract_facts, synthesize_all
    from kb_compiler.validate import validate_artifacts

    cdir = client_dir(repo_root, client_id)
    t0 = time.perf_counter()
    try:
        manifest = load_manifest(cdir)
        _write_state(cdir, running=True, stage="ingest", pct=2, error=None,
                     message="reading knowledge sources")

        sources = [str(s) for s in (manifest.get("knowledge_sources") or [])]
        kb_dir = cdir / "kb"
        if kb_dir.is_dir():
            sources.extend(str(p.relative_to(cdir)) for p in sorted(kb_dir.glob("*")) if p.is_file())
        chunks = ingest_sources(sources, cdir)
        if not chunks:
            raise ValueError("No readable knowledge sources — drop files into kb/ or set knowledge_sources")
        from kb_compiler.llm import EXTRACT_MODEL, SYNTH_MODEL

        _write_state(cdir, stage="extract", pct=10,
                     message=f"{len(chunks)} chunks — extracting facts ({EXTRACT_MODEL})")

        facts = await extract_facts(chunks)
        if not facts:
            raise ValueError("Fact extraction produced nothing — is the KB readable text?")
        _write_state(cdir, stage="synthesize", pct=30,
                     message=f"{len(facts)} grounded facts — building containers ({SYNTH_MODEL})")

        def _progress(msg: str) -> None:
            _write_state(cdir, message=msg)

        artifacts = await synthesize_all(facts, manifest, progress=_progress)
        _write_state(cdir, stage="validate", pct=65, message="running deterministic validators")

        validation = validate_artifacts(artifacts, chunks)
        _write_state(cdir, stage="critic", pct=75,
                     message=f"validation {'OK' if validation['ok'] else 'FAILED'} — critic pass")

        critic = await run_critic(artifacts, chunks, manifest)
        _write_state(cdir, stage="bake", pct=90, message="writing cartridge + lockfile")

        # Persist raw artifacts + grounding facts for the review screen
        write_json_atomic(cdir / "artifacts.json", artifacts)
        write_json_atomic(cdir / "facts.json", facts)
        lock = bake_cartridge(cdir, manifest, artifacts, validation, critic)

        elapsed = round(time.perf_counter() - t0, 1)
        ok = validation["ok"] and not (critic.get("blocking") or [])
        _write_state(
            cdir, running=False, stage="done", pct=100,
            message=(f"compiled in {elapsed}s — "
                     + ("READY FOR REVIEW" if ok else "BLOCKED: fix errors and recompile")),
            validation=validation, critic=critic, elapsed_s=elapsed,
        )
        return {"success": True, "validation": validation, "critic": critic, "lock": lock}
    except Exception as exc:
        logger.exception("[PIPELINE] compile failed for %s", client_id)
        _write_state(cdir, running=False, stage="error", error=str(exc),
                     message=f"compile failed: {exc}")
        return {"success": False, "error": str(exc)}
