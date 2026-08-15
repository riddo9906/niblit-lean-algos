"""Map Freqtrade API responses into Niblit domain models."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from domain import (
    AdapterHealth,
    Balance,
    Position,
)
from .errors import InvalidProviderResponseError

RUNTIME_STATE_MAP = {
    "running": "running",
    "stopped": "stopped",
    "reloading": "reloading",
    "initializing": "unconfigured",
    "error": "error",
    "fatal": "error",
}


def normalize_runtime_state(raw: str) -> str:
    """Map a raw Freqtrade state string to the Niblit runtime state."""
    return RUNTIME_STATE_MAP.get(raw.lower(), "unknown")


def _to_utc(value: Any) -> Optional[datetime]:
    """Convert a Freqtrade timestamp value to a timezone-aware UTC datetime.

    Handles:
    * timezone-aware ``datetime``
    * naive ``datetime`` (assumed UTC)
    * Unix integer/float timestamps
    * ISO-8601 strings (including trailing ``Z``)
    * ``None`` → returns ``None``

    Raises ``InvalidProviderResponseError`` for unparseable required timestamps.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            raise InvalidProviderResponseError(
                f"Invalid Unix timestamp: {value!r}"
            ) from None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # Handle trailing 'Z' (Zulu/UTC)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            # Try Unix timestamp as string
            try:
                return datetime.fromtimestamp(float(s), tz=timezone.utc)
            except (ValueError, OverflowError, OSError):
                raise InvalidProviderResponseError(
                    f"Invalid timestamp value: {value!r}"
                ) from None
    raise InvalidProviderResponseError(
        f"Unsupported timestamp type {type(value).__name__}: {value!r}"
    )


def map_health(
    *,
    backend: str,
    reachable: bool,
    authenticated: bool,
    runtime_state: str,
    version: Optional[str] = None,
    latency_ms: Optional[float] = None,
    error: Optional[str] = None,
    capabilities: Optional[List[str]] = None,
) -> AdapterHealth:
    return AdapterHealth(
        healthy=reachable and authenticated,
        backend=backend,
        reachable=reachable,
        authenticated=authenticated,
        runtime_state=runtime_state,
        version=version,
        latency_ms=latency_ms,
        capabilities=capabilities or [],
        error=error,
    )


def map_balance(raw: Dict[str, Any]) -> Balance:
    """Map a Freqtrade /balance entry to a Balance."""
    return Balance(
        currency=str(raw.get("currency", "")),
        free=float(raw.get("free", 0.0)),
        used=float(raw.get("used", 0.0)),
        total=float(raw.get("total", 0.0)),
        usd_value=float(raw["value"]) if raw.get("value") is not None else None,
    )


def map_position(raw: Dict[str, Any], is_open: bool = True) -> Position:
    """Map a Freqtrade trade object to a Position domain model."""
    symbol = str(raw.get("pair", ""))
    return Position(
        position_id=str(raw.get("trade_id", raw.get("id", symbol))),
        trade_id=str(raw.get("trade_id", raw.get("id"))) or None,
        symbol=symbol,
        side="long" if raw.get("is_short") is False else "short" if raw.get("is_short") else "long",
        status="open" if is_open else "closed",
        opened_at=_to_utc(raw.get("open_date", datetime.now(timezone.utc))),
        closed_at=_to_utc(raw["close_date"]) if raw.get("close_date") and not is_open else None,
        entry_price=float(raw.get("open_rate", 0.0) or 0.0),
        current_price=float(raw.get("current_rate", raw.get("close_rate", 0.0)) or 0.0),
        exit_price=float(raw["close_rate"]) if raw.get("close_rate") is not None else None,
        stake_amount=float(raw.get("stake_amount", 0.0) or 0.0),
        quantity=float(raw.get("amount", 0.0) or 0.0),
        profit_amount=float(raw.get("profit_abs", raw.get("profit", 0.0)) or 0.0),
        profit_ratio=float(raw.get("profit_ratio", 0.0) or 0.0),
        stop_loss=float(raw["stop_loss"]) if raw.get("stop_loss") is not None else None,
        take_profit=float(raw["take_profit"]) if raw.get("take_profit") is not None else None,
        leverage=float(raw.get("leverage", 1.0) or 1.0),
        strategy=raw.get("strategy"),
        entry_tag=raw.get("enter_tag"),
        exit_reason=raw.get("exit_reason") if not is_open else None,
        exchange=raw.get("exchange"),
        metadata=dict(raw),
    )