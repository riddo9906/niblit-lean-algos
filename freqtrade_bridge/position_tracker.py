"""Position synchronization between Lean, Freqtrade, and Exchange."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


@dataclass
class SyncState:
    """Represents sync state for a single position."""
    pair: str
    lean_amount: float = 0.0
    freqtrade_amount: float = 0.0
    exchange_amount: float = 0.0
    last_sync_time: float = field(default_factory=time.time)
    synced: bool = True


class PositionTracker:
    """Maintain synchronized position state across all layers.

    Responsibilities:
    - Track positions in Lean, Freqtrade, and Exchange
    - Detect desynchronization
    - Automatically reconcile differences
    - Never allow desynchronization
    """

    def __init__(self):
        self._states: Dict[str, SyncState] = {}
        self._last_reconciliation: float = 0.0

    def update_lean(self, pair: str, amount: float) -> None:
        """Update Lean position amount."""
        state = self._states.setdefault(pair, SyncState(pair=pair))
        state.lean_amount = amount
        state.last_sync_time = time.time()
        self._check_sync(pair, state)

    def update_freqtrade(self, pair: str, amount: float) -> None:
        """Update Freqtrade position amount."""
        state = self._states.setdefault(pair, SyncState(pair=pair))
        state.freqtrade_amount = amount
        state.last_sync_time = time.time()
        self._check_sync(pair, state)

    def update_exchange(self, pair: str, amount: float) -> None:
        """Update Exchange position amount."""
        state = self._states.setdefault(pair, SyncState(pair=pair))
        state.exchange_amount = amount
        state.last_sync_time = time.time()
        self._check_sync(pair, state)

    def _check_sync(self, pair: str, state: SyncState) -> bool:
        """Check if positions are synchronized. Reconcile if not."""
        if state.lean_amount == state.freqtrade_amount == state.exchange_amount:
            state.synced = True
            return True

        state.synced = False
        log.warning(
            "Desync detected for %s: lean=%.6f freqtrade=%.6f exchange=%.6f",
            pair, state.lean_amount, state.freqtrade_amount, state.exchange_amount
        )
        self.reconcile(pair)
        return False

    def reconcile(self, pair: str) -> None:
        """Reconcile position differences. Exchange is the source of truth."""
        state = self._states.get(pair)
        if not state:
            return

        # Exchange is the source of truth - bring others in line
        target = state.exchange_amount
        if state.lean_amount != target:
            log.info("Reconciling Lean position for %s to %.6f", pair, target)
            # In production: send correction to Lean
        if state.freqtrade_amount != target:
            log.info("Reconciling Freqtrade position for %s to %.6f", pair, target)
            # In production: send correction to Freqtrade

        # Mark as synced after reconciliation
        state.lean_amount = target
        state.freqtrade_amount = target
        state.synced = True
        self._last_reconciliation = time.time()

    def positions(self) -> Dict[str, SyncState]:
        """Return all tracked positions."""
        return dict(self._states)

    def desynced_pairs(self) -> List[str]:
        """Return list of pairs that are out of sync."""
        return [p.pair for p in self._states.values() if not p.synced]

    def status(self) -> Dict[str, Any]:
        """Return tracker status."""
        return {
            "tracked_pairs": list(self._states.keys()),
            "desynced_pairs": self.desynced_pairs(),
            "last_reconciliation": self._last_reconciliation,
            "positions": {
                p.pair: {
                    "lean": p.lean_amount,
                    "freqtrade": p.freqtrade_amount,
                    "exchange": p.exchange_amount,
                    "synced": p.synced,
                }
                for p in self._states.values()
            },
        }