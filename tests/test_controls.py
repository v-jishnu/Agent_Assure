"""
Tests for compliance control mapping and evidence data minimisation.
"""

import os
import tempfile

import pytest

from agentassure.detectors import PIIDetector, find_pii, normalize, redact, PATTERNS
from agentassure.events import AgentEvent, EventType
from agentassure.policy import ControlMapping, PolicyEngine, PolicyOutcome
from agentassure.evidence import EvidenceStore

import re


POLICY_WITH_CONTROLS = {
    "policies": [
        {
            "id": "FIN-001",
            "name": "Loan approval limit",
            "version": 1,
            "scope": {"tools": ["approve_loan"]},
            "condition": {"field": "amount", "operator": "gt", "value": 500000},
            "severity": "high",
            "action": "block",
            "mode": "enforce",
            "controls": {"iso42001": "A.9.4", "eu_ai_act": "Art. 14"},
        }
    ],
    "capabilities": {
        "forbidden": ["delete_customer"],
        "controls": {"iso42001": "A.9.2", "eu_ai_act": "Art. 14"},
    },
}


# ---------------------------------------------------------------- controls --

def test_rule_decision_carries_control_mapping():
    engine = PolicyEngine.load_from_dict(POLICY_WITH_CONTROLS)
    decision = engine.evaluate("loan-agent", "demo", "approve_loan", {"amount": 800000})

    assert decision.outcome == PolicyOutcome.BLOCK
    assert decision.controls is not None
    assert decision.controls.iso42001 == "A.9.4"
    assert decision.controls.eu_ai_act == "Art. 14"
    assert decision.controls.as_citation() == "ISO/IEC 42001 A.9.4 | EU AI Act Art. 14"


def test_capability_decision_carries_control_mapping():
    engine = PolicyEngine.load_from_dict(POLICY_WITH_CONTROLS)
    decision = engine.evaluate("loan-agent", "demo", "delete_customer", {})

    assert decision.outcome == PolicyOutcome.BLOCK
    assert decision.controls.as_citation() == "ISO/IEC 42001 A.9.2 | EU AI Act Art. 14"


def test_allow_decision_has_no_control_mapping():
    """An allowed action evidences no control breach, so it cites nothing."""
    engine = PolicyEngine.load_from_dict(POLICY_WITH_CONTROLS)
    decision = engine.evaluate("loan-agent", "demo", "approve_loan", {"amount": 1000})

    assert decision.outcome == PolicyOutcome.ALLOW
    assert decision.controls is None


def test_controls_are_optional_for_backward_compatibility():
    """Rules written before control mapping existed must still load."""
    engine = PolicyEngine.load_from_dict({
        "policies": [{"id": "OLD-001", "name": "Legacy rule", "action": "block"}]
    })
    assert engine.rules[0].controls is None


def test_partial_citation_when_only_one_framework_mapped():
    mapping = ControlMapping(iso42001="A.8.4")
    assert mapping.as_citation() == "ISO/IEC 42001 A.8.4"


# --------------------------------------------------------------- detectors --

@pytest.mark.parametrize("value,expected", [
    ("4392 8811 0246", "aadhaar"),
    ("ABCPS1234K", "pan"),
    ("+91 9876543210", "phone_in"),
    ("jane.doe@example.com", "email"),
])
def test_indian_identifiers_are_detected(value, expected):
    assert expected in find_pii(f"customer record: {value}")


@pytest.mark.parametrize("separator", [
    " ",        # ASCII space
    " ",   # narrow no-break space, observed in real model output
    " ",   # non-breaking space
    " ",   # thin space
])
def test_aadhaar_detected_despite_unicode_separators(separator):
    """
    A real model wrote an Aadhaar using U+202F, which reads identically to a
    space but slips past a naive `[ -]` class. Detection must canonicalise
    first, or the identifier reaches the audit log unmasked.
    """
    aadhaar = f"4392{separator}8811{separator}0246"
    assert re.search(PATTERNS["aadhaar"], normalize(aadhaar)), (
        f"U+{ord(separator):04X} defeated detection"
    )
    masked, count = redact(aadhaar, ["aadhaar"])
    assert count == 1
    assert "4392" not in masked


def test_zero_width_characters_do_not_hide_pii():
    assert "aadhaar" in find_pii("4392​ 8811 ​0246")


def test_pii_detector_signal_cites_a_control():
    signal = PIIDetector().detect("fetch_kyc_record", {"aadhaar": "4392 8811 0246"})
    assert signal is not None
    assert signal.controls.iso42001 == "A.8.4"


# ------------------------------------------------------- evidence masking --

@pytest.fixture()
def store():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield EvidenceStore(path)
    os.remove(path)


def test_evidence_store_masks_pii_but_keeps_the_chain_valid(store):
    """
    Data minimisation: the agent already received the real values, but the
    audit log must not retain them. Masking happens before hashing, so
    integrity still verifies over what is actually stored.
    """
    event = AgentEvent(
        trace_id="tr_1", span_id="sp_1", event_type=EventType.TOOL_RESULT,
        tool_name="fetch_kyc_record",
        output={"aadhaar": "4392 8811 0246", "pan": "ABCPS1234K"},
    )
    record = store.record_event(event, decision="ALLOW", reason="ok")

    assert "4392 8811 0246" not in record.output
    assert "ABCPS1234K" not in record.output
    assert "[REDACTED:aadhaar]" in record.output
    assert record.redactions == 2

    is_valid, errors = store.verify_integrity()
    assert is_valid, errors


def test_control_citation_is_persisted(store):
    event = AgentEvent(trace_id="tr_2", span_id="sp_2", event_type=EventType.POLICY_DECISION)
    store.record_event(
        event, policy_id="FIN-001", decision="BLOCK", reason="over limit",
        controls="ISO/IEC 42001 A.9.4 | EU AI Act Art. 14",
    )
    stored = store.get_records(trace_id="tr_2")[0]
    assert stored.controls == "ISO/IEC 42001 A.9.4 | EU AI Act Art. 14"


def test_hash_covers_every_field(store):
    """
    risk_score and parent_span_id used to sit outside the hash payload and
    could be edited without breaking verification. The hash is now computed
    over the whole record.
    """
    event = AgentEvent(trace_id="tr_3", span_id="sp_3", parent_span_id="sp_0",
                       event_type=EventType.TOOL_CALL)
    record = store.record_event(event, risk_score=0.1)
    original = record.record_hash

    record.risk_score = 0.9
    assert record.calculate_hash() != original

    record.risk_score = 0.1
    record.parent_span_id = "sp_tampered"
    assert record.calculate_hash() != original
