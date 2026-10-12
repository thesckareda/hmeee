"""
Documentation routes — Docus site served as a dashboard tab.

The Docus site (docs-docus/) generates static HTML. This module:
1. Serves the docs as embedded HTML in a dashboard tab
2. Provides a /docs route that loads Docus pages
3. Tracks doc status (coverage %)
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

logger = logging.getLogger("docs")

router = APIRouter(tags=["documentation"])

REPO_ROOT = Path(__file__).resolve().parent
DOCS_CONTENT = REPO_ROOT / "docs-docus" / "content"


@router.get("/api/docs/status")
async def api_docs_status():
    """Documentation coverage: how many of 22 docs are written."""
    docs_written = 0
    total_expected = 22

    for aud in ["product", "dev"]:
        aud_dir = DOCS_CONTENT / aud
        if aud_dir.is_dir():
            for md in aud_dir.glob("*.md"):
                text = md.read_text(encoding="utf-8").strip()
                if text and not "placeholder" in text.lower():
                    docs_written += 1

    return {
        "total": total_expected,
        "written": docs_written,
        "pct": round(100 * docs_written / total_expected) if total_expected else 0,
        "docs_url": "/docs-tab",
    }


@router.get("/docs-tab")
async def docs_tab():
    """Embedded documentation tab for the dashboard."""
    status = await api_docs_status()

    html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Speaksy Documentation</title>
    <style>
        :root {{
            --bg: #0d1117; --card: #161b22; --line: #30363d;
            --text: #e6edf3; --dim: #8b949e; --gold: #d4a843;
            --green: #3fb950; --red: #f85149;
        }}
        * {{ box-sizing: border-box; margin: 0; }}
        body {{
            background: var(--bg); color: var(--text);
            font: 14px -apple-system, 'Segoe UI', sans-serif;
            padding: 20px;
        }}
        h1 {{ font-size: 24px; margin-bottom: 8px; color: var(--gold); }}
        .stat {{
            font-size: 32px; color: var(--gold); font-weight: 600;
            margin: 20px 0;
        }}
        .links {{
            display: grid; grid-template-columns: 1fr 1fr;
            gap: 12px; margin: 20px 0;
        }}
        .link {{
            padding: 12px; background: var(--card); border: 1px solid var(--line);
            border-radius: 6px; text-decoration: none; color: var(--text);
        }}
        .link:hover {{ border-color: var(--gold); background: var(--line); }}
        .link strong {{ color: var(--gold); }}
        .status {{ color: var(--dim); font-size: 12px; margin-top: 4px; }}
        .bar {{
            height: 8px; background: var(--line); border-radius: 4px;
            overflow: hidden; margin: 12px 0;
        }}
        .bar-fill {{
            height: 100%; background: var(--gold); width: {status['pct']}%;
            transition: width 0.5s;
        }}
        .note {{
            background: var(--card); border-left: 3px solid var(--gold);
            padding: 12px; margin: 20px 0; border-radius: 4px;
            font-size: 13px; color: var(--dim);
        }}
    </style>
</head>
<body>
    <h1>📚 Documentation</h1>

    <div class="stat">{status['written']}/{status['total']}</div>
    <div class="bar"><div class="bar-fill"></div></div>
    <p style="color: var(--dim); font-size: 13px;">{status['pct']}% complete</p>

    <div class="note">
        <strong>Docus-based documentation site</strong> with all platform guides (operators + engineers).
        Built from <code>docs-docus/content/</code>, generated programmatically.
    </div>

    <div class="links">
        <a class="link" href="/docs/">
            <strong>→ Full Docs Site</strong>
            <div class="status">Complete documentation with all 22 guides</div>
        </a>

        <a class="link" href="https://github.com/sushrutdeshpande0202/speaksy/blob/main/DOCUMENTATION_MASTER_PLAN.md">
            <strong>→ Master Plan</strong>
            <div class="status">Writing blueprint & verification checklists</div>
        </a>

        <a class="link" href="/platform">
            <strong>→ KB Compiler</strong>
            <div class="status">Drop a KB, generate an agent</div>
        </a>

        <a class="link" href="https://nuxt.com/docs/getting-started">
            <strong>→ Docus (Nuxt Content)</strong>
            <div class="status">Documentation framework</div>
        </a>
    </div>

    <div class="note">
        <strong>To build the Docus site locally:</strong>
        <pre style="margin-top: 8px; font-size: 12px; overflow-x: auto;">cd docs-docus && npm install && npm run dev</pre>
        Opens at <code>http://localhost:3000</code>
    </div>

    <script>
        // Auto-refresh status every 30s
        setInterval(async () => {{
            const r = await fetch('/api/docs/status');
            const d = await r.json();
            document.querySelector('.bar-fill').style.width = d.pct + '%';
            document.querySelector('.stat').textContent = d.written + '/' + d.total;
            const p = Math.round(d.pct);
            document.querySelector('p').textContent = p + '% complete';
        }}, 30000);
    </script>
</body>
</html>"""

    return HTMLResponse(content=html)


@router.get("/docs/")
async def docs_index():
    """Serve the static docs HTML site."""
    docs_html = REPO_ROOT / "docs_html" / "index.html"
    if docs_html.is_file():
        return HTMLResponse(content=docs_html.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Documentation site building...</h1>", status_code=202)


@router.get("/docs/view")
async def docs_view(file: str = ""):
    """View individual markdown doc as HTML."""
    if not file or "/" not in file:
        # Redirect to index
        return HTMLResponse(content=Path(REPO_ROOT / "docs_html" / "index.html").read_text(encoding="utf-8"))

    category, doc_slug = file.split("/", 1)
    doc_path = REPO_ROOT / "docs-docus" / "content" / category / f"{doc_slug}.md"

    if not doc_path.is_file():
        return HTMLResponse(content="<h1>❌ Document not found</h1>", status_code=404)

    content = doc_path.read_text(encoding="utf-8")

    # Simple markdown to HTML (basic)
    import re
    html = content.replace("<", "&lt;").replace(">", "&gt;")
    html = re.sub(r"^# (.+)$", r"<h1>\1</h1>", html, flags=re.MULTILINE)
    html = re.sub(r"^## (.+)$", r"<h2>\1</h2>", html, flags=re.MULTILINE)
    html = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
    html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
    html = re.sub(r"\n```(.*?)\n(.*?)\n```", r"<pre><code>\2</code></pre>", html, flags=re.DOTALL)
    html = html.replace("\n\n", "</p><p>")
    html = f"<p>{html}</p>"

    # Wrap in full page
    page = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{doc_slug.replace('-', ' ').title()}</title>
    <style>
        :root {{ --bg: #0d1117; --card: #161b22; --line: #30363d; --text: #e6edf3; --gold: #d4a843; }}
        * {{ box-sizing: border-box; margin: 0; }}
        body {{ background: var(--bg); color: var(--text); font: 14px system-ui; padding: 20px; max-width: 900px; margin: 0 auto; line-height: 1.6; }}
        a {{ color: var(--gold); text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        pre {{ background: var(--card); padding: 12px; overflow-x: auto; border-radius: 6px; margin: 12px 0; }}
        h1, h2, h3 {{ color: var(--gold); margin: 16px 0 8px; }}
        h1 {{ font-size: 28px; }}
        h2 {{ font-size: 20px; margin-top: 24px; }}
        h3 {{ font-size: 16px; }}
        .nav {{ margin-bottom: 24px; }}
        .nav a {{ display: inline-block; padding: 8px 12px; background: var(--card); border-radius: 6px; margin-right: 8px; }}
    </style>
</head>
<body>
    <div class="nav">
        <a href="/docs/">← Back to all docs</a>
        <a href="/docs-tab">Docs Hub</a>
    </div>
    <article>
        {html}
    </article>
</body>
</html>"""
    return HTMLResponse(content=page)
