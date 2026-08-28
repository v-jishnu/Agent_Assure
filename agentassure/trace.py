"""
Trace and Span Context Management for AgentAssure
"""

import contextvars
import uuid
from typing import Optional, Tuple

_current_trace_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("trace_id", default=None)
_current_span_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("span_id", default=None)


def generate_trace_id() -> str:
    return f"tr_{uuid.uuid4().hex[:12]}"


def generate_span_id() -> str:
    return f"sp_{uuid.uuid4().hex[:12]}"


def reset_context():
    _current_trace_id.set(None)
    _current_span_id.set(None)


class TraceContext:
    """
    Context manager to manage active trace_id, span_id, and parent_span_id.
    """

    def __init__(self, trace_id: Optional[str] = None, parent_span_id: Optional[str] = None):
        self.trace_id = trace_id or _current_trace_id.get() or generate_trace_id()

        # If explicit parent_span_id not given, use current span_id in context if trace matches
        curr_t = _current_trace_id.get()
        if parent_span_id is not None:
            self.parent_span_id = parent_span_id
        elif curr_t and curr_t == self.trace_id:
            self.parent_span_id = _current_span_id.get()
        else:
            self.parent_span_id = None

        self.span_id = generate_span_id()

        self._token_trace = None
        self._token_span = None

    def __enter__(self):
        self._token_trace = _current_trace_id.set(self.trace_id)
        self._token_span = _current_span_id.set(self.span_id)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._token_trace:
            _current_trace_id.reset(self._token_trace)
        if self._token_span:
            _current_span_id.reset(self._token_span)

    @classmethod
    def get_current(cls) -> Tuple[str, Optional[str], Optional[str]]:
        t_id = _current_trace_id.get()
        s_id = _current_span_id.get()
        if not t_id:
            t_id = generate_trace_id()
        return t_id, s_id, None

