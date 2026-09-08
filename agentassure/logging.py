"""
Correlated Structured Logging for AgentAssure Runtime & Control Plane
Persists structured logs to SQLite and in-memory buffer with correlation queries.
"""

from collections import deque
from datetime import datetime, timezone
import json
import logging
import os
import sqlite3
import threading
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger("agentassure")


def current_iso_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class LogEntry(BaseModel):
    timestamp: str = Field(default_factory=current_iso_timestamp)
    level: str = "INFO"
    component: str = "runtime"
    message: str
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    event_id: Optional[str] = None
    policy_id: Optional[str] = None
    policy_version: Optional[int] = None
    decision: Optional[str] = None
    tool_name: Optional[str] = None
    extra: Dict[str, Any] = Field(default_factory=dict)

    def to_formatted_string(self) -> str:
        parts = [self.level.upper()]
        if self.trace_id:
            parts.append(f"trace={self.trace_id}")
        if self.span_id:
            parts.append(f"span={self.span_id}")
        if self.event_id:
            parts.append(f"event={self.event_id}")
        if self.component:
            parts.append(f"component={self.component}")
        if self.policy_id:
            parts.append(f"policy={self.policy_id}")
        if self.decision:
            parts.append(f"decision={self.decision}")
        if self.tool_name:
            parts.append(f"tool={self.tool_name}")

        prefix = " ".join(parts)
        return f"{prefix} \"{self.message}\""


class LogBuffer:
    """Thread-safe circular log buffer with SQLite persistence for querying correlated runtime logs."""

    def __init__(self, maxlen: int = 10000, db_path: Optional[str] = None):
        self._buffer: deque[LogEntry] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self.db_path = db_path or os.environ.get("AGENTASSURE_DB", "demo_evidence.db")
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS runtime_logs (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL,
                        level TEXT NOT NULL,
                        component TEXT NOT NULL,
                        message TEXT NOT NULL,
                        trace_id TEXT,
                        span_id TEXT,
                        event_id TEXT,
                        policy_id TEXT,
                        policy_version INTEGER,
                        decision TEXT,
                        tool_name TEXT,
                        extra TEXT
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_trace ON runtime_logs(trace_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_logs_event ON runtime_logs(event_id)")
                conn.commit()
        except Exception:
            pass

    def append(self, entry: LogEntry):
        with self._lock:
            self._buffer.append(entry)

        # Persist to SQLite
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO runtime_logs (
                        timestamp, level, component, message, trace_id, span_id,
                        event_id, policy_id, policy_version, decision, tool_name, extra
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    entry.timestamp,
                    entry.level,
                    entry.component,
                    entry.message,
                    entry.trace_id,
                    entry.span_id,
                    entry.event_id,
                    entry.policy_id,
                    entry.policy_version,
                    entry.decision,
                    entry.tool_name,
                    json.dumps(entry.extra),
                ))
                conn.commit()
        except Exception:
            pass

    def get_logs(
        self,
        trace_id: Optional[str] = None,
        event_id: Optional[str] = None,
        level: Optional[str] = None,
        limit: int = 200,
    ) -> List[LogEntry]:
        # Query SQLite first if table exists
        try:
            query = "SELECT * FROM runtime_logs"
            params = []
            conditions = []
            if trace_id:
                conditions.append("trace_id = ?")
                params.append(trace_id)
            if event_id:
                conditions.append("event_id = ?")
                params.append(event_id)
            if level:
                conditions.append("level = ?")
                params.append(level.upper())

            if conditions:
                query += " WHERE " + " AND ".join(conditions)
            query += " ORDER BY id DESC LIMIT ?"
            params.append(limit)

            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.cursor()
                cursor.execute(query, params)
                rows = cursor.fetchall()
                if rows:
                    results = []
                    for row in rows:
                        data = dict(row)
                        if data.get("extra"):
                            try:
                                data["extra"] = json.loads(data["extra"])
                            except Exception:
                                data["extra"] = {}
                        results.append(LogEntry.model_validate(data))
                    return list(reversed(results))
        except Exception:
            pass

        # Fallback to in-memory buffer
        with self._lock:
            results = []
            for entry in reversed(self._buffer):
                if trace_id and entry.trace_id != trace_id:
                    continue
                if event_id and entry.event_id != event_id:
                    continue
                if level and entry.level.upper() != level.upper():
                    continue
                results.append(entry)
                if len(results) >= limit:
                    break
            return list(reversed(results))

    def clear(self):
        with self._lock:
            self._buffer.clear()


default_log_buffer = LogBuffer()


def log_runtime(
    level: str,
    component: str,
    message: str,
    trace_id: Optional[str] = None,
    span_id: Optional[str] = None,
    event_id: Optional[str] = None,
    policy_id: Optional[str] = None,
    policy_version: Optional[int] = None,
    decision: Optional[str] = None,
    tool_name: Optional[str] = None,
    **extra,
) -> LogEntry:
    entry = LogEntry(
        level=level.upper(),
        component=component,
        message=message,
        trace_id=trace_id,
        span_id=span_id,
        event_id=event_id,
        policy_id=policy_id,
        policy_version=policy_version,
        decision=decision,
        tool_name=tool_name,
        extra=extra,
    )
    default_log_buffer.append(entry)

    lvl = level.lower()
    if lvl == "warn":
        log_func = logger.warning
    else:
        log_func = getattr(logger, lvl, logger.info)
    log_func(entry.to_formatted_string())

    return entry
