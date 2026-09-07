"""
Unit Tests for SQLite Evidence Repository & Tamper-Evident Hash Chain
"""

import pytest
import os
import tempfile
import sqlite3
from agentassure.evidence import EvidenceStore
from agentassure.events import AgentEvent, EventType, EventStatus


def test_evidence_recording_and_integrity():
    f = tempfile.NamedTemporaryFile("w", delete=False, suffix=".db")
    db_path = f.name
    f.close()


    try:
        store = EvidenceStore(db_path=db_path)

        ev1 = AgentEvent(
            trace_id="tr_100",
            span_id="sp_100",
            agent_id="test_agent",
            session_id="sess_1",
            event_type=EventType.TOOL_CALL,
            tool_name="check_credit",
            input={"cust_id": "C1"},
        )
        rec1 = store.record_event(ev1, policy_id=None, decision="ALLOW")

        ev2 = AgentEvent(
            trace_id="tr_100",
            span_id="sp_101",
            agent_id="test_agent",
            session_id="sess_1",
            event_type=EventType.POLICY_DECISION,
            tool_name="approve_loan",
            input={"amount": 800000},
            status=EventStatus.BLOCKED,
        )
        rec2 = store.record_event(ev2, policy_id="FIN-001", policy_version=1, decision="BLOCK", reason="Exceeds limit")

        assert rec2.previous_hash == rec1.record_hash

        # Verify integrity initially
        is_valid, errors = store.verify_integrity()
        assert is_valid is True
        assert len(errors) == 0

        # Simulate database tampering (unauthorized data alteration)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("UPDATE evidence_records SET reason = 'TAMPERED REASON' WHERE id = 2")
        conn.commit()
        conn.close()

        # Verify integrity detects tampering!
        is_valid_after, errors_after = store.verify_integrity()
        assert is_valid_after is False
        assert len(errors_after) > 0
        assert "payload tampered" in errors_after[0].lower()

    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
