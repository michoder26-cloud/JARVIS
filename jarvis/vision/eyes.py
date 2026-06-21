"""JARVIS's eyes — captures the screen and sends it to a multimodal AI.

This module gives JARVIS computer-vision capabilities by pairing a
pyautogui screenshot with a multimodal LLM (``minimax-m3`` on Ollama
Cloud's OpenAI-compatible endpoint). The AI can then:

* Describe what is currently visible on screen.
* Locate a specific UI element and return its pixel coordinates.
* Verify that a previously-requested action actually happened.

All public methods return plain dicts and **never raise** — they degrade
gracefully on headless Linux (no ``DISPLAY``), API failures, or JSON
parse errors.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Defaults
# --------------------------------------------------------------------------- #
DEFAULT_MODEL = os.environ.get("JARVIS_VISION_MODEL", "minimax-m3")
DEFAULT_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "https://ollama.com/v1")

# Reuse a screenshot if it was captured within this many seconds.
CACHE_TTL_SECONDS = 1.0

# Downscale large screenshots so the longer side is at most this many
# pixels before sending to the API (reduces tokens / cost / latency).
MAX_IMAGE_WIDTH = 1280

# Request timeouts (seconds).
TIMEOUT_DESCRIBE = 60
TIMEOUT_FIND = 45
TIMEOUT_VERIFY = 45


# --------------------------------------------------------------------------- #
# Lazy pyautogui import (mirrors jarvis/actions/desktop.py)
# --------------------------------------------------------------------------- #
_pyautogui = None
_pyautogui_error: Optional[str] = None


def _get_pyautogui():
    """Return the pyautogui module, or ``None`` if no display is available."""
    global _pyautogui, _pyautogui_error
    if _pyautogui is not None:
        return _pyautogui
    if _pyautogui_error is not None:
        # Already failed once — don't retry every call.
        return None
    try:
        import pyautogui  # type: ignore

        pyautogui.PAUSE = 0.1
        pyautogui.FAILSAFE = True
        _pyautogui = pyautogui
        return _pyautogui
    except Exception as exc:  # noqa: BLE001 — headless, missing X, etc.
        _pyautogui_error = str(exc)
        logger.warning("pyautogui unavailable: %s", exc)
        return None


def _no_display_error() -> Dict[str, Any]:
    return {
        "success": False,
        "error": (
            "No display available — cannot capture the screen. "
            f"Underlying error: {_pyautogui_error}"
        ),
    }


# --------------------------------------------------------------------------- #
# ScreenVision
# --------------------------------------------------------------------------- #
class ScreenVision:
    """JARVIS's eyes — captures screen and sends to multimodal AI for understanding.

    Parameters
    ----------
    model : str
        Multimodal model name (default ``"minimax-m3"``).
    base_url : str, optional
        OpenAI-compatible API base URL. Defaults to Ollama Cloud
        (``https://ollama.com/v1``).
    api_key : str, optional
        API key. If not passed, reads from ``OLLAMA_API_KEY`` env var.

    Examples
    --------
    >>> eyes = ScreenVision()
    >>> eyes.describe_screen()["description"]
    'A web browser showing the YouTube homepage...'
    >>> eyes.find_element("the search box")
    {'success': True, 'found': True, 'x': 400, 'y': 120, ...}
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
    ) -> None:
        self.model = model
        self.base_url = base_url or DEFAULT_BASE_URL
        self._api_key = api_key or os.environ.get("OLLAMA_API_KEY")
        self._client = None  # lazy OpenAI client

        # Screenshot cache: (base64_str, capture_time, width, height).
        self._cached_b64: Optional[str] = None
        self._cached_time: float = 0.0
        self._cached_size: Optional[tuple] = None

    # ------------------------------------------------------------------ #
    # OpenAI client
    # ------------------------------------------------------------------ #
    def _ensure_client(self):
        """Lazily create and cache the OpenAI client."""
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "openai library is not installed. Run `pip install openai`."
            ) from exc

        self._client = OpenAI(
            base_url=self.base_url,
            api_key=self._api_key or "ollama",
            timeout=TIMEOUT_DESCRIBE,
        )
        logger.debug(
            "ScreenVision OpenAI client ready: base_url=%s model=%s",
            self.base_url, self.model,
        )
        return self._client

    # ------------------------------------------------------------------ #
    # Screenshot capture
    # ------------------------------------------------------------------ #
    def capture_screen(self) -> bytes:
        """Take a screenshot and return it as PNG bytes.

        On a headless machine (no ``DISPLAY``), returns ``b""`` — callers
        should check via :meth:`_capture_png_bytes` for the error dict.
        """
        result = self._capture_png_bytes()
        if isinstance(result, dict):
            # Error dict — headless / pyautogui failure.
            return b""
        return result

    def capture_screen_base64(self, force_refresh: bool = False) -> str:
        """Take a screenshot and return it as a base64 string for API calls.

        Uses a short-lived cache (``CACHE_TTL_SECONDS``) so that rapid
        successive calls (e.g. ``describe_screen`` then ``find_element``)
        reuse the same frame instead of double-capturing.
        """
        now = time.monotonic()
        if (
            not force_refresh
            and self._cached_b64 is not None
            and (now - self._cached_time) < CACHE_TTL_SECONDS
        ):
            return self._cached_b64

        png = self._capture_png_bytes()
        if isinstance(png, dict):
            # Capture failed — propagate the error by stashing it so the
            # caller can retrieve it via _last_capture_error.
            self._last_capture_error = png
            return ""

        b64 = base64.b64encode(png).decode("ascii")
        self._cached_b64 = b64
        self._cached_time = now
        self._last_capture_error = None
        return b64

    def _capture_png_bytes(self):
        """Internal: returns PNG ``bytes`` on success, or an error ``dict``.

        Downsamples large images to ``MAX_IMAGE_WIDTH`` wide (preserving
        aspect ratio) to reduce API cost.
        """
        ag = _get_pyautogui()
        if ag is None:
            return _no_display_error()
        try:
            img = ag.screenshot()  # PIL.Image
        except Exception as exc:  # noqa: BLE001
            logger.error("Screenshot failed: %s", exc)
            return {"success": False, "error": f"Screenshot failed: {exc}"}

        # Record original size for coordinate scaling reference.
        orig_w, orig_h = img.size
        self._cached_size = (orig_w, orig_h)

        # Downscale if needed.
        if orig_w > MAX_IMAGE_WIDTH:
            scale = MAX_IMAGE_WIDTH / float(orig_w)
            new_w = MAX_IMAGE_WIDTH
            new_h = max(1, int(orig_h * scale))
            try:
                img = img.resize((new_w, new_h))  # PIL uses nearest by default
            except Exception as exc:  # noqa: BLE001
                logger.warning("Image resize failed, using original: %s", exc)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def screen_size(self) -> Dict[str, Any]:
        """Return the original screen dimensions captured in the last frame."""
        if self._cached_size:
            w, h = self._cached_size
            return {"success": True, "width": w, "height": h}
        ag = _get_pyautogui()
        if ag is None:
            return _no_display_error()
        try:
            w, h = ag.size()
            return {"success": True, "width": w, "height": h}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}

    def invalidate_cache(self) -> None:
        """Force the next capture call to take a fresh screenshot."""
        self._cached_b64 = None
        self._cached_time = 0.0

    # ------------------------------------------------------------------ #
    # API helpers
    # ------------------------------------------------------------------ #
    def _vision_call(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 500,
        force_refresh: bool = False,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Send the current screenshot + prompt to the multimodal model.

        Returns ``{"success": True, "text": "..."}`` or an error dict.
        """
        b64 = self.capture_screen_base64(force_refresh=force_refresh)
        if not b64:
            err = getattr(self, "_last_capture_error", None) or _no_display_error()
            return err

        try:
            client = self._ensure_client()
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"API client init failed: {exc}"}

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{b64}",
                    },
                },
            ],
        })

        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if timeout is not None:
            kwargs["timeout"] = timeout

        try:
            response = client.chat.completions.create(**kwargs)
            text = ""
            if response.choices:
                msg = response.choices[0].message
                text = getattr(msg, "content", "") or ""
            return {"success": True, "text": text}
        except Exception as exc:  # noqa: BLE001
            logger.error("Vision API call failed: %s", exc)
            return {"success": False, "error": f"Vision API call failed: {exc}"}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def describe_screen(
        self,
        question: str = (
            "What do you see on this screen? Describe all visible elements, "
            "buttons, text fields, and their approximate locations."
        ),
    ) -> Dict[str, Any]:
        """Send a screenshot to the multimodal AI and get a description.

        Returns
        -------
        dict
            ``{"success": True, "description": str, "elements": list}``
            on success, or ``{"success": False, "error": str}`` on failure.
        """
        result = self._vision_call(
            prompt=question,
            system=(
                "You are JARVIS, an AI assistant with computer vision. "
                "Describe what you see on the user's screen clearly and "
                "concisely. Mention visible applications, windows, buttons, "
                "text fields, menus, and their approximate screen positions "
                "(top-left, center, bottom-right, etc.)."
            ),
            max_tokens=600,
            timeout=TIMEOUT_DESCRIBE,
        )
        if not result.get("success"):
            return result

        text = result.get("text", "")
        # Best-effort element extraction: split on common delimiters.
        elements = self._extract_elements(text)
        return {
            "success": True,
            "description": text,
            "elements": elements,
        }

    def find_element(self, description: str) -> Dict[str, Any]:
        """Ask the AI to find a specific element on screen and return coordinates.

        Example
        -------
        >>> eyes.find_element("the YouTube search box")
        {'success': True, 'found': True, 'x': 400, 'y': 120,
         'description': 'A search input field at the top of the page.'}

        Returns
        -------
        dict
            ``{"success": True, "found": bool, "x": int, "y": int,
            "description": str}`` or ``{"success": False, "error": str}``.
        """
        if not description or not description.strip():
            return {"success": False, "error": "No element description provided."}

        # Get original screen size to tell the AI the coordinate space.
        size = self.screen_size()
        width = size.get("width", 1920) if size.get("success") else 1920
        height = size.get("height", 1080) if size.get("success") else 1080

        prompt = (
            f"Look at this screenshot and find: \"{description}\".\n\n"
            f"The screen resolution is {width}x{height} pixels. "
            f"Coordinates start at (0,0) in the top-left corner.\n\n"
            f"Respond with ONLY a JSON object, no other text, in this exact format:\n"
            f'{{"found": true, "x": <int>, "y": <int>, '
            f'"description": "<brief description of what you found>"}}\n\n'
            f"If you cannot find the element, respond with:\n"
            f'{{"found": false, "description": "<why not found>"}}'
        )

        system = (
            "You are a precise UI element locator. You analyze screenshots "
            "and return the pixel coordinates of requested elements. "
            "Always respond with valid JSON only — no markdown, no "
            "explanation outside the JSON."
        )

        result = self._vision_call(
            prompt=prompt,
            system=system,
            max_tokens=200,
            timeout=TIMEOUT_FIND,
        )
        if not result.get("success"):
            return result

        text = result.get("text", "").strip()
        parsed = self._parse_json_loose(text)
        if parsed is None:
            return {
                "success": True,
                "found": False,
                "description": text or "No response from AI.",
                "raw": text,
            }

        found = bool(parsed.get("found", False))
        out: Dict[str, Any] = {
            "success": True,
            "found": found,
            "description": parsed.get("description", ""),
        }
        if found:
            try:
                out["x"] = int(parsed.get("x", 0))
                out["y"] = int(parsed.get("y", 0))
            except (TypeError, ValueError):
                out["found"] = False
                out["error"] = "AI returned non-numeric coordinates."
        return out

    def verify_action(self, description: str) -> Dict[str, Any]:
        """Take a fresh screenshot and verify a described state was achieved.

        Example
        -------
        >>> eyes.verify_action("YouTube is now open")
        {'success': True, 'verified': True,
         'description': 'Yes, the YouTube homepage is visible.'}

        Returns
        -------
        dict
            ``{"success": True, "verified": bool, "description": str}``.
        """
        if not description or not description.strip():
            return {"success": False, "error": "No verification description provided."}

        prompt = (
            f"Look at this screenshot and verify whether the following is true:\n"
            f"\"{description}\"\n\n"
            f"Respond with ONLY a JSON object, no other text, in this format:\n"
            f'{{"verified": true, "description": "<what you actually see>"}}\n'
            f"or\n"
            f'{{"verified": false, "description": "<what you see instead>"}}'
        )

        system = (
            "You are a verification assistant. You compare screenshots "
            "against expected outcomes and report whether the expected "
            "state is visible. Always respond with valid JSON only."
        )

        # Force a fresh capture — the action may have just changed the screen.
        result = self._vision_call(
            prompt=prompt,
            system=system,
            max_tokens=200,
            force_refresh=True,
            timeout=TIMEOUT_VERIFY,
        )
        if not result.get("success"):
            return result

        text = result.get("text", "").strip()
        parsed = self._parse_json_loose(text)
        if parsed is None:
            # Fall back to heuristic: if the description text is present
            # and doesn't contain a clear "not", assume unverified.
            return {
                "success": True,
                "verified": False,
                "description": text or "No response from AI.",
                "raw": text,
            }

        return {
            "success": True,
            "verified": bool(parsed.get("verified", False)),
            "description": parsed.get("description", ""),
        }

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_json_loose(text: str) -> Optional[Dict[str, Any]]:
        """Parse JSON from a model response that may include stray text.

        Tries, in order:
        1. Direct ``json.loads``.
        2. First ``{...}`` substring via regex.
        3. Returns ``None`` if nothing parses.
        """
        if not text:
            return None
        # Strip markdown code fences if present.
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            pass
        # Extract the first {...} block.
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except (json.JSONDecodeError, TypeError):
                return None
        return None

    @staticmethod
    def _extract_elements(text: str) -> list:
        """Best-effort extraction of element mentions from a description.

        Splits the description into bullet/line items when possible. This
        is heuristic — the multimodal model's raw text is the source of
        truth and is always returned in ``description``.
        """
        if not text:
            return []
        # Split on common bullet markers and newlines.
        parts = re.split(r"(?:^|\n)\s*(?:[-*•]|\d+\.)\s+", text)
        elements = [p.strip() for p in parts if p.strip()]
        # If we only got one element (no bullets), split on newlines.
        if len(elements) <= 1:
            elements = [ln.strip() for ln in text.splitlines() if ln.strip()]
        # Trim to a reasonable number.
        return elements[:20]

    def __repr__(self) -> str:
        return f"<ScreenVision model={self.model!r} base_url={self.base_url!r}>"