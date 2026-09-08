"""
In-Process Live Event Publisher for AgentAssure
"""

import threading
from typing import Any, Callable, Dict, List
from agentassure.logging import log_runtime


class EventPublisher:
    """
    Thread-safe in-process event publisher.
    Guarantees:
    - Zero impact on agent execution if no subscribers or if a subscriber errors.
    - Non-blocking error handling.
    - Observability via structured runtime logs.
    """

    def __init__(self):
        self._subscribers: List[Callable[[Dict[str, Any]], Any]] = []
        self._lock = threading.Lock()

    def subscribe(self, callback: Callable[[Dict[str, Any]], Any]):
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[Dict[str, Any]], Any]):
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def publish(self, event_data: Dict[str, Any]):
        with self._lock:
            subscribers = list(self._subscribers)

        trace_id = event_data.get("trace_id")
        span_id = event_data.get("span_id")
        event_id = event_data.get("event_id")

        if not subscribers:
            # Still observable in debug/trace logs
            log_runtime(
                level="DEBUG",
                component="publisher",
                message=f"Event {event_id} published with no active listeners",
                trace_id=trace_id,
                span_id=span_id,
                event_id=event_id,
            )
            return

        for callback in subscribers:
            try:
                callback(event_data)
            except Exception as e:
                # Subscriber failure must NEVER crash runtime or agent
                log_runtime(
                    level="WARN",
                    component="publisher",
                    message=f"Subscriber callback error: {str(e)}",
                    trace_id=trace_id,
                    span_id=span_id,
                    event_id=event_id,
                )


default_event_publisher = EventPublisher()
