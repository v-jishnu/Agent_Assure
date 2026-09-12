"""
Cost Tracking for AgentAssure.

Captures model usage (input/output tokens, latency) per agent trace and
estimates USD cost using a built-in pricing table.  Cost data feeds into
both the evidence record and the CLI summary.

Design constraint: this module has NO dependency on any LLM SDK.
It is a pure accounting layer — it receives token counts from whichever
adapter or agent code chooses to report them.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# Pricing table — USD per 1 000 tokens (input rate, output rate)
# Rates are approximate and should be verified against provider pricing pages.
# ---------------------------------------------------------------------------

PRICING_TABLE: Dict[str, Dict[str, float]] = {
    # OpenAI
    "gpt-4o":                      {"input": 0.005,    "output": 0.015},
    "gpt-4o-mini":                  {"input": 0.00015,  "output": 0.0006},
    "gpt-4-turbo":                  {"input": 0.01,     "output": 0.03},
    "gpt-4":                        {"input": 0.03,     "output": 0.06},
    "gpt-3.5-turbo":                {"input": 0.0005,   "output": 0.0015},
    # Anthropic
    "claude-3-5-sonnet-20241022":   {"input": 0.003,    "output": 0.015},
    "claude-3-5-sonnet":            {"input": 0.003,    "output": 0.015},
    "claude-3-opus":                {"input": 0.015,    "output": 0.075},
    "claude-3-haiku":               {"input": 0.00025,  "output": 0.00125},
    # Google
    "gemini-1.5-pro":               {"input": 0.0035,   "output": 0.0105},
    "gemini-1.5-flash":             {"input": 0.000075, "output": 0.0003},
    "gemini-2.0-flash":             {"input": 0.0001,   "output": 0.0004},
    # Groq (fast inference)
    "llama-3.3-70b-versatile":      {"input": 0.00059,  "output": 0.00079},
    "llama-3.1-70b-versatile":      {"input": 0.00059,  "output": 0.00079},
    "llama-3.1-8b-instant":         {"input": 0.00005,  "output": 0.00008},
    "mixtral-8x7b-32768":           {"input": 0.00024,  "output": 0.00024},
    "gemma2-9b-it":                 {"input": 0.0002,   "output": 0.0002},
}


def _detect_provider(model: str) -> str:
    """Heuristically determine the provider from a model name."""
    m = model.lower()
    if m.startswith("gpt") or "openai" in m:
        return "openai"
    if m.startswith("claude") or "anthropic" in m:
        return "anthropic"
    if m.startswith("gemini") or "google" in m:
        return "google"
    if any(m.startswith(p) for p in ("llama", "mixtral", "gemma")):
        return "groq"
    return "unknown"


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """
    Estimate the USD cost of a single model call.

    Falls back to prefix matching so that versioned model names (e.g.
    "gpt-4o-2024-11-20") still hit the right entry.  Returns 0.0 if the
    model is completely unknown — we never want cost estimation to crash
    the governance runtime.
    """
    pricing = PRICING_TABLE.get(model)
    if not pricing:
        # Prefix match — try progressively shorter prefixes
        for key, price in PRICING_TABLE.items():
            if model.startswith(key):
                pricing = price
                break
    if not pricing:
        return 0.0

    return (
        input_tokens  * pricing["input"]  / 1_000
        + output_tokens * pricing["output"] / 1_000
    )


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CostRecord:
    """
    Accumulated cost for a single agent trace.

    All numeric fields are additive — multiple LLM calls within one trace
    are summed into a single record.
    """
    model: str = "unknown"
    provider: str = "unknown"
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    llm_calls: int = 0

    def to_dict(self) -> Dict:
        return {
            "model":         self.model,
            "provider":      self.provider,
            "input_tokens":  self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens":  self.total_tokens,
            "latency_ms":    round(self.latency_ms, 2),
            "cost_usd":      round(self.cost_usd, 6),
            "llm_calls":     self.llm_calls,
        }


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------

class CostTracker:
    """
    Thread-safe, per-trace cost accumulator.

    Usage (from an adapter or agent code):

        from agentassure.cost import default_cost_tracker
        default_cost_tracker.record(
            trace_id="tr_abc123",
            model="gpt-4o",
            input_tokens=512,
            output_tokens=128,
            latency_ms=340.0,
        )

    Usage (from CLI runner — read the total after the run):

        rec = default_cost_tracker.get("tr_abc123")
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._traces: Dict[str, CostRecord] = {}

    def record(
        self,
        trace_id: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: float = 0.0,
    ) -> CostRecord:
        """Add a single LLM call's usage to the running total for a trace."""
        cost = estimate_cost(model, input_tokens, output_tokens)
        provider = _detect_provider(model)

        with self._lock:
            rec = self._traces.get(trace_id)
            if rec is None:
                rec = CostRecord(
                    model=model,
                    provider=provider,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                    latency_ms=latency_ms,
                    cost_usd=cost,
                    llm_calls=1,
                )
                self._traces[trace_id] = rec
            else:
                rec.input_tokens  += input_tokens
                rec.output_tokens += output_tokens
                rec.total_tokens  += input_tokens + output_tokens
                rec.latency_ms    += latency_ms
                rec.cost_usd      += cost
                rec.llm_calls     += 1
                # Track the most-recently-seen model name
                rec.model    = model
                rec.provider = provider

        return rec

    def get(self, trace_id: str) -> Optional[CostRecord]:
        """Return the accumulated cost record for a trace, or None."""
        with self._lock:
            return self._traces.get(trace_id)

    def clear(self, trace_id: str) -> None:
        """Discard cost data for a completed trace (optional cleanup)."""
        with self._lock:
            self._traces.pop(trace_id, None)

    def all(self) -> Dict[str, CostRecord]:
        """Return a snapshot of all accumulated trace records."""
        with self._lock:
            return dict(self._traces)


# Singleton used across the process
default_cost_tracker = CostTracker()
