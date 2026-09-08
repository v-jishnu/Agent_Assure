# AgentAssure — Week 2 API Data Contracts & Control-Plane Reference

> **Author:** Person 1 (Runtime & Backend Foundation Owner)  
> **Audience:** Person 2 (Dashboard & Visualization Owner)  
> **Version:** Week 2 Baseline (v0.2.0)  
> **Status:** Verified & Stable

---

## 1. Quick Start & Server Details

- **Backend Base URL:** `http://127.0.0.1:8000`
- **Live WebSocket URL:** `ws://127.0.0.1:8000/ws/events`
- **Interactive OpenAPI Documentation:** `http://127.0.0.1:8000/docs`
- **Default Database:** `demo_evidence.db` (override via env var `AGENTASSURE_DB`)

### Launch Command
```powershell
python -m uvicorn server.api:app --reload --port 8000
```

---

## 2. Architecture & Invariants for Person 2

1. **Evidence Store is Source of Truth**: All historical views (`/traces`, `/traces/{trace_id}/events`, `/evidence/{trace_id}`) read directly from SQLite.
2. **WebSocket is Live Streaming Only**: Disconnected UI clients will not cause event loss. When a dashboard connects or reconnects, it can fetch historical state via REST and subscribe to `/ws/events` for real-time deltas.
3. **DAG Construction**: Events carry `trace_id`, `span_id`, and `parent_span_id`. Person 2 builds the execution graph dynamically from these pointers.
4. **Governance Invariants**:
   - `ALLOW`: tool function executes, `TOOL_RESULT` recorded.
   - `BLOCK`: tool function strictly intercepted, `POLICY_DECISION` recorded, tool never executes.
   - `ASK`: tool function paused, `APPROVAL_REQUESTED` recorded, pending approval created. Tool only executes after `POST /approvals/{id}/approve`.

---

## 3. REST API Contracts

### A. Health Check
`GET /health`
```json
{
  "status": "ok",
  "service": "AgentAssure Control Plane",
  "version": "0.2.0",
  "database": "demo_evidence.db"
}
```

---

### B. Trace List API
`GET /traces`

Provides aggregated trace runs, newest first.

```json
[
  {
    "trace_id": "tr_1907572740fc",
    "agent_id": "loan-agent",
    "session_id": "sess_compliant_001",
    "started_at": "2026-09-08T05:50:12.100000+00:00",
    "ended_at": "2026-09-08T05:50:12.850000+00:00",
    "duration_ms": 750.0,
    "status": "COMPLETED",
    "events_count": 8,
    "blocked_count": 0,
    "approval_count": 0,
    "tools": ["get_customer", "check_credit", "calculate_loan", "approve_loan"]
  },
  {
    "trace_id": "tr_428198fbb02a",
    "agent_id": "loan-agent",
    "session_id": "sess_violation_002",
    "started_at": "2026-09-08T05:50:10.000000+00:00",
    "ended_at": "2026-09-08T05:50:10.400000+00:00",
    "duration_ms": 400.0,
    "status": "BLOCKED",
    "events_count": 3,
    "blocked_count": 1,
    "approval_count": 0,
    "tools": ["approve_loan"]
  }
]
```

---

### C. Trace Detail API
`GET /traces/{trace_id}`

Status: `200 OK` or `404 Not Found`

```json
{
  "trace_id": "tr_1907572740fc",
  "agent_id": "loan-agent",
  "session_id": "sess_compliant_001",
  "started_at": "2026-09-08T05:50:12.100000+00:00",
  "ended_at": "2026-09-08T05:50:12.850000+00:00",
  "duration_ms": 750.0,
  "status": "COMPLETED",
  "event_count": 8,
  "blocked_count": 0,
  "approval_count": 0,
  "decisions": {
    "ALLOW": 4
  },
  "tools": ["approve_loan", "calculate_loan", "check_credit", "get_customer"]
}
```

---

### D. Trace Events (DAG Telemetry)
`GET /traces/{trace_id}/events`

Status: `200 OK` or `404 Not Found`  
Returns canonical events preserving parent-child telemetry for DAG rendering:

```json
[
  {
    "event_id": "evt_b12398401a89",
    "record_id": "rec_evt_b12398401a89",
    "trace_id": "tr_1907572740fc",
    "span_id": "sp_5820bb291a01",
    "parent_span_id": null,
    "agent_id": "loan-agent",
    "session_id": "sess_compliant_001",
    "event_type": "tool_call",
    "timestamp": "2026-09-08T05:50:12.100000+00:00",
    "tool_name": "get_customer",
    "input": {
      "customer_id": "CUST-101"
    },
    "output": null,
    "decision": "ALLOW",
    "policy_id": null,
    "policy_version": null,
    "reason": "No restrictive policies triggered; action permitted",
    "controls": null,
    "redactions": 0,
    "risk_score": 0.0,
    "previous_hash": "0000000000000000000000000000000000000000000000000000000000000000",
    "record_hash": "a4f89d97a9f0e4b9c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4"
  },
  {
    "event_id": "evt_c920194819aa",
    "record_id": "rec_evt_c920194819aa",
    "trace_id": "tr_1907572740fc",
    "span_id": "sp_5820bb291a01",
    "parent_span_id": null,
    "agent_id": "loan-agent",
    "session_id": "sess_compliant_001",
    "event_type": "tool_result",
    "timestamp": "2026-09-08T05:50:12.150000+00:00",
    "tool_name": "get_customer",
    "input": {
      "customer_id": "CUST-101"
    },
    "output": {
      "customer_id": "CUST-101",
      "name": "Jane Doe",
      "credit_score": 750
    },
    "decision": "ALLOW",
    "policy_id": null,
    "policy_version": null,
    "reason": "No restrictive policies triggered; action permitted",
    "controls": null,
    "redactions": 0,
    "risk_score": 0.0,
    "previous_hash": "a4f89d97a9f0e4b9c1d2e3f4a5b6c7d8e9f0a1b2c3d4e5f6a7b8c9d0e1f2a3b4",
    "record_hash": "b5e90d18b0f1e5c0d2e4f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6"
  }
]
```

---

### E. Single Event API
`GET /events/{event_id}`

Status: `200 OK` or `404 Not Found`  
Returns the single canonical event projection matching `event_id`.

---

### F. Policies API
`GET /policies`

```json
{
  "rules": [
    {
      "id": "FIN-001",
      "name": "Loan approval limit",
      "description": "Block loan approval requests exceeding ₹500,000.",
      "version": 1,
      "severity": "high",
      "action": "BLOCK",
      "mode": "enforce",
      "scope": {
        "agent": "loan-agent",
        "environment": "demo",
        "tools": ["approve_loan"]
      },
      "condition": {
        "field": "amount",
        "operator": "gt",
        "value": 500000
      },
      "controls": {
        "iso42001": "A.9.4",
        "eu_ai_act": "Art. 14",
        "note": "Intended use of the AI system and human oversight."
      }
    }
  ],
  "capabilities": {
    "allowed": ["get_customer", "check_credit", "calculate_loan"],
    "approval_required": ["transfer_money"],
    "forbidden": ["delete_customer", "delete_database", "export_customer_data"]
  },
  "total_rules": 3
}
```

`GET /policies/{policy_id}`
Returns details for a specific policy (e.g. `FIN-001`).

---

### G. Approvals API
`GET /approvals?status=PENDING&trace_id=tr_123`

```json
[
  {
    "approval_id": "appr_7c4190fa2b01",
    "trace_id": "tr_1907572740fc",
    "span_id": "sp_5820bb291a01",
    "event_id": "evt_b12398401a89",
    "agent_id": "loan-agent",
    "session_id": "sess_approval_003",
    "tool_name": "approve_loan",
    "tool_arguments": {
      "customer_id": "CUST-102",
      "amount": 400000
    },
    "policy_id": "FIN-002",
    "policy_version": 1,
    "reason": "Policy 'High-value loan human approval threshold' (FIN-002 v1) matched: field 'amount'=400000 gt 300000",
    "created_at": "2026-09-08T05:50:15.000000+00:00",
    "status": "PENDING",
    "resolved_at": null,
    "resolved_by": null
  }
]
```

`GET /approvals/{approval_id}`
Returns the approval record matching `approval_id`.

`POST /approvals/{approval_id}/approve`
Request Body (optional):
```json
{
  "resolved_by": "lead_credit_officer",
  "comment": "Income verified via bank statement"
}
```
Response:
```json
{
  "approval_id": "appr_7c4190fa2b01",
  "trace_id": "tr_1907572740fc",
  "span_id": "sp_5820bb291a01",
  "status": "APPROVED",
  "resolved_at": "2026-09-08T05:52:00.000000+00:00",
  "resolved_by": "lead_credit_officer"
}
```

`POST /approvals/{approval_id}/reject`
Request Body (optional):
```json
{
  "resolved_by": "lead_credit_officer",
  "comment": "DTI ratio too high"
}
```
Response:
```json
{
  "approval_id": "appr_7c4190fa2b01",
  "status": "REJECTED",
  "resolved_at": "2026-09-08T05:52:00.000000+00:00",
  "resolved_by": "lead_credit_officer"
}
```

---

### H. Evidence & Cryptographic Verification API
`GET /evidence/{trace_id}`
Returns raw, tamper-evident evidence records for that trace.

`GET /evidence/{trace_id}/verify`
Verifies SHA-256 hash chaining for the trace records:
```json
{
  "trace_id": "tr_1907572740fc",
  "valid": true,
  "records_checked": 8,
  "errors": [],
  "message": "Evidence chain verified successfully"
}
```
If tampered:
```json
{
  "trace_id": "tr_1907572740fc",
  "valid": false,
  "records_checked": 8,
  "errors": [
    "Record rec_evt_b12398401a89 hash mismatch: stored=abc, calculated=xyz"
  ],
  "message": "Integrity verification failed"
}
```

---

### I. Correlated Structured Logs API
`GET /logs?trace_id=tr_1907572740fc&limit=50`

Query Parameters:
- `trace_id` (optional): filter by trace
- `event_id` (optional): filter by event
- `level` (optional): `INFO`, `WARN`, `ERROR`, `DEBUG`
- `limit` (default 100, max 500)

```json
[
  {
    "timestamp": "2026-09-08T05:50:12.100000+00:00",
    "level": "INFO",
    "component": "runtime",
    "message": "Tool call requested: get_customer",
    "trace_id": "tr_1907572740fc",
    "span_id": "sp_5820bb291a01",
    "event_id": null,
    "policy_id": null,
    "policy_version": null,
    "decision": null,
    "tool_name": "get_customer"
  },
  {
    "timestamp": "2026-09-08T05:50:12.105000+00:00",
    "level": "INFO",
    "component": "policy",
    "message": "Pre-execution policy evaluation started",
    "trace_id": "tr_1907572740fc",
    "span_id": "sp_5820bb291a01",
    "event_id": "evt_b12398401a89",
    "tool_name": "get_customer"
  },
  {
    "timestamp": "2026-09-08T05:50:12.120000+00:00",
    "level": "INFO",
    "component": "enforcement",
    "message": "Action permitted: executing tool 'get_customer'",
    "trace_id": "tr_1907572740fc",
    "span_id": "sp_5820bb291a01",
    "event_id": "evt_b12398401a89",
    "decision": "ALLOW",
    "tool_name": "get_customer"
  }
]
```

---

## 4. Live WebSocket Contract
`WS /ws/events`

### On Connect
Server immediately sends:
```json
{
  "type": "connected",
  "message": "Connected to AgentAssure live event stream",
  "timestamp": "2026-09-08T05:50:00.000000"
}
```

### Live Event Broadcast Payload
Every runtime event finalized in the evidence store is immediately delivered across the WebSocket:

```json
{
  "event_id": "evt_7f81902ba102",
  "record_id": "rec_evt_7f81902ba102",
  "trace_id": "tr_1907572740fc",
  "span_id": "sp_5820bb291a01",
  "parent_span_id": null,
  "agent_id": "loan-agent",
  "session_id": "sess_compliant_001",
  "event_type": "tool_call",
  "timestamp": "2026-09-08T05:50:12.100000+00:00",
  "tool_name": "approve_loan",
  "input": {
    "customer_id": "CUST-101",
    "amount": 250000
  },
  "output": null,
  "status": "started",
  "metadata": {},
  "record_hash": "8f9021a8b417c8...",
  "decision": "ALLOW",
  "policy_id": null,
  "policy_version": null,
  "reason": "No restrictive policies triggered; action permitted",
  "controls": null
}
```

Client keep-alive: clients may send `"ping"` and server replies `"pong"`.

---

## 5. Scope Boundaries

### What Person 1 built
- SQLite tables for evidence and approvals.
- Correlated structured runtime logger and query endpoint (`/logs`).
- In-process event publisher (`EventPublisher`) with non-blocking error isolation.
- Active live WebSocket endpoint (`/ws/events`).
- Complete REST endpoints for traces, canonical events, policies, approvals, and evidence.
- Approval state machine with governance-guaranteed tool execution upon approval.

### What Person 2 must not modify
- The canonical `AgentEvent` schema and hash-chain algorithm in `agentassure/evidence.py`.
- Pre-execution interception semantics (`ALLOW` / `BLOCK` / `ASK`).
- The SQLite table layout or hash-generation rules.
