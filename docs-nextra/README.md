# Speaksy Documentation (Nextra)

Complete platform documentation site using [Nextra](https://nextra.site/) — a Next.js-based static documentation framework with dark mode, search, and automatic sidebar generation.

## Structure

```
pages/
├── index.mdx                # hub page (you are here)
├── product/                 # 8 operator guides
│   ├── 01-quickstart.mdx
│   ├── 02-dashboard-guide.mdx
│   ├── 03-controls-reference.mdx
│   ├── 04-caller-memory.mdx
│   ├── 05-knowledge-base-editing.mdx
│   ├── 06-platform-compiler.mdx
│   ├── 07-call-quality-playbook.mdx
│   ├── 08-troubleshooting.mdx
│   └── _meta.json           # nav structure
├── dev/                     # 14 engineering docs
│   ├── 01-architecture.mdx
│   ├── ... (13 more)
│   └── _meta.json
└── CHANGELOG-DOCS.mdx       # coverage stats & updates
```

## Development

```bash
# Install dependencies
npm install

# Start dev server (http://localhost:3001)
npm run dev

# Build for production
npm run build
npm start
```

## Editing

Each doc is a Markdown file with optional MDX (React components). The `_meta.json` files define sidebar navigation order.

**Doc template:**
```markdown
# Title

> **Source files:** `a.py`, `b.py` · **Tests:** `tests/test_x.py`

## Do it now (the 30-second version)

## How it works

## Related
- [Other doc](./sibling)
```

See `DOCUMENTATION_MASTER_PLAN.md` (repo root) for the full writing guide.

## Deployment

The docs site is served separately from the FastAPI app:
- FastAPI dashboard: `http://localhost:8000` (with `/docs` route)
- Docs site: `http://localhost:3001` (or `http://localhost:8000/docs` if proxied)
- Dashboard tab shows `/docs-tab` for status & quick links

## Status Tracking

The parent `docs_status_routes.py` module:
- Reads `pages/` structure
- Detects which docs are written vs. TBA (placeholder)
- Exposes `/api/docs/status` JSON endpoint
- Renders a dashboard tab with live progress

Run `python3 -c "from docs_status_routes import api_docs_status; import asyncio; print(asyncio.run(api_docs_status()))"` to check status.

## Backlinks & Cross-referencing

Each doc ends with a "Related" section:
```markdown
## Related
- [Quickstart](./01-quickstart)
- [Architecture](../dev/01-architecture)
```

Relative paths work because all docs are `.mdx` under `pages/`.

---

See the [Master Plan](../DOCUMENTATION_MASTER_PLAN.md) for phasing, verification checklists, and the ground-truth source map (which files to read before writing each doc).
