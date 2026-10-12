"""
Platform routes — the KB-compiler dashboard (SPEAKSY_PLATFORM_MASTER_PLAN §4).

/platform            → the UI (create client, drop KB, compile, review, approve)
/api/platform/...    → the APIs the UI drives (all auth-gated)

Generation runs on the kb_compiler stage contract (Gemini 3.1 Pro for
synthesis, Flash for extraction, separate critic pass); compile runs as a
background task and the UI polls compile_state.json.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from auth import require_auth

logger = logging.getLogger("inbound-voice-agent")

router = APIRouter(tags=["platform"])

REPO_ROOT = Path(__file__).resolve().parent
_CLIENT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,39}$")
_SAFE_FILENAME_RE = re.compile(r"^[\w.\- ]{1,120}$")
_ALLOWED_KB_EXT = {".txt", ".md", ".json", ".csv", ".html", ".htm", ".docx", ".pdf", ".xlsx", ".yaml", ".yml"}

_compile_tasks: dict[str, asyncio.Task] = {}


def _cdir(client_id: str) -> Path:
    if not _CLIENT_ID_RE.match(client_id or ""):
        raise HTTPException(status_code=400, detail="Invalid client_id (lowercase, digits, - _, 3-40 chars)")
    from kb_compiler.bake import client_dir

    return client_dir(REPO_ROOT, client_id)


@router.get("/api/platform/clients")
async def api_platform_clients(user: str = Depends(require_auth)):
    from kb_compiler.bake import clients_root, load_cartridge_summary
    from kb_compiler.llm import STAGE_MODELS, SYNTH_MODEL
    from kb_compiler.pipeline import read_compile_state

    root = clients_root(REPO_ROOT)
    clients = []
    if root.is_dir():
        for cdir in sorted(p for p in root.iterdir() if p.is_dir()):
            summary = load_cartridge_summary(cdir)
            summary["compile_state"] = read_compile_state(cdir)
            clients.append(summary)
    return {"clients": clients, "compiler_model": SYNTH_MODEL, "stage_models": dict(STAGE_MODELS)}


@router.post("/api/platform/clients")
async def api_platform_create_client(request: Request, user: str = Depends(require_auth)):
    """Create a cartridge: body = client.yaml fields as JSON."""
    import yaml

    body = await request.json()
    client_id = str(body.get("client_id") or "").strip().lower()
    cdir = _cdir(client_id)
    if (cdir / "client.yaml").is_file() and not body.get("overwrite"):
        raise HTTPException(status_code=409, detail="Client exists (pass overwrite=true to update)")

    manifest = {
        "client_id": client_id,
        "industry": str(body.get("industry") or "real_estate"),
        "lang_preset": str(body.get("lang_preset") or "tinglish"),
        "persona": {
            "name": str((body.get("persona") or {}).get("name") or "Agent"),
            "role": str((body.get("persona") or {}).get("role") or "Consultant"),
            "company": str((body.get("persona") or {}).get("company") or client_id),
        },
        "voice": body.get("voice") or {"tts_provider": "cartesia"},
        "llm": body.get("llm") or {"provider": "gemini", "model": "gemini-2.5-flash-lite",
                                   "temperature": 0.7, "max_tokens": 200},
        "stt": body.get("stt") or {"provider": "soniox"},
        "knowledge_sources": body.get("knowledge_sources") or [],
        "iq": body.get("iq") or {"disabled_primitives": []},
    }
    (cdir / "kb").mkdir(parents=True, exist_ok=True)
    (cdir / "client.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
    logger.info("[PLATFORM] Client %s created by %s", client_id, user)
    return {"success": True, "client_id": client_id}


@router.post("/api/platform/clients/{client_id}/kb")
async def api_platform_upload_kb(client_id: str, request: Request, user: str = Depends(require_auth)):
    """Drop a KB file. Body: {"filename": "...", "content_base64": "..."} or {"content": "text"}."""
    cdir = _cdir(client_id)
    if not (cdir / "client.yaml").is_file():
        raise HTTPException(status_code=404, detail="Client not found")
    body = await request.json()
    filename = str(body.get("filename") or "").strip()
    if not _SAFE_FILENAME_RE.match(filename) or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    if Path(filename).suffix.lower() not in _ALLOWED_KB_EXT:
        raise HTTPException(status_code=400, detail=f"Unsupported type (allowed: {sorted(_ALLOWED_KB_EXT)})")

    kb_dir = cdir / "kb"
    kb_dir.mkdir(parents=True, exist_ok=True)
    kb_total = sum(f.stat().st_size for f in kb_dir.glob("*") if f.is_file())
    if body.get("content_base64"):
        try:
            data = base64.b64decode(str(body["content_base64"]), validate=True)
        except Exception:
            raise HTTPException(status_code=400, detail="Bad base64 content")
        if len(data) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="File over 20MB")
        if kb_total + len(data) > 100 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Knowledge base over 100MB total — remove files first")
        (kb_dir / filename).write_bytes(data)
    elif body.get("content"):
        text = str(body["content"])
        if kb_total + len(text.encode("utf-8")) > 100 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Knowledge base over 100MB total — remove files first")
        (kb_dir / filename).write_text(text, encoding="utf-8")
    else:
        raise HTTPException(status_code=400, detail="content or content_base64 required")
    return {"success": True, "filename": filename,
            "kb_files": sorted(p.name for p in kb_dir.glob("*") if p.is_file())}


@router.delete("/api/platform/clients/{client_id}/kb")
async def api_platform_delete_kb(
    client_id: str,
    filename: str = Query(...),
    user: str = Depends(require_auth),
):
    cdir = _cdir(client_id)
    if not _SAFE_FILENAME_RE.match(filename) or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = cdir / "kb" / filename
    if path.is_file():
        path.unlink()
    return {"success": True}


@router.post("/api/platform/clients/{client_id}/compile")
async def api_platform_compile(client_id: str, user: str = Depends(require_auth)):
    """Run the full pipeline in the background; poll /status."""
    cdir = _cdir(client_id)
    if not (cdir / "client.yaml").is_file():
        raise HTTPException(status_code=404, detail="Client not found")

    existing = _compile_tasks.get(client_id)
    if existing and not existing.done():
        return {"success": True, "message": "Compile already running"}

    from kb_compiler.pipeline import run_compile

    task = asyncio.create_task(run_compile(client_id, REPO_ROOT), name=f"compile:{client_id}")
    _compile_tasks[client_id] = task
    logger.info("[PLATFORM] Compile started for %s by %s", client_id, user)
    return {"success": True, "message": "Compile started — poll /status"}


@router.get("/api/platform/clients/{client_id}/status")
async def api_platform_status(client_id: str, user: str = Depends(require_auth)):
    from kb_compiler.pipeline import reconcile_state

    task = _compile_tasks.get(client_id)
    task_alive = bool(task and not task.done())
    state = reconcile_state(_cdir(client_id), task_alive)
    state["task_alive"] = task_alive
    return state


@router.get("/api/platform/clients/{client_id}/review")
async def api_platform_review(client_id: str, user: str = Depends(require_auth)):
    """The human gate data: artifacts + grounding facts + validation + critic."""
    from kb_compiler.bake import load_lock

    cdir = _cdir(client_id)
    lock = load_lock(cdir)
    if lock is None:
        raise HTTPException(status_code=404, detail="Not compiled yet")
    artifacts = {}
    ap = cdir / "artifacts.json"
    if ap.is_file():
        artifacts = json.loads(ap.read_text(encoding="utf-8"))
    facts = []
    fp = cdir / "facts.json"
    if fp.is_file():
        facts = json.loads(fp.read_text(encoding="utf-8"))
    return {"lock": lock, "artifacts": artifacts, "facts": facts}


@router.post("/api/platform/clients/{client_id}/approve")
async def api_platform_approve(client_id: str, user: str = Depends(require_auth)):
    """Sign off + install into the live engine (backs up replaced packs)."""
    from kb_compiler.bake import approve_and_apply

    result = approve_and_apply(_cdir(client_id), REPO_ROOT, approved_by=user)
    if not result.get("success"):
        raise HTTPException(status_code=409, detail=result.get("error") or "Apply refused")
    return result


# ═════════════════════════════════════════════════════════════════════════════
# THE UI — /platform (token from localStorage 'speaksy-auth-token')
# ═════════════════════════════════════════════════════════════════════════════

@router.get("/platform", response_class=HTMLResponse, include_in_schema=False)
async def platform_page():
    return HTMLResponse(content=_PLATFORM_HTML)


_PLATFORM_HTML = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<script>try{var t=localStorage.getItem('speaksy-theme');document.documentElement.setAttribute('data-theme',t==='dark'?'dark':'light')}catch(e){document.documentElement.setAttribute('data-theme','light')}</script>
<title>Speaksy Platform — Client Compiler</title>
<style>
@font-face{font-family:"Cy Grotesk Wide";
  src:url("/static/vendor/fonts/cy-grotesk-wide-light.ttf") format("truetype");
  font-weight:100 900;font-style:normal;font-display:swap}
:root{
  --bg:#0a0c10;--surface:#12151c;--raised:#171b25;--inset:#0d1017;
  --line:#232a37;--line-soft:#1b212c;
  --text:#e8ecf3;--dim:#8b95a7;--faint:#5b6474;
  /* brand green (var names historical) */
  --gold:#34d399;--gold-soft:#10b981;--gold-dim:rgba(52,211,153,.12);--on-acc:#04241a;
  --sbtn-ring:0 0 0 1.5px #05070a;
  --green:#3fb950;--green-dim:rgba(63,185,80,.12);
  --red:#f85149;--red-dim:rgba(248,81,73,.10);
  --amber:#d29922;--amber-dim:rgba(210,153,34,.12);
  --blue:#58a6ff;--blue-dim:rgba(88,166,255,.10);
  --r:12px;--r-sm:8px;
  --shadow:0 8px 28px rgba(0,0,0,.45);
}
html[data-theme="light"]{
  --bg:#f5f6f8;--surface:#ffffff;--raised:#fafbfc;--inset:#eef1f0;
  --line:rgba(18,28,24,.13);--line-soft:rgba(18,28,24,.07);
  --text:#17211d;--dim:#5d6b66;--faint:#8b9691;
  --gold:#047857;--gold-soft:#047857;--gold-dim:rgba(4,120,87,.10);--on-acc:#ffffff;
  --sbtn-ring:0 0 0 0 rgba(0,0,0,0);
  --green:#1a7f37;--green-dim:rgba(26,127,55,.10);
  --red:#cf222e;--red-dim:rgba(207,34,46,.08);
  --amber:#9a6700;--amber-dim:rgba(154,103,0,.10);
  --blue:#0969da;--blue-dim:rgba(9,105,218,.08);
  --shadow:0 8px 28px rgba(18,28,24,.12);
}
*{box-sizing:border-box;margin:0;padding:0}
html{color-scheme:dark}
html[data-theme="light"]{color-scheme:light}
body{background:var(--bg);color:var(--text);
  font:14px/1.55 "Cy Grotesk Wide",-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  -webkit-font-smoothing:antialiased;min-height:100vh}
body::before{content:'';position:fixed;inset:0;pointer-events:none;z-index:0;
  background:radial-gradient(900px 420px at 75% -10%,rgba(52,211,153,.06),transparent 60%),
             radial-gradient(700px 380px at 10% 110%,rgba(88,166,255,.05),transparent 60%)}
html[data-theme="light"] body::before{content:none}
#app{position:relative;z-index:1;max-width:1280px;margin:0 auto;padding:20px 24px 64px}

/* ── topbar ─────────────────────────────────────────────────────────── */
.topbar{display:flex;align-items:center;gap:16px;padding:14px 0 22px;flex-wrap:wrap}
.brand{display:flex;align-items:baseline;gap:10px}
.brand .word{font-size:19px;font-weight:800;letter-spacing:.06em}
.brand .word em{font-style:normal;background:linear-gradient(120deg,var(--gold),var(--gold-soft));
  -webkit-background-clip:text;background-clip:text;color:transparent}
.brand .tag{color:var(--dim);font-size:12.5px}
.topbar .spacer{flex:1}
.modelchips{display:flex;gap:8px;flex-wrap:wrap}
.mchip{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;color:var(--dim);
  background:var(--surface);border:1px solid var(--line);border-radius:20px;padding:4px 11px}
.mchip b{color:var(--text);font-weight:600}
.mchip .dot{width:6px;height:6px;border-radius:50%;background:var(--gold)}
.mchip.pro .dot{background:var(--gold);box-shadow:0 0 6px rgba(52,211,153,.7)}
.backlink{color:var(--dim);text-decoration:none;font-size:12.5px;border:1px solid var(--line);
  border-radius:8px;padding:6px 12px;transition:.15s}
.backlink:hover{color:var(--text);border-color:var(--faint)}

/* ── layout ─────────────────────────────────────────────────────────── */
.grid{display:grid;grid-template-columns:320px minmax(0,1fr);gap:20px;align-items:start}
@media(max-width:900px){.grid{grid-template-columns:1fr}}
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);
  padding:18px;margin-bottom:18px}
.card h2{font-size:12px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
  color:var(--dim);margin-bottom:14px;display:flex;align-items:center;gap:8px}
.card h2 .right{margin-left:auto;font-weight:500;letter-spacing:0;text-transform:none}

/* ── client rail ────────────────────────────────────────────────────── */
.client{border:1px solid var(--line-soft);background:var(--raised);border-radius:10px;
  padding:12px 14px;margin-bottom:9px;cursor:pointer;transition:border-color .15s,transform .15s}
.client:hover{border-color:var(--faint)}
.client.sel{border-color:var(--gold);box-shadow:0 0 0 1px var(--gold-dim),var(--shadow)}
.client .row1{display:flex;align-items:center;gap:8px}
.client .id{font-weight:650;font-size:13.5px;letter-spacing:.01em;overflow:hidden;text-overflow:ellipsis}
.client .meta{color:var(--dim);font-size:12px;margin-top:3px}
.client .persona{color:var(--faint);font-size:11.5px;margin-top:1px}
.pill{margin-left:auto;flex:none;display:inline-flex;align-items:center;gap:5px;
  padding:2px 9px;border-radius:20px;font-size:10.5px;font-weight:700;letter-spacing:.06em}
.pill.live{background:var(--green-dim);color:var(--green)}
.pill.review{background:var(--amber-dim);color:var(--amber)}
.pill.blocked{background:var(--red-dim);color:var(--red)}
.pill.draft{background:var(--blue-dim);color:var(--blue)}
.pill.compiling{background:var(--gold-dim);color:var(--gold)}
.pill .spin{width:9px;height:9px;border:1.5px solid currentColor;border-top-color:transparent;
  border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* ── forms ──────────────────────────────────────────────────────────── */
label{display:block;font-size:11.5px;color:var(--dim);margin:10px 0 4px;font-weight:550}
input,select,textarea{width:100%;background:var(--inset);border:1px solid var(--line);
  border-radius:var(--r-sm);color:var(--text);padding:8px 11px;font-size:13px;
  font-family:inherit;transition:border-color .15s,box-shadow .15s;outline:none}
input:focus,select:focus{border-color:var(--gold-soft);box-shadow:0 0 0 3px var(--gold-dim)}
input::placeholder{color:var(--faint)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;color:#fff;border:0;
  border-radius:999px;padding:10px 20px;font-weight:400;font-size:13px;letter-spacing:.005em;cursor:pointer;
  margin-top:12px;transition:transform .18s cubic-bezier(.22,.61,.36,1),box-shadow .18s ease,filter .18s ease;
  font-family:inherit;
  background:linear-gradient(180deg,rgba(0,0,0,.28),rgba(0,0,0,.06) 22%,rgba(0,0,0,0) 48%),
    radial-gradient(120% 94% at 8% 0%,rgba(0,0,0,.4),transparent 46%),
    linear-gradient(145deg,#0e2410,#163d18 22%,#245c28 46%,#397e39 66%,#4c9349 84%,#5aa652 100%);
  box-shadow:var(--sbtn-ring,0 0 0 1.5px #05070a),inset 0 1.5px 0 rgba(255,255,255,.22),
    inset 0 -7px 14px rgba(0,0,0,.3),0 8px 20px -8px rgba(20,80,44,.3),0 12px 26px -12px rgba(90,180,120,.4)}
.btn:hover:not(:disabled){transform:translateY(-1px);filter:brightness(1.04);
  box-shadow:var(--sbtn-ring,0 0 0 1.5px #05070a),inset 0 1.5px 0 rgba(255,255,255,.24),
    inset 0 -7px 14px rgba(0,0,0,.3),0 14px 28px -8px rgba(20,80,44,.34),0 20px 40px -12px rgba(120,210,150,.55)}
.btn:disabled{opacity:.4;cursor:default}
.btn.ghost{background:none;color:var(--gold);border:1px solid var(--gold);box-shadow:none}
.btn.ghost:hover:not(:disabled){filter:none;transform:none;box-shadow:none;background:var(--gold-dim)}
.btn.green{background:linear-gradient(140deg,#46c05a,#37a34a);color:#08120a}
.btn.green:hover:not(:disabled){box-shadow:0 6px 18px rgba(63,185,80,.3)}
.btn.danger{background:transparent;color:var(--red);border:1px solid rgba(248,81,73,.4)}
.btn.block{width:100%}
details.adv{margin-top:12px;border-top:1px dashed var(--line);padding-top:10px}
details.adv summary{cursor:pointer;color:var(--faint);font-size:12px;user-select:none}
.hint{font-size:11.5px;color:var(--faint);margin-top:8px;line-height:1.5}

/* ── empty state / hero ─────────────────────────────────────────────── */
.hero{padding:44px 36px;text-align:center}
.hero h3{font-size:19px;font-weight:750;margin-bottom:10px}
.hero p{color:var(--dim);max-width:520px;margin:0 auto 26px}
.flow{display:flex;justify-content:center;gap:0;flex-wrap:wrap}
.flow .step{display:flex;align-items:center;gap:0}
.flow .node{background:var(--raised);border:1px solid var(--line);border-radius:10px;
  padding:10px 16px;font-size:12.5px;color:var(--dim)}
.flow .node b{display:block;color:var(--text);font-size:13px;margin-bottom:1px}
.flow .arrow{color:var(--faint);padding:0 10px;font-size:15px}

/* ── detail header ──────────────────────────────────────────────────── */
.dhead{display:flex;align-items:flex-start;gap:14px;flex-wrap:wrap}
.dhead .title{font-size:19px;font-weight:750;letter-spacing:.01em}
.dhead .sub{color:var(--dim);font-size:12.5px;margin-top:3px}
.dhead .badges{margin-left:auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.tagchip{font-size:11px;color:var(--dim);background:var(--inset);border:1px solid var(--line);
  border-radius:6px;padding:3px 9px}

/* ── KB dropzone ────────────────────────────────────────────────────── */
.dropzone{border:1.5px dashed var(--line);border-radius:10px;padding:26px 18px;text-align:center;
  color:var(--dim);cursor:pointer;transition:border-color .15s,background .15s;font-size:13px}
.dropzone:hover,.dropzone.over{border-color:var(--gold-soft);background:var(--gold-dim);color:var(--text)}
.dropzone .big{font-size:22px;display:block;margin-bottom:6px}
.kbfile{display:flex;align-items:center;gap:10px;padding:8px 4px;border-bottom:1px solid var(--line-soft);font-size:13px}
.kbfile:last-of-type{border-bottom:0}
.kbfile .ext{flex:none;font-size:9.5px;font-weight:800;letter-spacing:.05em;color:var(--gold);
  background:var(--gold-dim);border-radius:5px;padding:3px 6px;min-width:38px;text-align:center}
.kbfile .name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.kbfile .del{margin-left:auto;color:var(--faint);cursor:pointer;border:0;background:none;
  font-size:15px;padding:2px 6px;border-radius:5px;transition:.12s}
.kbfile .del:hover{color:var(--red);background:var(--red-dim)}

/* ── pipeline timeline ──────────────────────────────────────────────── */
.stages{display:flex;align-items:flex-start;margin:6px 0 16px}
.stage{flex:1;text-align:center;position:relative}
.stage .dot{width:26px;height:26px;border-radius:50%;margin:0 auto 7px;display:flex;
  align-items:center;justify-content:center;font-size:12px;font-weight:700;
  background:var(--inset);border:1.5px solid var(--line);color:var(--faint);
  transition:all .3s;position:relative;z-index:2}
.stage .lbl{font-size:10.5px;color:var(--faint);letter-spacing:.04em;text-transform:uppercase;font-weight:650}
.stage:not(:first-child)::before{content:'';position:absolute;top:13px;left:calc(-50% + 13px);
  right:calc(50% + 13px);height:1.5px;background:var(--line);z-index:1;transition:background .3s}
.stage.done .dot{background:var(--gold-dim);border-color:var(--gold);color:var(--gold)}
.stage.done .lbl{color:var(--dim)}
.stage.done:not(:first-child)::before{background:var(--gold-soft)}
.stage.active .dot{border-color:var(--gold);color:var(--gold);
  box-shadow:0 0 0 4px var(--gold-dim);animation:pulse 1.6s ease-in-out infinite}
.stage.active .lbl{color:var(--gold)}
.stage.active:not(:first-child)::before{background:var(--gold-soft)}
.stage.err .dot{background:var(--red-dim);border-color:var(--red);color:var(--red)}
.stage.err .lbl{color:var(--red)}
@keyframes pulse{0%,100%{box-shadow:0 0 0 3px var(--gold-dim)}50%{box-shadow:0 0 0 7px rgba(52,211,153,.05)}}
.pbar{height:7px;background:var(--inset);border-radius:5px;overflow:hidden;margin:4px 0 10px;position:relative}
.pbar i{display:block;height:100%;border-radius:5px;
  background:linear-gradient(90deg,var(--gold-soft),var(--gold));transition:width .6s ease;position:relative}
.pbar i::after{content:'';position:absolute;inset:0;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.35),transparent);
  animation:shimmer 1.8s infinite;opacity:0}
.pbar.running i::after{opacity:1}
@keyframes shimmer{0%{transform:translateX(-100%)}100%{transform:translateX(100%)}}
.pmsg{font-size:12.5px;color:var(--dim)}
.pmsg b{color:var(--text)}
.pmsg .err{color:var(--red)}

/* ── review ─────────────────────────────────────────────────────────── */
.verdict{display:flex;align-items:center;gap:10px;border-radius:10px;padding:12px 16px;
  font-weight:650;font-size:13.5px;margin-bottom:14px}
.verdict.ok{background:var(--green-dim);color:var(--green);border:1px solid rgba(63,185,80,.25)}
.verdict.bad{background:var(--red-dim);color:var(--red);border:1px solid rgba(248,81,73,.25)}
.verdict.pend{background:var(--amber-dim);color:var(--amber);border:1px solid rgba(210,153,34,.25)}
.statrow{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:14px}
.stat{background:var(--raised);border:1px solid var(--line-soft);border-radius:10px;
  padding:10px 16px;min-width:86px}
.stat .n{font-size:19px;font-weight:800;letter-spacing:.01em}
.stat .k{font-size:10.5px;color:var(--faint);text-transform:uppercase;letter-spacing:.07em;font-weight:650;margin-top:1px}
.findings{margin-bottom:12px}
.finding{display:flex;gap:9px;font-size:12.5px;padding:7px 10px;border-radius:8px;margin-bottom:6px;line-height:1.45}
.finding.err{background:var(--red-dim);color:#ffb3ae}
.finding.warn{background:var(--amber-dim);color:#e8c584}
.finding .ico{flex:none;font-weight:800}
details.styleblock summary{cursor:pointer;color:var(--faint);font-size:12px;margin:4px 0 8px;user-select:none}

/* tabs */
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin:16px 0 14px;flex-wrap:wrap}
.tabs a{color:var(--dim);cursor:pointer;font-size:12.5px;font-weight:600;padding:7px 13px;
  border-radius:8px 8px 0 0;border-bottom:2px solid transparent;transition:.12s;user-select:none}
.tabs a:hover{color:var(--text)}
.tabs a.on{color:var(--gold);border-bottom-color:var(--gold)}
.tabbody{min-height:120px}

/* artifact cards */
.faq{border:1px solid var(--line-soft);background:var(--raised);border-radius:10px;padding:13px 15px;margin-bottom:10px}
.faq .cat{font-size:10px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;
  color:var(--blue);background:var(--blue-dim);border-radius:5px;padding:2px 8px;display:inline-block;margin-bottom:7px}
.faq .ans{font-size:13.5px;line-height:1.55}
.faq .qwrap{margin-top:9px}
.faq .qwrap summary{font-size:11.5px;color:var(--faint);cursor:pointer;user-select:none}
.qchips{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.qchip{font-size:11.5px;color:var(--dim);background:var(--inset);border:1px solid var(--line-soft);
  border-radius:14px;padding:3px 10px}
.items{display:grid;grid-template-columns:repeat(auto-fill,minmax(290px,1fr));gap:12px}
.item{border:1px solid var(--line-soft);background:var(--raised);border-radius:10px;padding:14px 16px}
.item .nm{font-weight:700;font-size:14px}
.item .loc{color:var(--dim);font-size:12px;margin:2px 0 8px}
.item .kv{display:flex;justify-content:space-between;font-size:12.5px;padding:3px 0;border-bottom:1px dashed var(--line-soft)}
.item .kv:last-of-type{border-bottom:0}
.item .kv .k{color:var(--faint)}
.item .usp{font-size:12px;color:#e8c584;background:var(--amber-dim);border-radius:7px;padding:7px 10px;margin-top:9px;line-height:1.45}
.item .scarce{display:inline-block;font-size:10.5px;font-weight:750;color:var(--red);
  background:var(--red-dim);border-radius:5px;padding:2px 8px;margin-top:8px;margin-right:6px}
.obj{border:1px solid var(--line-soft);background:var(--raised);border-radius:10px;padding:13px 15px;margin-bottom:10px}
.obj .ty{font-size:11px;font-weight:800;letter-spacing:.07em;text-transform:uppercase;color:var(--gold);margin-bottom:8px}
.obj .duo{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:760px){.obj .duo{grid-template-columns:1fr}}
.obj .half .h{font-size:10.5px;color:var(--faint);text-transform:uppercase;letter-spacing:.06em;font-weight:700;margin-bottom:4px}
.obj .half .t{font-size:12.5px;line-height:1.5;color:var(--text)}
.mono{background:var(--inset);border:1px solid var(--line-soft);border-radius:var(--r-sm);
  padding:12px;font:12px/1.55 ui-monospace,'SF Mono',Menlo,monospace;overflow:auto;
  max-height:420px;white-space:pre-wrap;word-break:break-word}
.fact{display:flex;gap:10px;font-size:12.5px;padding:7px 4px;border-bottom:1px solid var(--line-soft);line-height:1.5}
.fact .src{flex:none;font:10.5px/1.9 ui-monospace,Menlo,monospace;color:var(--blue);
  background:var(--blue-dim);border-radius:5px;padding:0 7px;height:fit-content}
.quote{border-left:3px solid var(--gold);background:var(--raised);border-radius:0 8px 8px 0;
  padding:11px 14px;font-size:13.5px;font-style:italic;margin:8px 0}

/* approve zone */
.approvezone{border-top:1px solid var(--line);margin-top:18px;padding-top:16px;
  display:flex;align-items:center;gap:14px;flex-wrap:wrap}
.locknote{font-size:11.5px;color:var(--faint);line-height:1.6}
.liveline{display:flex;align-items:center;gap:8px;color:var(--green);font-weight:650;font-size:13.5px}
.liveline .dot{width:8px;height:8px;border-radius:50%;background:var(--green);
  box-shadow:0 0 8px rgba(63,185,80,.8);animation:pulse-g 2s infinite}
@keyframes pulse-g{50%{opacity:.5}}

/* modal */
.modal-bg{position:fixed;inset:0;background:rgba(4,6,10,.7);backdrop-filter:blur(3px);
  display:flex;align-items:center;justify-content:center;z-index:50;opacity:0;pointer-events:none;transition:.18s}
.modal-bg.show{opacity:1;pointer-events:auto}
.modal{background:var(--surface);border:1px solid var(--line);border-radius:14px;
  padding:24px;max-width:440px;width:92%;box-shadow:var(--shadow);transform:translateY(8px);transition:.18s}
.modal-bg.show .modal{transform:none}
.modal h3{font-size:15.5px;margin-bottom:8px}
.modal p{color:var(--dim);font-size:13px;line-height:1.6}
.modal .acts{display:flex;gap:10px;justify-content:flex-end;margin-top:18px}

/* toasts */
#toasts{position:fixed;right:20px;bottom:20px;z-index:60;display:flex;flex-direction:column;gap:8px}
.toast{background:var(--raised);border:1px solid var(--line);border-left:3px solid var(--gold);
  border-radius:10px;padding:11px 16px;font-size:13px;box-shadow:var(--shadow);
  animation:tin .25s ease;max-width:380px}
.toast.ok{border-left-color:var(--green)}
.toast.err{border-left-color:var(--red)}
@keyframes tin{from{opacity:0;transform:translateY(8px)}}
.sub{color:var(--dim);font-size:12.5px}
.skel{color:var(--faint);font-size:12.5px;padding:6px 0}
</style></head><body>
<div id="app">
  <div class="topbar">
    <div class="brand">
      <span class="word">SPEAKSY <em>PLATFORM</em></span>
      <span class="tag">drop a knowledge base · ship an agent</span>
    </div>
    <div class="spacer"></div>
    <div class="modelchips" id="modelchips"></div>
    <a class="backlink" href="/">← Dashboard</a>
  </div>

  <div class="grid">
    <div>
      <div class="card">
        <h2>Clients <span class="right sub" id="clientcount"></span></h2>
        <div id="clients"><div class="skel">loading…</div></div>
      </div>
      <div class="card">
        <h2>New client</h2>
        <label>Client ID</label><input id="n_id" placeholder="apex-insurance" autocomplete="off">
        <label>Industry</label>
        <select id="n_ind">
          <option value="real_estate">Real estate</option><option value="finance">Finance</option>
          <option value="insurance">Insurance</option><option value="healthcare">Healthcare</option>
          <option value="ecommerce">E-commerce</option>
        </select>
        <label>Language preset</label>
        <select id="n_preset">
          <option value="tinglish">Tinglish (Telugu + English)</option>
          <option value="hte">HTE (Hindi + English)</option>
          <option value="english">English</option>
        </select>
        <label>Agent name</label><input id="n_name" placeholder="Meera">
        <label>Agent role</label><input id="n_role" placeholder="Senior Insurance Advisor">
        <label>Company</label><input id="n_co" placeholder="Apex Insurance">
        <details class="adv"><summary>Voice &amp; LLM (advanced)</summary>
          <label>TTS provider</label>
          <select id="n_tts"><option>cartesia</option><option>sarvam</option><option>elevenlabs</option><option>gemini</option></select>
          <label>Voice ID (optional)</label><input id="n_voice" placeholder="inherit from engine default">
          <label>Call LLM model</label><input id="n_llm" value="gemini-2.5-flash-lite">
          <label>Temperature</label><input id="n_temp" type="number" step="0.05" min="0" max="1" value="0.7">
        </details>
        <button class="btn block" onclick="createClient()">＋ Create cartridge</button>
        <div class="hint">One YAML, zero code. Next: select it, drop KB files, compile, review, go live.</div>
      </div>
    </div>

    <div id="detail">
      <div class="card hero">
        <h3>One frozen engine, infinite clients</h3>
        <p>Select a client on the left — or create one. Drop their brochures, FAQ exports and price
        sheets; the compiler generates every pack, prompt and story, grounds every claim against the
        source, and nothing goes live without your sign-off.</p>
        <div class="flow">
          <div class="step"><div class="node"><b>1 · Knowledge</b>drop any files</div><span class="arrow">→</span></div>
          <div class="step"><div class="node"><b>2 · Compile</b>extract → synthesize → validate</div><span class="arrow">→</span></div>
          <div class="step"><div class="node"><b>3 · Review</b>every claim beside its source</div><span class="arrow">→</span></div>
          <div class="step"><div class="node"><b>4 · Live</b>hot-reload, zero restart</div></div>
        </div>
      </div>
    </div>
  </div>
</div>

<div class="modal-bg" id="modalbg">
  <div class="modal">
    <h3 id="mtitle"></h3>
    <p id="mbody"></p>
    <div class="acts">
      <button class="btn ghost" onclick="closeModal()">Cancel</button>
      <button class="btn green" id="mconfirm">Confirm</button>
    </div>
  </div>
</div>
<div id="toasts"></div>

<script>
const T = localStorage.getItem('speaksy-auth-token') || '';
const H = {'Authorization':'Bearer '+T,'Content-Type':'application/json'};
const STAGES = ['ingest','extract','synthesize','validate','critic','bake'];
let SEL=null, POLL=null, CLIENTS=[], ART=null;

function $(id){return document.getElementById(id)}
function v(id){return $(id).value.trim()}
function esc(s){return String(s??'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;')}
function toast(msg,cls){const t=document.createElement('div');t.className='toast '+(cls||'');
  t.textContent=msg;$('toasts').appendChild(t);setTimeout(()=>{t.style.opacity=0;setTimeout(()=>t.remove(),300)},4200)}

async function api(p,opt={}){
  const r=await fetch(p,Object.assign({headers:H},opt));
  if(r.status===401){document.body.innerHTML=
    '<div style="max-width:420px;margin:18vh auto;text-align:center;font-family:inherit">'+
    '<h2 style="margin-bottom:10px">Not signed in</h2><p style="color:#8b95a7">Log in to the '+
    'dashboard first, then reopen <b>/platform</b>.</p>'+
    '<p style="margin-top:18px"><a href="/" style="color:#10b981">Go to dashboard →</a></p></div>';throw new Error('auth')}
  const j=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(typeof j.detail==='string'?j.detail:(r.status+''));
  return j}

/* ── clients rail ─────────────────────────────────────────── */
function pillFor(c){
  const st=c.compile_state||{};
  if(st.running)return '<span class="pill compiling"><span class="spin"></span>COMPILING</span>';
  if(c.approved)return '<span class="pill live">● LIVE</span>';
  if(c.compiled)return c.validation_ok?'<span class="pill review">REVIEW</span>':'<span class="pill blocked">BLOCKED</span>';
  return '<span class="pill draft">DRAFT</span>'}

async function loadClients(){
  const d=await api('/api/platform/clients');CLIENTS=d.clients;
  const mc=$('modelchips');
  if(mc&&d.stage_models){mc.innerHTML=
    `<span class="mchip pro"><span class="dot"></span>synthesis <b>${esc(d.stage_models.synthesize)}</b></span>`+
    `<span class="mchip"><span class="dot"></span>extract <b>${esc(d.stage_models.extract)}</b></span>`+
    `<span class="mchip"><span class="dot"></span>critic <b>${esc(d.stage_models.critic)}</b></span>`}
  $('clientcount').textContent=d.clients.length?d.clients.length+' cartridge'+(d.clients.length>1?'s':''):'';
  const el=$('clients');el.innerHTML='';
  if(!d.clients.length){el.innerHTML='<div class="skel">No cartridges yet — create the first one below.</div>';return}
  d.clients.forEach(c=>{
    const div=document.createElement('div');
    div.className='client'+(SEL===c.client_id?' sel':'');
    const p=c.persona||{};
    div.innerHTML=`<div class="row1"><span class="id">${esc(c.client_id)}</span>${pillFor(c)}</div>
      <div class="meta">${esc(c.industry||'—')} · ${esc(c.lang_preset||'—')} · ${c.kb_files.length} KB file${c.kb_files.length===1?'':'s'}</div>
      ${p.name?`<div class="persona">${esc(p.name)} — ${esc(p.role||'')}</div>`:''}`;
    div.onclick=()=>select(c.client_id);
    el.appendChild(div)})}

async function createClient(){
  const id=v('n_id');
  if(!id){toast('Client ID required','err');return}
  try{
    await api('/api/platform/clients',{method:'POST',body:JSON.stringify({
      client_id:id,industry:v('n_ind'),lang_preset:v('n_preset'),
      persona:{name:v('n_name'),role:v('n_role'),company:v('n_co')},
      voice:{tts_provider:v('n_tts'),...(v('n_voice')?{voice_id:v('n_voice')}:{})},
      llm:{provider:'gemini',model:v('n_llm')||'gemini-2.5-flash-lite',
           temperature:parseFloat(v('n_temp'))||0.7,max_tokens:200}})});
    toast('Cartridge '+id+' created','ok');
    ['n_id','n_name','n_role','n_co'].forEach(x=>$(x).value='');
    await loadClients();select(id)
  }catch(e){toast(e.message,'err')}}

/* ── selection + polling ──────────────────────────────────── */
async function select(id){
  SEL=id;clearInterval(POLL);ART=null;
  await render();await loadClients();
  POLL=setInterval(async()=>{
    const s=await api(`/api/platform/clients/${SEL}/status`).catch(()=>null);
    if(!s)return;
    paintStatus(s);
    if(!s.running&&!s.task_alive&&s.stage!=='idle'){clearInterval(POLL);render();loadClients()}
  },2200)}

function client(){return CLIENTS.find(c=>c.client_id===SEL)}

/* ── detail render ────────────────────────────────────────── */
async function render(){
  const d=$('detail');
  const c=(await api('/api/platform/clients')).clients.find(x=>x.client_id===SEL);
  CLIENTS=CLIENTS.map(x=>x.client_id===c.client_id?c:x);
  if(!c){d.innerHTML='';return}
  const p=c.persona||{};
  const kb=c.kb_files.map(f=>{
    const ext=(f.split('.').pop()||'').toUpperCase();
    return `<div class="kbfile"><span class="ext">${esc(ext)}</span><span class="name">${esc(f)}</span>
      <button class="del" title="remove" onclick="delKb('${esc(f)}')">✕</button></div>`}).join('')
    ||'<div class="skel">No knowledge files yet — drop brochures, FAQ exports, price sheets…</div>';

  d.innerHTML=`
  <div class="card">
    <div class="dhead">
      <div>
        <div class="title">${esc(SEL)}</div>
        <div class="sub">${esc(p.name||'Agent')} · ${esc(p.role||'')} — ${esc(p.company||'')}</div>
      </div>
      <div class="badges">
        <span class="tagchip">${esc(c.industry||'—')}</span>
        <span class="tagchip">${esc(c.lang_preset||'—')}</span>
        ${c.model?`<span class="tagchip">compiled · ${esc(c.model)}</span>`:''}
        ${pillFor(c)}
      </div>
    </div>
  </div>

  <div class="card">
    <h2>Knowledge base <span class="right sub">${c.kb_files.length} file${c.kb_files.length===1?'':'s'}</span></h2>
    ${kb}
    <div class="dropzone" id="dz" style="margin-top:14px" onclick="$('kbpick').click()">
      <span class="big">⇪</span>Drop files here or click to browse
      <div class="hint" style="margin-top:4px">PDF · DOCX · XLSX · CSV · TXT · MD · HTML · JSON — up to 20&nbsp;MB each</div>
    </div>
    <input type="file" id="kbpick" multiple hidden>
    <button class="btn" onclick="compile()" ${c.kb_files.length?'':'disabled'}>⚙ Compile cartridge</button>
    ${c.kb_files.length?'':'<div class="hint">Add at least one knowledge file to compile.</div>'}
  </div>

  <div class="card">
    <h2>Compiler pipeline</h2>
    <div class="stages" id="stagerow">${STAGES.map(s=>
      `<div class="stage" data-s="${s}"><div class="dot">·</div><div class="lbl">${s}</div></div>`).join('')}</div>
    <div class="pbar" id="pwrap"><i id="pbar" style="width:0"></i></div>
    <div class="pmsg" id="pmsg">idle</div>
  </div>

  <div class="card" id="reviewcard"><h2>Review &amp; approve</h2>
    <div class="sub" id="revbody">${c.compiled?'loading review…':'Compile first — the human gate opens here: every generated answer beside its grounding, validation and critic verdicts, then one-click go-live.'}</div>
  </div>`;

  wireDropzone();
  const s=await api(`/api/platform/clients/${SEL}/status`);paintStatus(s);
  if(c.compiled)showReview()}

/* ── pipeline paint ───────────────────────────────────────── */
function paintStatus(s){
  const bar=$('pbar');if(!bar)return;
  bar.style.width=(s.pct||0)+'%';
  $('pwrap').classList.toggle('running',!!s.running);
  const idx=STAGES.indexOf(s.stage);
  document.querySelectorAll('#stagerow .stage').forEach((el,i)=>{
    el.className='stage';
    if(s.stage==='done'||s.pct===100&&!s.error){el.classList.add('done');el.querySelector('.dot').textContent='✓'}
    else if(s.stage==='error'){if(i<=idx||idx<0){el.classList.add(i===idx?'err':'done');el.querySelector('.dot').textContent=i===idx?'✕':'✓'}}
    else if(idx>=0){
      if(i<idx){el.classList.add('done');el.querySelector('.dot').textContent='✓'}
      else if(i===idx){el.classList.add('active');el.querySelector('.dot').textContent=i+1}
      else el.querySelector('.dot').textContent=i+1}
    else el.querySelector('.dot').textContent=i+1});
  $('pmsg').innerHTML=`<b>${esc(s.stage||'idle')}</b> — ${esc(s.message||'')}`
    +(s.error?` <span class="err">${esc(s.error)}</span>`:'')
    +(s.elapsed_s&&!s.running?` <span class="sub">· ${s.elapsed_s}s</span>`:'')}

/* ── KB upload ────────────────────────────────────────────── */
function wireDropzone(){
  const dz=$('dz'),pick=$('kbpick');if(!dz)return;
  ;['dragenter','dragover'].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();dz.classList.add('over')}));
  ;['dragleave','drop'].forEach(ev=>dz.addEventListener(ev,e=>{e.preventDefault();dz.classList.remove('over')}));
  dz.addEventListener('drop',e=>uploadFiles(e.dataTransfer.files));
  pick.addEventListener('change',()=>uploadFiles(pick.files))}

async function uploadFiles(files){
  if(!files||!files.length)return;
  let ok=0;
  for(const f of files){
    try{
      const buf=await f.arrayBuffer();
      let bin='';const bytes=new Uint8Array(buf);
      for(let i=0;i<bytes.length;i+=32768)bin+=String.fromCharCode.apply(null,bytes.subarray(i,i+32768));
      await api(`/api/platform/clients/${SEL}/kb`,{method:'POST',
        body:JSON.stringify({filename:f.name,content_base64:btoa(bin)})});
      ok++
    }catch(e){toast(f.name+': '+e.message,'err')}}
  if(ok)toast(ok+' file'+(ok>1?'s':'')+' added','ok');
  render();loadClients()}

async function delKb(f){
  await api(`/api/platform/clients/${SEL}/kb?filename=${encodeURIComponent(f)}`,{method:'DELETE'});
  toast(f+' removed');render();loadClients()}

async function compile(){
  await api(`/api/platform/clients/${SEL}/compile`,{method:'POST'});
  toast('Compile started','ok');select(SEL)}

/* ── review ───────────────────────────────────────────────── */
async function showReview(){
  let r;try{r=await api(`/api/platform/clients/${SEL}/review`)}catch(e){return}
  ART=r;
  const lock=r.lock||{},val=lock.validation||{},cr=lock.critic||{},st=val.stats||{};
  const blocking=cr.blocking||[],styles=cr.style||[],errs=val.errors||[],warns=val.warnings||[];
  const ok=val.ok&&!blocking.length;
  const sm=lock.stage_models||{};

  const verdict=ok
    ?`<div class="verdict ok">✓ Grounded &amp; validated — every claim traces to the source. Critic clean.</div>`
    :`<div class="verdict bad">✕ Blocked — fix the findings below and recompile. Nothing ships unsigned.</div>`;

  const stats=`<div class="statrow">
    <div class="stat"><div class="n">${st.faq_entries||0}</div><div class="k">FAQs</div></div>
    <div class="stat"><div class="n">${st.faq_paraphrases||0}</div><div class="k">Paraphrases</div></div>
    <div class="stat"><div class="n">${st.items||0}</div><div class="k">Items</div></div>
    <div class="stat"><div class="n">${st.objection_stories||0}</div><div class="k">Stories</div></div>
    <div class="stat"><div class="n">${st.hot_facts||0}</div><div class="k">Hot facts</div></div>
    <div class="stat"><div class="n">${(ART.facts||[]).length}</div><div class="k">Grounded facts</div></div>
  </div>`;

  const findings=
    errs.map(e=>`<div class="finding err"><span class="ico">✕</span><span>${esc(e)}</span></div>`).join('')
    +blocking.map(b=>`<div class="finding err"><span class="ico">✕</span><span><b>critic:</b> ${esc(b.reason||JSON.stringify(b))}</span></div>`).join('')
    +warns.map(w=>`<div class="finding warn"><span class="ico">⚠</span><span>${esc(w)}</span></div>`).join('');
  const styleblock=styles.length?`<details class="styleblock"><summary>✎ ${styles.length} style suggestion${styles.length>1?'s':''} (non-blocking)</summary>
    ${styles.map(s=>`<div class="finding warn"><span class="ico">✎</span><span>${esc(s.suggestion||JSON.stringify(s))}</span></div>`).join('')}</details>`:'';

  const tabs=`<div class="tabs">
    <a class="on" onclick="tab(this,'faq')">FAQ</a>
    <a onclick="tab(this,'items')">Inventory</a>
    <a onclick="tab(this,'market')">Market &amp; objections</a>
    <a onclick="tab(this,'persona')">Persona</a>
    <a onclick="tab(this,'facts')">Grounding</a>
    <a onclick="tab(this,'raw')">Raw JSON</a>
  </div><div class="tabbody" id="tabbody"></div>`;

  const approve=lock.approved
    ?`<div class="approvezone"><div class="liveline"><span class="dot"></span>LIVE — approved by ${esc(lock.approved_by||'?')} · ${esc((lock.applied_at||'').replace('T',' ').slice(0,16))}</div></div>`
    :`<div class="approvezone">
        <button class="btn green" ${ok?'':'disabled'} onclick="askApprove()">✔ Approve &amp; make LIVE</button>
        <div class="locknote">Installs packs into the engine (existing packs backed up) and hot-reloads config — zero restart.<br>
        compiler v${esc(lock.compiler_version||'?')} · synth ${esc(sm.synthesize||lock.model||'?')} · extract ${esc(sm.extract||'?')} · critic ${esc(sm.critic||'?')} · compiled ${esc((lock.created_at||'').replace('T',' ').slice(0,16))}</div>
      </div>`;

  $('revbody').innerHTML=verdict+stats+`<div class="findings">${findings}${styleblock}</div>`+tabs+approve;
  tab(document.querySelector('#reviewcard .tabs a'),'faq')}

function tab(el,k){
  document.querySelectorAll('#reviewcard .tabs a').forEach(a=>a.classList.remove('on'));
  if(el)el.classList.add('on');
  const a=(ART&&ART.artifacts)||{},body=$('tabbody');
  if(k==='faq'){
    const faqs=(a.company_knowledge||{}).faq||[];
    const hot=(a.company_knowledge||{}).hot_facts||[];
    body.innerHTML=(hot.length?`<div class="faq"><span class="cat">hot facts</span>${hot.map(h=>`<div class="ans">• ${esc(h)}</div>`).join('')}</div>`:'')
      +(faqs.map(f=>`<div class="faq"><span class="cat">${esc(f.category||'faq')}</span>
        <div class="ans">${esc(f.answer||'')}</div>
        <details class="qwrap"><summary>${(f.questions||[]).length} caller phrasings</summary>
        <div class="qchips">${(f.questions||[]).map(q=>`<span class="qchip">${esc(q)}</span>`).join('')}</div></details></div>`).join('')
      ||'<div class="skel">no FAQ entries</div>')}
  else if(k==='items'){
    const items=(a.inventory||{}).properties||[];
    body.innerHTML=items.length?`<div class="items">${items.map(i=>`<div class="item">
      <div class="nm">${esc(i.name||i.listing_id||'')}</div>
      <div class="loc">${esc([i.location,i.micro_market].filter(Boolean).join(' · '))}</div>
      ${i.builder?`<div class="kv"><span class="k">Builder</span><span>${esc(i.builder)}</span></div>`:''}
      ${(i.configurations||[]).length?`<div class="kv"><span class="k">Config</span><span>${esc(i.configurations.join(', '))}</span></div>`:''}
      ${i.price_range?`<div class="kv"><span class="k">Price</span><span>${esc(i.price_range)}</span></div>`:''}
      ${i.possession?`<div class="kv"><span class="k">Possession</span><span>${esc(i.possession)}</span></div>`:''}
      ${i.rera_id?`<div class="kv"><span class="k">RERA</span><span>${esc(i.rera_id)}</span></div>`:''}
      ${i.usp?`<div class="usp">${esc(i.usp)}</div>`:''}
      ${i.units_left!=null?`<span class="scarce">${esc(i.units_left)} units left</span>`:''}
      ${i.offer_expiry?`<span class="scarce">offer ends ${esc(i.offer_expiry)}</span>`:''}
    </div>`).join('')}</div>`:'<div class="skel">no items</div>'}
  else if(k==='market'){
    const m=a.market_competitor||{};
    const mm=(m.micro_markets||[]).map(x=>`<div class="kv" style="display:flex;justify-content:space-between;font-size:12.5px;padding:6px 4px;border-bottom:1px solid var(--line-soft)">
      <span><b>${esc(x.name||'')}</b> <span class="sub">${esc(x.usp||'')}</span></span>
      <span>${esc(x.price_per_sqft||'')}${x.appreciation_5yr?' · ↑'+esc(x.appreciation_5yr):''}</span></div>`).join('');
    const comp=(m.competitors||[]).map(x=>`<div class="fact"><span class="src">${esc(x.name||'')}</span><span>${esc(x.our_positioning||'')}</span></div>`).join('');
    const scripts=m.objection_scripts||{},stories=m.objection_stories||{};
    const keys=[...new Set([...Object.keys(scripts),...Object.keys(stories)])];
    const objs=keys.map(ty=>`<div class="obj"><div class="ty">${esc(ty.replace(/_/g,' '))}</div><div class="duo">
      <div class="half"><div class="h">Quick rebuttal</div><div class="t">${esc(scripts[ty]||'—')}</div></div>
      <div class="half"><div class="h">30-sec proof story</div><div class="t">${esc(stories[ty]||'—')}</div></div>
    </div></div>`).join('');
    body.innerHTML=(mm?`<div class="faq"><span class="cat">micro markets</span>${mm}</div>`:'')
      +(comp?`<div class="faq"><span class="cat">competitors</span>${comp}</div>`:'')
      +(objs||'<div class="skel">no objection content</div>')}
  else if(k==='persona'){
    const p=a.persona||{};
    body.innerHTML=`${p.first_line?`<div class="sub" style="margin-bottom:4px">Opens every call with</div><div class="quote">“${esc(p.first_line)}”</div>`:''}
      ${p.agent_instructions?`<div class="sub" style="margin:12px 0 4px">System persona</div><div class="mono">${esc(p.agent_instructions)}</div>`:''}
      ${(p.stt_hints||[]).length?`<div class="sub" style="margin:12px 0 6px">STT vocabulary hints</div>
        <div class="qchips">${p.stt_hints.map(h=>`<span class="qchip">${esc(h)}</span>`).join('')}</div>`:''}`}
  else if(k==='facts'){
    const facts=ART.facts||[];
    body.innerHTML=facts.length?`<div class="sub" style="margin-bottom:8px">Every generated claim must trace back to one of these ${facts.length} extracted facts — the grounding trail.</div>`
      +facts.map(f=>`<div class="fact"><span class="src">${esc(f.source_ref||'?')}</span><span>${esc(f.claim||'')}${f.value?` <b>(${esc(f.value)})</b>`:''}</span></div>`).join('')
      :'<div class="skel">facts not recorded for this compile — recompile to capture the grounding trail</div>'}
  else{body.innerHTML=`<div class="mono">${esc(JSON.stringify(a,null,2))}</div>`}}

/* ── approve modal ────────────────────────────────────────── */
function askApprove(){
  $('mtitle').textContent='Make this cartridge LIVE?';
  $('mbody').innerHTML='This installs the compiled packs into the running engine and hot-reloads config. '+
    'Replaced packs are backed up first — rollback is one click on the previous cartridge.';
  $('mconfirm').onclick=doApprove;
  $('modalbg').classList.add('show')}
function closeModal(){$('modalbg').classList.remove('show')}
async function doApprove(){
  closeModal();
  try{
    const r=await api(`/api/platform/clients/${SEL}/approve`,{method:'POST'});
    toast('LIVE ✓ installed: '+(r.installed||[]).join(', '),'ok');
    render();loadClients()
  }catch(e){toast(e.message,'err')}}
$('modalbg').addEventListener('click',e=>{if(e.target===e.currentTarget)closeModal()});

loadClients();
</script></body></html>"""
