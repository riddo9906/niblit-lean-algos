"""SQLite-backed execution repository for idempotent trading execution."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from domain.execution import (
    EXECUTION_STATES,
    FINAL_EXECUTION_STATES,
    RECONCILIATION_STATES,
)
from domain.trading_decision import CANONICAL_CONTRACT_VERSION

# Execution repository schema version. Bumping this does not drop tables; the
# migration below adds columns in place so existing databases keep opening.
SCHEMA_VERSION = "2"

# Columns present in the initial (v1) executions table. Migrations only ADD
# columns — never drop the execution table.
_BASE_COLUMNS = (
    "decision_id",
    "command",
    "backend",
    "request_timestamp",
    "response_timestamp",
    "result",
    "provider_trade_id",
    "status",
)

# New lifecycle columns, appended in-place via ALTER TABLE (migration-safe).
_LIFECYCLE_COLUMN_DDL = {
    "schema_version": "TEXT",
    "execution_mode": "TEXT",
    "symbol": "TEXT",
    "action": "TEXT",
    "requested_stake": "REAL",
    "adjusted_stake": "REAL",
    "leverage": "REAL",
    "validation_result": "TEXT",
    "risk_result": "TEXT",
    "provider_request_id": "TEXT",
    "provider_order_id": "TEXT",
    "provider_tag": "TEXT",
    "reconciliation_status": "TEXT",
    "last_reconciled_at": "TEXT",
    "error_category": "TEXT",
}

_STATE_COLUMNS = EXECUTION_STATES
_RECONCILIATION_COLUMNS = RECONCILIATION_STATES


def _json_default(obj: Any) -> Any:
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    return str(obj)


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, default=_json_default)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExecutionRepository:
    """Persists execution records keyed by decision_id for idempotency.

    Uses one shared SQLite connection with ``check_same_thread=False`` so the
    trading API can serve requests on worker threads distinct from boot.
    All access is serialized through ``threading.RLock`` — SQLite connections
    are not safe for concurrent use even with check_same_thread disabled.

    Schema evolution is migration-safe: new columns are added in place using
    ``ALTER TABLE``. The executions table is never dropped, so existing
    databases continue to open and their records remain queryable.
    """

    def __init__(self, db_path: str = "niblit_executions.db"):
        self._db_path = Path(db_path)
        if str(db_path) != ':memory:':
            self._db_path.parent.mkdir(parents=True,exist_ok=True)
        self._lock = threading.RLock()
        # check_same_thread=False: trading API may serve requests on worker
        # threads distinct from the boot thread that opened the connection.
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        with self._lock:
            self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create the executions table if missing, else migrate in place."""
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
        self._migrate_columns()

    def _migrate_columns(self) -> None:
        """Add any lifecycle columns that are missing (idempotent ALTERs)."""
        existing = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(executions)")
        }
        for col, ddl_type in _LIFECYCLE_COLUMN_DDL.items():
            if col not in existing:
                self._conn.execute(
                    f"ALTER TABLE executions ADD COLUMN {col} {ddl_type}"
                )
        self._conn.commit()

    def record_request(
        self,
        decision_id: str,
        command: Dict[str, Any],
        backend: str,
        execution_mode: Optional[str] = None,
        validation_result: Optional[Dict[str, Any]] = None,
        risk_result: Optional[Dict[str, Any]] = None,
        provider_tag: Optional[str] = None,
        status: str = "received",
    ) -> None:
        """Create (or refresh) an execution record for a decision.

        Backward-compatible entry point. Default status is ``received`` — it is
        NOT ``submitted``. ``submitted`` is only written after a provider
        response proves acceptance.
        """
        with self._lock:
            now = _now()
            command_obj = command or {}
            self._conn.execute(
                """
                INSERT INTO executions (
                    decision_id, command, backend, request_timestamp, status,
                    schema_version, execution_mode, symbol, action,
                    requested_stake, leverage,
                    validation_result, risk_result, provider_tag
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(decision_id) DO UPDATE SET
                    command = excluded.command,
                    backend = excluded.backend,
                    status = excluded.status,
                    schema_version = excluded.schema_version,
                    execution_mode = excluded.execution_mode,
                    symbol = excluded.symbol,
                    action = excluded.action,
                    requested_stake = excluded.requested_stake,
                    leverage = excluded.leverage,
                    validation_result = excluded.validation_result,
                    risk_result = excluded.risk_result,
                    provider_tag = excluded.provider_tag
                """,
                (
                    decision_id,
                    _json_dumps(command_obj),
                    backend,
                    now,
                    status,
                    SCHEMA_VERSION,
                    execution_mode,
                    command_obj.get("symbol"),
                    command_obj.get("action"),
                    command_obj.get("stake_amount"),
                    command_obj.get("leverage"),
                    _json_dumps(validation_result) if validation_result is not None else None,
                    _json_dumps(risk_result) if risk_result is not None else None,
                    provider_tag,
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
        """Record the execution result for a decision (backward-compatible)."""
        with self._lock:
            self._conn.execute(
                """
                UPDATE executions
                SET result = ?, provider_trade_id = ?, status = ?, response_timestamp = ?,
                    provider_request_id = ?
                WHERE decision_id = ?
                """,
                (
                    _json_dumps(result),
                    provider_trade_id,
                    status,
                    _now(),
                    provider_trade_id,
                    decision_id,
                ),
            )
            self._conn.commit()

    def has_decision(self, decision_id: str) -> bool:
        """Return True if an execution record exists for a decision."""
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

    def get_execution(self, decision_id: str) -> Optional[Dict[str, Any]]:
        """Return the full execution record for a decision (or None)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM executions WHERE decision_id = ?", (decision_id,)
            ).fetchone()
            if row is None:
                return None
            return self._row_to_dict(row)

    def list_executions(
        self,
        limit: int = 50,
        status: Optional[str] = None,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return a list of execution records with optional filters."""
        query = "SELECT * FROM executions"
        clauses: List[str] = []
        params: List[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if symbol:
            clauses.append("symbol = ?")
            params.append(symbol)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY request_timestamp DESC LIMIT ?"
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
            return [self._row_to_dict(r) for r in rows]

    def find_by_provider_trade_id(self, trade_id: str) -> List[Dict[str, Any]]:
        """Return execution records matching a provider trade id."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM executions WHERE provider_trade_id = ? OR provider_request_id = ?",
                (trade_id, trade_id),
            ).fetchall()
            return [self._row_to_dict(r) for r in rows]

    # ── Lifecycle transitions ───────────────────────────────────────────────

    def mark_status(
        self,
        decision_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        provider_trade_id: Optional[str] = None,
        error_category: Optional[str] = None,
        provider_tag: Optional[str] = None,
        adjusted_stake: Optional[float] = None,
    ) -> None:
        """Generic lifecycle transition used by all typed marks."""
        with self._lock:
            sets = ["status = ?", "response_timestamp = ?"]
            params: List[Any] = [status, _now()]
            if result is not None:
                sets.append("result = ?")
                params.append(_json_dumps(result))
            if provider_trade_id is not None:
                sets.append("provider_trade_id = ?")
                params.append(provider_trade_id)
            if error_category is not None:
                sets.append("error_category = ?")
                params.append(error_category)
            if provider_tag is not None:
                sets.append("provider_tag = ?")
                params.append(provider_tag)
            if adjusted_stake is not None:
                sets.append("adjusted_stake = ?")
                params.append(adjusted_stake)
            params.append(decision_id)
            self._conn.execute(
                f"UPDATE executions SET {', '.join(sets)} WHERE decision_id = ?",
                params,
            )
            self._conn.commit()

    def mark_submitting(self, decision_id: str, provider_request_id: Optional[str] = None) -> None:
        """Persist that a provider submission is in-flight (NOT yet accepted)."""
        with self._lock:
            if provider_request_id is not None:
                self._conn.execute(
                    "UPDATE executions SET status = ?, provider_request_id = ? WHERE decision_id = ?",
                    ("submitting", provider_request_id, decision_id),
                )
            else:
                self._conn.execute(
                    "UPDATE executions SET status = ? WHERE decision_id = ?",
                    ("submitting", decision_id),
                )
            self._conn.commit()

    def mark_submitted(self, decision_id: str, result: Dict[str, Any],
                       provider_trade_id: Optional[str], provider_tag: Optional[str] = None) -> None:
        """Mark provider-accepted only after a response proves acceptance."""
        self.mark_status(
            decision_id, "submitted", result=result,
            provider_trade_id=provider_trade_id, provider_tag=provider_tag,
        )

    def mark_confirmed_open(self, decision_id: str, result: Optional[Dict[str, Any]] = None) -> None:
        self.mark_status(decision_id, "confirmed_open", result=result)

    def mark_confirmed_closed(self, decision_id: str, result: Optional[Dict[str, Any]] = None) -> None:
        self.mark_status(decision_id, "confirmed_closed", result=result)

    def mark_ambiguous(self, decision_id: str, result: Optional[Dict[str, Any]] = None) -> None:
        self.mark_status(decision_id, "ambiguous", result=result)

    def mark_rejected(self, decision_id: str, error_category: Optional[str] = None) -> None:
        self.mark_status(decision_id, "rejected", error_category=error_category)

    def mark_failed(self, decision_id: str, error_category: Optional[str] = None) -> None:
        self.mark_status(decision_id, "failed", error_category=error_category)

    # ── Reconciliation ──────────────────────────────────────────────────────

    def update_reconciliation(
        self,
        decision_id: str,
        reconciliation_status: str,
        result: Optional[Dict[str, Any]] = None,
        provider_trade_id: Optional[str] = None,
    ) -> None:
        """Persist reconciliation results for a decision."""
        with self._lock:
            self._conn.execute(
                """
                UPDATE executions
                SET reconciliation_status = ?, last_reconciled_at = ?,
                    result = ?, provider_trade_id = ?
                WHERE decision_id = ?
                """,
                (
                    reconciliation_status,
                    _now(),
                    _json_dumps(result) if result is not None else None,
                    provider_trade_id,
                    decision_id,
                ),
            )
            self._conn.commit()

    def _row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        keys = [d[1] for d in self._conn.execute("PRAGMA table_info(executions)")]
        data = dict(zip(keys, row))
        for col in ("command", "result", "validation_result", "risk_result"):
            if data.get(col):
                try:
                    data[col] = json.loads(data[col])
                except (ValueError, TypeError):
                    pass
        return data

    def close(self) -> None:
        with self._lock:
            self._conn.close()

