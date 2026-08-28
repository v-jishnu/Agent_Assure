"""
Unit Tests for Trace & Span Context Management
"""

import pytest
from agentassure.trace import TraceContext, generate_trace_id, generate_span_id


def test_id_generation():
    t_id = generate_trace_id()
    s_id = generate_span_id()
    assert t_id.startswith("tr_")
    assert s_id.startswith("sp_")


def test_trace_context_nesting():
    with TraceContext() as trace1:
        t_id1 = trace1.trace_id
        s_id1 = trace1.span_id
        assert trace1.parent_span_id is None

        with TraceContext(trace_id=t_id1, parent_span_id=s_id1) as trace2:
            assert trace2.trace_id == t_id1
            assert trace2.parent_span_id == s_id1
            assert trace2.span_id != s_id1
