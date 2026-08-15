"""SQLite-backed execution repository for idempotent trading execution."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


def _json_default(obj: Any) -> Any:
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, default=_json_default)


class ExecutionRepository:
    """Persists execution records keyed by decision_id for idempotency.

    Uses one shared SQLite connection with ``check_same_thread=False`` so the
    trading API can serve requests on worker threads distinct from boot.
    All access is serialized through ``threading.RLock`` — SQLite connections
    are not safe for concurrent use even with check_same_thread disabled.
    """

    def __init__(self, db_path: str = "niblit_executions.db"):
        self._db_path = Path(db_path)
        self._lock = threading.RLock()
        # check_same_thread=False: trading API may serve requests on worker
        # threads distinct from the boot thread that opened the connection.
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        with self._lock:
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS executions (
                    decision_id TEXT PRIMARY KEY,
                    command TEXT NOT NULL,
                    backend TEXT NOT NULL,
                    request_timestamp TEXT NOT NULL,
                    response_timestamp TEXT,
                    result TEXT,
                    provider_trade_id TEXT,
                    status TEXT NOT NULL
                )
            """)
            self._conn.commit()

    def record_request(self, decision_id: str, command: Dict[str, Any], backend: str) -> None:
        """Record that an execution request was made for a decision."""
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO executions
                (decision_id, command, backend, request_timestamp, status)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    _json_dumps(command),
                    backend,
                    datetime.now(timezone.utc).isoformat(),
                    "submitted",
                ),
            )
            self._conn.commit()

    def record_result(
        self,
        decision_id: str,
        result: Dict[str, Any],
        provider_trade_id: Optional[str],
        status: str,
    ) -> None:
        """Record the execution result for a decision."""
        with self._lock:
            self._conn.execute(
                """
                UPDATE executions
                SET result = ?, provider_trade_id = ?, status = ?, response_timestamp = ?
                WHERE decision_id = ?
                """,
                (
                    _json_dumps(result),
                    provider_trade_id,
                    status,
                    datetime.now(timezone.utc).isoformat(),
                    decision_id,
                ),
            )
            self._conn.commit()

    def has_decision(self, decision_id: str) -> bool:
        """Return True if a decision has already been executed."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM executions WHERE decision_id = ?", (decision_id,)
            ).fetchone()
            return row is not None

    def get_status(self, decision_id: str) -> Optional[str]:
        """Return the persisted status for a decision."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM executions WHERE decision_id = ?", (decision_id,)
            ).fetchone()
            return row[0] if row else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()
