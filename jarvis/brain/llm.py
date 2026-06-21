"""LLM client for JARVIS — OpenAI-compatible (works with Ollama Cloud).

Uses the ``openai`` Python library to connect to Ollama Cloud's
OpenAI-compatible API endpoint (``/v1/chat/completions``).

Supports:
* Plain chat (text in → text out)
* Tool calling (function calling — LLM decides which tool to call)
* Streaming responses (for real-time TTS feel)
* Conversation history (via :class:`ConversationHistory`)
* Graceful error handling (API down, timeouts, unsupported tools)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# Default configuration — can be overridden via env or constructor.
DEFAULT_MODEL = os.environ.get("JARVIS_MODEL", "glm-5.2")
DEFAULT_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "https://ollama.com/v1")

# Timeouts (seconds).
TIMEOUT_CHAT = 30
TIMEOUT_TOOLS = 60
TIMEOUT_STREAM = 120

# Message type — dict.
Message = Dict[str, Any]
ToolSchema = Dict[str, Any]

# Callback: on_tool_call(name: str, args: dict) -> dict (result)
ToolCallCallback = Callable[[str, Dict[str, Any]], Dict[str, Any]]


class LLMClient:
    """OpenAI-compatible chat client with function-calling support.

    Parameters
    ----------
    model : str
        Model name (default ``"glm-5.2"``).
    base_url : str, optional
        API base URL. Defaults to ``https://ollama.com/v1`` (Ollama Cloud).
    api_key : str, optional
        API key. If not passed, reads from ``OLLAMA_API_KEY`` env var.
    timeout : int
        Default request timeout in seconds.

    Examples
    --------
    >>> client = LLMClient()  # Ollama Cloud
    >>> resp = client.chat([{"role": "user", "content": "Hello"}])
    >>> print(resp["content"])
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = TIMEOUT_CHAT,
    ) -> None:
        self.model = model
        self.base_url = base_url or DEFAULT_BASE_URL
        self.timeout = timeout
        self._api_key = api_key or os.environ.get("OLLAMA_API_KEY")
        self._client = None  # lazy

    # ------------------------------------------------------------------ #
    # Client management
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
            api_key=self._api_key or "ollama",  # Ollama Cloud needs a key
            timeout=self.timeout,
        )
        logger.debug("OpenAI client ready: base_url=%s model=%s", self.base_url, self.model)
        return self._client

    # ------------------------------------------------------------------ #
    # Plain chat
    # ------------------------------------------------------------------ #
    def chat(
        self,
        messages: Sequence[Message],
        tools: Optional[Sequence[ToolSchema]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send messages to the LLM and return the response.

        Returns
        -------
        dict
            Response with keys: ``content`` (str), ``tool_calls`` (list),
            ``role`` (str), ``raw`` (original response object).
        """
        try:
            client = self._ensure_client()
            kwargs: Dict[str, Any] = {
                "model": self.model,
                "messages": list(messages),
            }
            if tools:
                kwargs["tools"] = list(tools)
                kwargs["tool_choice"] = "auto"
            if options:
                # Map common options to OpenAI API params.
                if "temperature" in options:
                    kwargs["temperature"] = options["temperature"]
                if "max_tokens" in options:
                    kwargs["max_tokens"] = options["max_tokens"]

            response = client.chat.completions.create(**kwargs)
            return self._parse_response(response)

        except Exception as exc:
            logger.error("LLM chat failed: %s", exc)
            return {
                "content": self._error_message(exc),
                "tool_calls": [],
                "role": "assistant",
                "raw": None,
                "error": str(exc),
            }

    # ------------------------------------------------------------------ #
    # Chat with tools (full function-calling loop)
    # ------------------------------------------------------------------ #
    def chat_with_tools(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolSchema],
        on_tool_call: ToolCallCallback,
        max_iterations: int = 5,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send messages with tools and handle the tool-call loop.

        The flow:
        1. Send messages + tools to the LLM.
        2. If the LLM returns ``tool_calls``, invoke ``on_tool_call(name, args)``
           for each, collect results, append them as tool messages, and
           send back to the LLM.
        3. Repeat until the LLM responds with text only (no tool calls) or
           ``max_iterations`` is reached.
        """
        working_messages = list(messages)
        all_tool_results: List[Dict[str, Any]] = []

        for iteration in range(max_iterations):
            try:
                client = self._ensure_client()
                kwargs: Dict[str, Any] = {
                    "model": self.model,
                    "messages": working_messages,
                    "tools": list(tools),
                    "tool_choice": "auto",
                }
                if options:
                    if "temperature" in options:
                        kwargs["temperature"] = options["temperature"]
                    if "max_tokens" in options:
                        kwargs["max_tokens"] = options["max_tokens"]

                response = client.chat.completions.create(**kwargs)
            except Exception as exc:
                logger.error("LLM chat_with_tools failed on iteration %d: %s",
                             iteration, exc)
                return {
                    "content": self._error_message(exc),
                    "tool_calls": [],
                    "role": "assistant",
                    "raw": None,
                    "error": str(exc),
                    "tool_results": all_tool_results,
                }

            parsed = self._parse_response(response)
            tool_calls = parsed.get("tool_calls", [])

            # No tool calls → we're done, LLM gave a final text answer.
            if not tool_calls:
                parsed["tool_results"] = all_tool_results
                return parsed

            # --- Dispatch each tool call ---
            # Build the assistant message with tool calls for the history.
            assistant_msg: Dict[str, Any] = {
                "role": "assistant",
                "content": parsed.get("content", "") or "",
            }
            # Re-serialize tool calls for OpenAI format.
            assistant_msg["tool_calls"] = [
                {
                    "id": tc.get("id", f"call_{iteration}_{i}"),
                    "type": "function",
                    "function": {
                        "name": tc["function"]["name"],
                        "arguments": json.dumps(
                            tc["function"]["arguments"],
                            ensure_ascii=False,
                        ),
                    },
                }
                for i, tc in enumerate(tool_calls)
            ]
            working_messages.append(assistant_msg)

            for tc in tool_calls:
                name, arguments = self._extract_tool_call(tc)
                logger.info("Tool call: %s(%s)", name, arguments)

                # Invoke the handler.
                try:
                    result = on_tool_call(name, arguments)
                except Exception as exc:
                    logger.exception("on_tool_call callback raised for %r", name)
                    result = {"success": False, "result": None, "error": str(exc)}

                all_tool_results.append({
                    "name": name,
                    "arguments": arguments,
                    "result": result,
                })

                # Feed the result back to the LLM as a tool message.
                result_str = json.dumps(result, ensure_ascii=False, default=str)
                tool_msg_id = tc.get("id", f"call_{iteration}_{name}")
                working_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_msg_id,
                    "content": result_str,
                })

        # Exhausted iterations — return last best effort.
        logger.warning("chat_with_tools exhausted %d iterations", max_iterations)
        return {
            "content": "I've completed the requested actions, sir.",
            "tool_calls": [],
            "role": "assistant",
            "raw": None,
            "tool_results": all_tool_results,
        }

    # ------------------------------------------------------------------ #
    # Streaming
    # ------------------------------------------------------------------ #
    def chat_stream(
        self,
        messages: Sequence[Message],
        tools: Optional[Sequence[ToolSchema]] = None,
        options: Optional[Dict[str, Any]] = None,
    ):
        """Stream the chat response, yielding content chunks as they arrive.

        Yields
        ------
        dict
            Each chunk: ``{"content": str, "done": bool}``.
        """
        try:
            client = self._ensure_client()
            kwargs: Dict[str, Any] = {
                "model": self.model,
                "messages": list(messages),
                "stream": True,
            }
            if tools:
                kwargs["tools"] = list(tools)
                kwargs["tool_choice"] = "auto"
            if options:
                if "temperature" in options:
                    kwargs["temperature"] = options["temperature"]
                if "max_tokens" in options:
                    kwargs["max_tokens"] = options["max_tokens"]

            for chunk in client.chat.completions.create(**kwargs):
                if chunk.choices and chunk.choices[0].delta.content:
                    yield {"content": chunk.choices[0].delta.content, "done": False}
                if chunk.choices and chunk.choices[0].finish_reason:
                    yield {"content": "", "done": True}

        except Exception as exc:
            logger.error("LLM stream failed: %s", exc)
            yield {"content": self._error_message(exc), "done": True, "error": str(exc)}

    # ------------------------------------------------------------------ #
    # Convenience: chat with a ConversationHistory object
    # ------------------------------------------------------------------ #
    def chat_history(
        self,
        history,
        tools: Optional[Sequence[ToolSchema]] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Chat using a :class:`ConversationHistory` object."""
        messages = history.get_messages()
        response = self.chat(messages, tools=tools, options=options)
        if response.get("content"):
            history.add_assistant(response["content"])
        return response

    def chat_history_with_tools(
        self,
        history,
        tools: Sequence[ToolSchema],
        on_tool_call: ToolCallCallback,
        max_iterations: int = 5,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Like :meth:`chat_with_tools` but uses a ConversationHistory object."""
        messages = history.get_messages()
        response = self.chat_with_tools(
            messages, tools, on_tool_call,
            max_iterations=max_iterations, options=options,
        )
        if response.get("content"):
            history.add_assistant(response["content"])
        return response

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_response(response: Any) -> Dict[str, Any]:
        """Normalize an OpenAI ChatCompletion response into a plain dict."""
        try:
            choice = response.choices[0]
            msg = choice.message
            content = msg.content or ""
            role = getattr(msg, "role", "assistant") or "assistant"

            # Extract tool calls.
            tool_calls = []
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.function.name
                    try:
                        arguments = json.loads(tc.function.arguments)
                    except (json.JSONDecodeError, TypeError):
                        arguments = {}
                    tool_calls.append({
                        "id": tc.id,
                        "function": {"name": name, "arguments": arguments},
                    })

            return {
                "content": content,
                "tool_calls": tool_calls,
                "role": role,
                "raw": response,
            }
        except Exception as exc:
            logger.error("Failed to parse LLM response: %s", exc)
            return {
                "content": "",
                "tool_calls": [],
                "role": "assistant",
                "raw": response,
                "error": str(exc),
            }

    @staticmethod
    def _extract_tool_call(tc: Any) -> tuple:
        """Extract (name, arguments_dict) from a tool call object."""
        if isinstance(tc, dict):
            func = tc.get("function", {})
            name = func.get("name", "")
            args = func.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            return name, args

        # Pydantic-style object.
        func = getattr(tc, "function", None)
        if func is None:
            return "", {}
        name = getattr(func, "name", "")
        args = getattr(func, "arguments", "{}")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        return name, args

    @staticmethod
    def _error_message(exc: Exception) -> str:
        """Generate a user-friendly spoken error message."""
        msg = str(exc).lower()
        if "timeout" in msg or "timed out" in msg:
            return "ขออภัย ใช้เวลานานเกินไปครับ"  # Sorry, that took too long
        if "connection" in msg or "network" in msg or "unreachable" in msg:
            return "ไม่สามารถเชื่อมต่อกับ AI ได้ครับ"  # Can't connect to AI
        return "ขออภัย เกิดข้อผิดพลาดครับ"  # Sorry, an error occurred

    def __repr__(self) -> str:
        return f"<LLMClient model={self.model!r} base_url={self.base_url!r}>"