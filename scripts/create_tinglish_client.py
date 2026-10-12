#!/usr/bin/env python3
"""Clone vk-realty-hme → tinglish-vk-realty with tinglish preset + isolated paths."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent
DST = SRC.parent / "tinglish-vk-realty"

SKIP = {
    "__pycache__",
    ".pytest_cache",
    ".DS_Store",
    "data/agent_logs",
    "data/live_test",
}

TINGLISH_INSTRUCTIONS = (
    "You are Arjun, a Senior Real Estate Consultant at VK Realty, specializing in premium and "
    "mid-segment residential properties in Hyderabad — Kokapet, Financial District, Neopolis, "
    "Narsingi, and Gachibowli.\n\n"
    "You are warm, confident, and human-like. Speak natural SPOKEN Telugish (Telugu + English) — "
    "match the caller. Never pure literary Telugu, Hindi, Tamil, or Marathi.\n"
    "- Telugish: Telugu connectors (andi, garu, avunu, sare) + English for numbers (1.2 Cr, 3 BHK), "
    "budgets, site visit, EMI, parking, place names (Kokapet, Gachibowli), and property terms.\n"
    "- English-only caller → clear Indian English with the same English property terms.\n"
    "- Sound like a real phone consultant, not a textbook or news bulletin.\n"
    "Keep responses short, natural, and conversational (1–2 sentences)."
)

TINGLISH_FIRST_LINE = (
    "Hello andi! Nenu Arjun, VK Realty nundi matladutunnanu. "
    "Meeku property search lo ela help cheyyalo cheppandi?"
)

TINGLISH_IDLE = {
    "threshold_sec": 30,
    "max_prompts": 1,
    "prompt_message": "Hello andi, vinipisthundaa?",
    "last_message": "Parledu andi, time unte malli call cheyyandi. Thank you!",
    "end_after_max": False,
}


def _should_skip(path: Path) -> bool:
    parts = set(path.parts)
    if ".env" in path.name:
        return True
    for skip in SKIP:
        if skip in str(path.relative_to(SRC)):
            return True
    return False


def _copy_tree() -> None:
    if DST.exists():
        shutil.rmtree(DST)
    DST.mkdir(parents=True)

    for root, dirs, files in os.walk(SRC):
        root_path = Path(root)
        rel = root_path.relative_to(SRC)
        if any(s in rel.parts for s in ("__pycache__", ".pytest_cache")):
            dirs[:] = []
            continue
        dest_root = DST / rel
        dest_root.mkdir(parents=True, exist_ok=True)
        for name in files:
            src_file = root_path / name
            if _should_skip(src_file):
                continue
            shutil.copy2(src_file, dest_root / name)


def _replace_in_file(path: Path, replacements: list[tuple[str, str]]) -> None:
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return
    original = text
    for old, new in replacements:
        text = text.replace(old, new)
    if text != original:
        path.write_text(text, encoding="utf-8")


def _walk_replace(replacements: list[tuple[str, str]], *, extensions: set[str]) -> None:
    for root, _, files in os.walk(DST):
        for name in files:
            if Path(name).suffix not in extensions:
                continue
            _replace_in_file(Path(root) / name, replacements)


def _patch_config() -> None:
    cfg_path = DST / "config.json"
    raw = json.loads(cfg_path.read_text(encoding="utf-8"))
    raw["lang_preset"] = "tinglish"
    raw["language"] = "te"
    raw["languages"] = ["te", "en-in"]
    raw["agent_instructions"] = TINGLISH_INSTRUCTIONS
    raw["first_line"] = TINGLISH_FIRST_LINE
    raw["tts_language"] = "te-IN"
    raw["user_idle"] = {**(raw.get("user_idle") or {}), **TINGLISH_IDLE}
    if isinstance(raw.get("assistant_settings"), dict):
        ast = raw["assistant_settings"]
        ast["languages"] = ["te", "en-in"]
        ast["system_prompt"] = TINGLISH_INSTRUCTIONS
        if isinstance(ast.get("welcome"), dict):
            ast["welcome"]["message"] = TINGLISH_FIRST_LINE
    cfg_path.write_text(json.dumps(raw, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")


def _patch_lang_presets() -> None:
    for rel in ("knowledge/lang_presets.json", "data/lang_presets.json"):
        path = DST / rel
        if not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "active_preset_id": "tinglish",
            "presets": [
                {
                    "id": "tinglish",
                    "label": "Telugu + English (Tinglish)",
                    "description": "Spoken Telugish — English for numbers and property terms.",
                    "enabled": True,
                    "agent_instructions": TINGLISH_INSTRUCTIONS,
                    "languages": ["te", "en-in"],
                    "default_language": "te",
                    "tts_voice": "3f2297f1-cac9-4942-b0bd-5d9bb3c35946",
                    "tts_voice_display": "Nivas Telugu/EN",
                    "tts_language": "te-IN",
                    "first_line": TINGLISH_FIRST_LINE,
                    "language_directive": (
                        "LANGUAGE: Natural Telugish ONLY — spoken Telugu + English. "
                        "Never pure literary Telugu, Hindi, Tamil, or Marathi."
                    ),
                    "interrupt_ack": "andi or sare",
                    "voice_per_lang": {
                        "te": "3f2297f1-cac9-4942-b0bd-5d9bb3c35946",
                        "en-in": "3f2297f1-cac9-4942-b0bd-5d9bb3c35946",
                    },
                    "user_idle": TINGLISH_IDLE,
                }
            ],
        }
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _rename_frontend() -> None:
    old = DST / "frontend" / "vk-realty-hme"
    new = DST / "frontend" / "tinglish-vk-realty"
    if old.exists() and not new.exists():
        shutil.move(str(old), str(new))


def _write_env_example() -> None:
    example = SRC / ".env.example"
    if example.is_file():
        text = example.read_text(encoding="utf-8")
    else:
        text = ""
    text = text.replace("CLIENT_ID=vk-realty-hme", "CLIENT_ID=tinglish-vk-realty")
    text = text.replace(
        "LIVEKIT_AGENT_NAME=inbound-voice-demo",
        "LIVEKIT_AGENT_NAME=inbound-voice-tinglish",
    )
    if "RE_MEMORY_DIR" not in text:
        text += "\nRE_MEMORY_DIR=~/.hermes/tinglish-vk-realty\n"
    else:
        text = text.replace("RE_MEMORY_DIR=", "RE_MEMORY_DIR=~/.hermes/tinglish-vk-realty\n# was: RE_MEMORY_DIR=")
    if "CLIENT_HTTP_PORT" not in text:
        text += "CLIENT_HTTP_PORT=8002\n"
    (DST / ".env.example").write_text(text, encoding="utf-8")


def _patch_agent_defaults() -> None:
    path = DST / "agent.py"
    _replace_in_file(
        path,
        [
            ('"lang_preset": "hme"', '"lang_preset": "tinglish"'),
            ('"language": "hi"', '"language": "te"'),
            ('"prompt_message": "Hello, sun pa rahe hain?"', '"prompt_message": "Hello andi, vinipisthundaa?"'),
        ],
    )


def _patch_config_store() -> None:
    _replace_in_file(
        DST / "config_store.py",
        [
            ('MAIN_AGENT_LABEL = "VK Realty — Arjun (Main Agent)"', 'MAIN_AGENT_LABEL = "VK Realty Tinglish — Arjun"'),
            ('"hme"', '"tinglish"'),
        ],
    )


def _patch_docker() -> None:
    _replace_in_file(
        DST / "docker-compose.yml",
        [
            ("vk-realty-hme:", "tinglish-vk-realty:"),
            ("vk-realty-hme-client", "tinglish-vk-realty-client"),
            ("CLIENT_ID=vk-realty-hme", "CLIENT_ID=tinglish-vk-realty"),
            ("8001}:8000", "8002}:8000"),
        ],
    )
    _replace_in_file(
        DST / "deploy.sh",
        [
            ("/vk-realty-hme", "/tinglish-vk-realty"),
            ("8001", "8002"),
        ],
    )


def _write_readme_header() -> None:
    readme = DST / "README.md"
    if readme.is_file():
        text = readme.read_text(encoding="utf-8")
        banner = (
            "# VK Realty Tinglish — Speaksy AI Voice Client\n\n"
            "> **Clone of vk-realty-hme** with `lang_preset: tinglish` (Telugu + English), "
            "Hyderabad knowledge packs, and isolated `CLIENT_ID` / memory / LiveKit agent name.\n\n"
        )
        if not text.startswith("# VK Realty Tinglish"):
            readme.write_text(banner + text.replace("# VK Realty HME", "# VK Realty Tinglish (was HME)"), encoding="utf-8")


def main() -> int:
    print(f"Copying {SRC} → {DST}")
    _copy_tree()
    _rename_frontend()

    repl = [
        ("vk-realty-hme", "tinglish-vk-realty"),
        ('"lang_preset": "hme"', '"lang_preset": "tinglish"'),
        ("LANG_PRESET", "hme",),  # handled below
    ]
    _walk_replace(
        [
            ("vk-realty-hme", "tinglish-vk-realty"),
            ('lang_preset", "hme"', 'lang_preset", "tinglish"'),
            ('lang_preset": "hme"', 'lang_preset": "tinglish"'),
            ('active_preset_id": "hme"', 'active_preset_id": "tinglish"'),
            ("VK Realty HME", "VK Realty Tinglish"),
        ],
        extensions={".py", ".js", ".html", ".md", ".sh", ".yml", ".json", ".css"},
    )
    # LiveKit agent name only in env/deploy files (avoid clobbering agent_name in config.json)
    for rel in (".env.example", "docker-compose.yml", "deploy.sh", "CLIENT_SETUP.md"):
        p = DST / rel
        if p.is_file():
            _replace_in_file(p, [("inbound-voice-demo", "inbound-voice-tinglish")])

    _patch_config()
    _patch_lang_presets()
    _patch_agent_defaults()
    _patch_config_store()
    _patch_docker()
    _write_env_example()
    _write_readme_header()

    # Fresh runtime data dir
    data = DST / "data"
    data.mkdir(exist_ok=True)
    (data / ".gitkeep").write_text("", encoding="utf-8")

    print(f"Done: {DST}")
    print("Next:")
    print(f"  cd {DST}")
    print("  cp .env.example .env   # fill keys (use port 8002, separate Supabase optional)")
    print("  python3 scripts/verify_setup.py")
    print("  DISABLE_AUTH=true python3 -m uvicorn ui_server:app --host 0.0.0.0 --port 8002")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())