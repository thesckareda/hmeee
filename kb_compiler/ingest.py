"""
Stage A — INGEST (deterministic, no LLM).

Any dropped format → normalized text chunks with source refs.
Native: .txt .md .json .csv .html and http(s) URLs.
Optional (used when the library is installed): .docx (python-docx),
.pdf (pymupdf → pypdf), .xlsx (openpyxl).
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("kb-compiler")

CHUNK_CHARS = 3000


def _strip_html(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _read_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md", ".yaml", ".yml"):
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".json":
        try:
            return json.dumps(json.loads(path.read_text(encoding="utf-8")), indent=1, ensure_ascii=False)
        except json.JSONDecodeError:
            return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".csv":
        out = io.StringIO()
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.reader(f):
                out.write(" | ".join(cell.strip() for cell in row) + "\n")
        return out.getvalue()
    if suffix in (".html", ".htm"):
        return _strip_html(path.read_text(encoding="utf-8", errors="replace"))
    if suffix == ".docx":
        try:
            import docx  # type: ignore

            document = docx.Document(str(path))
            return "\n".join(p.text for p in document.paragraphs if p.text.strip())
        except ImportError:
            logger.warning("[INGEST] python-docx not installed — skipping %s", path.name)
            return ""
    if suffix == ".pdf":
        for lib in ("fitz", "pypdf"):
            try:
                if lib == "fitz":
                    import fitz  # type: ignore

                    doc = fitz.open(str(path))
                    return "\n".join(page.get_text() for page in doc)
                import pypdf  # type: ignore

                reader = pypdf.PdfReader(str(path))
                return "\n".join((page.extract_text() or "") for page in reader.pages)
            except ImportError:
                continue
        logger.warning("[INGEST] no PDF library installed — skipping %s", path.name)
        return ""
    if suffix == ".xlsx":
        try:
            import openpyxl  # type: ignore

            wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
            lines = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    cells = [str(c) for c in row if c is not None]
                    if cells:
                        lines.append(" | ".join(cells))
            return "\n".join(lines)
        except ImportError:
            logger.warning("[INGEST] openpyxl not installed — skipping %s", path.name)
            return ""
    logger.warning("[INGEST] Unsupported format %s — skipping", path.name)
    return ""


def _fetch_url(url: str) -> str:
    try:
        import httpx

        resp = httpx.get(url, timeout=30.0, follow_redirects=True)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "")
        return _strip_html(resp.text) if "html" in ctype else resp.text
    except Exception as exc:
        logger.warning("[INGEST] URL fetch failed %s: %s", url, exc)
        return ""


def _chunk(text: str, source: str) -> list[dict[str, Any]]:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        return []
    chunks: list[dict[str, Any]] = []
    paragraphs = text.split("\n\n")
    buf = ""
    for para in paragraphs:
        if len(buf) + len(para) > CHUNK_CHARS and buf:
            chunks.append({"source": source, "chunk_id": f"{source}#{len(chunks)}", "text": buf.strip()})
            buf = ""
        buf += para + "\n\n"
    if buf.strip():
        chunks.append({"source": source, "chunk_id": f"{source}#{len(chunks)}", "text": buf.strip()})
    return chunks


def ingest_sources(sources: list[str], base_dir: Path) -> list[dict[str, Any]]:
    """Resolve globs/paths/URLs into text chunks. Never raises on a bad source."""
    chunks: list[dict[str, Any]] = []
    for src in sources:
        src = str(src).strip()
        if not src:
            continue
        if src.startswith(("http://", "https://")):
            chunks.extend(_chunk(_fetch_url(src), src))
            continue
        pattern = Path(src)
        candidates: list[Path]
        if pattern.is_absolute():
            candidates = sorted(pattern.parent.glob(pattern.name)) if any(c in src for c in "*?[") else [pattern]
        else:
            candidates = sorted(base_dir.glob(src)) if any(c in src for c in "*?[") else [base_dir / src]
        for path in candidates:
            if path.is_file():
                chunks.extend(_chunk(_read_file(path), path.name))
    logger.info("[INGEST] %d chunks from %d sources", len(chunks), len(sources))
    return chunks
