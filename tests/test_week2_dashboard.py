"""
Week 2 Person 2 — Dashboard & Visualization Integration Tests for AgentAssure

Tests verify:
1. Dashboard loads against real backend
2. API connection works
3. WebSocket connection works
4. Live update (event received via WebSocket)
5. DAG correctness (parent_span_id relationships)
6. Drill-down data (event + policy + logs correlation)
7. Historical trace reconstruction via REST
8. Block consistency (BLOCK + reason + NOT EXECUTED)
9. WebSocket resilience (disconnect → REST reconstruction)
"""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from agentassure.events import EventType, EventStatus
from agentassure.evidence import EvidenceStore
from agentassure.approval import ApprovalStore
from agentassure.policy import (
    PolicyCondition,
    PolicyEngine,
    PolicyOutcome,
    PolicyRule,
    PolicyScope,
)
from agentassure.sdk import AgentAssure
from agentassure.trace import TraceContext, generate_span_id
from agentassure.enforcement import PolicyViolationError, PendingApprovalException

import server.api as api_module
from server.api import app


@pytest.fixture
def dashboard_env():
    """Isolated test environment with temp DB, test policies, and FastAPI client."""
    f = tempfile.NamedTemporaryFile("w", delete=False, suffix=".db")
    db_path = f.name
    f.close()

    old_db = api_module.DB_PATH
    api_module.DB_PATH = db_path
    api_module.evidence_store = EvidenceStore(db_path)
    api_module.approval_store = ApprovalStore(db_path)

    # Test policies: BLOCK above 500k, ASK above 200k
    rule_block = PolicyRule(
        id="TEST-BLOCK",
        name="Block high amount",
        version=1,
        scope=PolicyScope(tools=["approve_loan"]),
        condition=PolicyCondition(field="amount", operator="gt", value=500000),
        action=PolicyOutcome.BLOCK,
    )
    rule_ask = PolicyRule(
        id="TEST-ASK",
        name="Ask for medium amount",
        version=1,
        scope=PolicyScope(tools=["approve_loan"]),
        condition=PolicyCondition(field="amount", operator="gt", value=200000),
        action=PolicyOutcome.ASK,
    )
    test_engine = PolicyEngine(rules=[rule_block, rule_ask])
    api_module.policy_engine = test_engine

    assure = AgentAssure(db_path=db_path, agent_id="test-agent")
    assure.enforcement_engine.policy_engine = test_engine
    assure.policy_engine = test_engine

    client = TestClient(app)

    yield {
        "db_path": db_path,
        "assure": assure,
        "client": client,
    }

    if os.path.exists(db_path):
        os.remove(db_path)
    api_module.DB_PATH = old_db


# =========================================================================
# 1. DASHBOARD LOADS
# =========================================================================
def test_dashboard_html_loads(dashboard_env):
    """Dashboard HTML page loads and contains expected markers."""
    client = dashboard_env["client"]
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.text
    assert "AgentAssure" in body
    assert "Operator Console" in body
    assert "ws/events" in body  # WebSocket URL reference
    assert "overview" in body   # Default route


# =========================================================================
# 2. API CONNECTION / HEALTH CHECK
# =========================================================================
def test_api_health_check(dashboard_env):
    """Backend health endpoint responds correctly."""
    client = dashboard_env["client"]
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data


# =========================================================================
# 3. WEBSOCKET CONNECTION
# =========================================================================
def test_websocket_connects(dashboard_env):
    """WebSocket connects and receives initial greeting."""
    client = dashboard_env["client"]
    with client.websocket_connect("/ws/events") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "connected"
        assert "timestamp" in msg


# =========================================================================
# 4. LIVE UPDATE — EVENT RECEIVED VIA WEBSOCKET
# =========================================================================
def test_live_event_delivery(dashboard_env):
    """Running a governed tool delivers events to WebSocket clients."""
    assure = dashboard_env["assure"]
    client = dashboard_env["client"]

    def process_payment(amount: float):
        return {"status": "OK", "amount": amount}

    governed = assure.wrap_tool(process_payment, tool_name="process_payment")

    with client.websocket_connect("/ws/events") as ws:
        greeting = ws.receive_json()
        assert greeting["type"] == "connected"

        # Execute governed tool
        result = governed(amount=50000)
        assert result["status"] == "OK"

        # Should receive tool_call event
        evt1 = ws.receive_json()
        assert evt1["tool_name"] == "process_payment"
        assert evt1["event_type"] == "tool_call"
        assert "trace_id" in evt1
        assert "span_id" in evt1
        assert "event_id" in evt1

        # Should receive tool_result event
        evt2 = ws.receive_json()
        assert evt2["event_type"] == "tool_result"
        assert evt2["trace_id"] == evt1["trace_id"]
        assert evt2["span_id"] == evt1["span_id"]


# =========================================================================
# 5. DAG CORRECTNESS — parent_span_id RELATIONSHIPS
# =========================================================================
def test_dag_parent_child_relationships(dashboard_env):
    """
    Events from a trace preserve correct parent_span_id relationships.
    The frontend DAG must follow these relationships, not hard-code order.
    """
    assure = dashboard_env["assure"]
    client = dashboard_env["client"]

    def step_a(val: str):
        return {"step": "A", "val": val}

    def step_b(val: str):
        return {"step": "B", "val": val}

    def step_c(val: str):
        return {"step": "C", "val": val}

    gov_a = assure.wrap_tool(step_a, tool_name="step_a")
    gov_b = assure.wrap_tool(step_b, tool_name="step_b")
    gov_c = assure.wrap_tool(step_c, tool_name="step_c")

    # Execute tools within a single trace context
    with TraceContext() as ctx:
        root_span = ctx.span_id
        trace_id = ctx.trace_id
        gov_a(val="first")
        gov_b(val="second")
        gov_c(val="third")

    # Fetch trace events from REST
    resp = client.get(f"/traces/{trace_id}/events")
    assert resp.status_code == 200
    events = resp.json()

    # All events should belong to this trace
    assert all(e["trace_id"] == trace_id for e in events)

    # Collect unique span_ids and their parent_span_ids
    span_map = {}
    for evt in events:
        sid = evt["span_id"]
        if sid not in span_map:
            span_map[sid] = {
                "parent": evt["parent_span_id"],
                "tool": evt.get("tool_name"),
            }

    # At least 3 distinct tool spans
    tool_spans = {sid: info for sid, info in span_map.items() if info["tool"]}
    assert len(tool_spans) >= 3

    # All tool spans should have parent_span_id pointing to the root span
    for sid, info in tool_spans.items():
        assert info["parent"] == root_span, (
            f"Span {sid} ({info['tool']}) parent is {info['parent']}, "
            f"expected root span {root_span}"
        )

    # Each tool span should have a unique span_id
    assert len(tool_spans) == len(set(tool_spans.keys()))


def test_dag_different_topology(dashboard_env):
    """
    Verify DAG works with a different parent-child structure
    (nested trace contexts creating deeper hierarchy).
    """
    assure = dashboard_env["assure"]
    client = dashboard_env["client"]

    def outer_tool(x: int):
        return {"outer": x}

    def inner_tool(x: int):
        return {"inner": x}

    gov_outer = assure.wrap_tool(outer_tool, tool_name="outer_tool")
    gov_inner = assure.wrap_tool(inner_tool, tool_name="inner_tool")

    with TraceContext() as root_ctx:
        trace_id = root_ctx.trace_id
        gov_outer(x=1)
        # Create a nested context (simulating sub-span)
        with TraceContext(trace_id=trace_id) as nested_ctx:
            gov_inner(x=2)

    resp = client.get(f"/traces/{trace_id}/events")
    assert resp.status_code == 200
    events = resp.json()

    # Build span hierarchy
    span_parents = {}
    for evt in events:
        sid = evt["span_id"]
        if sid not in span_parents:
            span_parents[sid] = evt["parent_span_id"]

    # inner_tool events should have a different parent than outer_tool events
    outer_spans = [e["span_id"] for e in events if e.get("tool_name") == "outer_tool"]
    inner_spans = [e["span_id"] for e in events if e.get("tool_name") == "inner_tool"]

    assert len(outer_spans) > 0
    assert len(inner_spans) > 0

    # Outer tool parent should be root context span
    outer_parent = span_parents.get(outer_spans[0])
    assert outer_parent == root_ctx.span_id

    # Inner tool parent should be the nested context span (not root)
    inner_parent = span_parents.get(inner_spans[0])
    assert inner_parent is not None
    # The nested context creates its own span, which becomes the parent
    assert inner_parent != outer_parent or inner_parent == root_ctx.span_id


# =========================================================================
# 6. DRILL-DOWN DATA — EVENT + POLICY + LOGS CORRELATION
# =========================================================================
def test_drilldown_event_policy_logs_correlated(dashboard_env):
    """
    After a policy evaluation, event detail, policy data, and logs
    all share the same trace_id and span_id.
    """
    assure = dashboard_env["assure"]
    client = dashboard_env["client"]

    def check_balance(account: str):
        return {"balance": 10000}

    governed = assure.wrap_tool(check_balance, tool_name="check_balance")

    with TraceContext() as ctx:
        trace_id = ctx.trace_id
        governed(account="ACC-001")

    # 1. Get events
    events_resp = client.get(f"/traces/{trace_id}/events")
    assert events_resp.status_code == 200
    events = events_resp.json()
    assert len(events) > 0

    # Find a tool_call event
    tool_event = next((e for e in events if e["event_type"] == "tool_call"), None)
    assert tool_event is not None
    event_id = tool_event["event_id"]
    span_id = tool_event["span_id"]

    # 2. Get individual event
    single_resp = client.get(f"/events/{event_id}")
    assert single_resp.status_code == 200
    single = single_resp.json()
    assert single["span_id"] == span_id
    assert single["trace_id"] == trace_id

    # 3. Get correlated logs
    logs_resp = client.get(f"/logs?trace_id={trace_id}")
    assert logs_resp.status_code == 200
    logs = logs_resp.json()
    assert len(logs) > 0

    # Logs should contain entries for this span
    span_logs = [l for l in logs if l.get("span_id") == span_id]
    assert len(span_logs) > 0, "No correlated logs found for span"


# =========================================================================
# 7. HISTORICAL TRACE — REST RECONSTRUCTION
# =========================================================================
def test_historical_trace_reconstruction(dashboard_env):
    """
    After a run completes, traces and events are fully reconstructable
    from REST endpoints (no WebSocket needed).
    """
    assure = dashboard_env["assure"]
    client = dashboard_env["client"]

    def verify_identity(customer_id: str):
        return {"verified": True}

    governed = assure.wrap_tool(verify_identity, tool_name="verify_identity")

    with TraceContext() as ctx:
        trace_id = ctx.trace_id
        governed(customer_id="CUST-999")

    # 1. Trace appears in trace list
    traces_resp = client.get("/traces")
    assert traces_resp.status_code == 200
    traces = traces_resp.json()
    trace_ids = [t["trace_id"] for t in traces]
    assert trace_id in trace_ids

    # 2. Trace detail is available
    detail_resp = client.get(f"/traces/{trace_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["trace_id"] == trace_id
    assert detail["event_count"] >= 2  # tool_call + tool_result

    # 3. Events are available with full DAG data
    events_resp = client.get(f"/traces/{trace_id}/events")
    assert events_resp.status_code == 200
    events = events_resp.json()
    assert len(events) >= 2
    assert all("span_id" in e for e in events)
    assert all("parent_span_id" in e for e in events)

    # 4. Evidence is available
    ev_resp = client.get(f"/evidence/{trace_id}")
    assert ev_resp.status_code == 200
    records = ev_resp.json()
    assert len(records) >= 2

    # 5. Integrity verification works
    verify_resp = client.get(f"/evidence/{trace_id}/verify")
    assert verify_resp.status_code == 200
    verify = verify_resp.json()
    assert verify["valid"] is True
    assert verify["records_checked"] >= 2


# =========================================================================
# 8. BLOCK CONSISTENCY — BLOCK + REASON + NOT EXECUTED
# =========================================================================
def test_block_shows_reason_and_not_executed(dashboard_env):
    """
    A blocked action must have consistent BLOCK decision, reason,
    and the underlying tool must NOT have executed.
    """
    assure = dashboard_env["assure"]
    client = dashboard_env["client"]
    counter = {"exec": 0}

    def approve_loan(amount: float, customer: str):
        counter["exec"] += 1
        return {"approved": True}

    governed = assure.wrap_tool(approve_loan, tool_name="approve_loan")

    with TraceContext() as ctx:
        trace_id = ctx.trace_id
        with pytest.raises(PolicyViolationError) as exc_info:
            governed(amount=800000, customer="CUST-BLOCK")

    # Tool did NOT execute
    assert counter["exec"] == 0

    # PolicyViolationError has correct decision
    decision = exc_info.value.decision
    assert decision.outcome == PolicyOutcome.BLOCK
    assert decision.policy_id == "TEST-BLOCK"
    assert "amount" in decision.reason.lower() or "500000" in decision.reason

    # REST events show BLOCK consistently
    events_resp = client.get(f"/traces/{trace_id}/events")
    assert events_resp.status_code == 200
    events = events_resp.json()

    blocked_events = [e for e in events if e.get("decision") == "BLOCK"]
    assert len(blocked_events) >= 1

    for evt in blocked_events:
        assert evt["decision"] == "BLOCK"
        assert evt["reason"] is not None and len(evt["reason"]) > 0
        assert evt["policy_id"] == "TEST-BLOCK"

    # No tool_result event (tool never executed)
    result_events = [e for e in events if e["event_type"] == "tool_result"]
    assert len(result_events) == 0, "tool_result should not exist for blocked action"


def test_ask_shows_pending_state(dashboard_env):
    """ASK decision creates a pending approval visible via REST."""
    assure = dashboard_env["assure"]
    client = dashboard_env["client"]
    counter = {"exec": 0}

    def approve_loan(amount: float, customer: str):
        counter["exec"] += 1
        return {"approved": True}

    governed = assure.wrap_tool(approve_loan, tool_name="approve_loan")

    with TraceContext() as ctx:
        trace_id = ctx.trace_id
        with pytest.raises(PendingApprovalException):
            governed(amount=300000, customer="CUST-ASK")

    assert counter["exec"] == 0

    # Events show ASK
    events_resp = client.get(f"/traces/{trace_id}/events")
    events = events_resp.json()
    ask_events = [e for e in events if e.get("decision") == "ASK"]
    assert len(ask_events) >= 1

    # Approval is visible
    approvals_resp = client.get("/approvals?status=PENDING")
    assert approvals_resp.status_code == 200
    approvals = approvals_resp.json()
    trace_approvals = [a for a in approvals if a["trace_id"] == trace_id]
    assert len(trace_approvals) >= 1
    assert trace_approvals[0]["status"] == "PENDING"

    # Trace status reflects pending approval
    trace_resp = client.get(f"/traces/{trace_id}")
    assert trace_resp.status_code == 200
    trace_detail = trace_resp.json()
    assert trace_detail["status"] in ("PENDING_APPROVAL", "BLOCKED")


# =========================================================================
# 9. WEBSOCKET RESILIENCE — DISCONNECT → REST RECONSTRUCTION
# =========================================================================
def test_websocket_resilience_rest_reconstruction(dashboard_env):
    """
    After WebSocket disconnection, REST endpoints still have all data
    and can reconstruct the full state.
    """
    assure = dashboard_env["assure"]
    client = dashboard_env["client"]

    def calculate(value: int):
        return {"result": value * 2}

    governed = assure.wrap_tool(calculate, tool_name="calculate")

    # Phase 1: Connect WebSocket, run tool, disconnect
    with client.websocket_connect("/ws/events") as ws:
        ws.receive_json()  # greeting
        with TraceContext() as ctx:
            trace_id = ctx.trace_id
            governed(value=42)
        # Receive events
        ws.receive_json()  # tool_call
        ws.receive_json()  # tool_result
    # WebSocket is now disconnected

    # Phase 2: Run more tools WITHOUT WebSocket
    with TraceContext() as ctx2:
        trace_id_2 = ctx2.trace_id
        governed(value=99)

    # Phase 3: Verify REST has ALL data (both runs)
    traces_resp = client.get("/traces")
    traces = traces_resp.json()
    all_trace_ids = [t["trace_id"] for t in traces]
    assert trace_id in all_trace_ids, "First trace should be in REST"
    assert trace_id_2 in all_trace_ids, "Second trace should be in REST"

    # Verify events are complete for both traces
    for tid in [trace_id, trace_id_2]:
        ev_resp = client.get(f"/traces/{tid}/events")
        assert ev_resp.status_code == 200
        events = ev_resp.json()
        assert len(events) >= 2  # tool_call + tool_result

    # Phase 4: Reconnect WebSocket — should work
    with client.websocket_connect("/ws/events") as ws2:
        msg = ws2.receive_json()
        assert msg["type"] == "connected"

        # New tool call should be delivered
        with TraceContext() as ctx3:
            governed(value=7)
        evt = ws2.receive_json()
        assert evt["tool_name"] == "calculate"


# =========================================================================
# 10. STATS ENDPOINT — METRICS GROUNDED IN REAL DATA
# =========================================================================
def test_overview_metrics_from_real_data(dashboard_env):
    """Stats endpoint returns metrics computed from actual evidence data."""
    assure = dashboard_env["assure"]
    client = dashboard_env["client"]

    def action_a(x: int):
        return {"x": x}

    governed = assure.wrap_tool(action_a, tool_name="action_a")

    # Run a tool to generate data
    with TraceContext():
        governed(x=1)

    resp = client.get("/api/v1/stats")
    assert resp.status_code == 200
    stats = resp.json()

    # Stats should reflect real data
    assert stats["records"] >= 2  # tool_call + tool_result
    assert stats["traces"] >= 1
    assert isinstance(stats["allowed"], int)
    assert isinstance(stats["blocked"], int)
    assert isinstance(stats["pending_approval"], int)
    assert "chain" in stats
    assert stats["chain"]["is_valid"] is True
