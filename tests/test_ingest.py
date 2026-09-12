"""
Tests for AgentAssure Policy Ingestion (RAG & Heuristic).
"""

from agentassure.ingest.loader import load_document
from agentassure.ingest.resolver import HeuristicResolver, detect_resolver
from agentassure.ingest.normalizer import normalize_candidate
from agentassure.policy import PolicyRule, PolicyOutcome


def test_loader_text_and_markdown(tmp_path):
    txt_file = tmp_path / "policy.txt"
    txt_file.write_text("All loan disbursements exceeding $50,000 must be approved by a senior officer.", encoding="utf-8")

    content = load_document(str(txt_file))
    assert "disbursements" in content

    md_file = tmp_path / "policy.md"
    md_file.write_text("# Security Rules\n\nDirect SQL access is strictly prohibited.", encoding="utf-8")
    content_md = load_document(str(md_file))
    assert "Direct SQL" in content_md


def test_heuristic_resolver_extraction():
    text = (
        "Policy 1: Direct SQL queries are forbidden.\n"
        "Policy 2: Transfers greater than 10000 require human review.\n"
        "Policy 3: Never allow deletion of customer records."
    )
    resolver = HeuristicResolver()
    candidates = resolver.extract(text)

    assert len(candidates) >= 1
    # Check candidate structure
    first = candidates[0]
    assert "description" in first
    assert "action_hint" in first
    assert "source_excerpt" in first


def test_normalizer_candidate_to_rule():
    candidate = {
        "name": "High Value Transfer Control",
        "description": "Transfers over 50000 must be blocked",
        "action": "BLOCK",
        "tool": "transfer_funds",
        "field": "amount",
        "operator": "gt",
        "value": 50000,
        "severity": "high",
        "controls": {
            "iso42001": "A.9.4",
            "eu_ai_act": "Art. 14",
        }
    }

    rule = normalize_candidate(candidate, prefix="TEST", index=1)
    assert isinstance(rule, PolicyRule)
    assert rule.id == "TEST-001"
    assert rule.action == PolicyOutcome.BLOCK
    assert rule.condition is not None
    assert rule.condition.field == "amount"
    assert rule.condition.operator == "gt"
    assert rule.condition.value == 50000
    assert rule.controls is not None
    assert rule.controls.iso42001 == "A.9.4"
