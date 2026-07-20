"""Capture the actual source documents into the run, so the bundle holds the
primary files the agents read, not just links. Best-effort and safe: deduped by
URL, size-capped, concurrent, every result recorded in a manifest. Placeholder
(test-mode) URLs are skipped cleanly.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import mimetypes
import re
import ssl
import urllib.request
from pathlib import Path

try:  # use certifi's trust store so HTTPS works on macOS local runs too
    import certifi
    _SSL = ssl.create_default_context(cafile=certifi.where())
except Exception:
    _SSL = ssl.create_default_context()

MAX_BYTES = 25 * 1024 * 1024   # 25 MB per file
TIMEOUT = 20
# A real browser User-Agent, many IGO/government sites (e.g. unctad.org) 403 a
# non-browser agent, which would leave grounding unable to check the source.
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_EXT = {"application/pdf": ".pdf", "text/html": ".html", "text/plain": ".txt",
        "application/json": ".json", "text/csv": ".csv"}


def _ext_for(ctype: str, url: str) -> str:
    ctype = (ctype or "").split(";")[0].strip().lower()
    if ctype in _EXT:
        return _EXT[ctype]
    low = url.lower().split("?")[0]
    for e in (".pdf", ".html", ".htm", ".csv", ".json", ".xlsx", ".docx"):
        if low.endswith(e):
            return ".html" if e == ".htm" else e
    return mimetypes.guess_extension(ctype) or ".bin"


def _fetch_one(url: str, dest: Path) -> dict:
    if not url or not url.lower().startswith(("http://", "https://")):
        return {"url": url, "status": "skipped", "reason": "no valid url"}
    if "example.org" in url:
        return {"url": url, "status": "skipped", "reason": "placeholder (test mode)"}
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL) as resp:
            ctype = resp.headers.get("Content-Type", "")
            data = resp.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return {"url": url, "status": "skipped", "reason": "over size cap"}
        name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10] + _ext_for(ctype, url)
        (dest / name).write_bytes(data)
        return {"url": url, "status": "saved", "file": name, "bytes": len(data),
                "content_type": ctype.split(";")[0].strip()}
    except Exception as e:  # network, 404, timeout, ssl, etc.
        return {"url": url, "status": "failed", "reason": str(e)[:140]}


def capture(urls: list[str], dest_dir, workers: int = 6) -> dict[str, dict]:
    """Fetch each unique URL into dest_dir. Returns a url -> result manifest."""
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    unique = [u for u in dict.fromkeys(urls) if u]
    results: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_fetch_one, u, dest): u for u in unique}
        for fut in concurrent.futures.as_completed(futures):
            r = fut.result()
            results[r["url"]] = r
    (dest / "sources.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def load_manifest(dest_dir) -> dict[str, dict]:
    p = Path(dest_dir) / "sources.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", s.lower())).strip()


def _read_source(url: str):
    """Fetch a URL, return (bytes, content_type) or (None, None)."""
    if not url or not url.lower().startswith(("http://", "https://")) or "example.org" in url:
        return None, None
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_SSL) as resp:
            ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
            data = resp.read(MAX_BYTES + 1)
        return (None, None) if len(data) > MAX_BYTES else (data, ctype)
    except Exception:
        return None, None


def _html_to_text(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)  # drop css/js
    return re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", " ", raw))


def _pdf_to_text(data: bytes) -> str:
    try:
        import io
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(data))
        out = []
        for i, page in enumerate(reader.pages):
            if i >= 80:            # cap very long reports
                break
            out.append(page.extract_text() or "")
        return "\n".join(out)
    except Exception:
        return ""


def fetch_text(url: str, max_chars: int = 60000) -> str:
    """The full readable text of the source, HTML stripped or PDF extracted, or
    '' if it could not be read. This is what lets the Reader actually read the
    report or paper rather than skim search snippets."""
    data, ctype = _read_source(url)
    if data is None:
        return ""
    is_pdf = ctype == "application/pdf" or url.lower().split("?")[0].endswith(".pdf")
    if is_pdf:
        text = _pdf_to_text(data)
    elif ctype in ("text/html", "text/plain", "application/xhtml+xml", ""):
        text = _html_to_text(data.decode("utf-8", "ignore"))
    else:
        return ""
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:max_chars]


def quote_grounded(url: str, quote: str):
    """Does the confirming quote actually appear in the source? Turns the verifier
    from model-trust into policy-trust. Now reads PDFs too (extracted text), not
    only HTML. Returns True (found), False (read but not found), or None
    (uncheckable: no url, a fetch failure, or an unsupported binary)."""
    if not quote or not url or not url.lower().startswith(("http://", "https://")):
        return None
    if "example.org" in url:
        return None
    text = _norm(fetch_text(url, max_chars=400000))
    nq = _norm(quote)
    if not text:
        return None
    if not nq or len(nq) < 8:
        return None
    if nq in text:
        return True
    key = " ".join(nq.split()[:9])          # allow light truncation/paraphrase
    if len(key) >= 20 and key in text:
        return True
    return False
