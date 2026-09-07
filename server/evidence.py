"""
Re-export EvidenceStore and EvidenceRecord from agentassure.evidence
for backwards compatibility. Core implementation lives in agentassure.evidence.
"""

from agentassure.evidence import EvidenceRecord, EvidenceStore, GENESIS_HASH

__all__ = ["EvidenceRecord", "EvidenceStore", "GENESIS_HASH"]
