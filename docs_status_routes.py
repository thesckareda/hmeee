"""
Documentation status tracking & dashboard integration.

The docs site lives at docs-nextra/ (Nextra scaffold). This module
tracks which docs are TBA vs. written and exposes a dashboard tab.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter

logger = logging.getLogger("docs-status")

router = APIRouter(tags=["documentation"])

REPO_ROOT = Path(__file__).resolve().parent
DOCS_NEXTRA = REPO_ROOT / "docs-nextra" / "pages"

# All 22 planned docs — the ground truth list
_PLANNED_DOCS = {
    "product": [
        ("01-quickstart", "Quickstart"),
        ("02-dashboard-guide", "Dashboard Guide"),
        ("03-controls-reference", "Controls Reference"),
        ("04-caller-memory", "Caller Memory"),
        ("05-knowledge-base-editing", "KB Editing"),
        ("06-platform-compiler", "Platform Compiler"),
        ("07-call-quality-playbook", "Quality Playbook"),
        ("08-troubleshooting", "Troubleshooting"),
    ],
    "dev": [
        ("01-architecture", "Architecture"),
        ("02-call-lifecycle", "Call Lifecycle"),
        ("03-turn-pipeline", "Turn Pipeline"),
        ("04-conversational-iq", "Conversational IQ"),
        ("05-memory-system", "Memory System"),
        ("06-knowledge-retrieval", "Knowledge Retrieval"),
        ("07-providers", "Providers"),
        ("08-audio-layer", "Audio Layer"),
        ("09-kb-compiler", "KB Compiler"),
        ("10-config-reference", "Config Reference (generated)"),
        ("11-api-reference", "API Reference (generated)"),
        ("12-testing", "Testing"),
        ("13-deployment", "Deployment"),
        ("14-runbook", "Runbook"),
    ],
}


def _doc_status(audience: str, slug: str) -> str:
    """Check if a doc file exists and has content (not just a placeholder)."""
    path = DOCS_NEXTRA / audience / f"{slug}.mdx"
    if not path.is_file():
        return "tba"
    text = path.read_text(encoding="utf-8").strip()
    if text == "" or "placeholder" in text.lower():
        return "tba"
    # Very basic heuristic: if it has more than just a title, it's written
    lines = [l.strip() for l in text.split("\n") if l.strip() and not l.startswith("#")]
    return "written" if len(lines) > 3 else "tba"


@router.get("/api/docs/status")
async def api_docs_status():
    """Overall documentation completion status."""
    docs = {}
    total = 0
    written = 0
    for audience, items in _PLANNED_DOCS.items():
        docs[audience] = []
        for slug, title in items:
            status = _doc_status(audience, slug)
            docs[audience].append({"slug": slug, "title": title, "status": status})
            total += 1
            if status == "written":
                written += 1
    return {
        "docs": docs,
        "total": total,
        "written": written,
        "pct": round(100 * written / total) if total else 0,
        "nextra_url": "/docs",
        "master_plan": "DOCUMENTATION_MASTER_PLAN.md",
    }


@router.get("/docs-tab")
async def docs_tab_html():
    """Dashboard tab: documentation status (returns the HTML above)."""
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=_DOCS_TAB_HTML)


# HTML for the dashboard docs status tab
_DOCS_TAB_HTML = r"""<div style="padding: 20px;">
<h2>📚 Documentation Status</h2>
<div id="docstatus" style="margin: 20px 0; font-size: 14px;">
  <div style="text-align: center; padding: 40px;">
    <div style="font-size: 32px; color: #d4a843; margin-bottom: 10px;" id="docpct">0%</div>
    <div style="color: #8b949e;" id="doccnt">0 of 0 docs written</div>
  </div>
  <div style="margin: 20px 0;">
    <h3 style="color: #d4a843; margin-top: 20px; margin-bottom: 10px;">Operator Guides (Product)</h3>
    <div id="prod-list"></div>

    <h3 style="color: #d4a843; margin-top: 20px; margin-bottom: 10px;">Engineering Docs (Dev)</h3>
    <div id="dev-list"></div>
  </div>
  <div style="margin-top: 20px; padding: 10px; background: #161b22; border-radius: 6px;">
    <a href="/docs" style="color: #d4a843; text-decoration: none; font-weight: 600;">
      → Open Full Documentation Site
    </a><br/>
    <a href="https://github.com/sushrutdeshpande0202/speaksy/blob/main/DOCUMENTATION_MASTER_PLAN.md"
       style="color: #8b949e; text-decoration: none; font-size: 12px; margin-top: 8px; display: block;">
      Master Plan (GitHub)
    </a>
  </div>
</div>
<style>
.docitem { display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px solid #30363d; }
.docitem:last-child { border: none; }
.doctitle { color: #e6edf3; }
.docstatus { font-size: 11px; padding: 2px 8px; border-radius: 4px; }
.docstatus.tba { background: #2d2308; color: #d29922; }
.docstatus.written { background: #12351c; color: #3fb950; }
</style>
<script>
fetch('/api/docs/status').then(r=>r.json()).then(d=>{
  document.getElementById('docpct').textContent = d.pct+'%';
  document.getElementById('doccnt').textContent = d.written+' of '+d.total+' docs written';
  ['product','dev'].forEach(aud=>{
    const list = document.getElementById(aud===''?'prod-list':'prod-list');
    const ul = document.getElementById((aud==='product'?'prod':'dev')+'-list');
    (d.docs[aud]||[]).forEach(doc=>{
      const div = document.createElement('div');
      div.className = 'docitem';
      div.innerHTML = `<span class="doctitle">${doc.title}</span>
        <span class="docstatus ${doc.status}">${doc.status.toUpperCase()}</span>`;
      ul.appendChild(div);
    });
  });
}).catch(e=>console.error(e));
</script>
"""
