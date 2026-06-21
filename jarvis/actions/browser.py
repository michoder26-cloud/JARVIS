"""Browser automation using Playwright.

Provides functions for controlling a browser (Chrome/Chromium) via Playwright's
sync API.  A single :class:`BrowserController` instance is reused across calls so
the browser stays open between commands — this mirrors how a voice assistant
should behave (user sees the browser window persist).

On Windows the browser launches in headed mode (the user can see it).  On Linux
it launches headless by default, which is suitable for a headless VPS.
"""

from __future__ import annotations

import os
import platform
import time
from typing import Optional

from playwright.sync_api import sync_playwright, Error as PlaywrightError

__all__ = [
    "open_browser",
    "search_youtube",
    "search_web",
    "navigate_to",
    "close_browser",
    "take_browser_screenshot",
]


IS_WINDOWS = platform.system() == "Windows"
# Headed on Windows, headless on Linux (VPS usually has no display).
_HEADLESS = not IS_WINDOWS

# Default timeout for page operations (ms).  Generous so slow networks still work.
_DEFAULT_TIMEOUT = 15_000


class BrowserController:
    """Singleton controller wrapping a Playwright Chromium browser."""

    _instance: Optional["BrowserController"] = None
    _playwright = None
    _browser = None
    _page = None

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._page = None

    # ------------------------------------------------------------------ #
    # singleton
    # ------------------------------------------------------------------ #
    @classmethod
    def get_instance(cls) -> "BrowserController":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    def ensure_started(self) -> None:
        """Start Playwright + Chromium if not already running."""
        if self._browser is not None and self._page is not None:
            # Verify the browser is still alive; if it was closed externally,
            # restart.
            try:
                _ = self._page.evaluate("1")  # cheap no-op
                return
            except PlaywrightError:
                self._reset()

        self._playwright = sync_playwright().start()
        launch_kwargs: dict = {"headless": _HEADLESS}
        # Prefer installed Chrome when available; fall back to bundled Chromium.
        try:
            self._browser = self._playwright.chromium.launch(
                channel="chrome", **launch_kwargs
            )
        except PlaywrightError:
            # channel="chrome" only works if Chrome is installed.  On systems
            # without it (e.g. fresh Linux VPS) fall back to Playwright's
            # bundled Chromium.
            self._browser = self._playwright.chromium.launch(**launch_kwargs)

        self._page = self._browser.new_page()
        self._page.set_default_timeout(_DEFAULT_TIMEOUT)

    def _reset(self) -> None:
        """Tear down everything so :meth:`ensure_started` can rebuild."""
        for attr in ("_page", "_browser"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def close(self) -> None:
        self._reset()

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #
    @property
    def page(self):
        self.ensure_started()
        return self._page

    def goto(self, url: str) -> None:
        self.page.goto(url, wait_until="domcontentloaded")

    def click_search_box(self) -> None:
        """Try a few well-known search-box selectors until one works."""
        selectors = [
            "input[name='q']",
            "input#search",
            "input[type='search']",
            "input[aria-label*='Search' i]",
            "input[placeholder*='Search' i]",
            "input.search",
        ]
        for sel in selectors:
            try:
                self.page.wait_for_selector(sel, timeout=4000)
                self.page.fill(sel, "")
                return sel
            except PlaywrightError:
                continue
        return None


# ---------------------------------------------------------------------- #
# Public action functions
# ---------------------------------------------------------------------- #
def open_browser(url: str, search: Optional[str] = None) -> dict:
    """Open the browser to *url*.

    If *search* is provided, navigate to *url* and type the query into the first
    available search box, then press Enter.
    """
    try:
        ctrl = BrowserController.get_instance()
        ctrl.ensure_started()
        ctrl.goto(url)
        result: dict = {"success": True, "url": url}
        if search:
            sel = ctrl.click_search_box()
            if sel is None:
                result["search"] = search
                result["error"] = "No search box found on page; navigated only."
                return result
            ctrl.page.fill(sel, search)
            ctrl.page.keyboard.press("Enter")
            ctrl.page.wait_for_load_state("domcontentloaded")
            result["search"] = search
            result["final_url"] = ctrl.page.url
        return result
    except Exception as exc:  # noqa: BLE001 — must never crash
        return {"success": False, "error": str(exc)}


def search_youtube(query: str) -> dict:
    """Open YouTube, type *query* in the search box, and press Enter."""
    try:
        ctrl = BrowserController.get_instance()
        ctrl.ensure_started()
        ctrl.goto("https://www.youtube.com")
        # YouTube's search input id is historically `search`.
        try:
            ctrl.page.wait_for_selector("input#search", timeout=8000)
            ctrl.page.fill("input#search", query)
        except PlaywrightError:
            # Fallback to generic search-box detection.
            sel = ctrl.click_search_box()
            if sel is None:
                return {
                    "success": False,
                    "error": "YouTube search box not found",
                    "url": ctrl.page.url,
                }
            ctrl.page.fill(sel, query)
        ctrl.page.keyboard.press("Enter")
        # Wait for results page to settle.
        try:
            ctrl.page.wait_for_load_state("domcontentloaded", timeout=8000)
        except PlaywrightError:
            pass
        time.sleep(0.5)
        return {"success": True, "query": query, "url": ctrl.page.url}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def search_web(query: str) -> dict:
    """Search Google for *query* and return the top organic results."""
    try:
        ctrl = BrowserController.get_instance()
        ctrl.ensure_started()
        url = f"https://www.google.com/search?q={query.replace(' ', '+')}"
        ctrl.goto(url)
        # Collect result titles + hrefs from the results page.
        results: list[dict] = []
        try:
            # Accept the consent dialog if present (EU Google).
            for btn_text in ("Accept all", "I agree", "Agree", "Alle akzeptieren"):
                try:
                    btn = ctrl.page.get_by_role("button", name=btn_text, exact=False)
                    if btn.count() > 0:
                        btn.first.click()
                        break
                except Exception:
                    continue
        except Exception:
            pass
        try:
            items = ctrl.page.eval_on_selector_all(
                "div.g",
                """els => els.map(e => {
                    const a = e.querySelector('a[href]');
                    const t = e.querySelector('h3');
                    return a ? {title: t ? t.innerText : '', url: a.href} : null;
                }).filter(Boolean)""",
            )
            results = items[:8] if items else []
        except PlaywrightError:
            pass
        return {
            "success": True,
            "query": query,
            "url": ctrl.page.url,
            "results": results,
        }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def navigate_to(url: str) -> dict:
    """Navigate the current browser tab to *url*."""
    try:
        ctrl = BrowserController.get_instance()
        ctrl.ensure_started()
        ctrl.goto(url)
        return {"success": True, "url": ctrl.page.url}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def close_browser() -> dict:
    """Close the persistent browser instance."""
    try:
        ctrl = BrowserController.get_instance()
        ctrl.close()
        return {"success": True, "message": "Browser closed."}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}


def take_browser_screenshot() -> dict:
    """Take a screenshot of the current browser page.

    The PNG bytes are returned in the ``png`` field (base64-encoded) so the
    caller can decide whether to save or transmit it.  A timestamped filename
    is also included for convenience.
    """
    try:
        ctrl = BrowserController.get_instance()
        ctrl.ensure_started()
        ts = time.strftime("%Y%m%d-%H%M%S")
        path = os.path.join(os.getcwd(), f"browser_screenshot_{ts}.png")
        ctrl.page.screenshot(path=path)
        import base64

        with open(path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode("ascii")
        return {
            "success": True,
            "path": path,
            "png_base64": b64,
            "url": ctrl.page.url,
        }
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": str(exc)}