"""
'agentassure run <command>' — governed subprocess runner.

Workflow:
  1. Load agentassure.yaml config
  2. Inject governance env vars into the subprocess environment
  3. Spawn the subprocess and stream its output live
  4. After exit, query the evidence DB for traces created during the run
  5. Render the boxed CLI summary
  6. Export each new trace as a JSON file
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from agentassure.cli.display import console, render_run_summary
from agentassure.config import (
    AgentAssureConfig,
    get_db_path,
    get_policy_path,
    load_config,
)


def _existing_trace_ids(db_path: str) -> Set[str]:
    """Return the set of trace_ids already in the DB (pre-run snapshot)."""
    try:
        from agentassure.evidence import EvidenceStore
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        store = EvidenceStore(db_path)
        return {r.trace_id for r in store.get_records()}
    except Exception:
        return set()


def _build_events_for_trace(db_path: str, trace_id: str) -> List[Dict[str, Any]]:
    """Return event dicts for a single trace from the evidence DB."""
    try:
        from agentassure.evidence import EvidenceStore
        store = EvidenceStore(db_path)
        records = store.get_records(trace_id=trace_id)
        events = []
        for r in records:
            inp = r.input
            out = r.output
            try:
                if inp and inp.startswith(("{", "[")):
                    inp = json.loads(inp)
            except Exception:
                pass
            try:
                if out and out.startswith(("{", "[")):
                    out = json.loads(out)
            except Exception:
                pass
            events.append({
                "event_id":       r.event_id,
                "trace_id":       r.trace_id,
                "span_id":        r.span_id,
                "parent_span_id": r.parent_span_id,
                "event_type":     r.event_type,
                "timestamp":      r.timestamp,
                "tool_name":      r.tool_name,
                "decision":       r.decision,
                "policy_id":      r.policy_id,
                "policy_version": r.policy_version,
                "reason":         r.reason,
                "controls":       r.controls,
                "input":          inp,
                "output":         out,
                "record_hash":    r.record_hash,
                # cost fields (may be None on old records)
                "model":          getattr(r, "model", None),
                "input_tokens":   getattr(r, "input_tokens", 0) or 0,
                "output_tokens":  getattr(r, "output_tokens", 0) or 0,
                "cost_usd":       getattr(r, "cost_usd", 0.0) or 0.0,
            })
        return events
    except Exception:
        return []


def _build_governance_summary(events: List[Dict[str, Any]]) -> Dict[str, int]:
    blocked  = sum(1 for e in events if e.get("decision") == "BLOCK")
    pending  = sum(1 for e in events if e.get("decision") == "ASK")
    approved = sum(1 for e in events if e.get("decision") == "APPROVED")
    allowed  = sum(1 for e in events if e.get("decision") == "ALLOW")
    return {
        "blocked":  blocked,
        "pending":  pending,
        "approved": approved,
        "allowed":  allowed,
    }


def _build_cost_summary(events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Aggregate cost across all LLM events in a trace."""
    total_in  = sum(e.get("input_tokens",  0) or 0 for e in events)
    total_out = sum(e.get("output_tokens", 0) or 0 for e in events)
    total_usd = sum(e.get("cost_usd",      0) or 0 for e in events)
    # Pick the most recently seen model name
    models = [e.get("model") for e in events if e.get("model")]
    model  = models[-1] if models else None

    if not (total_in or total_out or total_usd):
        return None

    return {
        "model":         model or "unknown",
        "input_tokens":  total_in,
        "output_tokens": total_out,
        "total_tokens":  total_in + total_out,
        "cost_usd":      round(total_usd, 6),
        "llm_calls":     len([e for e in events if e.get("model")]),
    }


def _compute_duration_ms(events: List[Dict[str, Any]]) -> float:
    timestamps = [e.get("timestamp") for e in events if e.get("timestamp")]
    if len(timestamps) < 2:
        return 0.0
    try:
        ts_sorted = sorted(timestamps)
        t0 = datetime.fromisoformat(ts_sorted[0])
        t1 = datetime.fromisoformat(ts_sorted[-1])
        return round(abs((t1 - t0).total_seconds() * 1000), 1)
    except Exception:
        return 0.0


def _export_json(
    trace_id: str,
    agent_id: str,
    events: List[Dict[str, Any]],
    governance: Dict[str, int],
    cost: Optional[Dict[str, Any]],
    duration_ms: float,
    json_dir: str,
) -> Optional[Path]:
    """Write a JSON trace file and return its path."""
    try:
        out_dir = Path(json_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "trace_id":   trace_id,
            "agent_id":   agent_id,
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "duration_ms": duration_ms,
            "governance":  governance,
            "cost":        cost,
            "events":      events,
        }
        dest = out_dir / f"{trace_id}.json"
        dest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return dest
    except Exception as exc:
        console.print(f"[dim]Warning: could not write JSON trace: {exc}[/dim]")
        return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_governed(
    *,
    command: List[str],
    config_path: Optional[str] = None,
    no_summary: bool = False,
    json_out_override: Optional[str] = None,
) -> None:
    """
    Spawn *command* as a subprocess with AgentAssure governance env vars set,
    then render a post-run summary.
    """
    # ── Load config ──────────────────────────────────────────────────────
    config: AgentAssureConfig = load_config(config_path)
    db_path     = get_db_path(config)
    policy_path = get_policy_path(config)
    json_dir    = json_out_override or (
        config.output.json if config.output.json else ".agentassure/traces/"
    )

    # Ensure data directory exists before the subprocess writes to it
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    # ── Snapshot existing traces ─────────────────────────────────────────
    before_ids = _existing_trace_ids(db_path)

    # ── Build subprocess environment ─────────────────────────────────────
    env = os.environ.copy()
    env["AGENTASSURE_ACTIVE"]      = "1"
    env["AGENTASSURE_DB"]          = str(Path(db_path).resolve())
    env["AGENTASSURE_AGENT_ID"]    = config.agent_id
    env["AGENTASSURE_ENVIRONMENT"] = config.environment
    if policy_path:
        env["AGENTASSURE_POLICY"] = str(Path(policy_path).resolve())

    # ── Inform user ──────────────────────────────────────────────────────
    console.print(
        f"[dim]AgentAssure[/dim] [cyan]>[/cyan] "
        f"[dim]{' '.join(command)}[/dim]"
    )
    if policy_path:
        console.print(f"[dim]  Policy: {policy_path}[/dim]")
    console.print(f"[dim]  DB:     {db_path}[/dim]")
    console.print("")

    # ── Run subprocess ───────────────────────────────────────────────────
    try:
        result = subprocess.run(
            command,
            env=env,
            # Inherit stdin/stdout/stderr so the agent's output is visible live
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        exit_code = result.returncode
    except FileNotFoundError as exc:
        console.print(f"[red]Error:[/red] Command not found — {exc}")
        sys.exit(1)

    # ── Collect new traces ───────────────────────────────────────────────
    after_ids  = _existing_trace_ids(db_path)
    new_ids    = after_ids - before_ids

    if not new_ids:
        console.print(
            "\n[dim]No AgentAssure traces recorded during this run.[/dim]\n"
            "[dim]Make sure your agent uses @aa.govern on its tool functions.[/dim]"
        )
        sys.exit(exit_code)

    # ── Render summary for each new trace (usually just one) ─────────────
    for trace_id in sorted(new_ids):
        events      = _build_events_for_trace(db_path, trace_id)
        governance  = _build_governance_summary(events)
        cost        = _build_cost_summary(events)
        duration_ms = _compute_duration_ms(events)

        if not no_summary:
            console.print("")
            render_run_summary(
                trace_id=trace_id,
                agent_id=config.agent_id,
                duration_ms=duration_ms,
                events=events,
                governance=governance,
                cost=cost,
                policy_resolution="deterministic",
            )

        # ── Export JSON ──────────────────────────────────────────────────
        dest = _export_json(
            trace_id=trace_id,
            agent_id=config.agent_id,
            events=events,
            governance=governance,
            cost=cost,
            duration_ms=duration_ms,
            json_dir=json_dir,
        )
        if dest:
            console.print(f"[dim]  Trace JSON: {dest}[/dim]")

    sys.exit(exit_code)
