"""
A real agent loop for the AgentAssure demo.

The model is given a task and a toolset and decides what to do. Nothing here
scripts a violation: when the loan agent attempts a ₹25,00,000 disbursement,
it is because the model chose that tool with that argument after checking
credit and running a risk assessment. A hardcoded sequence of calls would
prove nothing about a control layer — the interesting question is whether
governance holds against a decision nobody wrote down in advance.

The loop is deliberately ordinary; it is the same shape every agent framework
implements. AgentAssure is not woven into it. The tools were wrapped with
assure.wrap_tool() before they ever reached this function.
"""

import json
from typing import Any, Callable, Dict, List, Optional

from agentassure import PolicyViolationError, PendingApprovalException


class AgentRunResult:
    """What the agent did, and how the run ended."""

    def __init__(self):
        self.turns: int = 0
        self.tool_calls: List[str] = []
        self.final_text: str = ""
        self.stopped_by: Optional[str] = None
        self.decision = None  # PolicyDecision, when governance ended the run


def run_agent(
    client,
    system_prompt: str,
    task: str,
    tools: Dict[str, Callable[..., Any]],
    schemas: List[Dict[str, Any]],
    max_turns: int = 8,
    result: Optional[AgentRunResult] = None,
    verbose: bool = True,
) -> AgentRunResult:
    """
    Drive the model until it stops calling tools or the turn budget runs out.

    A governance exception is not an error — it is the system working — so it
    is caught here, recorded on the result, and ends the run. The partial tool
    trail is preserved: what the agent tried before being stopped is the part
    worth showing.
    """
    result = result or AgentRunResult()
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task},
    ]

    for _ in range(max_turns):
        result.turns += 1
        message = client.chat(messages, tools=schemas)
        calls = message.get("tool_calls") or []

        if not calls:
            result.final_text = message.get("content") or ""
            return result

        # Echo the assistant turn back verbatim; the API requires every tool
        # result to reference the call that produced it.
        messages.append({
            "role": "assistant",
            "content": message.get("content") or "",
            "tool_calls": calls,
        })

        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            result.tool_calls.append(name)

            if verbose:
                shown = ", ".join(f"{k}={v!r}" for k, v in args.items())
                print(f"    [model chose] {name}({shown})")

            function = tools.get(name)
            if function is None:
                # The model invented a tool. Tell it so rather than crashing:
                # an unknown name is a model error, not a governance event.
                output: Any = {"error": f"No such tool: {name}"}
            else:
                # Drop argument names the model invented, so a malformed call
                # is corrected by the model instead of raising a TypeError.
                clean = {k: v for k, v in args.items() if k.isidentifier()}
                try:
                    output = function(**clean)
                except PolicyViolationError as e:
                    result.stopped_by = "BLOCK"
                    result.decision = e.decision
                    return result
                except PendingApprovalException as e:
                    result.stopped_by = "ASK"
                    result.decision = e.decision
                    return result
                except TypeError as e:
                    output = {"error": f"Invalid arguments for {name}: {e}"}

            messages.append({
                "role": "tool",
                "tool_call_id": call["id"],
                "content": json.dumps(output, default=str)[:2000],
            })

    result.stopped_by = "max_turns"
    return result
