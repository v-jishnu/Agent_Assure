"""
Explainable Anomaly and Security Detectors for AgentAssure
"""

import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class DetectorSignal(BaseModel):
    detector_name: str
    detected: bool
    signal_type: str
    severity: str
    reason: str
    details: Dict[str, Any] = Field(default_factory=dict)



class BaseDetector:
    def detect(self, tool_name: str, tool_input: Any) -> Optional[DetectorSignal]:
        raise NotImplementedError


class PIIDetector(BaseDetector):
    def __init__(self):
        self.email_regex = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
        self.phone_regex = re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b")
        self.ssn_regex = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

    def detect(self, tool_name: str, tool_input: Any) -> Optional[DetectorSignal]:
        text = str(tool_input)
        detected_types = []

        if self.email_regex.search(text):
            detected_types.append("email")
        if self.phone_regex.search(text):
            detected_types.append("phone")
        if self.ssn_regex.search(text):
            detected_types.append("SSN")

        if detected_types:
            return DetectorSignal(
                detector_name="PIIDetector",
                detected=True,
                signal_type="PII_EXPOSURE",
                severity="medium",
                reason=f"PII detected in input to tool '{tool_name}': {', '.join(detected_types)}",
                details={"detected_types": detected_types},
            )
        return None


class SecretDetector(BaseDetector):
    def __init__(self):
        self.secret_regex = re.compile(r"(?:api_key|token|secret|password|bearer)\s*[:=]\s*['\"]?([a-zA-Z0-9_-]{16,})['\"]?", re.IGNORECASE)

    def detect(self, tool_name: str, tool_input: Any) -> Optional[DetectorSignal]:
        text = str(tool_input)
        if self.secret_regex.search(text):
            return DetectorSignal(
                detector_name="SecretDetector",
                detected=True,
                signal_type="SECRET_EXPOSURE",
                severity="high",
                reason=f"Potential secret/token detected in input to tool '{tool_name}'",
                details={},
            )
        return None


class RestrictedToolDetector(BaseDetector):
    def __init__(self, restricted_tools: Optional[List[str]] = None):
        self.restricted_tools = restricted_tools or ["system_shell", "raw_sql_exec", "delete_database"]

    def detect(self, tool_name: str, tool_input: Any) -> Optional[DetectorSignal]:
        if tool_name in self.restricted_tools:
            return DetectorSignal(
                detector_name="RestrictedToolDetector",
                detected=True,
                signal_type="RESTRICTED_TOOL",
                severity="critical",
                reason=f"Tool '{tool_name}' belongs to restricted system tool category",
                details={"tool_name": tool_name},
            )
        return None
