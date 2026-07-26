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
import ipaddress
import re
import socket
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

_BLOCKED_HOSTS = {"localhost", "metadata", "metadata.google.internal"}


def _is_internal(url: str) -> bool:
    """True if a URL points at a private, loopback, link-local, or metadata host,
    so a hallucinated or injection-planted URL can never make us fetch internal
    services (e.g. the 169.254.169.254 cloud-metadata endpoint). Hostnames are
    resolved and every resolved address is checked, to catch a public name that
    maps to an internal IP."""
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        return True
    if not host or host in _BLOCKED_HOSTS or host.endswith((".localhost", ".internal")):
        return True

    def _bad(ip_str: str) -> bool:
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return False
        return (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified)

    if _bad(host):                       # host is an IP literal
        return True
    try:                                 # host is a name: resolve and check every A/AAAA
        infos = socket.getaddrinfo(host, None)
        return any(_bad(info[4][0]) for info in infos)
    except Exception:
        return True                      # cannot resolve, do not fetch

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
    if _is_internal(url):
        return {"url": url, "status": "skipped", "reason": "internal or private host"}
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
    if (not url or not url.lower().startswith(("http://", "https://"))
            or "example.org" in url or _is_internal(url)):
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


_TEXT_CACHE: dict[str, str] = {}


def _extract_full(url: str) -> str:
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
    return re.sub(r"\n{3,}", "\n\n", text).strip()[:400000]


_LINK_CACHE: dict[str, str] = {}
# strong signatures of a page with no real content, an error page, an empty search,
# or a placeholder. If one of these leads the page (top of the body), the page is
# treated as dead even though it returned a 200 status.
_DEAD_MARKERS = (
    "page not found", "404 error", "error 404", "page you requested",
    "page you were looking for", "page you are looking for", "page cannot be found",
    "page could not be found", "page does not exist", "this page does not exist",
    "no results found", "no results were found", "no matching results",
    "0 results found", "nothing found", "nothing to show", "no content available",
    "content unavailable", "content not available", "content not found",
    "under construction", "under maintenance", "coming soon", "sorry, this page",
    "we can't find the", "we cannot find the", "we couldn't find the",
    "requested page could not", "could not be found")
# weaker signals, counted only when the page is also short or bounced to the home
_WEAK_MARKERS = ("no longer available", "temporarily unavailable", "not available",
                 "does not exist", "no records", "no items", "no data available")
_HOME_PATHS = {"", "home", "en", "index", "index.html", "index.php", "en/home"}


def link_status(url: str) -> str:
    """One opened-and-inspected verdict for a URL, so nothing blank or missing is
    ever shown. Returns:
      - "ok"      : resolves and carries real content
      - "dead"    : a hard 404/410, OR a soft-404 (200 status but a not-found body
                    or a silent redirect to the site home), the case a status code
                    alone misses
      - "empty"   : resolves but has almost no readable content (blank or JS shell)
      - "blocked" : 401/403/429, exists but bot-protected, do not drop
      - "unknown" : a network error, uncheckable, do not drop
    Cached per URL within a run."""
    if not url or not url.lower().startswith(("http://", "https://")) or "example.org" in url:
        return "ok"
    if _is_internal(url):
        return "dead"                    # never surface an internal or private URL
    if url in _LINK_CACHE:
        return _LINK_CACHE[url]

    def _finish(v: str) -> str:
        if len(_LINK_CACHE) >= 256:
            _LINK_CACHE.clear()
        _LINK_CACHE[url] = v
        return v

    try:
        req = urllib.request.Request(url, headers=HEADERS)  # GET, follows redirects
        with urllib.request.urlopen(req, timeout=14, context=_SSL) as resp:
            final = resp.geturl()                            # where redirects landed
            ctype = (resp.headers.get_content_type() or "").lower()
            raw = resp.read(65536)                           # first 64 KB is enough
    except urllib.error.HTTPError as e:
        return _finish("dead" if e.code in (404, 410) else
                       "blocked" if e.code in (401, 403, 429) else "unknown")
    except Exception:
        return _finish("unknown")

    is_pdf = ctype == "application/pdf" or url.lower().split("?")[0].endswith(".pdf")
    if is_pdf:
        return _finish("ok" if raw[:5] == b"%PDF-" or len(raw) > 2000 else "empty")

    from urllib.parse import urlparse
    text = _html_to_text(raw.decode("utf-8", "ignore"))
    low = text.lower()
    top = low[:2000]                                     # page-level messages sit near the top
    landed_home = urlparse(final).path.strip("/").lower() in _HOME_PATHS
    req_was_deep = len(urlparse(url).path.strip("/")) > 1
    # a strong no-content or error signature leading the page: dead, even at 200
    if any(m in top for m in _DEAD_MARKERS):
        return _finish("dead")
    # a weaker signal only counts when the page is also thin or bounced to the home
    if any(m in low for m in _WEAK_MARKERS) and (landed_home or len(text) < 1800):
        return _finish("dead")
    if landed_home and req_was_deep and len(text) < 1200:
        return _finish("dead")
    if len(text.strip()) < 250:                          # blank, placeholder, or JS shell
        return _finish("empty")
    return _finish("ok")


def link_dead(url: str) -> bool:
    """True when a URL is a hard or soft 404, so a stale, hallucinated, or
    redirected-away link is dropped before it is ever shown. Blocked, empty, and
    uncheckable links return False: we do not drop what we cannot disprove."""
    return link_status(url) == "dead"


def fetch_text(url: str, max_chars: int = 60000) -> str:
    """The full readable text of the source, HTML stripped or PDF extracted, or
    '' if it could not be read. Cached per URL so the Reader's read and the
    Verifier's grounding fetch the same report once, not twice."""
    if not url:
        return ""
    full = _TEXT_CACHE.get(url)
    if full is None:
        full = _extract_full(url)
        if len(_TEXT_CACHE) >= 64:
            _TEXT_CACHE.clear()
        _TEXT_CACHE[url] = full
    return full[:max_chars]


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
