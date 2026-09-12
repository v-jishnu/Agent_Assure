"""
OpenAI SDK adapter for AgentAssure cost tracking.

Call instrument() once at startup (before any openai calls) to automatically
capture token usage from every chat completion call.  The data flows into
the global CostTracker keyed by the current trace_id.

This module is purely opt-in.  If you don't import it, AgentAssure works
exactly as before — no cost data is captured, no openai import happens.

Usage:
    from agentassure.adapters.openai import instrument
    instrument()   # call once at agent startup
"""

from __future__ import annotations

import time
from typing import Any

_instrumented = False


def instrument() -> None:
    """
    Monkey-patch openai.chat.completions.create to capture token usage.

    Safe to call multiple times — only patches once.
    """
    global _instrumented
    if _instrumented:
        return

    try:
        import openai
    except ImportError:
        raise ImportError(
            "openai package is required to use the OpenAI adapter.\n"
            "Install it with: pip install openai"
        )

    original_create = openai.chat.completions.create

    def _patched_create(*args: Any, **kwargs: Any) -> Any:
        t0 = time.monotonic()
        response = original_create(*args, **kwargs)
        latency_ms = (time.monotonic() - t0) * 1000

        _record_usage(response, latency_ms)
        return response

    openai.chat.completions.create = _patched_create  # type: ignore[method-assign]
    _instrumented = True


def _record_usage(response: Any, latency_ms: float) -> None:
    """Extract usage from the response object and write to CostTracker."""
    try:
        from agentassure.cost import default_cost_tracker
        from agentassure.trace import TraceContext

        usage = getattr(response, "usage", None)
        if usage is None:
            return

        model       = getattr(response, "model", "gpt-4o")
        input_tok   = getattr(usage, "prompt_tokens",     0) or 0
        output_tok  = getattr(usage, "completion_tokens", 0) or 0

        trace_id, _, _ = TraceContext.get_current()

        default_cost_tracker.record(
            trace_id=trace_id,
            model=model,
            input_tokens=input_tok,
            output_tokens=output_tok,
            latency_ms=latency_ms,
        )
    except Exception:
        # Never let adapter errors affect the agent runtime
        pass
