"""
Interactive 'agentassure ingest' implementation.

Flow:
  1. Load document → text
  2. Auto-detect LLM resolver (OpenAI / Groq / heuristic)
  3. Extract policy candidates
  4. Normalize candidates → PolicyRule objects
  5. Interactive review (unless --yes)
  6. Append accepted rules to the configured policy YAML file
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import click
import yaml

from agentassure.cli.display import console, render_ingest_candidate
from agentassure.config import AgentAssureConfig, get_policy_path


def run_ingest(
    *,
    file: str,
    output: Optional[str] = None,
    accept_all: bool = False,
    config: Optional[AgentAssureConfig] = None,
) -> None:
    from agentassure.ingest.loader import load_document
    from agentassure.ingest.normalizer import normalize_candidates
    from agentassure.ingest.resolver import auto_detect_resolver

    # ── Step 1: Load document ────────────────────────────────────────────
    console.print(f"[cyan]Extracting text from[/cyan] {file}…")
    try:
        text = load_document(file)
    except (FileNotFoundError, ValueError, ImportError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    if not text.strip():
        console.print("[yellow]Warning:[/yellow] Document appears to be empty.")
        sys.exit(0)

    # ── Step 2: Resolve LLM backend ──────────────────────────────────────
    resolver = auto_detect_resolver()
    console.print(f"[dim]Policy resolution: {resolver.name}[/dim]")
    console.print("")

    # ── Step 3: Extract candidates ───────────────────────────────────────
    try:
        candidates: List[Dict[str, Any]] = resolver.extract(text)
    except Exception as exc:
        console.print(f"[red]Extraction error:[/red] {exc}")
        sys.exit(1)

    if not candidates:
        console.print("[yellow]No policy clauses detected in this document.[/yellow]")
        sys.exit(0)

    console.print(f"Found [bold]{len(candidates)}[/bold] policy candidate(s):\n")

    # ── Step 4: Normalize ────────────────────────────────────────────────
    doc_name = Path(file).stem
    rules = normalize_candidates(candidates, doc_name=doc_name)

    # ── Step 5: Interactive review ───────────────────────────────────────
    accepted_rules = []
    for i, (cand, rule) in enumerate(zip(candidates, rules), start=1):
        render_ingest_candidate(i, {**cand, "id": rule.id})
        console.print(f"  [dim]→ Rule {rule.id}: action={rule.action.value}[/dim]")

        if accept_all:
            accepted_rules.append(rule)
            console.print(f"  [green]✓[/green] Accepted (--yes)")
        else:
            choice = click.prompt(
                "  Accept? [y/n/skip]",
                default="y",
                show_default=True,
            ).strip().lower()
            if choice in ("y", "yes"):
                accepted_rules.append(rule)
                console.print(f"  [green]✓[/green] Accepted")
            elif choice in ("s", "skip"):
                console.print(f"  [dim]Skipped[/dim]")
            else:
                console.print(f"  [yellow]Rejected[/yellow]")
        console.print("")

    if not accepted_rules:
        console.print("[dim]No rules accepted. Policy file unchanged.[/dim]")
        return

    # ── Step 6: Write to YAML ────────────────────────────────────────────
    dest_path = _resolve_output(output, config)
    _append_rules_to_yaml(accepted_rules, dest_path)

    console.print(
        f"[green]✓[/green] {len(accepted_rules)} rule(s) written to "
        f"[cyan]{dest_path}[/cyan]"
    )
    console.print("[dim]Run 'agentassure policy list' to verify.[/dim]")


def _resolve_output(
    explicit: Optional[str], config: Optional[AgentAssureConfig]
) -> Path:
    if explicit:
        return Path(explicit)
    # Try to find the configured policy file
    policy_path = get_policy_path(config)
    if policy_path:
        return Path(policy_path)
    # Default: create in policies/ directory
    return Path("policies") / "policy.yaml"


def _append_rules_to_yaml(rules, dest: Path) -> None:
    """Append new rules to an existing (or new) policy YAML file."""
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        with open(dest, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    existing_policies = data.get("policies", [])
    existing_ids = {r.get("id") for r in existing_policies}

    new_rules_data = []
    for rule in rules:
        rule_dict = rule.model_dump(mode="json", exclude_none=True)
        if rule_dict["id"] not in existing_ids:
            new_rules_data.append(rule_dict)

    data["policies"] = existing_policies + new_rules_data
    if "capabilities" not in data:
        data["capabilities"] = {}

    with open(dest, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
