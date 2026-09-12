"""
AgentAssure CLI — the primary user interface.

Entry point registered in pyproject.toml as:
    agentassure = "agentassure.cli.main:app"

Command groups:
    agentassure init              scaffold config in a repo
    agentassure run <cmd>         governed subprocess runner
    agentassure policy ...        list / validate / show rules
    agentassure trace ...         list / show traces
    agentassure approvals ...     list / approve / reject
    agentassure ingest <file>     extract policies from a document
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

import click

from agentassure.cli.display import console


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config(config_path=None):
    from agentassure.config import load_config
    return load_config(config_path)


def _get_stores(config=None, db=None):
    """Return (EvidenceStore, ApprovalStore) from config or explicit db path."""
    from agentassure.config import get_db_path
    from agentassure.evidence import EvidenceStore
    from agentassure.approval import ApprovalStore
    db_path = db or get_db_path(config)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    return EvidenceStore(db_path), ApprovalStore(db_path)


def _get_policy_engine(config=None, policy=None):
    from agentassure.config import get_policy_path
    from agentassure.policy import PolicyEngine
    policy_path = policy or get_policy_path(config)
    if policy_path and Path(policy_path).exists():
        return PolicyEngine.load_from_yaml(policy_path)
    return PolicyEngine()


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(version="0.2.0", package_name="agentassure")
def app():
    """
    AgentAssure — lightweight runtime governance for AI agents.

    \b
    Quick start:
      agentassure init                     scaffold config in current repo
      agentassure run python my_agent.py   run with governance active
      agentassure trace list               inspect recent traces
      agentassure policy list              see active rules

    Install:
      pip install git+https://github.com/v-jishnu/Agent_Assure.git

    Docs:
      https://github.com/v-jishnu/Agent_Assure/blob/main/USER_GUIDE.md
    """
    pass


# ─── INIT ────────────────────────────────────────────────────────────────────

@app.command()
@click.option("--agent-id",    default=None, help="Agent name (e.g. loan-agent)")
@click.option("--environment", default=None,
              type=click.Choice(["development", "staging", "production"]),
              help="Target environment")
@click.option("--force", is_flag=True, help="Overwrite existing agentassure.yaml")
def init(agent_id, environment, force):
    """Scaffold AgentAssure configuration in the current repository."""
    from agentassure.cli.init_cmd import run_init
    run_init(agent_id=agent_id, environment=environment, force=force)


# ─── RUN ─────────────────────────────────────────────────────────────────────

@app.command(
    name="run",
    context_settings={"ignore_unknown_options": True, "allow_extra_args": True},
)
@click.argument("command", nargs=-1, required=True)
@click.option("--config", "-c", default=None, help="Path to agentassure.yaml")
@click.option("--no-summary", is_flag=True, help="Skip post-run CLI summary")
@click.option("--json-out",   default=None, help="Override JSON output directory")
def run_cmd(command, config, no_summary, json_out):
    """
    Run a command with AgentAssure governance active.

    \b
    Examples:
      agentassure run python my_agent.py
      agentassure run python -m my_package.agent --flag
      agentassure run node agent.js
    """
    from agentassure.cli.runner import run_governed
    run_governed(
        command=list(command),
        config_path=config,
        no_summary=no_summary,
        json_out_override=json_out,
    )


# ─── POLICY ──────────────────────────────────────────────────────────────────

@app.group()
def policy():
    """Manage and inspect governance policies."""
    pass


@policy.command("list")
@click.option("--policy", "-p", default=None, help="Explicit path to policy YAML")
@click.option("--config", "-c", default=None)
def policy_list(policy, config):
    """List all active policy rules and capability boundaries."""
    from agentassure.cli.display import render_policy_table
    cfg    = _load_config(config)
    engine = _get_policy_engine(cfg, policy=policy)
    rules  = [r.model_dump() for r in engine.rules]
    if not rules:
        console.print(
            "[dim]No policy rules loaded.[/dim]\n"
            "[dim]Hint: run 'agentassure init' to create a policy file.[/dim]"
        )
    else:
        render_policy_table(rules)

    caps = engine.capabilities
    if caps.allowed or caps.approval_required or caps.forbidden:
        console.print("")
        if caps.allowed:
            console.print(f"  [green]Allowed:[/green]           {', '.join(caps.allowed)}")
        if caps.approval_required:
            console.print(f"  [yellow]Approval required:[/yellow] {', '.join(caps.approval_required)}")
        if caps.forbidden:
            console.print(f"  [red]Forbidden:[/red]         {', '.join(caps.forbidden)}")


@policy.command("validate")
@click.argument("file", required=False, default=None, type=click.Path(exists=True))
@click.option("--policy", "-p", default=None, help="Path to policy YAML")
@click.option("--config", "-c", default=None)
def policy_validate(file, policy, config):
    """Validate a policy YAML file against the governance schema."""
    import yaml as _yaml
    from agentassure.config import get_policy_path
    from agentassure.policy import validate_policy_rule

    target = file or policy
    if not target:
        cfg = _load_config(config)
        target = get_policy_path(cfg)

    if not target or not Path(target).exists():
        console.print("[red]No policy file found to validate.[/red]")
        sys.exit(1)

    with open(target, encoding="utf-8") as f:
        data = _yaml.safe_load(f) or {}

    rules = data.get("policies", [])
    if not rules:
        console.print("[yellow]No policy rules found in file.[/yellow]")
        return

    all_valid = True
    for rule in rules:
        result = validate_policy_rule(rule)
        rid = rule.get("id", "unknown")
        if result["valid"]:
            console.print(f"  [green]✓[/green] {rid} — valid")
        else:
            all_valid = False
            console.print(f"  [red]✗[/red] {rid} — invalid")
            for err in result["errors"]:
                console.print(f"      [red]→[/red] {err}")

    if all_valid:
        console.print(f"\n[green]All {len(rules)} rule(s) are valid.[/green]")
    else:
        console.print("\n[red]Validation failed. Fix errors above before deploying.[/red]")
        sys.exit(1)


@policy.command("show")
@click.argument("policy_id")
def policy_show(policy_id):
    """Show the full definition of a single policy rule."""
    cfg    = _load_config()
    engine = _get_policy_engine(cfg)
    rule   = engine.get_rule(policy_id)
    if not rule:
        console.print(f"[red]Policy '{policy_id}' not found.[/red]")
        sys.exit(1)
    console.print_json(json.dumps(rule.model_dump(), indent=2, default=str))


# ─── TRACE ───────────────────────────────────────────────────────────────────

@app.group()
def trace():
    """Inspect agent execution traces."""
    pass


@trace.command("list")
@click.option("--limit", "-n", default=20, show_default=True, help="Max traces to show")
@click.option("--db", default=None, help="Explicit path to SQLite database")
@click.option("--config", "-c", default=None)
def trace_list(limit, db, config):
    """List recent agent execution traces."""
    from datetime import datetime
    from agentassure.cli.display import render_trace_list

    cfg                  = _load_config(config)
    ev_store, _ap_store  = _get_stores(cfg, db=db)
    records              = ev_store.get_records()

    grouped: dict = {}
    for r in records:
        g = grouped.setdefault(r.trace_id, {
            "trace_id":     r.trace_id,
            "agent_id":     r.agent_id,
            "started_at":   r.timestamp,
            "ended_at":     r.timestamp,
            "events_count": 0,
            "blocked_count":  0,
            "approval_count": 0,
            "status":       "COMPLETED",
            "_timestamps":  [],
            "_decisions":   [],
        })
        g["events_count"] += 1
        g["_timestamps"].append(r.timestamp)
        if r.decision:
            g["_decisions"].append(r.decision)
        if r.decision == "BLOCK":
            g["blocked_count"] += 1
        elif r.decision == "ASK":
            g["approval_count"] += 1

    result = []
    for g in grouped.values():
        ts       = sorted(g.pop("_timestamps"))
        decisions = g.pop("_decisions")
        g["started_at"] = ts[0]
        g["ended_at"]   = ts[-1]
        try:
            t0 = datetime.fromisoformat(ts[0])
            t1 = datetime.fromisoformat(ts[-1])
            g["duration_ms"] = round(abs((t1 - t0).total_seconds() * 1000), 1)
        except Exception:
            g["duration_ms"] = 0
        g["status"] = (
            "BLOCKED" if "BLOCK" in decisions
            else "PENDING_APPROVAL" if "ASK" in decisions
            else "COMPLETED"
        )
        result.append(g)

    result.sort(key=lambda x: x["started_at"], reverse=True)
    render_trace_list(result[:limit])


@trace.command("show")
@click.argument("trace_id")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON")
@click.option("--config", "-c", default=None)
def trace_show(trace_id, as_json, config):
    """Show the full execution detail of a trace."""
    from agentassure.cli.display import render_trace_tree

    cfg                 = _load_config()
    ev_store, _ap_store = _get_stores(cfg)
    records             = ev_store.get_records(trace_id=trace_id)

    if not records:
        console.print(f"[red]Trace '{trace_id}' not found.[/red]")
        sys.exit(1)

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
            "event_type":     r.event_type,
            "tool_name":      r.tool_name,
            "decision":       r.decision,
            "policy_id":      r.policy_id,
            "policy_version": r.policy_version,
            "reason":         r.reason,
            "controls":       r.controls,
            "timestamp":      r.timestamp,
            "input":          inp,
            "output":         out,
        })

    if as_json:
        click.echo(json.dumps(events, indent=2, default=str))
    else:
        render_trace_tree(trace_id, events)
        console.print(f"\n[dim]{len(records)} events total[/dim]")


# ─── APPROVALS ───────────────────────────────────────────────────────────────

@app.group()
def approvals():
    """Manage pending action approvals (human-in-the-loop)."""
    pass


@approvals.command("list")
@click.option(
    "--status",
    type=click.Choice(["PENDING", "APPROVED", "REJECTED"], case_sensitive=False),
    default=None,
)
@click.option("--db", default=None, help="Explicit path to SQLite database")
@click.option("--config", "-c", default=None)
def approvals_list(status, db, config):
    """List approval requests."""
    from agentassure.cli.display import render_approval_list
    cfg             = _load_config(config)
    _, ap_store     = _get_stores(cfg, db=db)
    items           = ap_store.list_approvals(status=status)
    render_approval_list([a.model_dump() for a in items])


@approvals.command("approve")
@click.argument("approval_id")
@click.option("--by", default="operator", show_default=True, help="Approver name")
def approvals_approve(approval_id, by):
    """Approve a pending action."""
    from agentassure.approval import ApprovalStatus
    cfg         = _load_config()
    _, ap_store = _get_stores(cfg)
    try:
        updated = ap_store.resolve_approval(
            approval_id, ApprovalStatus.APPROVED, resolved_by=by
        )
        if updated:
            console.print(
                f"[green]✓[/green] Approval [cyan]{approval_id}[/cyan] "
                f"APPROVED by {by}"
            )
        else:
            console.print(f"[red]Approval '{approval_id}' not found.[/red]")
            sys.exit(1)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)


@approvals.command("reject")
@click.argument("approval_id")
@click.option("--by", default="operator", show_default=True, help="Rejector name")
def approvals_reject(approval_id, by):
    """Reject a pending action."""
    from agentassure.approval import ApprovalStatus
    cfg         = _load_config()
    _, ap_store = _get_stores(cfg)
    try:
        updated = ap_store.resolve_approval(
            approval_id, ApprovalStatus.REJECTED, resolved_by=by
        )
        if updated:
            console.print(
                f"[yellow]✗[/yellow] Approval [cyan]{approval_id}[/cyan] "
                f"REJECTED by {by}"
            )
        else:
            console.print(f"[red]Approval '{approval_id}' not found.[/red]")
            sys.exit(1)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)


# ─── INGEST ──────────────────────────────────────────────────────────────────

@app.command()
@click.argument("file", type=click.Path(exists=True))
@click.option("--output", "-o", default=None, help="Output YAML (default: configured policy file)")
@click.option("--yes", "-y", is_flag=True, help="Accept all extracted rules without review")
def ingest(file, output, yes):
    """
    Extract governance policies from an organizational document.

    \b
    Supported formats: PDF, DOCX, TXT, Markdown

    \b
    Examples:
      agentassure ingest Financial_Policy.pdf
      agentassure ingest docs/security.docx --output policies/security.yaml
    """
    from agentassure.cli.ingest_cmd import run_ingest
    cfg = _load_config()
    run_ingest(file=file, output=output, accept_all=yes, config=cfg)


if __name__ == "__main__":
    app()
