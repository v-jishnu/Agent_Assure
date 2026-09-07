"""
SQLite Evidence Repository with Tamper-Evident Hash Chain
"""

from contextlib import contextmanager
import sqlite3
import json
import hashlib
from typing import List, Optional, Tuple
from pydantic import BaseModel
from agentassure.events import AgentEvent
from agentassure.detectors import redact


GENESIS_HASH = "0000000000000000000000000000000000000000000000000000000000000000"


class EvidenceRecord(BaseModel):
    id: Optional[int] = None
    record_id: str
    event_id: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    agent_id: str
    session_id: str
    timestamp: str
    event_type: str
    tool_name: Optional[str] = None
    input: Optional[str] = None  # JSON string
    output: Optional[str] = None  # JSON string
    policy_id: Optional[str] = None
    policy_version: Optional[int] = None
    decision: Optional[str] = None
    reason: Optional[str] = None
    controls: Optional[str] = None  # e.g. "ISO/IEC 42001 A.9.4 | EU AI Act Art. 14"
    redactions: int = 0
    risk_score: float = 0.0
    previous_hash: str = GENESIS_HASH
    record_hash: str = ""

    def calculate_hash(self) -> str:
        """
        Hash every field of the record except its own hash and row id.

        Canonical JSON rather than a delimiter-joined string: a '|' appearing
        inside any value (a reason, a tool input) would otherwise shift the
        field boundaries, so two different records could hash identically.
        Building the payload from the model also means a field added later is
        covered automatically — previously risk_score and parent_span_id were
        outside the hash and could be edited without breaking verification.
        """
        payload = self.model_dump(exclude={"id", "record_hash"})
        canonical = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class EvidenceStore:
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
                CREATE TABLE IF NOT EXISTS evidence_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    record_id TEXT UNIQUE NOT NULL,
                    event_id TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    span_id TEXT NOT NULL,
                    parent_span_id TEXT,
                    agent_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    tool_name TEXT,
                    input TEXT,
                    output TEXT,
                    policy_id TEXT,
                    policy_version INTEGER,
                    decision TEXT,
                    reason TEXT,
                    controls TEXT,
                    redactions INTEGER DEFAULT 0,
                    risk_score REAL DEFAULT 0.0,
                    previous_hash TEXT NOT NULL,
                    record_hash TEXT NOT NULL
                )
            """)
            conn.commit()

    def get_latest_record_hash(self) -> str:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT record_hash FROM evidence_records ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            if row:
                return row["record_hash"]
            return GENESIS_HASH

    def record_event(
        self,
        event: AgentEvent,
        policy_id: Optional[str] = None,
        policy_version: Optional[int] = None,
        decision: Optional[str] = None,
        reason: Optional[str] = None,
        controls: Optional[str] = None,
        risk_score: float = 0.0,
        mask_pii: bool = True,
    ) -> EvidenceRecord:

        previous_hash = self.get_latest_record_hash()

        input_str = json.dumps(event.input) if event.input is not None else None
        output_str = json.dumps(event.output) if event.output is not None else None

        # Data minimisation. The agent already received the real values — a
        # KYC lookup must return an Aadhaar for the workflow to function — but
        # the audit log has no such need, so the retained copy is masked.
        # Separating what the system may *process* from what it may *retain*
        # is what stops the evidence store becoming a PII repository.
        redactions = 0
        if mask_pii:
            if input_str:
                input_str, n_in = redact(input_str)
                redactions += n_in
            if output_str:
                output_str, n_out = redact(output_str)
                redactions += n_out

        record = EvidenceRecord(
            record_id=f"rec_{event.event_id}",
            event_id=event.event_id,
            trace_id=event.trace_id,
            span_id=event.span_id,
            parent_span_id=event.parent_span_id,
            agent_id=event.agent_id,
            session_id=event.session_id,
            timestamp=event.timestamp,
            event_type=event.event_type.value if hasattr(event.event_type, "value") else str(event.event_type),
            tool_name=event.tool_name,
            input=input_str,
            output=output_str,
            policy_id=policy_id,
            policy_version=policy_version,
            decision=decision,
            reason=reason,
            controls=controls,
            redactions=redactions,
            risk_score=risk_score,
            previous_hash=previous_hash,
            record_hash="",
        )
        record.record_hash = record.calculate_hash()

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO evidence_records (
                    record_id, event_id, trace_id, span_id, parent_span_id,
                    agent_id, session_id, timestamp, event_type, tool_name,
                    input, output, policy_id, policy_version, decision, reason,
                    controls, redactions, risk_score, previous_hash, record_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record.record_id, record.event_id, record.trace_id, record.span_id, record.parent_span_id,
                record.agent_id, record.session_id, record.timestamp, record.event_type, record.tool_name,
                record.input, record.output, record.policy_id, record.policy_version, record.decision, record.reason,
                record.controls, record.redactions, record.risk_score, record.previous_hash, record.record_hash
            ))
            conn.commit()

        return record

    def get_records(self, trace_id: Optional[str] = None, session_id: Optional[str] = None) -> List[EvidenceRecord]:
        query = "SELECT * FROM evidence_records"
        params = []
        conditions = []

        if trace_id:
            conditions.append("trace_id = ?")
            params.append(trace_id)
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY id ASC"

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [EvidenceRecord(**dict(row)) for row in rows]

    def verify_integrity(self) -> Tuple[bool, List[str]]:
        """
        Verifies the tamper-evident hash chain across all stored evidence records.
        """
        errors = []
        records = self.get_records()

        expected_prev_hash = GENESIS_HASH
        for rec in records:
            if rec.previous_hash != expected_prev_hash:
                errors.append(
                    f"Record ID {rec.id} ({rec.record_id}) previous_hash mismatch! "
                    f"Expected {expected_prev_hash}, got {rec.previous_hash}"
                )

            calculated_hash = rec.calculate_hash()
            if rec.record_hash != calculated_hash:
                errors.append(
                    f"Record ID {rec.id} ({rec.record_id}) payload tampered! "
                    f"Stored hash: {rec.record_hash}, calculated hash: {calculated_hash}"
                )

            expected_prev_hash = rec.record_hash

        is_valid = len(errors) == 0
        return is_valid, errors
