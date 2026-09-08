"""
Approval Backend Primitives for AgentAssure (Week 2)
"""

from contextlib import contextmanager
from datetime import datetime, timezone
from enum import Enum
import json
import sqlite3
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


def generate_approval_id() -> str:
    return f"appr_{uuid.uuid4().hex[:12]}"


def current_iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class ApprovalRequest(BaseModel):
    approval_id: str = Field(default_factory=generate_approval_id)
    trace_id: str
    span_id: str
    event_id: str
    agent_id: str = "default_agent"
    session_id: str = "default_session"
    tool_name: str
    tool_arguments: Dict[str, Any] = Field(default_factory=dict)
    policy_id: Optional[str] = None
    policy_version: Optional[int] = None
    reason: Optional[str] = None
    created_at: str = Field(default_factory=current_iso_timestamp)
    status: ApprovalStatus = ApprovalStatus.PENDING
    resolved_at: Optional[str] = None
    resolved_by: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()


class ApprovalStore:
    def __init__(self, db_path: str = "agentassure.db"):
        self.db_path = db_path
        self._init_db()

    @contextmanager
    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    span_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    tool_arguments TEXT,
                    policy_id TEXT,
                    policy_version INTEGER,
                    reason TEXT,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    resolved_at TEXT,
                    resolved_by TEXT
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_approvals_trace ON approvals(trace_id)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status)")
            conn.commit()

    def create_approval(
        self,
        trace_id: str,
        span_id: str,
        event_id: str,
        tool_name: str,
        tool_arguments: Dict[str, Any],
        agent_id: str = "default_agent",
        session_id: str = "default_session",
        policy_id: Optional[str] = None,
        policy_version: Optional[int] = None,
        reason: Optional[str] = None,
    ) -> ApprovalRequest:
        approval = ApprovalRequest(
            trace_id=trace_id,
            span_id=span_id,
            event_id=event_id,
            agent_id=agent_id,
            session_id=session_id,
            tool_name=tool_name,
            tool_arguments=tool_arguments,
            policy_id=policy_id,
            policy_version=policy_version,
            reason=reason,
            status=ApprovalStatus.PENDING,
        )

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO approvals (
                    approval_id, trace_id, span_id, event_id, agent_id, session_id,
                    tool_name, tool_arguments, policy_id, policy_version, reason,
                    created_at, status, resolved_at, resolved_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                approval.approval_id,
                approval.trace_id,
                approval.span_id,
                approval.event_id,
                approval.agent_id,
                approval.session_id,
                approval.tool_name,
                json.dumps(approval.tool_arguments),
                approval.policy_id,
                approval.policy_version,
                approval.reason,
                approval.created_at,
                approval.status.value,
                approval.resolved_at,
                approval.resolved_by,
            ))
            conn.commit()

        return approval

    def get_approval(self, approval_id: str) -> Optional[ApprovalRequest]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            if data.get("tool_arguments"):
                try:
                    data["tool_arguments"] = json.loads(data["tool_arguments"])
                except Exception:
                    data["tool_arguments"] = {}
            return ApprovalRequest.model_validate(data)

    def list_approvals(
        self, trace_id: Optional[str] = None, status: Optional[str] = None
    ) -> List[ApprovalRequest]:
        query = "SELECT * FROM approvals"
        params = []
        conditions = []
        if trace_id:
            conditions.append("trace_id = ?")
            params.append(trace_id)
        if status:
            conditions.append("status = ?")
            params.append(status.upper())

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            results = []
            for row in rows:
                data = dict(row)
                if data.get("tool_arguments"):
                    try:
                        data["tool_arguments"] = json.loads(data["tool_arguments"])
                    except Exception:
                        data["tool_arguments"] = {}
                results.append(ApprovalRequest.model_validate(data))
            return results

    def resolve_approval(
        self,
        approval_id: str,
        status: ApprovalStatus,
        resolved_by: Optional[str] = "operator",
    ) -> Optional[ApprovalRequest]:
        if status not in (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED):
            raise ValueError(f"Invalid resolution status: {status}")

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM approvals WHERE approval_id = ?", (approval_id,))
            row = cursor.fetchone()
            if not row:
                return None

            current_status = row["status"]
            if current_status != ApprovalStatus.PENDING.value:
                raise ValueError(
                    f"Cannot resolve approval {approval_id}: current status is {current_status}"
                )

            resolved_at = current_iso_timestamp()
            cursor.execute("""
                UPDATE approvals
                SET status = ?, resolved_at = ?, resolved_by = ?
                WHERE approval_id = ?
            """, (status.value, resolved_at, resolved_by, approval_id))
            conn.commit()

        return self.get_approval(approval_id)

    def is_action_approved(self, trace_id: str, tool_name: str) -> bool:
        """Check if an approval for (trace_id, tool_name) has been granted."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 1 FROM approvals
                WHERE trace_id = ? AND tool_name = ? AND status = 'APPROVED'
                LIMIT 1
            """, (trace_id, tool_name))
            return cursor.fetchone() is not None

    def is_action_rejected(self, trace_id: str, tool_name: str) -> bool:
        """Check if an approval for (trace_id, tool_name) has been rejected."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 1 FROM approvals
                WHERE trace_id = ? AND tool_name = ? AND status = 'REJECTED'
                LIMIT 1
            """, (trace_id, tool_name))
            return cursor.fetchone() is not None
