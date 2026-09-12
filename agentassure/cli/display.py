"""
Rich-based terminal display for AgentAssure CLI.

All user-facing output goes through this module so the look-and-feel is
consistent across every command.  Nothing in here touches the governance
core — it is a pure presentation layer.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import sys
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

# Ensure Windows terminals support UTF-8 characters without cp1252 charmap errors
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Shared console — import this in CLI commands to keep output on stdout.
console = Console(legacy_windows=False)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_DECISION_ICON: Dict[Optional[str], tuple] = {
    "ALLOW":  ("✓", "green"),
    "BLOCK":  ("✗", "red"),
    "ASK":    ("⏸", "yellow"),
    "SHADOW": ("~", "dim"),
    None:     ("·", "dim"),
}


def _icon(decision: Optional[str]) -> tuple[str, str]:
    return _DECISION_ICON.get(decision, ("·", "dim"))


# ---------------------------------------------------------------------------
# Post-run summary panel
# ---------------------------------------------------------------------------

def render_run_summary(
    *,
    trace_id: str,
    agent_id: str,
    duration_ms: float,
    events: List[Dict[str, Any]],
    governance: Dict[str, int],
    cost: Optional[Dict[str, Any]] = None,
    policy_resolution: str = "deterministic",
) -> None:
    """
    Render the boxed AGENTASSURE RUN panel shown after 'agentassure run' exits.

    Example output:

        ╭──────────────────────────── AGENTASSURE RUN ────────────────────────────╮
        │ Agent:    loan-agent                                                     │
        │ Trace:    tr_8f21cd3a11b4                                                │
        │ Duration: 4820ms                                                         │
        │                                                                          │
        │ EXECUTION                                                                │
        │  ✓ get_customer                                                          │
        │  ✓ check_credit                                                          │
        │  ✗ approve_loan              BLOCKED                                     │
        │                                                                          │
        │ GOVERNANCE                                                               │
        │  Policy:    FIN-001                                                      │
        │  Reason:    Amount exceeds configured limit                              │
        │  Execution: PREVENTED                                                    │
        │                                                                          │
        │ MODEL USAGE                                                              │
        │  Tokens:    10,133  (in: 8,420  out: 1,713)                             │
        │  Est. cost: $0.0230  (₹1.91)                                            │
        │  Model:     gpt-4o                                                       │
        │                                                                          │
        │ Policy resolution: deterministic                                         │
        │ Governance LLM calls: 0                                                  │
        ╰──────────────────────────────────────────────────────────────────────────╯
    """
    lines: List[str] = []

    # ── Header ──────────────────────────────────────────────────────────────
    lines.append(f"[bold]Agent:[/bold]    {agent_id}")
    lines.append(f"[bold]Trace:[/bold]    {trace_id}")
    lines.append(f"[bold]Duration:[/bold] {duration_ms:.0f}ms")

    # ── Execution tree ───────────────────────────────────────────────────────
    lines.append("")
    lines.append("[bold]EXECUTION[/bold]")
    seen_tools: set = set()
    for evt in events:
        tool = evt.get("tool_name")
        if not tool or tool in seen_tools:
            continue
        decision = evt.get("decision")
        sym, color = _icon(decision)
        suffix = ""
        if decision == "BLOCK":
            suffix = "   [bold red]BLOCKED[/bold red]"
        elif decision == "ASK":
            suffix = "   [bold yellow]PENDING APPROVAL[/bold yellow]"
        lines.append(f"  [{color}]{sym}[/{color}] {tool}{suffix}")
        seen_tools.add(tool)

    # ── Governance detail ────────────────────────────────────────────────────
    blocked_events = [e for e in events if e.get("decision") in ("BLOCK", "ASK")]
    if blocked_events:
        lines.append("")
        lines.append("[bold]GOVERNANCE[/bold]")
        evt = blocked_events[0]
        lines.append(f"  Policy:    [cyan]{evt.get('policy_id', 'N/A')}[/cyan] v{evt.get('policy_version', 1)}")
        lines.append(f"  Reason:    {evt.get('reason', 'N/A')}")
        if evt.get("decision") == "BLOCK":
            lines.append("  Execution: [bold red]PREVENTED[/bold red]")
        else:
            lines.append("  Execution: [bold yellow]AWAITING APPROVAL[/bold yellow]")

    # ── Cost / model usage ───────────────────────────────────────────────────
    if cost:
        lines.append("")
        lines.append("[bold]MODEL USAGE[/bold]")
        total  = cost.get("total_tokens", 0)
        inp    = cost.get("input_tokens", 0)
        out    = cost.get("output_tokens", 0)
        c_usd  = cost.get("cost_usd", 0.0)
        model  = cost.get("model", "unknown")
        calls  = cost.get("llm_calls", 0)
        c_inr  = c_usd * 83.0  # approximate INR conversion
        lines.append(f"  LLM calls: {calls}")
        lines.append(f"  Tokens:    {total:,}  (in: {inp:,}  out: {out:,})")
        lines.append(f"  Est. cost: ${c_usd:.4f}  (₹{c_inr:.2f})")
        lines.append(f"  Model:     {model}")

    # ── Footer ───────────────────────────────────────────────────────────────
    lines.append("")
    lines.append(f"[dim]Policy resolution: {policy_resolution}[/dim]")
    lines.append("[dim]Governance LLM calls: 0[/dim]")

    console.print(
        Panel(
            "\n".join(lines),
            title="[bold cyan]AGENTASSURE RUN[/bold cyan]",
            border_style="cyan",
            expand=False,
        )
    )


# ---------------------------------------------------------------------------
# Trace tree
# ---------------------------------------------------------------------------

def render_trace_tree(trace_id: str, events: List[Dict[str, Any]]) -> None:
    """Render a tree view of trace events with governance annotations."""
    tree = Tree(f"[bold]Trace[/bold] [cyan]{trace_id}[/cyan]")
    seen: set = set()
    for evt in events:
        tool = evt.get("tool_name")
        if not tool or tool in seen:
            continue
        decision = evt.get("decision")
        sym, color = _icon(decision)
        label = f"[{color}]{sym}[/{color}] {tool}"
        if decision == "BLOCK":
            label += " [bold red]  [BLOCKED — NOT EXECUTED][/bold red]"
        elif decision == "ASK":
            label += " [bold yellow]  [PENDING APPROVAL][/bold yellow]"
        elif decision == "ALLOW":
            label += " [green]  [ALLOWED][/green]"

        node = tree.add(label)
        if evt.get("policy_id"):
            node.add(
                f"[dim]Policy: {evt.get('policy_id')} v{evt.get('policy_version', 1)}[/dim]"
            )
        if evt.get("reason") and decision not in (None, "ALLOW"):
            node.add(f"[dim]Reason: {evt.get('reason')}[/dim]")
        if evt.get("controls"):
            node.add(f"[dim]Controls: {evt.get('controls')}[/dim]")
        seen.add(tool)
    console.print(tree)


# ---------------------------------------------------------------------------
# Policy table
# ---------------------------------------------------------------------------

def render_policy_table(rules: List[Dict[str, Any]]) -> None:
    """Render policy rules as a formatted table."""
    table = Table(
        title="Active Policy Rules",
        box=box.ROUNDED,
        border_style="cyan",
        header_style="bold cyan",
        show_lines=False,
    )
    table.add_column("ID",       style="cyan",  no_wrap=True)
    table.add_column("Name")
    table.add_column("Action",   justify="center")
    table.add_column("Mode",     justify="center")
    table.add_column("Severity", justify="center")
    table.add_column("Ver",      justify="right")

    _MODE_CLR   = {"enforce": "green", "audit": "yellow", "disabled": "red dim"}
    _ACT_CLR    = {"ALLOW": "green", "BLOCK": "red", "ASK": "yellow", "SHADOW": "dim"}
    _SEV_CLR    = {"low": "dim", "medium": "yellow", "high": "red", "critical": "bold red"}

    for r in rules:
        action = r.get("action", "ALLOW")
        mode   = r.get("mode",   "enforce")
        sev    = r.get("severity", "low")
        table.add_row(
            r.get("id",   ""),
            r.get("name", ""),
            f"[{_ACT_CLR.get(action, '')}]{action}[/]",
            f"[{_MODE_CLR.get(mode, '')}]{mode}[/]",
            f"[{_SEV_CLR.get(sev,  '')}]{sev}[/]",
            str(r.get("version", 1)),
        )
    console.print(table)


# ---------------------------------------------------------------------------
# Approval list
# ---------------------------------------------------------------------------

def render_approval_list(approvals: List[Dict[str, Any]]) -> None:
    """Render approval requests as a colored table."""
    if not approvals:
        console.print("[dim]No approvals found.[/dim]")
        return

    table = Table(
        title="Approvals",
        box=box.ROUNDED,
        border_style="yellow",
        header_style="bold yellow",
    )
    table.add_column("ID",      no_wrap=True, max_width=24)
    table.add_column("Tool",    no_wrap=True)
    table.add_column("Status",  justify="center")
    table.add_column("Policy")
    table.add_column("Reason",  max_width=50)
    table.add_column("Created", no_wrap=True, max_width=20)

    _STATUS_CLR = {"PENDING": "yellow", "APPROVED": "green", "REJECTED": "red"}

    for a in approvals:
        status = a.get("status", "PENDING")
        table.add_row(
            a.get("approval_id", ""),
            a.get("tool_name",   ""),
            f"[{_STATUS_CLR.get(status, 'white')}]{status}[/]",
            a.get("policy_id",  ""),
            (a.get("reason") or "")[:50],
            (a.get("created_at") or "")[:19],
        )
    console.print(table)


# ---------------------------------------------------------------------------
# Trace list
# ---------------------------------------------------------------------------

def render_trace_list(traces: List[Dict[str, Any]]) -> None:
    """Render a list of traces as a formatted table."""
    if not traces:
        console.print("[dim]No traces found.[/dim]")
        return

    table = Table(
        title="Recent Traces",
        box=box.ROUNDED,
        border_style="cyan",
        header_style="bold cyan",
    )
    table.add_column("Trace ID",  no_wrap=True, max_width=22)
    table.add_column("Agent",     no_wrap=True)
    table.add_column("Status",    justify="center")
    table.add_column("Events",    justify="right")
    table.add_column("Blocked",   justify="right")
    table.add_column("Started",   no_wrap=True, max_width=20)
    table.add_column("Duration",  justify="right")

    _STATUS_CLR = {
        "COMPLETED":        "green",
        "BLOCKED":          "red",
        "PENDING_APPROVAL": "yellow",
        "APPROVED":         "cyan",
    }

    for t in traces:
        status  = t.get("status", "COMPLETED")
        dur     = t.get("duration_ms")
        dur_str = f"{dur:.0f}ms" if dur else "N/A"
        table.add_row(
            t.get("trace_id",    ""),
            t.get("agent_id",    ""),
            f"[{_STATUS_CLR.get(status, 'white')}]{status}[/]",
            str(t.get("events_count",  0)),
            str(t.get("blocked_count", 0)),
            (t.get("started_at") or "")[:19],
            dur_str,
        )
    console.print(table)


# ---------------------------------------------------------------------------
# Ingest candidate review
# ---------------------------------------------------------------------------

def render_ingest_candidate(index: int, candidate: Dict[str, Any]) -> None:
    """Render a single extracted policy candidate for interactive review."""
    rule_id  = candidate.get("id", f"RULE-{index:03d}")
    desc     = candidate.get("description", "")
    action   = candidate.get("action", "BLOCK")
    cond     = candidate.get("condition", {})
    excerpt  = candidate.get("source_excerpt", "")

    _ACT_CLR = {"ALLOW": "green", "BLOCK": "red", "ASK": "yellow"}
    color    = _ACT_CLR.get(action, "white")

    lines = [
        f"[bold cyan]{rule_id}[/bold cyan]  [{color}]{action}[/]",
        f"  {desc}",
    ]
    if cond:
        field = cond.get("field", "?")
        op    = cond.get("operator", "?")
        val   = cond.get("value", "?")
        lines.append(f"  [dim]Condition: {field} {op} {val}[/dim]")
    if excerpt:
        lines.append(f"  [dim]Source: \"{excerpt[:80]}{'...' if len(excerpt)>80 else ''}\"[/dim]")

    console.print("\n".join(lines))
