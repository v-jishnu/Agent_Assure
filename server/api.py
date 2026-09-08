"""
FastAPI Server Endpoints for AgentAssure (Control Plane, Trace/Events, Approvals, Evidence, WebSockets)
"""

from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agentassure.approval import ApprovalRequest, ApprovalStatus, ApprovalStore
from agentassure.events import AgentEvent, EventStatus, EventType
from agentassure.evidence import EvidenceRecord, EvidenceStore
from agentassure.logging import LogEntry, default_log_buffer, log_runtime
from agentassure.policy import PolicyEngine
from agentassure.publisher import default_event_publisher
from server.websocket import ws_manager


DB_PATH = os.environ.get("AGENTASSURE_DB", "demo_evidence.db")
POLICY_PATH = os.environ.get(
    "AGENTASSURE_POLICY",
    os.path.join(os.path.dirname(__file__), "..", "policies", "loan.yaml"),
)
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="AgentAssure Governance API", version="0.2.0")

evidence_store = EvidenceStore(DB_PATH)
approval_store = ApprovalStore(DB_PATH)

# Load policy engine if file exists
if os.path.exists(POLICY_PATH):
    try:
        policy_engine = PolicyEngine.load_from_yaml(POLICY_PATH)
    except Exception:
        policy_engine = PolicyEngine()
else:
    policy_engine = PolicyEngine()


class ApprovalActionBody(BaseModel):
    resolved_by: Optional[str] = "operator"
    comment: Optional[str] = None


def _format_event_from_record(rec: EvidenceRecord) -> Dict[str, Any]:
    """Projects an EvidenceRecord into the canonical event shape for DAG / Event readers."""
    inp = rec.input
    out = rec.output
    try:
        if inp and isinstance(inp, str) and (inp.startswith("{") or inp.startswith("[")):
            inp = json.loads(inp)
    except Exception:
        pass
    try:
        if out and isinstance(out, str) and (out.startswith("{") or out.startswith("[")):
            out = json.loads(out)
    except Exception:
        pass

    return {
        "event_id": rec.event_id,
        "record_id": rec.record_id,
        "trace_id": rec.trace_id,
        "span_id": rec.span_id,
        "parent_span_id": rec.parent_span_id,
        "agent_id": rec.agent_id,
        "session_id": rec.session_id,
        "event_type": rec.event_type,
        "timestamp": rec.timestamp,
        "tool_name": rec.tool_name,
        "input": inp,
        "output": out,
        "decision": rec.decision,
        "policy_id": rec.policy_id,
        "policy_version": rec.policy_version,
        "reason": rec.reason,
        "controls": rec.controls,
        "redactions": rec.redactions,
        "risk_score": rec.risk_score,
        "previous_hash": rec.previous_hash,
        "record_hash": rec.record_hash,
    }


def _calculate_duration(start_ts: str, end_ts: Optional[str]) -> Optional[float]:
    if not start_ts or not end_ts:
        return None
    try:
        s = datetime.fromisoformat(start_ts)
        e = datetime.fromisoformat(end_ts)
        return round(abs((e - s).total_seconds() * 1000), 2)
    except Exception:
        return None


# -------------------------------------------------------------------------
# 1. HEALTH CHECK
# -------------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "AgentAssure Control Plane",
        "version": "0.2.0",
        "database": DB_PATH,
    }


# -------------------------------------------------------------------------
# 2. TRACE APIS
# -------------------------------------------------------------------------
@app.get("/traces")
def list_traces():
    """Lists aggregated agent traces with execution status, counts, and duration."""
    records = evidence_store.get_records()
    grouped: Dict[str, Dict[str, Any]] = {}

    for r in records:
        g = grouped.setdefault(
            r.trace_id,
            {
                "trace_id": r.trace_id,
                "agent_id": r.agent_id,
                "session_id": r.session_id,
                "started_at": r.timestamp,
                "ended_at": r.timestamp,
                "duration_ms": 0.0,
                "status": "COMPLETED",
                "events_count": 0,
                "blocked_count": 0,
                "approval_count": 0,
                "tools": [],
                "_timestamps": [],
                "_decisions": [],
            },
        )
        g["events_count"] += 1
        g["_timestamps"].append(r.timestamp)

        if r.decision:
            g["_decisions"].append(r.decision)
        if r.decision == "BLOCK":
            g["blocked_count"] += 1
        elif r.decision == "ASK":
            g["approval_count"] += 1

        if r.tool_name and r.tool_name not in g["tools"]:
            g["tools"].append(r.tool_name)

    trace_list = []
    for g in grouped.values():
        ts_list = sorted(g.pop("_timestamps"))
        decisions = g.pop("_decisions")
        g["started_at"] = ts_list[0]
        g["ended_at"] = ts_list[-1]
        g["duration_ms"] = _calculate_duration(g["started_at"], g["ended_at"])

        if "BLOCK" in decisions:
            g["status"] = "BLOCKED"
        elif "ASK" in decisions:
            # Check if pending approval is still open or resolved
            approvals = approval_store.list_approvals(trace_id=g["trace_id"])
            if any(a.status == ApprovalStatus.PENDING for a in approvals):
                g["status"] = "PENDING_APPROVAL"
            elif any(a.status == ApprovalStatus.APPROVED for a in approvals):
                g["status"] = "APPROVED"
            else:
                g["status"] = "PENDING_APPROVAL"
        else:
            g["status"] = "COMPLETED"

        trace_list.append(g)

    return sorted(trace_list, key=lambda x: x["started_at"], reverse=True)


@app.get("/traces/{trace_id}")
def get_trace_detail(trace_id: str):
    """Returns detailed summary and decisions for a single trace."""
    records = evidence_store.get_records(trace_id=trace_id)
    if not records:
        raise HTTPException(status_code=404, detail=f"Trace not found: {trace_id}")

    ts_list = sorted(r.timestamp for r in records)
    started_at = ts_list[0]
    ended_at = ts_list[-1]
    decisions = Counter(r.decision for r in records if r.decision)
    tools = [r.tool_name for r in records if r.tool_name]

    status = "COMPLETED"
    if decisions.get("BLOCK", 0) > 0:
        status = "BLOCKED"
    elif decisions.get("ASK", 0) > 0:
        approvals = approval_store.list_approvals(trace_id=trace_id)
        if any(a.status == ApprovalStatus.PENDING for a in approvals):
            status = "PENDING_APPROVAL"
        elif any(a.status == ApprovalStatus.APPROVED for a in approvals):
            status = "APPROVED"

    return {
        "trace_id": trace_id,
        "agent_id": records[0].agent_id,
        "session_id": records[0].session_id,
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": _calculate_duration(started_at, ended_at),
        "status": status,
        "event_count": len(records),
        "blocked_count": decisions.get("BLOCK", 0),
        "approval_count": decisions.get("ASK", 0),
        "decisions": dict(decisions),
        "tools": sorted(list(set(tools))),
    }


@app.get("/traces/{trace_id}/events")
def get_trace_events(trace_id: str):
    """
    Returns canonical events for the trace.
    Preserves trace_id, span_id, parent_span_id for frontend DAG reconstruction.
    """
    records = evidence_store.get_records(trace_id=trace_id)
    if not records:
        raise HTTPException(status_code=404, detail=f"Trace not found: {trace_id}")

    return [_format_event_from_record(r) for r in records]


# -------------------------------------------------------------------------
# 3. EVENT APIS
# -------------------------------------------------------------------------
@app.get("/events/{event_id}")
def get_event(event_id: str):
    """Retrieves an individual event by its event_id."""
    records = evidence_store.get_records()
    for r in records:
        if r.event_id == event_id:
            return _format_event_from_record(r)

    raise HTTPException(status_code=404, detail=f"Event not found: {event_id}")


# -------------------------------------------------------------------------
# 4. POLICY APIS
# -------------------------------------------------------------------------
@app.get("/policies")
def list_policies():
    """Lists loaded policy rules and capability boundaries."""
    return {
        "rules": [r.model_dump() for r in policy_engine.rules],
        "capabilities": policy_engine.capabilities.model_dump(),
        "total_rules": len(policy_engine.rules),
    }


@app.get("/policies/{policy_id}")
def get_policy(policy_id: str):
    """Retrieves a single policy rule by ID."""
    for r in policy_engine.rules:
        if r.id == policy_id:
            return r.model_dump()

    # Check capability policy pseudo-ids
    if policy_id in ("CAPABILITY-FORBIDDEN", "CAPABILITY-APPROVAL-REQUIRED"):
        return {
            "id": policy_id,
            "type": "capability",
            "capabilities": policy_engine.capabilities.model_dump(),
        }

    raise HTTPException(status_code=404, detail=f"Policy not found: {policy_id}")


# -------------------------------------------------------------------------
# 5. APPROVAL APIS
# -------------------------------------------------------------------------
@app.get("/approvals")
def list_approvals(
    trace_id: Optional[str] = None,
    status: Optional[str] = None,
):
    """Lists approval requests, optionally filtered by status or trace_id."""
    return [a.model_dump() for a in approval_store.list_approvals(trace_id=trace_id, status=status)]


@app.get("/approvals/{approval_id}")
def get_approval(approval_id: str):
    """Retrieves a single approval by ID."""
    approval = approval_store.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail=f"Approval not found: {approval_id}")
    return approval.model_dump()


@app.post("/approvals/{approval_id}/approve")
def approve_action(approval_id: str, body: Optional[ApprovalActionBody] = None):
    """
    Approves a pending human sign-off action.
    Transitions status to APPROVED, records APPROVAL_RESOLVED evidence, and publishes live event.
    """
    approval = approval_store.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail=f"Approval not found: {approval_id}")

    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve approval {approval_id}: current status is {approval.status.value}",
        )

    resolved_by = (body.resolved_by if body else None) or "operator"
    updated = approval_store.resolve_approval(
        approval_id=approval_id,
        status=ApprovalStatus.APPROVED,
        resolved_by=resolved_by,
    )

    # Record resolution event in evidence store
    resolve_event = AgentEvent(
        trace_id=approval.trace_id,
        span_id=approval.span_id,
        agent_id=approval.agent_id,
        session_id=approval.session_id,
        event_type=EventType.APPROVAL_RESOLVED,
        tool_name=approval.tool_name,
        input=approval.tool_arguments,
        status=EventStatus.COMPLETED,
        metadata={
            "approval_id": approval_id,
            "resolution": "APPROVED",
            "resolved_by": resolved_by,
            "comment": body.comment if body else None,
        },
    )
    rec = evidence_store.record_event(
        event=resolve_event,
        policy_id=approval.policy_id,
        policy_version=approval.policy_version,
        decision="APPROVED",
        reason=f"Action approved by {resolved_by}",
    )

    # Publish live event
    evt_dict = resolve_event.to_dict()
    evt_dict["record_id"] = rec.record_id
    evt_dict["record_hash"] = rec.record_hash
    evt_dict["decision"] = "APPROVED"
    evt_dict["reason"] = f"Action approved by {resolved_by}"
    default_event_publisher.publish(evt_dict)

    log_runtime(
        level="INFO",
        component="approval",
        message=f"Approval {approval_id} APPROVED by {resolved_by}",
        trace_id=approval.trace_id,
        span_id=approval.span_id,
        event_id=resolve_event.event_id,
        decision="APPROVED",
        tool_name=approval.tool_name,
    )

    return updated.model_dump() if updated else {}


@app.post("/approvals/{approval_id}/reject")
def reject_action(approval_id: str, body: Optional[ApprovalActionBody] = None):
    """
    Rejects a pending human sign-off action.
    Transitions status to REJECTED, records APPROVAL_RESOLVED evidence, and publishes live event.
    """
    approval = approval_store.get_approval(approval_id)
    if not approval:
        raise HTTPException(status_code=404, detail=f"Approval not found: {approval_id}")

    if approval.status != ApprovalStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject approval {approval_id}: current status is {approval.status.value}",
        )

    resolved_by = (body.resolved_by if body else None) or "operator"
    updated = approval_store.resolve_approval(
        approval_id=approval_id,
        status=ApprovalStatus.REJECTED,
        resolved_by=resolved_by,
    )

    resolve_event = AgentEvent(
        trace_id=approval.trace_id,
        span_id=approval.span_id,
        agent_id=approval.agent_id,
        session_id=approval.session_id,
        event_type=EventType.APPROVAL_RESOLVED,
        tool_name=approval.tool_name,
        input=approval.tool_arguments,
        status=EventStatus.BLOCKED,
        metadata={
            "approval_id": approval_id,
            "resolution": "REJECTED",
            "resolved_by": resolved_by,
            "comment": body.comment if body else None,
        },
    )
    rec = evidence_store.record_event(
        event=resolve_event,
        policy_id=approval.policy_id,
        policy_version=approval.policy_version,
        decision="REJECTED",
        reason=f"Action rejected by {resolved_by}",
    )

    evt_dict = resolve_event.to_dict()
    evt_dict["record_id"] = rec.record_id
    evt_dict["record_hash"] = rec.record_hash
    evt_dict["decision"] = "REJECTED"
    evt_dict["reason"] = f"Action rejected by {resolved_by}"
    default_event_publisher.publish(evt_dict)

    log_runtime(
        level="WARN",
        component="approval",
        message=f"Approval {approval_id} REJECTED by {resolved_by}",
        trace_id=approval.trace_id,
        span_id=approval.span_id,
        event_id=resolve_event.event_id,
        decision="REJECTED",
        tool_name=approval.tool_name,
    )

    return updated.model_dump() if updated else {}


# -------------------------------------------------------------------------
# 6. EVIDENCE APIS
# -------------------------------------------------------------------------
@app.get("/evidence/{trace_id}")
def get_trace_evidence(trace_id: str):
    """Retrieves all evidence records for a specific trace."""
    records = evidence_store.get_records(trace_id=trace_id)
    if not records:
        raise HTTPException(status_code=404, detail=f"No evidence found for trace: {trace_id}")
    return records


@app.get("/evidence/{trace_id}/verify")
def verify_trace_integrity(trace_id: str):
    """
    Verifies cryptographic hash chain integrity for the specified trace.
    Returns clear verification result with record counts and error list.
    """
    records = evidence_store.get_records(trace_id=trace_id)
    if not records:
        raise HTTPException(status_code=404, detail=f"No evidence found for trace: {trace_id}")

    # Also check global chain validity
    all_valid, all_errors = evidence_store.verify_integrity()

    # Verify this trace's individual records
    trace_errors = []
    for rec in records:
        calculated_hash = rec.calculate_hash()
        if rec.record_hash != calculated_hash:
            trace_errors.append(
                f"Record {rec.record_id} hash mismatch: stored={rec.record_hash}, calculated={calculated_hash}"
            )

    valid = len(trace_errors) == 0 and all_valid
    errors = trace_errors if trace_errors else all_errors

    return {
        "trace_id": trace_id,
        "valid": valid,
        "records_checked": len(records),
        "errors": errors,
        "message": "Evidence chain verified successfully" if valid else "Integrity verification failed",
    }


# -------------------------------------------------------------------------
# 7. CORRELATED LOGGING API
# -------------------------------------------------------------------------
@app.get("/logs")
def get_logs(
    trace_id: Optional[str] = None,
    event_id: Optional[str] = None,
    level: Optional[str] = None,
    limit: int = Query(default=100, le=500),
):
    """Retrieves correlated runtime logs by trace_id, event_id, or level."""
    logs = default_log_buffer.get_logs(
        trace_id=trace_id,
        event_id=event_id,
        level=level,
        limit=limit,
    )
    return [l.model_dump() for l in logs]


# -------------------------------------------------------------------------
# 8. WEBSOCKET LIVE EVENT STREAM
# -------------------------------------------------------------------------
@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket):
    """
    Live WebSocket event feed.
    Streams canonical AgentEvents to connected control-plane / dashboard clients.
    """
    await ws_manager.connect(websocket)
    try:
        # Send initial connection acknowledgment
        await websocket.send_json({
            "type": "connected",
            "message": "Connected to AgentAssure live event stream",
            "timestamp": datetime.now().isoformat(),
        })
        while True:
            # Keep socket open and listen for any client messages/pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        log_runtime(
            level="WARN",
            component="websocket",
            message=f"WebSocket connection error: {e}",
        )
        ws_manager.disconnect(websocket)


# -------------------------------------------------------------------------
# 9. BACKWARD-COMPATIBLE API V1 ROUTES
# -------------------------------------------------------------------------
@app.get("/api/v1/evidence", response_model=List[EvidenceRecord])
def legacy_get_evidence(trace_id: Optional[str] = None, session_id: Optional[str] = None):
    return evidence_store.get_records(trace_id=trace_id, session_id=session_id)


@app.get("/api/v1/evidence/verify")
def legacy_verify_integrity():
    is_valid, errors = evidence_store.verify_integrity()
    return {
        "is_valid": is_valid,
        "errors": errors,
        "status": "SECURE" if is_valid else "TAMPERED_DETECTED",
    }


@app.get("/api/v1/stats")
def legacy_stats() -> Dict:
    records = evidence_store.get_records()
    is_valid, errors = evidence_store.verify_integrity()

    decisions = Counter(r.decision for r in records if r.decision)
    by_control: Dict[str, Dict] = {}
    for r in records:
        if not r.controls or r.decision in (None, "ALLOW"):
            continue
        entry = by_control.setdefault(
            r.controls, {"citation": r.controls, "count": 0, "policies": set()}
        )
        entry["count"] += 1
        if r.policy_id:
            entry["policies"].add(r.policy_id)

    return {
        "records": len(records),
        "traces": len({r.trace_id for r in records}),
        "sessions": len({r.session_id for r in records}),
        "blocked": decisions.get("BLOCK", 0),
        "pending_approval": decisions.get("ASK", 0),
        "allowed": decisions.get("ALLOW", 0),
        "redactions": sum(r.redactions for r in records),
        "cited": sum(1 for r in records if r.controls),
        "chain": {"is_valid": is_valid, "errors": errors},
        "by_control": [
            {**v, "policies": sorted(v["policies"])}
            for v in sorted(by_control.values(), key=lambda x: -x["count"])
        ],
    }


@app.get("/api/v1/traces")
def legacy_traces() -> List[Dict]:
    return list_traces()


# Mount static console last
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="console")
