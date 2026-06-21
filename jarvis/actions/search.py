"""Web search via DuckDuckGo (no API key required).

Primary backend: the ``ddgs`` package (the maintained successor to
``duckduckgo_search``).  Fallback: an httpx GET against DuckDuckGo's HTML
endpoint with regex parsing of the result cards.

The public entry point is :func:`search_web_api`, which always returns a dict
of shape::

    {
        "success": True,
        "query": "...",
        "results": [{"title", "snippet", "url"}, ...],
        "formatted": "พบ N ผลลัพธ์: 1. ... 2. ...",
    }
"""

from __future__ import annotations

import re
import html as _html
import urllib.parse
from typing import Any, Dict, List

__all__ = ["search_web_api"]


# ---------------------------------------------------------------------- #
# Primary backend: ddgs
# ---------------------------------------------------------------------- #
def _search_with_ddgs(query: str, max_results: int) -> List[Dict[str, str]]:
    """Return results from the ``ddgs`` package, or raise on failure."""
    from ddgs import DDGS  # type: ignore

    out: List[Dict[str, str]] = []
    with DDGS() as ddgs:
        for item in ddgs.text(query, max_results=max_results):
            out.append({
                "title": (item.get("title") or "").strip(),
                "snippet": (item.get("body") or item.get("snippet") or "").strip(),
                "url": (item.get("href") or item.get("url") or "").strip(),
            })
    return out


# ---------------------------------------------------------------------- #
# Fallback backend: httpx + regex against DuckDuckGo HTML
# ---------------------------------------------------------------------- #
_RESULT_BLOCK = re.compile(
    r'<a[^>]+class="result__a"[^>]*>(.*?)</a>.*?'
    r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_HREF = re.compile(r'href="([^"]+)"')
_TAG = re.compile(r"<[^>]+>")
_UDDG = re.compile(r"uddg=([^&\"]+)")


def _strip_tags(s: str) -> str:
    s = _TAG.sub("", s)
    s = _html.unescape(s)
    return s.strip()


def _resolve_ddg_url(href: str) -> str:
    """DuckDuckGo wraps result URLs in a redirect; extract the real target."""
    m = _UDDG.search(href)
    if m:
        return urllib.parse.unquote(m.group(1))
    return href


def _search_with_httpx(query: str, max_results: int) -> List[Dict[str, str]]:
    """Return results parsed from DuckDuckGo's HTML endpoint."""
    import httpx  # type: ignore

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
    }
    resp = httpx.get(
        "https://html.duckduckgo.com/html/",
        params={"q": query},
        headers=headers,
        timeout=15,
        follow_redirects=True,
    )
    resp.raise_for_status()
    body = resp.text

    out: List[Dict[str, str]] = []
    for m in _RESULT_BLOCK.finditer(body):
        block_a, block_snip = m.group(1), m.group(2)
        href_match = _HREF.search(m.group(0))
        url = _resolve_ddg_url(href_match.group(1)) if href_match else ""
        out.append({
            "title": _strip_tags(block_a),
            "snippet": _strip_tags(block_snip),
            "url": url,
        })
        if len(out) >= max_results:
            break
    return out


# ---------------------------------------------------------------------- #
# Public action
# ---------------------------------------------------------------------- #
def _format_results(query: str, results: List[Dict[str, str]]) -> str:
    """Build a TTS-friendly summary of the results."""
    n = len(results)
    if n == 0:
        return f"ไม่พบผลลัพธ์สำหรับ '{query}'"
    lines = [f"พบ {n} ผลลัพธ์สำหรับ '{query}':"]
    for i, r in enumerate(results, 1):
        title = r.get("title") or "(ไม่มีชื่อ)"
        snippet = r.get("snippet") or ""
        if snippet:
            lines.append(f"{i}. {title} - {snippet}")
        else:
            lines.append(f"{i}. {title}")
    return " ".join(lines)


def search_web_api(query: str, max_results: int = 5) -> Dict[str, Any]:
    """Search the web via DuckDuckGo (no API key required).

    Returns a dict with ``success``, ``query``, ``results`` (list of
    ``{title, snippet, url}``), and ``formatted`` (text for TTS).
    """
    if not query or not query.strip():
        return {"success": False, "error": "No search query provided."}
    query = query.strip()
    max_results = max(1, min(int(max_results or 5), 20))

    # Try the ddgs package first, then fall back to httpx HTML scraping.
    results: List[Dict[str, str]] = []
    backend = "ddgs"
    try:
        results = _search_with_ddgs(query, max_results)
    except Exception:
        backend = "httpx"
        try:
            results = _search_with_httpx(query, max_results)
        except Exception as exc:
            return {
                "success": False,
                "query": query,
                "error": f"Both search backends failed: {exc}",
            }

    return {
        "success": True,
        "query": query,
        "backend": backend,
        "results": results,
        "formatted": _format_results(query, results),
    }