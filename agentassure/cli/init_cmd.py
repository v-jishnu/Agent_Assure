"""
'agentassure init' — scaffold AgentAssure configuration in a repository.

Creates:
  agentassure.yaml        repo-level config
  policies/               policy directory with a starter template
  .agentassure/           runtime data directory (db, trace exports)
  .gitignore update       ignore .agentassure/ data

Prints integration instructions tailored to the detected agent framework.
"""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from typing import Optional

import click

from agentassure.cli.display import console


# ---------------------------------------------------------------------------
# Example policy template — generic enough for any domain
# ---------------------------------------------------------------------------

_EXAMPLE_POLICY = """\
# AgentAssure policy file
# Reference: https://github.com/v-jishnu/Agent_Assure/blob/main/USER_GUIDE.md

# Capability boundaries — what the agent is permitted to do at all
capabilities:
  allowed:
    - get_data
    - search
    - summarize
  approval_required:
    - send_email
    - write_file
  forbidden:
    - delete_database
    - system_shell

# Policy rules — evaluated against every tool call before execution
policies:
  - id: COST-001
    name: Cost guard
    description: Pause for approval when a single trace exceeds $0.10
    version: 1
    condition:
      field: cost_usd
      operator: gt
      value: 0.10
    severity: medium
    action: ASK
    mode: enforce
    controls:
      note: Prevents runaway LLM spend from autonomous agents
"""


_AGENTASSURE_YAML = """\
# AgentAssure — repo-level configuration
# Run 'agentassure --help' for available commands.

agent_id: {agent_id}
environment: {environment}
policies: ./policies/
db: .agentassure/evidence.db
output:
  cli: true
  json: .agentassure/traces/
  email: null
"""


# ---------------------------------------------------------------------------
# Framework detection
# ---------------------------------------------------------------------------

def _detect_framework() -> Optional[str]:
    """Return the agent framework this repo appears to use, if detectable."""
    try:
        import langgraph  # noqa: F401
        return "langgraph"
    except ImportError:
        pass
    try:
        import openai  # noqa: F401
        return "openai"
    except ImportError:
        pass
    try:
        import anthropic  # noqa: F401
        return "anthropic"
    except ImportError:
        pass
    try:
        import groq  # noqa: F401
        return "groq"
    except ImportError:
        pass
    return None


def _integration_instructions(framework: Optional[str], agent_id: str) -> str:
    base = textwrap.dedent(f"""\
        Add governance to your agent tools with a single decorator:

          from agentassure import AgentAssure
          aa = AgentAssure()   # reads agentassure.yaml automatically

          @aa.govern
          def your_tool_function(arg1, arg2):
              ...              # your existing code unchanged

        Then run your agent through AgentAssure:

          agentassure run python your_agent.py
    """)

    if framework == "langgraph":
        return base + textwrap.dedent("""
        LangGraph detected.  You can also use the LangGraph adapter:

          from agentassure.adapters.langgraph import wrap_langgraph_tools
          tools = wrap_langgraph_tools(tools, aa)
        """)
    elif framework == "openai":
        return base + textwrap.dedent("""
        OpenAI SDK detected.  Capture token costs automatically:

          from agentassure.adapters.openai import instrument
          instrument()   # call once at startup, before any openai calls
        """)
    return base


# ---------------------------------------------------------------------------
# Main init function
# ---------------------------------------------------------------------------

def run_init(
    *,
    agent_id: Optional[str] = None,
    environment: Optional[str] = None,
    force: bool = False,
) -> None:
    cwd = Path.cwd()
    config_file = cwd / "agentassure.yaml"
    policies_dir = cwd / "policies"
    data_dir = cwd / ".agentassure"
    traces_dir = data_dir / "traces"
    gitignore = cwd / ".gitignore"

    # ── Already initialized? ──────────────────────────────────────────────
    if config_file.exists() and not force:
        console.print(
            "[yellow]agentassure.yaml already exists.[/yellow] "
            "Use --force to overwrite."
        )
        return

    # ── Prompt for missing values ─────────────────────────────────────────
    if not agent_id:
        agent_id = click.prompt(
            "Agent name",
            default=cwd.name.lower().replace(" ", "-") or "my-agent",
        )
    if not environment:
        environment = click.prompt(
            "Environment",
            default="development",
            type=click.Choice(["development", "staging", "production"]),
        )

    # ── Create agentassure.yaml ───────────────────────────────────────────
    config_file.write_text(
        _AGENTASSURE_YAML.format(agent_id=agent_id, environment=environment),
        encoding="utf-8",
    )
    console.print(f"  [green]✓[/green] Created [cyan]agentassure.yaml[/cyan]")

    # ── Create policies/ with example ─────────────────────────────────────
    policies_dir.mkdir(exist_ok=True)
    policy_file = policies_dir / "policy.yaml"
    if not policy_file.exists() or force:
        policy_file.write_text(_EXAMPLE_POLICY, encoding="utf-8")
        console.print(f"  [green]✓[/green] Created [cyan]policies/policy.yaml[/cyan]")
    else:
        console.print(f"  [dim]·  policies/policy.yaml already exists, skipped[/dim]")

    # ── Create .agentassure/ runtime directory ────────────────────────────
    data_dir.mkdir(exist_ok=True)
    traces_dir.mkdir(exist_ok=True)
    (data_dir / ".gitkeep").touch()
    console.print(f"  [green]✓[/green] Created [cyan].agentassure/[/cyan]")

    # ── Update .gitignore ─────────────────────────────────────────────────
    ignore_entry = ".agentassure/\n"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if ".agentassure" not in content:
            with gitignore.open("a", encoding="utf-8") as f:
                f.write("\n# AgentAssure runtime data\n" + ignore_entry)
            console.print(f"  [green]✓[/green] Updated [cyan].gitignore[/cyan]")
    else:
        gitignore.write_text(
            "# AgentAssure runtime data\n" + ignore_entry, encoding="utf-8"
        )
        console.print(f"  [green]✓[/green] Created [cyan].gitignore[/cyan]")

    # ── Detect framework ──────────────────────────────────────────────────
    framework = _detect_framework()
    if framework:
        console.print(f"\n[dim]Detected framework: {framework}[/dim]")

    # ── Integration instructions ──────────────────────────────────────────
    instructions = _integration_instructions(framework, agent_id)
    console.print(
        f"\n[bold green]AgentAssure initialized for '{agent_id}'.[/bold green]\n"
    )
    for line in instructions.splitlines():
        console.print(f"  {line}" if line.startswith(" ") else line)

    console.print(
        "\n[dim]Install: pip install git+https://github.com/v-jishnu/Agent_Assure.git[/dim]"
    )
