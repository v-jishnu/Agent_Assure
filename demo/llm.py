"""
Groq LLM client with tool calling, for the AgentAssure demo agent.

This module deliberately lives under demo/ rather than agentassure/. The
governance layer must not depend on any model provider — it governs whatever
the agent happens to call. Putting a Groq import inside the SDK would couple
the control plane to one vendor and undermine that claim.
"""

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-120b"


def load_dotenv(path: Optional[str] = None) -> None:
    """
    Read KEY=VALUE lines from .env into the environment.

    Avoids a python-dotenv dependency. The file is gitignored, so credentials
    never enter version control.
    """
    env_path = Path(path) if path else Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


class GroqError(RuntimeError):
    pass


def _retry_after(err, detail: str) -> Optional[float]:
    """Seconds Groq asked us to wait, from the header or the message body."""
    header = err.headers.get("retry-after") if err.headers else None
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    match = re.search(r"try again in ([\d.]+)s", detail)
    return float(match.group(1)) if match else None


class GroqClient:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        temperature: float = 0.1,
        max_tokens: int = 700,
        timeout: int = 60,
        retries: int = 5,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.retries = retries

        load_dotenv()
        self.api_key = os.environ.get("GROQ_API_KEY")

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.api_key:
            raise GroqError(
                "GROQ_API_KEY is not set. Copy .env.example to .env and add "
                "your key, or export GROQ_API_KEY in your shell."
            )

        body = json.dumps(payload).encode("utf-8")
        last_error: Optional[Exception] = None

        for attempt in range(self.retries):
            request = urllib.request.Request(
                GROQ_URL,
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    # Groq's edge returns 403 to requests without a
                    # User-Agent. Do not remove this header.
                    "User-Agent": "agentassure/0.1",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:400]
                last_error = GroqError(f"Groq HTTP {e.code}: {detail}")
                if e.code not in (408, 429) and e.code < 500:
                    raise last_error from e
                # The free tier caps tokens per minute and tells us exactly
                # how long to wait. Honour that rather than guessing.
                wait = _retry_after(e, detail) or (2 ** attempt)
                time.sleep(min(wait + 0.5, 30))
            except (urllib.error.URLError, TimeoutError) as e:
                last_error = GroqError(f"Groq call failed: {type(e).__name__}: {e}")
                time.sleep(2 ** attempt)

        raise last_error or GroqError("Groq call failed")

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        One turn. Returns the assistant message, which may carry `tool_calls`
        instead of `content`.
        """
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        return self._post(payload)["choices"][0]["message"]


def tool_schema(
    name: str,
    description: str,
    properties: Dict[str, Any],
    required: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Build one OpenAI-style function schema for the tools parameter."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required if required is not None else list(properties),
            },
        },
    }


STRING = {"type": "string"}
NUMBER = {"type": "number"}
INTEGER = {"type": "integer"}
