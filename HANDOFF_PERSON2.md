# AgentAssure — Person 2 → Person 3 Handoff

> **Author:** Person 2 (Operator control-plane & visualization)
> **Date:** Week 2
> **Scope:** Dashboard shell, live monitor, DAG visualization, drill-down, evidence view

---

## 1. Dashboard Start Command

```powershell
# Start the backend server (serves dashboard at http://localhost:8000)
python -m uvicorn server.api:app --reload
```

## 2. Backend Dependency / Start Command

```powershell
# Install dependencies
pip install -r requirements.txt

# (Optional) Generate demo evidence data without Groq API key
python demo/dry_run_week2.py

# Start server
python -m uvicorn server.api:app --reload
```

The dashboard loads at `http://localhost:8000`. No separate frontend build step is required.

## 3. Route Map

| Route (hash)           | View            | Status          |
| :--------------------- | :-------------- | :-------------- |
| `#overview`            | Overview        | ✅ Complete      |
| `#monitor`             | Live Monitor    | ✅ Complete      |
| `#traces`              | Trace List      | ✅ Complete      |
| `#trace-detail/{id}`   | Trace Detail + DAG | ✅ Complete   |
| `#policies`            | Policies        | ⚙ Person 3     |
| `#approvals`           | Approvals       | ⚙ Person 3     |
| `#evidence`            | Evidence        | ✅ Complete      |

### Routes Ready for Person 3

- **`#policies`** — Currently displays loaded policy rules and capability boundaries (read-only from `GET /policies`). Person 3 should add: edit controls, action toggling (BLOCK↔ASK), condition editing, version management, and policy reload.

- **`#approvals`** — Currently displays approval list (read-only from `GET /approvals`). Person 3 should add: approve/reject buttons calling `POST /approvals/{id}/approve` and `POST /approvals/{id}/reject`, approval history filtering, and real-time updates.

## 4. WebSocket Behaviour

- **Endpoint:** `ws://<host>:<port>/ws/events`
- **Connection greeting:** `{"type": "connected", "message": "...", "timestamp": "..."}`
- **Keepalive:** Client sends `"ping"`, server responds `"pong"` (every 25s)
- **Event payload:** Full canonical event dict including `event_id`, `trace_id`, `span_id`, `parent_span_id`, `tool_name`, `input`, `output`, `decision`, `policy_id`, `reason`, `controls`, `record_id`, `record_hash`
- **Auto-reconnect:** Exponential backoff from 1s to 30s max
- **Deduplication:** Events tracked by `event_id` in `Set`

## 5. Component Structure

```
server/static/index.html    ← Single-file SPA (zero-build, vanilla JS)
│
├── CSS (~320 lines)
│   ├── Root variables & reset
│   ├── Layout (sidebar, main, drawer)
│   ├── Navigation, cards, tables, chips
│   ├── DAG visualization styles
│   ├── Timeline styles
│   └── Drawer detail panel styles
│
├── HTML (~50 lines)
│   ├── Sidebar navigation with WebSocket status indicator
│   ├── Main content container (views rendered here)
│   └── Drawer overlay + panel
│
└── JavaScript (~750 lines)
    ├── S (State)           — Global state object
    ├── API                 — REST client wrapper
    ├── WS                  — WebSocket manager with auto-reconnect
    ├── navigate()          — Hash-based router
    ├── DAG                 — Build/layout/render from span relationships
    ├── Views               — Page renderers (overview, monitor, traces, etc.)
    └── Drawer              — Node drill-down panel
```

## 6. Trace / DAG Data Model

Events from `GET /traces/{id}/events` contain:

```json
{
  "event_id": "evt_...",
  "trace_id": "tr_...",
  "span_id": "sp_...",
  "parent_span_id": "sp_..." | null,
  "agent_id": "...",
  "session_id": "...",
  "event_type": "tool_call|tool_result|policy_decision|approval_requested|...",
  "timestamp": "ISO 8601",
  "tool_name": "...",
  "input": {...},
  "output": {...},
  "decision": "ALLOW|BLOCK|ASK|null",
  "policy_id": "...|null",
  "policy_version": 1,
  "reason": "...",
  "controls": "ISO/IEC 42001 A.9.4 | EU AI Act Art. 14",
  "record_hash": "sha256..."
}
```

### DAG Construction Logic

1. Group events by `span_id`
2. For each span, aggregate: tool_name, highest-priority decision (BLOCK > ASK > ALLOW), status, input/output
3. Build parent→child edges from `parent_span_id`
4. If `parent_span_id` references a span not in the events, create a synthetic "Agent" root node
5. Layout using layered BFS positioning
6. Render as inline SVG with click handlers

## 7. Node Detail Data Model

When a DAG node is clicked, the drawer shows:

| Field        | Source                                       |
| :----------- | :------------------------------------------- |
| Tool name    | `span.tool_name`                             |
| Decision     | Highest-priority decision from span events   |
| Execution    | `NOT EXECUTED` if BLOCK, `PENDING` if ASK    |
| Arguments    | `span.input` (from first tool_call event)    |
| Output       | `span.output` (from tool_result event)       |
| Policy ID    | From the decision event                      |
| Policy Ver.  | From the decision event                      |
| Reason       | From the decision event                      |
| Controls     | Compliance citation from the decision event  |
| Logs         | `GET /logs?trace_id=...` filtered by span_id |
| Evidence     | `GET /evidence/{trace_id}` filtered by span_id |

## 8. Completed Screens

| Screen         | Description                                                  |
| :------------- | :----------------------------------------------------------- |
| Overview       | Metric cards (actions, traces, blocked, approvals, PII, allowed), control coverage bars, recent traces, evidence chain status |
| Live Monitor   | Real-time WebSocket event stream with connection indicator, timeline rows showing timestamp/tool/decision/trace |
| Traces         | Historical trace list with ID, agent, session, start time, duration, event count, blocked count, approval count, status |
| Trace Detail   | Trace summary, interactive SVG DAG from span relationships, event timeline table, node click → drawer |
| Evidence       | Trace selection → evidence records table with integrity verification status (VERIFIED/FAILED + record count) |

## 9. Screens Ready for Person 3

| Screen    | What Exists                          | What Person 3 Should Add                     |
| :-------- | :----------------------------------- | :------------------------------------------- |
| Policies  | Read-only list of rules/capabilities | Edit, toggle, version, reload                |
| Approvals | Read-only list of approvals          | Approve/reject buttons, real-time updates    |

### Integration Points for Person 3

- The `Views.policies()` and `Views.approvals()` functions in `index.html` are clearly separated and can be extended.
- The placeholder banners mark Person 3 scope visually.
- Backend endpoints `POST /approvals/{id}/approve` and `POST /approvals/{id}/reject` already exist and work.
- Policy editing would need a new backend endpoint (currently policies load from YAML on server start).

## 10. Backend / UI Integration Issues

- **No known integration issues.** All REST endpoints match their documented contracts.
- The WebSocket event payload from the SDK publisher closely matches the REST event format, enabling seamless merge.
- Evidence verification works end-to-end (`GET /evidence/{id}/verify`).
- Correlated log queries work via `GET /logs?trace_id=...&event_id=...`.

## 11. Primary Demo Steps

### Scenario A — Normal Execution
```
1. Start server: python -m uvicorn server.api:app --reload
2. Run dry run: python demo/dry_run_week2.py
3. Open http://localhost:8000
4. Navigate to Traces → click a COMPLETED trace
5. DAG shows all tools with ✓ ALLOW
6. Click any node → drawer shows arguments, policy, logs, evidence
```

### Scenario B — Block
```
1. Look for trace with BLOCKED status
2. DAG shows approve_loan node with ✗ BLOCK
3. Node label shows "BLOCK · NOT EXECUTED"
4. Click node → drawer shows reason, policy FIN-001, NOT EXECUTED warning
5. Logs show "Policy matched" and "Underlying tool execution skipped"
6. Evidence shows the decision record
```

### Scenario C — ASK / Pending Approval
```
1. Look for trace with PENDING_APPROVAL status
2. DAG shows approve_loan node with ⏸ ASK · PENDING
3. Click node → drawer shows pending approval notice
4. Navigate to Approvals → see pending approval record
5. (Person 3 will add approve/reject buttons here)
```

## 12. Known Limitations

1. **Overview metrics refresh:** Overview page auto-refreshes every 5 seconds via polling. This could be changed to WebSocket-driven updates by Person 3.
2. **DAG layout:** Uses simple BFS layered layout. Very wide trees may require horizontal scrolling.
3. **Log correlation:** Fetches all trace logs then filters client-side by span_id. For very large traces, a server-side span_id filter could improve performance.
4. **No policy hot-reload:** Policies are loaded from YAML at server startup. Runtime policy changes require server restart (Person 3 scope).

## 13. What Remains for Person 3

```
[ ] Policy editing UI (create/edit/delete/toggle rules)
[ ] Policy version management
[ ] Policy hot-reload without server restart
[ ] Approval management UI (approve/reject buttons)
[ ] Approval notification/alerts
[ ] Real-time approval state updates in DAG
[ ] Policy change → different governance result demo (Scenario D)
[ ] Full approval workflow demo end-to-end in UI
```

## 14. Test Suite

```powershell
# Run all tests
pytest tests/ -v
```

### Dashboard-specific tests (`tests/test_week2_dashboard.py`):
- `test_dashboard_html_loads` — HTML loads with expected markers
- `test_api_health_check` — Backend health endpoint
- `test_websocket_connects` — WebSocket greeting
- `test_live_event_delivery` — Tool execution → WebSocket events
- `test_dag_parent_child_relationships` — Span hierarchy correctness
- `test_dag_different_topology` — Nested trace contexts
- `test_drilldown_event_policy_logs_correlated` — Cross-endpoint correlation
- `test_historical_trace_reconstruction` — REST-only state reconstruction
- `test_block_shows_reason_and_not_executed` — BLOCK consistency
- `test_ask_shows_pending_state` — ASK/PENDING visibility
- `test_websocket_resilience_rest_reconstruction` — Disconnect/reconnect
- `test_overview_metrics_from_real_data` — Stats grounded in evidence
