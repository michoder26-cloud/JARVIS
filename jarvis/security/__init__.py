"""JARVIS Security — prompt-injection detection and input sanitisation.

This package currently provides :class:`~jarvis.security.scanner.InjectionScanner`,
a lightweight regex-based scanner that flags potential prompt-injection
attempts in user input before it is sent to the LLM.
"""

from .scanner import InjectionScanner

__all__ = ["InjectionScanner"]