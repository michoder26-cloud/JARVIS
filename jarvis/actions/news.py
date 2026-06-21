"""News RSS connector (Python stdlib only — no extra dependencies).

Fetches and parses RSS 2.0 and Atom feeds from Thai and English news
sources.  The public entry point is :func:`get_news`, which returns::

    {
        "success": True,
        "headlines": [{"title", "source", "url", "published"}, ...],
        "formatted": "ข่าวล่าสุด: 1. ... (จาก ...). 2. ...",
    }
"""

from __future__ import annotations

import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Tuple

__all__ = ["get_news"]

# ---------------------------------------------------------------------- #
# Feed catalog
# ---------------------------------------------------------------------- #
# (source_name, feed_url)
_FEEDS: Dict[str, List[Tuple[str, str]]] = {
    "general": [
        ("Thairath", "https://www.thairath.co.th/rss/news"),
        ("Bangkok Post", "https://www.bangkokpost.com/rss/data/topstories.xml"),
        ("The Nation Thailand", "https://www.nationthailand.com/rss/category/nation"),
        ("BBC", "https://feeds.bbci.co.uk/news/rss.xml"),
        ("Reuters", "https://www.reutersagency.com/feed/?best-topics=top-news&post_type=best"),
    ],
    "tech": [
        ("Thairath Tech", "https://www.thairath.co.th/rss/tech"),
        ("BBC Tech", "https://feeds.bbci.co.uk/news/technology/rss.xml"),
        ("The Nation Tech", "https://www.nationthailand.com/rss/category/nation/tech"),
    ],
    "business": [
        ("Bangkok Post Business", "https://www.bangkokpost.com/rss/data/business.xml"),
        ("BBC Business", "https://feeds.bbci.co.uk/news/business/rss.xml"),
        ("Reuters Business", "https://www.reutersagency.com/feed/?best-topics=business-finance&post_type=best"),
    ],
}

# A safe fallback list used when the chosen category has no feeds.
_DEFAULT_FEEDS = _FEEDS["general"]

# Browser-like User-Agent — many RSS servers reject bare urllib.
_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


# ---------------------------------------------------------------------- #
# Fetch
# ---------------------------------------------------------------------- #
def _fetch(url: str, timeout: int = 10) -> str:
    """Fetch *url* and return its body as text. Raises on HTTP error."""
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    # RSS/Atom is XML (UTF-8 in practice); decode defensively.
    return data.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------- #
# Parse
# ---------------------------------------------------------------------- #
def _text(elem) -> str:
    """Get trimmed text of an Element, or '' if None."""
    if elem is None:
        return ""
    return (elem.text or "").strip()


def _parse_rss2(root: ET.Element, source: str) -> List[Dict[str, str]]:
    """Parse an RSS 2.0 feed."""
    out: List[Dict[str, str]] = []
    # RSS 2.0: rss > channel > item
    channel = root.find("channel")
    if channel is None:
        return out
    for item in channel.findall("item"):
        title = _text(item.find("title"))
        link = _text(item.find("link"))
        pub = _text(item.find("pubDate"))
        out.append({
            "title": title,
            "source": source,
            "url": link,
            "published": pub,
        })
    return out


def _parse_atom(root: ET.Element, source: str) -> List[Dict[str, str]]:
    """Parse an Atom feed."""
    out: List[Dict[str, str]] = []
    # Atom namespaces vary; match by local tag name.
    for entry in root.iter():
        if not entry.tag.endswith("}entry") and entry.tag != "entry":
            continue
        title = ""
        link = ""
        pub = ""
        for child in entry:
            tag = child.tag.split("}")[-1]
            if tag == "title":
                title = _text(child)
            elif tag == "link":
                href = child.get("href")
                if href:
                    link = href
                elif child.text:
                    link = child.text.strip()
            elif tag in ("published", "updated"):
                if not pub:
                    pub = _text(child)
        out.append({
            "title": title,
            "source": source,
            "url": link,
            "published": pub,
        })
    return out


def _parse_feed(text: str, source: str) -> List[Dict[str, str]]:
    """Parse RSS 2.0 or Atom, auto-detecting the format."""
    root = ET.fromstring(text)
    tag = root.tag.lower()
    if tag.endswith("}rss") or tag == "rss":
        return _parse_rss2(root, source)
    # Atom root is <feed ...>
    if tag.endswith("}feed") or tag == "feed":
        return _parse_atom(root, source)
    # Some feeds wrap a single channel.
    if tag.endswith("}channel") or tag == "channel":
        return _parse_rss2(root, source)
    return []


# ---------------------------------------------------------------------- #
# Public action
# ---------------------------------------------------------------------- #
def _format_headlines(headlines: List[Dict[str, str]]) -> str:
    """Build a TTS-friendly summary of the headlines."""
    n = len(headlines)
    if n == 0:
        return "ไม่พบข่าวล่าสุดในขณะนี้"
    parts = ["ข่าวล่าสุด:"]
    for i, h in enumerate(headlines, 1):
        title = h.get("title") or "(ไม่มีหัวข้อ)"
        src = h.get("source") or "แหล่งข่าว"
        parts.append(f"{i}. {title} (จาก {src}).")
    return " ".join(parts)


def get_news(max_items: int = 5, category: str = "general") -> Dict[str, Any]:
    """Fetch latest news headlines from RSS feeds.

    Uses Python stdlib only (``urllib`` + ``xml.etree.ElementTree``).

    Returns a dict with ``success``, ``headlines`` (list of
    ``{title, source, url, published}``), and ``formatted`` (text for TTS).
    """
    max_items = max(1, min(int(max_items or 5), 50))
    category = (category or "general").strip().lower()
    feeds = _FEEDS.get(category, _DEFAULT_FEEDS)

    all_headlines: List[Dict[str, str]] = []
    errors: List[str] = []
    for source, url in feeds:
        try:
            body = _fetch(url)
            items = _parse_feed(body, source)
            all_headlines.extend(items)
        except Exception as exc:  # noqa: BLE001 — keep going on feed failures
            errors.append(f"{source}: {exc}")
        # Stop early once we have enough to fill the request, but keep a
        # little extra so inter-source rounding doesn't underfill.
        if len(all_headlines) >= max_items * 2:
            break

    if not all_headlines:
        return {
            "success": False,
            "error": "No headlines could be fetched.",
            "feed_errors": errors,
            "formatted": "ไม่พบข่าวล่าสุดในขณะนี้",
        }

    headlines = all_headlines[:max_items]
    return {
        "success": True,
        "category": category,
        "headlines": headlines,
        "formatted": _format_headlines(headlines),
        "feed_errors": errors,
    }