"""JARVIS Vision-Action Loop — the autonomous "see → think → act → verify" cycle.

This module gives JARVIS the ability to autonomously execute complex
multi-step computer tasks by combining screen vision with computer
control:

1.  The user gives a command (e.g. "open YouTube and search for Thai music").
2.  JARVIS takes a screenshot to *see* the current state of the screen.
3.  The screenshot + command are sent to a multimodal LLM (minimax-m3).
4.  The LLM decides what *single* action to take next and returns it as a
    JSON object (``{"action": "...", "args": {...}, "reasoning": "..."}``).
5.  JARVIS executes that action via the smart-actions registry.
6.  JARVIS takes another screenshot to *verify* the outcome.
7.  The LLM inspects the new screenshot and either says the task is
    complete (``{"action": "done", ...}``) or proposes the next action.
8.  Steps 4–7 repeat until the LLM reports ``done`` or ``max_steps`` is
    reached (default 10) — preventing infinite loops.
9.  JARVIS speaks the final result.

The loop is intentionally defensive: every vision call, action
execution, and JSON parse is wrapped so that a failure in one step
degrades gracefully and never crashes JARVIS.  The :meth:`stop` method
lets the user (or another thread) interrupt a running loop.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

__all__ = ["VisionActionLoop"]


# --------------------------------------------------------------------------- #
# Per-step timeout (seconds).  Each iteration of the loop must complete
# (vision call + action) within this budget, otherwise we abort the step.
# --------------------------------------------------------------------------- #
STEP_TIMEOUT_SECONDS = 30

# Short pause after an action so the UI has time to settle before the
# next screenshot.
_ACTION_SETTLE_SECONDS = 1.5


# --------------------------------------------------------------------------- #
# The set of actions the loop is allowed to execute.  These map directly
# onto handlers registered in ``jarvis.actions.registry.ACTION_REGISTRY``
# (and therefore ``jarvis.actions.smart``).
# --------------------------------------------------------------------------- #
_ALLOWED_ACTIONS = {
    "smart_click",
    "smart_type",
    "open_website",
    "scroll",
    "press_key",
    "switch_window",
    "verify_screen",
}


class VisionActionLoop:
    """Autonomous vision-action loop for complex computer tasks.

    Flow
    ----
    1. User gives a command (e.g., "open YouTube and search for Thai music")
    2. JARVIS takes a screenshot to see the current state
    3. Sends screenshot + command to AI
    4. AI decides what action to take (click, type, etc.)
    5. JARVIS executes the action
    6. JARVIS takes another screenshot to verify
    7. AI checks if the task is complete
    8. If not, repeat from step 4
    9. If yes, speak the result

    This is like having a human sit at the computer and follow
    instructions.

    Parameters
    ----------
    llm_client
        A :class:`jarvis.brain.llm.LLMClient` used for *text* reasoning
        (e.g. summarising the outcome).  May be ``None`` if only vision
        is desired, but a final spoken summary won't be generated.
    speaker
        A :class:`jarvis.tts.speaker.TTSSpeaker` used to narrate progress
        and the final result.  May be ``None`` to mute the loop.
    screen_vision
        A :class:`jarvis.vision.eyes.ScreenVision` instance used to
        capture screenshots and talk to the multimodal model.  If
        ``None`` the loop tries to create one lazily.
    max_steps
        Hard cap on the number of iterations to prevent infinite loops
        (default 10).
    """

    def __init__(
        self,
        llm_client: Optional[Any] = None,
        speaker: Optional[Any] = None,
        screen_vision: Optional[Any] = None,
        max_steps: int = 10,
    ) -> None:
        self.llm = llm_client  # LLMClient for text reasoning
        self.speaker = speaker  # TTSSpeaker for speaking
        self.eyes = screen_vision  # ScreenVision for screenshots
        self.max_steps = max_steps
        self._should_stop = False

        # A transcript of every step — handy for debugging / logging.
        self._trace: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def execute_task(self, command: str) -> dict:
        """Execute a complex multi-step task using vision and actions.

        Parameters
        ----------
        command
            The full task description in the user's language, e.g.
            ``"open YouTube and search for Thai music"``.

        Returns
        -------
        dict
            ``{"success": bool, "steps_taken": int, "description": str,
            "trace": list}``
        """
        if not command or not command.strip():
            return {
                "success": False,
                "steps_taken": 0,
                "description": "No command provided.",
                "trace": [],
            }

        self._should_stop = False
        self._trace = []

        # Lazily obtain a ScreenVision if the caller didn't provide one.
        eyes = self._get_eyes()
        if eyes is None:
            desc = (
                "I cannot see the screen — vision is unavailable on this "
                "system, so I cannot complete the task."
            )
            self._speak(desc)
            return {
                "success": False,
                "steps_taken": 0,
                "description": desc,
                "trace": self._trace,
            }

        # Announce the task.
        opening = f"Starting task: {command}"
        logger.info("VisionActionLoop: %s", opening)
        self._speak("เริ่มทำงานตามคำสั่งครับ")  # "Starting the task"

        steps_taken = 0
        last_action_desc = "the initial screen"
        final_description = ""

        for step in range(1, self.max_steps + 1):
            if self._should_stop:
                logger.info("VisionActionLoop stopped by user at step %d", step - 1)
                final_description = "Task stopped by the user."
                break

            steps_taken = step
            step_start = time.monotonic()
            logger.info("VisionActionLoop step %d/%d", step, self.max_steps)

            # 1. Capture the current screen (fresh frame each step).
            eyes.invalidate_cache()
            screenshot_ok = eyes.capture_screen_base64(force_refresh=True)
            if not screenshot_ok:
                err = getattr(eyes, "_last_capture_error", None) or {
                    "error": "Screenshot capture failed."
                }
                final_description = (
                    "I couldn't capture the screen. "
                    f"Reason: {err.get('error', 'unknown')}"
                )
                self._trace.append({
                    "step": step, "phase": "capture", "error": final_description,
                })
                break

            # 2. Ask the vision model what to do next.
            vision_result = self._ask_vision(eyes, command, last_action_desc)
            if not vision_result.get("success"):
                final_description = (
                    "I couldn't analyse the screen. "
                    f"Reason: {vision_result.get('error', 'unknown')}"
                )
                self._trace.append({
                    "step": step, "phase": "vision", "error": final_description,
                })
                break

            action_obj = vision_result.get("action") or {}
            action_name = (action_obj.get("action") or "").strip().lower()
            action_args = action_obj.get("args") or {}
            reasoning = action_obj.get("reasoning") or ""

            logger.info(
                "VisionActionLoop step %d → action=%s args=%s reasoning=%s",
                step, action_name, action_args, reasoning,
            )
            self._trace.append({
                "step": step,
                "phase": "decide",
                "action": action_name,
                "args": action_args,
                "reasoning": reasoning,
            })

            # 3. Is the task complete?
            if action_name == "done":
                final_description = reasoning or "Task complete."
                logger.info("VisionActionLoop: DONE — %s", final_description)
                break

            # 4. Execute the action (with a per-step timeout).
            action_result = self._execute_action_timed(
                action_name, action_args, step_start,
            )
            self._trace.append({
                "step": step,
                "phase": "act",
                "action": action_name,
                "result": action_result,
            })

            if not action_result.get("success"):
                # Log but continue — the next vision call may route around
                # the failure (e.g. retry, try something else).
                logger.warning(
                    "VisionActionLoop step %d action %s failed: %s",
                    step, action_name, action_result.get("error"),
                )

            # 5. Brief settle so the UI updates before the next screenshot.
            time.sleep(_ACTION_SETTLE_SECONDS)

            # Record what we just did for the next vision prompt.
            last_action_desc = self._describe_last_action(
                action_name, action_args, action_result,
            )

            # Check the per-step timeout — if we've already blown past it,
            # abort the loop rather than dragging on forever.
            elapsed = time.monotonic() - step_start
            if elapsed > STEP_TIMEOUT_SECONDS * 2:
                logger.warning(
                    "VisionActionLoop step %d exceeded budget (%.1fs) — aborting",
                    step, elapsed,
                )
                final_description = (
                    "The task was taking too long; I stopped after "
                    f"{step} steps."
                )
                break

        else:
            # Loop completed without a `done` signal.
            final_description = (
                f"I couldn't fully complete the task after {self.max_steps} "
                "steps. Here is what I managed: " + last_action_desc
            )
            logger.warning(
                "VisionActionLoop exhausted %d steps without completion",
                self.max_steps,
            )

        # Speak the final outcome.
        self._speak(final_description)

        success = "couldn't" not in final_description.lower() and \
                  "stopped by the user" not in final_description.lower()
        return {
            "success": success,
            "steps_taken": steps_taken,
            "description": final_description,
            "trace": self._trace,
        }

    def stop(self) -> None:
        """Stop the loop (e.g., user says 'stop').

        Safe to call from another thread — sets a flag that the loop
        checks at the top of each iteration.
        """
        self._should_stop = True
        logger.info("VisionActionLoop.stop() requested")

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    def _get_eyes(self):
        """Return the ScreenVision instance, creating one lazily if needed."""
        if self.eyes is not None:
            return self.eyes
        try:
            from jarvis.vision.eyes import ScreenVision  # type: ignore

            self.eyes = ScreenVision()
            return self.eyes
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not initialise ScreenVision: %s", exc)
            return None

    def _speak(self, text: str) -> None:
        """Speak *text* via the TTS speaker if one is configured."""
        if not text:
            return
        print(f"[JARVIS] {text}")
        if self.speaker is not None:
            try:
                self.speaker.speak(text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("TTS speak failed: %s", exc)

    # --- Vision call --------------------------------------------------- #
    def _ask_vision(
        self,
        eyes: Any,
        command: str,
        last_action_desc: str,
    ) -> Dict[str, Any]:
        """Send a screenshot + command to the multimodal model and parse
        the JSON action it returns.

        Returns ``{"success": True, "action": {...}}`` or an error dict.
        """
        prompt = (
            f'You are controlling a computer. The user wants: "{command}".\n'
            f"Here is a screenshot of the current screen.\n"
            f"Previously I just performed: {last_action_desc}\n\n"
            "What single action should you take next? "
            "Respond with ONLY a JSON object, no other text:\n"
            '{"action": "smart_click|smart_type|open_website|scroll|'
            'press_key|switch_window|done", '
            '"args": {...}, "reasoning": "..."}\n\n'
            "If the task is complete, respond with: "
            '{"action": "done", "reasoning": "task is complete because..."}\n\n'
            "Action argument formats:\n"
            '- smart_click: {"description": "visual description of element"}\n'
            '- smart_type: {"field_description": "...", "text": "..."}\n'
            '- open_website: {"url": "youtube.com"}\n'
            '- scroll: {"direction": "up|down", "clicks": 3}\n'
            '- press_key: {"key": "enter"}\n'
            '- switch_window: {}\n'
        )
        system = (
            "You are JARVIS, an AI assistant that controls a computer by "
            "looking at screenshots and deciding the next mouse/keyboard "
            "action. Always respond with a single valid JSON object and "
            "nothing else."
        )

        try:
            result = eyes._vision_call(
                prompt=prompt,
                system=system,
                max_tokens=300,
                force_refresh=True,
                timeout=STEP_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": f"Vision call failed: {exc}"}

        if not result.get("success"):
            return result

        text = result.get("text", "").strip()
        parsed = _parse_action_json(text)
        if parsed is None:
            return {
                "success": False,
                "error": f"Could not parse action JSON from model reply: {text[:200]}",
                "raw": text,
            }
        return {"success": True, "action": parsed}

    # --- Action execution --------------------------------------------- #
    def _execute_action_timed(
        self,
        action_name: str,
        action_args: Dict[str, Any],
        step_start: float,
    ) -> Dict[str, Any]:
        """Execute a single action through the registry, with a timeout."""
        remaining = STEP_TIMEOUT_SECONDS - (time.monotonic() - step_start)
        if remaining <= 0:
            return {
                "success": False,
                "error": f"Step timeout expired before executing {action_name}",
            }

        if action_name not in _ALLOWED_ACTIONS:
            return {
                "success": False,
                "error": (
                    f"Action '{action_name}' is not allowed in the vision-"
                    f"action loop. Allowed: {sorted(_ALLOWED_ACTIONS)}"
                ),
            }

        try:
            from jarvis.actions.registry import execute_action  # lazy

            # execute_action already swallows exceptions and returns dicts.
            return execute_action(action_name, action_args)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": str(exc)}

    @staticmethod
    def _describe_last_action(
        action_name: str,
        action_args: Dict[str, Any],
        result: Dict[str, Any],
    ) -> str:
        """Produce a short human-readable description of the last action."""
        if action_name == "smart_click":
            return f"clicked on {action_args.get('description', 'an element')}"
        if action_name == "smart_type":
            return (
                f"typed '{action_args.get('text', '')}' into "
                f"{action_args.get('field_description', 'a field')}"
            )
        if action_name == "open_website":
            return f"opened website {action_args.get('url', '')}"
        if action_name == "scroll":
            return f"scrolled {action_args.get('direction', 'down')}"
        if action_name == "press_key":
            return f"pressed {action_args.get('key', 'a key')}"
        if action_name == "switch_window":
            return "switched window"
        if action_name == "verify_screen":
            return f"verified {action_args.get('description', 'the screen')}"
        return f"performed {action_name}"


# --------------------------------------------------------------------------- #
# Robust JSON parsing for model responses
# --------------------------------------------------------------------------- #
def _parse_action_json(text: str) -> Optional[Dict[str, Any]]:
    """Parse a JSON action object from a model response.

    Handles:
    * Pure JSON.
    * JSON wrapped in ```json ... ``` markdown code fences.
    * JSON surrounded by stray prose (we extract the first ``{...}``).
    * Common quote/escaping quirks.

    Returns the parsed dict or ``None`` if nothing usable was found.
    """
    if not text:
        return None

    cleaned = text.strip()

    # Strip markdown code fences.
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.strip()

    # Direct parse.
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            return obj
    except (json.JSONDecodeError, TypeError):
        pass

    # Extract the first {...} block (greedy but DOTALL so nested braces
    # are captured).
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        candidate = match.group(0)
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, TypeError):
            pass
        # Last resort: strip trailing commas which some models emit.
        fixed = re.sub(r",\s*([}\]])", r"\1", candidate)
        try:
            obj = json.loads(fixed)
            if isinstance(obj, dict):
                return obj
        except (json.JSONDecodeError, TypeError):
            pass

    return None