"""
Canonical Agent Events Schema for AgentAssure
"""

from enum import Enum
from typing import Any, Dict, Optional
from datetime import datetime, timezone
import uuid
from pydantic import BaseModel, Field


class EventType(str, Enum):
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    LLM_REQUEST = "llm_request"
    LLM_RESPONSE = "llm_response"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    POLICY_DECISION = "policy_decision"
    ERROR = "error"


class EventStatus(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    PENDING_APPROVAL = "pending_approval"
    FAILED = "failed"


def generate_event_id() -> str:
    return f"evt_{uuid.uuid4().hex[:12]}"


def current_iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class AgentEvent(BaseModel):
    event_id: str = Field(default_factory=generate_event_id)
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    agent_id: str = "default_agent"
    session_id: str = "default_session"
    event_type: EventType
    timestamp: str = Field(default_factory=current_iso_timestamp)
    tool_name: Optional[str] = None
    input: Optional[Any] = None
    output: Optional[Any] = None
    status: EventStatus = EventStatus.STARTED
    metadata: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentEvent":
        return cls.model_validate(data)
