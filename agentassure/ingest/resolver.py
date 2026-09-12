"""
LLM Resolver interface for AgentAssure policy ingestion.

Design: the resolver sits behind a small interface so governance
runtime code never imports an LLM SDK directly.  The RAG / extraction
layer auto-detects which provider is available and falls back gracefully
to a deterministic heuristic extractor when no API key is present.

    OPENAI_API_KEY present  → OpenAIResolver
    GROQ_API_KEY present    → GroqResolver
    otherwise               → HeuristicResolver (keyword-based, no LLM)
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List


# ---------------------------------------------------------------------------
# Base interface
# ---------------------------------------------------------------------------

class BaseResolver(ABC):
    """
    A resolver turns raw document text into a list of policy candidates.
    Each candidate is a dict with at least:
      - description   (str)   human-readable description of the rule
      - action_hint   (str)   "BLOCK" | "ASK" | "ALLOW"
      - condition_hint (str)  natural-language description of the condition
      - source_excerpt (str)  the original sentence(s) this was derived from
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name shown in CLI footer."""
        ...

    @abstractmethod
    def extract(self, text: str) -> List[Dict[str, Any]]:
        """Extract policy candidates from document text."""
        ...


# ---------------------------------------------------------------------------
# Heuristic resolver (no LLM required)
# ---------------------------------------------------------------------------

# Keywords that suggest a blocking or restricting policy
_BLOCK_SIGNALS = [
    "must not", "shall not", "prohibited", "forbidden", "not permitted",
    "not allowed", "restricted", "disallowed", "cannot", "will not",
    "is prohibited", "are prohibited",
]
_ASK_SIGNALS = [
    "require approval", "requires approval", "must be approved",
    "secondary approval", "human review", "escalate", "manual review",
    "authorization required", "authorized by",
]
_AMOUNT_SIGNALS = ["exceed", "above", "greater than", "more than", "over"]
_SCORE_SIGNALS  = ["below", "less than", "under", "minimum score", "credit score"]


class HeuristicResolver(BaseResolver):
    """
    Keyword-based policy extractor.

    Works without any LLM.  Quality is lower than an LLM-backed resolver
    but it is always available and completely deterministic — useful for
    quick bootstrapping or offline environments.
    """

    @property
    def name(self) -> str:
        return "heuristic fallback"

    def extract(self, text: str) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        for line in lines:
            lower = line.lower()

            # Determine action hint
            action = "ALLOW"
            if any(sig in lower for sig in _BLOCK_SIGNALS):
                action = "BLOCK"
            elif any(sig in lower for sig in _ASK_SIGNALS):
                action = "ASK"
            else:
                continue  # line doesn't appear policy-relevant

            # Build a rough condition hint
            condition_hint = ""
            if any(sig in lower for sig in _AMOUNT_SIGNALS):
                condition_hint = "amount exceeds threshold"
            elif any(sig in lower for sig in _SCORE_SIGNALS):
                condition_hint = "score is below threshold"

            candidates.append({
                "description":     line[:200],
                "action_hint":     action,
                "condition_hint":  condition_hint,
                "source_excerpt":  line[:300],
            })

        return candidates


# ---------------------------------------------------------------------------
# OpenAI resolver
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """\
You are a governance policy extraction assistant.
Given the following document text, identify all sentences or clauses that
describe a rule, restriction, limit, or approval requirement that an AI
agent should enforce before taking an action.

For each policy clause you find, output a JSON object with:
  - "description": concise one-line description of the rule
  - "action_hint": one of "BLOCK", "ASK", or "ALLOW"
    (BLOCK = prevent the action, ASK = require human approval, ALLOW = permit)
  - "condition_hint": natural-language description of the condition
    (e.g. "amount > 500000", "credit_score < 650", "tool is delete_customer")
  - "source_excerpt": the exact sentence(s) from the document that led to this rule

Output ONLY a JSON array of objects.  If no policy clauses are found, output [].

Document text:
{text}
"""


class OpenAIResolver(BaseResolver):
    """LLM-backed resolver using the OpenAI Chat Completions API."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self._api_key = api_key
        self._model   = model

    @property
    def name(self) -> str:
        return f"RAG (OpenAI/{self._model})"

    def extract(self, text: str) -> List[Dict[str, Any]]:
        import json as _json
        try:
            from openai import OpenAI
        except ImportError:
            raise ImportError(
                "openai package is required for OpenAI policy extraction. "
                "Install it with: pip install openai"
            )

        client   = OpenAI(api_key=self._api_key)
        prompt   = _EXTRACTION_PROMPT.format(text=text[:8000])  # token guard

        response = client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            response_format={"type": "json_object"}
            if "gpt-4" in self._model else None,
        )
        raw = response.choices[0].message.content or "[]"

        # The model may wrap the array in {"policies": [...]}
        parsed = _json.loads(raw)
        if isinstance(parsed, list):
            return parsed
        for key in ("policies", "rules", "candidates", "results"):
            if key in parsed and isinstance(parsed[key], list):
                return parsed[key]
        return []


# ---------------------------------------------------------------------------
# Groq resolver
# ---------------------------------------------------------------------------

class GroqResolver(BaseResolver):
    """LLM-backed resolver using the Groq inference API."""

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        self._api_key = api_key
        self._model   = model

    @property
    def name(self) -> str:
        return f"RAG (Groq/{self._model})"

    def extract(self, text: str) -> List[Dict[str, Any]]:
        import json as _json
        try:
            from groq import Groq
        except ImportError:
            raise ImportError(
                "groq package is required for Groq policy extraction. "
                "Install it with: pip install groq"
            )

        client   = Groq(api_key=self._api_key)
        prompt   = _EXTRACTION_PROMPT.format(text=text[:6000])

        response = client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
        )
        raw = response.choices[0].message.content or "[]"

        # Strip markdown code fences if present
        raw = raw.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
            raw = raw.rstrip("`").strip()

        try:
            parsed = _json.loads(raw)
        except _json.JSONDecodeError:
            return []

        if isinstance(parsed, list):
            return parsed
        for key in ("policies", "rules", "candidates", "results"):
            if key in parsed and isinstance(parsed[key], list):
                return parsed[key]
        return []


# ---------------------------------------------------------------------------
# Auto-detect factory
# ---------------------------------------------------------------------------

def auto_detect_resolver() -> BaseResolver:
    """
    Return the best available resolver based on API keys in the environment.

    Priority: OpenAI > Groq > Heuristic
    """
    openai_key = os.environ.get("OPENAI_API_KEY")
    if openai_key:
        return OpenAIResolver(api_key=openai_key)

    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        return GroqResolver(api_key=groq_key)

    return HeuristicResolver()


detect_resolver = auto_detect_resolver
