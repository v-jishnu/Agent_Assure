"""
Explainable Anomaly and Security Detectors for AgentAssure
"""

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from agentassure.policy import ControlMapping


# Characters that carry no visible width and can be inserted between digits to
# break a naive pattern match while leaving the text perfectly readable.
_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)


def normalize(text: str) -> str:
    """
    Canonicalise text before pattern matching.

    Real models emit typographic separators. An observed gpt-oss response
    wrote an Aadhaar as "4392 8811 0246" using NARROW NO-BREAK SPACE
    (U+202F). That reads identically to a human but defeats a `[ -]` character
    class, so the identifier sails past detection and lands in the audit log
    looking perfectly legible. Every Unicode space becomes an ASCII space and
    zero-width characters are dropped, so detection sees one canonical form.
    """
    if not text:
        return text
    text = text.translate(_ZERO_WIDTH)
    return "".join(
        " " if unicodedata.category(ch) == "Zs" else ch for ch in text
    )


# Named patterns, so rules and detectors refer to them by name rather than
# embedding raw regex. Indian BFSI identifiers are the priority here: this is
# a lending agent operating under the DPDP Act, not a US one.
PATTERNS: Dict[str, str] = {
    "aadhaar": r"\b\d{4}[ -]\d{4}[ -]\d{4}\b",
    "pan": r"\b[A-Z]{5}\d{4}[A-Z]\b",
    "phone_in": r"(?:\+91[ -]?)?\b[6-9]\d{9}\b",
    "email": r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+",
    "card": r"\b(?:\d{4}[ -]){3}\d{4}\b",
    "ifsc": r"\b[A-Z]{4}0[A-Z0-9]{6}\b",
}

SECRET_PATTERN = (
    r"(?:api_key|token|secret|password|bearer)\s*[:=]\s*"
    r"['\"]?([a-zA-Z0-9_-]{16,})['\"]?"
)


def find_pii(text: str) -> List[str]:
    """Names of the PII patterns present in text, after canonicalisation."""
    canonical = normalize(text)
    return [
        name for name, pattern in PATTERNS.items()
        if re.search(pattern, canonical)
    ]


def redact(text: str, pattern_names: Optional[List[str]] = None) -> Tuple[str, int]:
    """
    Mask every occurrence of the named patterns. Returns (text, count).

    The text is canonicalised first, so masking sees exactly what detection
    saw — otherwise an identifier written with exotic separators would be
    reported as a finding and then survive into the stored record.
    """
    canonical = normalize(text)
    total = 0
    for name in (pattern_names or list(PATTERNS)):
        pattern = PATTERNS.get(name)
        if not pattern:
            continue
        canonical, n = re.subn(pattern, f"[REDACTED:{name}]", canonical)
        total += n
    return canonical, total


class DetectorSignal(BaseModel):
    detector_name: str
    detected: bool
    signal_type: str
    severity: str
    reason: str
    details: Dict[str, Any] = Field(default_factory=dict)
    controls: Optional[ControlMapping] = None


class BaseDetector:
    def detect(self, tool_name: str, tool_input: Any) -> Optional[DetectorSignal]:
        raise NotImplementedError


class PIIDetector(BaseDetector):
    """
    Detects Indian personal and financial identifiers.

    Severity is deliberately medium: PII reaching a tool is usually legitimate
    (a KYC lookup must return an Aadhaar). The control that matters is what is
    *retained* in the evidence log, which the store handles by masking. Raising
    this to high would block the agent from doing its job.
    """

    CONTROLS = ControlMapping(
        iso42001="A.8.4",
        eu_ai_act="Art. 10",
        note="Data privacy and data governance for AI systems",
    )

    def detect(self, tool_name: str, tool_input: Any) -> Optional[DetectorSignal]:
        detected_types = find_pii(str(tool_input))
        if detected_types:
            return DetectorSignal(
                detector_name="PIIDetector",
                detected=True,
                signal_type="PII_EXPOSURE",
                severity="medium",
                reason=(
                    f"PII detected in input to tool '{tool_name}': "
                    f"{', '.join(detected_types)}"
                ),
                details={"detected_types": detected_types},
                controls=self.CONTROLS,
            )
        return None


class SecretDetector(BaseDetector):
    CONTROLS = ControlMapping(
        iso42001="A.8.4",
        eu_ai_act="Art. 15",
        note="Accuracy, robustness and cybersecurity",
    )

    def __init__(self):
        self.secret_regex = re.compile(SECRET_PATTERN, re.IGNORECASE)

    def detect(self, tool_name: str, tool_input: Any) -> Optional[DetectorSignal]:
        if self.secret_regex.search(normalize(str(tool_input))):
            return DetectorSignal(
                detector_name="SecretDetector",
                detected=True,
                signal_type="SECRET_EXPOSURE",
                severity="high",
                reason=f"Potential secret/token detected in input to tool '{tool_name}'",
                details={},
                controls=self.CONTROLS,
            )
        return None


class RestrictedToolDetector(BaseDetector):
    CONTROLS = ControlMapping(
        iso42001="A.9.2",
        eu_ai_act="Art. 14",
        note="Responsible use and human oversight of AI systems",
    )

    def __init__(self, restricted_tools: Optional[List[str]] = None):
        self.restricted_tools = restricted_tools or [
            "system_shell", "raw_sql_exec", "delete_database",
        ]

    def detect(self, tool_name: str, tool_input: Any) -> Optional[DetectorSignal]:
        if tool_name in self.restricted_tools:
            return DetectorSignal(
                detector_name="RestrictedToolDetector",
                detected=True,
                signal_type="RESTRICTED_TOOL",
                severity="critical",
                reason=f"Tool '{tool_name}' belongs to restricted system tool category",
                details={"tool_name": tool_name},
                controls=self.CONTROLS,
            )
        return None
