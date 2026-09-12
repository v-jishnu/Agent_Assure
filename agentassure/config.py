"""
AgentAssure Configuration Loader.

Reads agentassure.yaml from the current working directory (or an explicit
path).  All values fall back gracefully to safe defaults so that the SDK
works even without a config file — important for the case where a user
adds @aa.govern before running 'agentassure init'.

Priority order for each setting:
    1. Explicit constructor argument (sdk.py / CLI flag)
    2. Environment variable (set by 'agentassure run' for subprocess isolation)
    3. agentassure.yaml in CWD
    4. Built-in default
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field, ConfigDict


# ---------------------------------------------------------------------------
# Config schema
# ---------------------------------------------------------------------------

class OutputConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    cli: bool = True
    json_path: Optional[str] = Field(default=".agentassure/traces/", alias="json")
    email: Optional[str] = None

    @property
    def json(self) -> Optional[str]:
        return self.json_path


class AgentAssureConfig(BaseModel):
    agent_id: str = "default-agent"
    environment: str = "development"
    policies: str = "./policies/"
    db: str = ".agentassure/evidence.db"
    output: OutputConfig = OutputConfig()


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_CONFIG_FILENAMES = ("agentassure.yaml", "agentassure.yml")


def load_config(config_path: Optional[str] = None) -> AgentAssureConfig:
    """
    Load configuration from a YAML file, returning defaults if not found.

    Args:
        config_path: Explicit path to a config file.  When None the loader
                     searches for agentassure.yaml in the current directory.
    """
    search_paths: list[Path] = []

    if config_path:
        search_paths.append(Path(config_path))
    else:
        for name in _CONFIG_FILENAMES:
            search_paths.append(Path.cwd() / name)

    for p in search_paths:
        if p.exists():
            try:
                with open(p, encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                return AgentAssureConfig.model_validate(data)
            except Exception:
                # Malformed YAML → fall through to defaults
                pass

    return AgentAssureConfig()


# ---------------------------------------------------------------------------
# Helpers used by both CLI and SDK
# ---------------------------------------------------------------------------

def get_db_path(config: Optional[AgentAssureConfig] = None) -> str:
    """
    Resolve the database path.

    Environment variable AGENTASSURE_DB always wins so that
    'agentassure run' can inject a clean path into the subprocess.
    """
    env_db = os.environ.get("AGENTASSURE_DB")
    if env_db:
        return env_db
    if config:
        return config.db
    return ".agentassure/evidence.db"


def get_policy_path(config: Optional[AgentAssureConfig] = None) -> Optional[str]:
    """
    Resolve the policy YAML path.

    When the configured value is a directory, we look for common filenames
    inside it rather than requiring users to know the exact filename.
    """
    env_policy = os.environ.get("AGENTASSURE_POLICY")
    if env_policy:
        return env_policy

    policies_val = (config.policies if config else None) or "./policies/"
    p = Path(policies_val)

    if p.is_dir():
        for fname in (
            "policy.yaml", "policy.yml",
            "policies.yaml", "policies.yml",
            "loan.yaml", "loan.yml",
        ):
            candidate = p / fname
            if candidate.exists():
                return str(candidate)
        return None

    if p.exists():
        return str(p)

    return None


def get_agent_id(config: Optional[AgentAssureConfig] = None) -> str:
    return os.environ.get("AGENTASSURE_AGENT_ID") or (config.agent_id if config else "default-agent")


def get_environment(config: Optional[AgentAssureConfig] = None) -> str:
    return os.environ.get("AGENTASSURE_ENVIRONMENT") or (config.environment if config else "development")
