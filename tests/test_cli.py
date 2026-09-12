"""
Tests for AgentAssure CLI commands.
"""

from click.testing import CliRunner
from agentassure.cli.main import app


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "AgentAssure" in result.output
    assert "runtime governance" in result.output
    assert "run" in result.output
    assert "policy" in result.output
    assert "trace" in result.output
    assert "approvals" in result.output
    assert "ingest" in result.output
    assert "init" in result.output


def test_cli_policy_help():
    runner = CliRunner()
    result = runner.invoke(app, ["policy", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output
    assert "validate" in result.output


def test_cli_policy_list():
    runner = CliRunner()
    result = runner.invoke(app, ["policy", "list", "--policy", "policies/loan.yaml"])
    assert result.exit_code == 0
    assert "POL-LOAN" in result.output or "Rules" in result.output


def test_cli_policy_validate():
    runner = CliRunner()
    result = runner.invoke(app, ["policy", "validate", "--policy", "policies/loan.yaml"])
    assert result.exit_code == 0
    assert "valid" in result.output.lower()


def test_cli_trace_list(tmp_path):
    runner = CliRunner()
    db_file = str(tmp_path / "cli_test.db")
    result = runner.invoke(app, ["trace", "list", "--db", db_file])
    assert result.exit_code == 0


def test_cli_approvals_list(tmp_path):
    runner = CliRunner()
    db_file = str(tmp_path / "cli_test.db")
    result = runner.invoke(app, ["approvals", "list", "--db", db_file])
    assert result.exit_code == 0


def test_cli_init_help():
    runner = CliRunner()
    result = runner.invoke(app, ["init", "--help"])
    assert result.exit_code == 0


def test_cli_run_help():
    runner = CliRunner()
    result = runner.invoke(app, ["run", "--help"])
    assert result.exit_code == 0


def test_cli_ingest_help():
    runner = CliRunner()
    result = runner.invoke(app, ["ingest", "--help"])
    assert result.exit_code == 0
