"""
NiblitSignalMixin — Freqtrade-compatible Niblit AI signal bridge.

Reads the same JSON sidecar file written by Niblit's TradingBrain.
In live / dry-run mode the signal is consulted inside confirm_trade_entry()
and confirm_trade_exit().  In backtesting the mixin gracefully no-ops
(returns neutral) so strategies work without a running Niblit instance.

Signal file format (set NIBLIT_SIGNAL_FILE env var; default /tmp/niblit_lean_signal.json):
    {
        "signal":      "BUY" | "SELL" | "HOLD",
        "confidence":  0.0 – 1.0,
        "symbol":      "BTC/USDT",
        "price":       65432.10,
        "timestamp":   1713100000,
        "indicators":  { "rsi": 45.2, ... },
        "regime":      "bullish" | "bearish" | "ranging",
        "risk_pct":    0.02
    }
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_SIGNAL_FILE = os.environ.get(
    "NIBLIT_SIGNAL_FILE",
    os.path.join(os.environ.get("TMPDIR", "/tmp"), "niblit_lean_signal.json"),
)
_MAX_SIGNAL_AGE_SECS: int = int(os.environ.get("NIBLIT_SIGNAL_MAX_AGE", "300"))
_NIBLIT_MIN_CONF: float = float(os.environ.get("NIBLIT_MIN_CONF", "0.55"))


class NiblitSignalMixin:
    """
    Mixin for Freqtrade IStrategy subclasses.

    Include this in your strategy's MRO **before** IStrategy:

        class MyStrategy(NiblitSignalMixin, IStrategy):
            ...

    Then call:
        self.niblit_signal()             → "BUY" | "SELL" | "HOLD" | None
        self.niblit_confidence()         → float 0-1
        self.niblit_regime()             → str
        self.niblit_risk_pct(default)    → float
        self.niblit_block_entry(pair, direction)  → True = block trade
    """

    # Class-level cache (shared across all instances) — protected by _niblit_lock
    _niblit_last_read: float = 0.0
    _niblit_last_data: Optional[Dict[str, Any]] = None
    _niblit_lock: threading.Lock = threading.Lock()

    # Override in subclass to tune veto logic
    niblit_min_conf: float = _NIBLIT_MIN_CONF
    niblit_signal_file: str = _DEFAULT_SIGNAL_FILE
    niblit_max_age: int = _MAX_SIGNAL_AGE_SECS

    # ── public helpers ─────────────────────────────────────────────────────

    def niblit_signal(self) -> Optional[str]:
        data = self._niblit_read()
        return data.get("signal") if data else None

    def niblit_confidence(self) -> float:
        data = self._niblit_read()
        return float(data.get("confidence", 0.5)) if data else 0.5

    def niblit_regime(self) -> str:
        data = self._niblit_read()
        return str(data.get("regime", "ranging")) if data else "ranging"

    def niblit_risk_pct(self, default: float = 0.02) -> float:
        data = self._niblit_read()
        return float(data.get("risk_pct", default)) if data else default

    def niblit_block_entry(self, pair: str, is_long: bool) -> bool:
        """
        Return True when Niblit's signal contradicts the proposed trade direction
        with sufficient confidence (>= niblit_min_conf).
        Always returns False during backtesting (no live signal file).
        """
        sig = self.niblit_signal()
        if sig is None:
            return False
        conf = self.niblit_confidence()
        if conf < self.niblit_min_conf:
            return False
        if is_long and sig == "SELL":
            logger.info("Niblit veto LONG entry (sig=SELL conf=%.2f)", conf)
            return True
        if not is_long and sig == "BUY":
            logger.info("Niblit veto SHORT entry (sig=BUY conf=%.2f)", conf)
            return True
        return False

    # ── internal ──────────────────────────────────────────────────────────

    def _niblit_read(self) -> Optional[Dict[str, Any]]:
        now = time.time()
        with NiblitSignalMixin._niblit_lock:
            if now - NiblitSignalMixin._niblit_last_read < 5.0 and \
                    NiblitSignalMixin._niblit_last_data is not None:
                return NiblitSignalMixin._niblit_last_data

            NiblitSignalMixin._niblit_last_read = now
            try:
                path = getattr(self, "niblit_signal_file", _DEFAULT_SIGNAL_FILE)
                if not os.path.isfile(path):
                    NiblitSignalMixin._niblit_last_data = None
                    return None
                with open(path, "r", encoding="utf-8") as fh:
                    data: Dict[str, Any] = json.load(fh)
            except (OSError, ValueError, json.JSONDecodeError):
                NiblitSignalMixin._niblit_last_data = None
                return None

            ts = data.get("timestamp", 0)
            max_age = getattr(self, "niblit_max_age", _MAX_SIGNAL_AGE_SECS)
            if ts and (now - float(ts)) > max_age:
                NiblitSignalMixin._niblit_last_data = None
                return None

            NiblitSignalMixin._niblit_last_data = data
            return data
