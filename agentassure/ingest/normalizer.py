"""
Policy normalizer — converts raw extraction candidates into PolicyRule objects.

The normalizer is a pure Python mapping layer: it receives a candidate dict
produced by a resolver and attempts to construct a valid PolicyRule from it.
No LLM is involved at this stage — all field mapping is deterministic.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from agentassure.policy import PolicyCondition, PolicyOutcome, PolicyRule, SeverityLevel


# ---------------------------------------------------------------------------
# Action mapping
# ---------------------------------------------------------------------------

def _map_action(hint: str) -> PolicyOutcome:
    h = (hint or "").upper().strip()
    if h in ("BLOCK", "DENY", "PREVENT", "FORBIDDEN", "PROHIBIT"):
        return PolicyOutcome.BLOCK
    if h in ("ASK", "APPROVAL", "REVIEW", "ESCALATE"):
        return PolicyOutcome.ASK
    if h in ("ALLOW", "PERMIT", "ALLOWED"):
        return PolicyOutcome.ALLOW
    return PolicyOutcome.BLOCK  # safe default


# ---------------------------------------------------------------------------
# Condition extraction from a natural-language hint
# ---------------------------------------------------------------------------

# Patterns for numeric threshold conditions (e.g. "amount > 500000")
_NUMERIC_PATTERNS = [
    # "amount exceeds 500000" / "amount > 500000"
    (r"(\w+)\s*(?:exceeds?|>|greater\s+than|above|over)\s*([\d,]+)", "gt"),
    (r"(\w+)\s*(?:<|less\s+than|below|under)\s*([\d,]+)",            "lt"),
    (r"(\w+)\s*(?:equals?|=|is)\s*([\d,]+)",                        "eq"),
    (r"(\w+)\s*(?:>=|at\s+least|minimum\s+of)\s*([\d,]+)",          "gte"),
    (r"(\w+)\s*(?:<=|at\s+most|maximum\s+of)\s*([\d,]+)",           "lte"),
]

# Tool-name conditions
_TOOL_PATTERNS = [
    r"tool\s+(?:is\s+)?['\"]?(\w+)['\"]?",
    r"use\s+(?:of\s+)?(\w+)\s+tool",
    r"call(?:ing)?\s+(\w+)",
]


def _extract_condition(hint: str) -> Optional[Dict[str, Any]]:
    """Try to parse a structured condition from a natural-language hint."""
    if not hint:
        return None

    # Numeric threshold
    for pattern, operator in _NUMERIC_PATTERNS:
        m = re.search(pattern, hint, re.IGNORECASE)
        if m:
            field = m.group(1).lower()
            value_str = m.group(2).replace(",", "")
            try:
                value = int(value_str)
            except ValueError:
                try:
                    value = float(value_str)
                except ValueError:
                    continue
            return {"field": field, "operator": operator, "value": value}

    # Tool reference
    for pattern in _TOOL_PATTERNS:
        m = re.search(pattern, hint, re.IGNORECASE)
        if m:
            return {"field": "tool_name", "operator": "eq", "value": m.group(1)}

    return None


# ---------------------------------------------------------------------------
# Severity inference
# ---------------------------------------------------------------------------

def _infer_severity(action: PolicyOutcome, description: str) -> SeverityLevel:
    desc_lower = description.lower()
    if action == PolicyOutcome.BLOCK:
        if any(w in desc_lower for w in ("critical", "security", "delete", "export", "secret")):
            return SeverityLevel.CRITICAL
        if any(w in desc_lower for w in ("high", "exceed", "prohibit", "forbidden")):
            return SeverityLevel.HIGH
        return SeverityLevel.MEDIUM
    if action == PolicyOutcome.ASK:
        return SeverityLevel.MEDIUM
    return SeverityLevel.LOW


# ---------------------------------------------------------------------------
# Public normalizer
# ---------------------------------------------------------------------------

def normalize_candidates(
    candidates: List[Dict[str, Any]],
    doc_name: str = "DOC",
    id_prefix: Optional[str] = None,
) -> List[PolicyRule]:
    """
    Convert a list of extraction candidates into PolicyRule objects.

    Args:
        candidates: Raw dicts from a resolver.extract() call.
        doc_name:   Document name used to derive rule IDs.
        id_prefix:  Optional explicit prefix for rule IDs.

    Returns:
        List of PolicyRule instances.  Candidates that cannot be normalized
        are silently skipped (a warning is printed for each).
    """
    prefix = id_prefix or _derive_prefix(doc_name)
    rules: List[PolicyRule] = []

    for i, cand in enumerate(candidates, start=1):
        rule_id = f"{prefix}-{i:03d}"
        try:
            rule = _normalize_one(cand, rule_id=rule_id)
            rules.append(rule)
        except Exception as exc:
            print(f"  [warn] Skipping candidate {rule_id}: {exc}")

    return rules


def normalize_candidate(cand: Dict[str, Any], prefix: str = "RULE", index: int = 1) -> PolicyRule:
    rule_id = f"{prefix}-{index:03d}"
    return _normalize_one(cand, rule_id=rule_id)


def _normalize_one(cand: Dict[str, Any], rule_id: str) -> PolicyRule:
    description  = str(cand.get("description", cand.get("name", ""))).strip()
    action_hint  = str(cand.get("action_hint", cand.get("action", "BLOCK"))).strip()
    cond_hint    = str(cand.get("condition_hint", "")).strip()

    action    = _map_action(action_hint)
    condition = _extract_condition(cond_hint)
    if not condition and "field" in cand and "operator" in cand and "value" in cand:
        condition = {
            "field": cand["field"],
            "operator": cand["operator"],
            "value": cand["value"],
        }
    severity  = _infer_severity(action, description)
    if "severity" in cand:
        try:
            severity = SeverityLevel(str(cand["severity"]).lower())
        except Exception:
            pass

    rule_kwargs: Dict[str, Any] = {
        "id":          rule_id,
        "name":        cand.get("name", description[:80] if description else rule_id),
        "description": description,
        "action":      action,
        "severity":    severity,
        "mode":        "enforce",
    }

    if condition:
        rule_kwargs["condition"] = PolicyCondition(**condition)

    if "controls" in cand and cand["controls"]:
        from agentassure.policy import ControlMapping
        if isinstance(cand["controls"], dict):
            rule_kwargs["controls"] = ControlMapping(**cand["controls"])
        elif isinstance(cand["controls"], ControlMapping):
            rule_kwargs["controls"] = cand["controls"]

    return PolicyRule.model_validate(rule_kwargs)


def _derive_prefix(doc_name: str) -> str:
    """Turn a document filename into a short uppercase prefix."""
    stem = re.sub(r"[^a-zA-Z0-9]", "", Path(doc_name).stem).upper()
    return stem[:6] if stem else "RULE"


# Avoid a module-level import of Path before the function body
from pathlib import Path  # noqa: E402 (intentional for _derive_prefix)
