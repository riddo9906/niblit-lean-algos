"""Paper trading adapter with deterministic simulated execution."""
from __future__ import annotations

import json
import math
import os
import copy
import tempfile
import threading
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from adapters.base import TradingAdapter
from domain import (
    AdapterHealth,
    Balance,
    ExecutionCommand,
    ExecutionResult,
    MarketState,
    PerformanceSummary,
    Position,
    RuntimeStatus,
)


class PaperAdapter(TradingAdapter):
    """Deterministic internal paper trading adapter.

    Supports balances, positions, orders, fills, realized/unrealized PnL, fees.
    Persists state to a JSON file. Works without Lean or Freqtrade.
    """

    def __init__(self, initial_capital: float = 10000.0, state_file: Optional[str] = None):
        if not math.isfinite(initial_capital) or initial_capital <= 0:
            raise ValueError("initial_capital must be finite and positive")
        self._lock = threading.RLock()
        self._initial_capital = initial_capital
        self._state_file = Path(state_file or os.getenv("NIBLIT_PAPER_STATE_FILE", "paper_state.json"))
        self._running = False
        self._started_at: Optional[datetime] = None
        self._balances: List[Balance] = [Balance(currency=os.getenv("NIBLIT_PAPER_CURRENCY", "USDT"), free=initial_capital, used=0.0, total=initial_capital)]
        self._positions: List[Position] = []
        self._executions: List[Dict[str, Any]] = []
        self._realized_pnl: float = 0.0
        self._fees: float = 0.0
        self._prices: Dict[str, float] = {}
        self._load_state()

    def _load_state(self) -> None:
        if self._state_file.exists():
            try:
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                self._restore(data)
            except Exception as exc:
                raise ValueError(f"Paper account state could not be recovered: {self._state_file}") from exc

    def _restore(self, data: dict) -> None:
        if data.get("executions") and "positions" not in data:
            raise ValueError("Legacy paper state omits positions; select a new paper account file instead of inventing recovery")
        self._initial_capital = data.get("initial_capital", self._initial_capital)
        self._balances = [Balance(**b) for b in data.get("balances", [])] or self._balances
        self._realized_pnl = data.get("realized_pnl", 0.0)
        self._fees = data.get("fees", 0.0)
        self._executions = data.get("executions", [])
        self._prices = data.get("prices", {})
        self._positions = []
        for raw in data.get("positions", []):
            raw = dict(raw)
            for key in ("opened_at", "closed_at"):
                if raw.get(key): raw[key] = datetime.fromisoformat(raw[key])
            self._positions.append(Position(**raw))

    def _state(self) -> dict:
        return {"schema_version": 2, "initial_capital": self._initial_capital, "balances": [asdict(b) for b in self._balances],
                "positions": [asdict(p) for p in self._positions], "prices": self._prices,
                "realized_pnl": self._realized_pnl, "fees": self._fees, "executions": self._executions}

    def _save_state(self) -> None:
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self._state_file.parent, delete=False) as stream:
                temporary = stream.name
                json.dump(self._state(), stream, indent=2, allow_nan=False, default=lambda value: value.isoformat())
                stream.flush()
                os.fsync(stream.fileno())
            for attempt in range(6):
                try:
                    os.replace(temporary, self._state_file)
                    break
                except PermissionError:
                    if attempt == 5: raise
                    time.sleep(.01*(attempt+1))
        finally:
            if temporary and os.path.exists(temporary): os.unlink(temporary)

    async def close(self) -> None:
        self._save_state()

    async def health(self) -> AdapterHealth:
        return AdapterHealth(
            healthy=self._running,
            backend="paper",
            reachable=True,
            authenticated=True,
            runtime_state="running" if self._running else "stopped",
            capabilities={"health", "balances", "positions", "execute", "cancel"},
        )

    async def start(self) -> RuntimeStatus:
        self._running = True
        self._started_at = datetime.now(timezone.utc)
        return RuntimeStatus(state="running", backend="paper", started_at=self._started_at)

    async def stop(self) -> RuntimeStatus:
        self._running = False
        self._save_state()
        return RuntimeStatus(state="stopped", backend="paper", stopped_at=datetime.now(timezone.utc))

    async def get_runtime_status(self) -> RuntimeStatus:
        return RuntimeStatus(state="running" if self._running else "stopped", backend="paper", started_at=self._started_at)

    async def get_balances(self) -> Sequence[Balance]:
        return copy.deepcopy(self._balances)

    async def get_open_positions(self) -> Sequence[Position]:
        return copy.deepcopy([p for p in self._positions if p.status == "open"])

    async def get_closed_positions(self, limit: Optional[int] = None) -> Sequence[Position]:
        closed = [p for p in self._positions if p.status == "closed"]
        if limit:
            closed = closed[-limit:]
        return copy.deepcopy(closed)

    async def get_performance(self) -> PerformanceSummary:
        now = datetime.now(timezone.utc)
        wins = sum(1 for p in self._positions if p.status == "closed" and p.profit_amount > 0)
        losses = sum(1 for p in self._positions if p.status == "closed" and p.profit_amount <= 0)
        total_profit = sum(p.profit_amount for p in self._positions if p.status == "closed" and p.profit_amount > 0)
        total_loss = sum(p.profit_amount for p in self._positions if p.status == "closed" and p.profit_amount <= 0)
        total = wins + losses
        return PerformanceSummary(
            start_time=self._started_at or now,
            end_time=now,
            total_trades=total,
            winning_trades=wins,
            losing_trades=losses,
            total_profit=total_profit,
            total_loss=total_loss,
            win_rate=wins / total if total else 0.0,
        )

    async def get_market_state(self, symbols: Optional[Sequence[str]] = None) -> MarketState:
        from domain import MarketTicker

        sym = symbols[0] if symbols else "BTC/USDT"
        price = self._prices.get(sym, 50000.0)
        ticker = MarketTicker(symbol=sym, bid=price * 0.999, ask=price * 1.001, last=price, volume=1000.0)
        return MarketState(symbol=sym, timestamp=datetime.now(timezone.utc), ticker=ticker, source="paper")

    def set_price(self, symbol: str, price: float) -> None:
        """Mark positions from an explicitly supplied validated provider quote."""
        if not math.isfinite(price) or price <= 0:
            raise ValueError("price must be finite and positive")
        with self._lock:
            if self._prices.get(symbol) == price:
                return
            before=json.loads(json.dumps(self._state(),default=lambda value:value.isoformat()))
            self._prices[symbol] = price
            for position in self._positions:
                if position.status == "open" and position.symbol == symbol:
                    position.current_price = price
                    gross = (price - position.entry_price) * position.quantity * (1 if position.side == "long" else -1)
                    position.profit_amount = gross - position.metadata.get("entry_fee", 0)
                    position.profit_ratio = position.profit_amount / position.stake_amount if position.stake_amount else 0
            try: self._save_state()
            except Exception:
                self._restore(before)
                raise

    @property
    def portfolio_equity(self) -> float:
        return sum(b.total for b in self._balances) + sum(
            (p.current_price - p.entry_price) * p.quantity * (1 if p.side == "long" else -1)
            for p in self._positions if p.status == "open")

    def account(self) -> dict:
        with self._lock:
            return copy.deepcopy({**self._state(), "equity": self.portfolio_equity})

    async def execute_order(self, command: ExecutionCommand) -> ExecutionResult:
        with self._lock:
            if not self._running:
                return ExecutionResult(success=False, status="stopped", error="Paper account is not running")
            if command.decision_id and any(e.get("decision_id") == command.decision_id for e in self._executions):
                return ExecutionResult(success=False, status="duplicate", error="Decision already filled")
            if command.order_type not in (None, "market") or command.leverage not in (None, 1, 1.0):
                return ExecutionResult(success=False, status="unsupported", error="Paper account supports unleveraged market orders")
            before = json.loads(json.dumps(self._state(), default=lambda value: value.isoformat()))
            try:
                return self._fill_order(command)
            except Exception as exc:
                self._restore(before)
                return ExecutionResult(success=False, status="persistence_error", error=str(exc))

    def _fill_order(self, command: ExecutionCommand) -> ExecutionResult:
        if command.action in ("enter_long", "enter_short"):
            stake = command.stake_amount or 0
            if not math.isfinite(stake) or stake <= 0:
                return ExecutionResult(success=False, status="rejected", error="Invalid stake")
            trade_id = str(uuid.uuid4())[:8]
            price = self._prices.get(command.symbol)
            if price is None:
                return ExecutionResult(success=False, status="rejected", error="No validated paper quote for symbol")
            fee = stake * 0.001
            balance = self._balances[0]
            if stake + fee > balance.free:
                return ExecutionResult(success=False, status="rejected", error="Insufficient available capital including fees")
            balance.free -= stake + fee
            balance.used += stake
            balance.total = balance.free + balance.used
            self._fees += fee
            pos = Position(
                position_id=trade_id,
                trade_id=trade_id,
                symbol=command.symbol,
                side="long" if command.action == "enter_long" else "short",
                status="open",
                opened_at=datetime.fromisoformat(command.metadata['market_timestamp']) if command.metadata.get('market_timestamp') else datetime.now(timezone.utc),
                entry_price=price,
                current_price=price,
                profit_amount=-fee,
                profit_ratio=-fee / stake,
                stake_amount=command.stake_amount or 0,
                quantity=(command.stake_amount or 0) / price if price > 0 else 0,
                stop_loss=command.stop_loss,
                take_profit=command.take_profit,
                strategy=command.metadata.get('strategy'),
                entry_tag=command.metadata.get("provider_tag") or command.decision_id,
                metadata={**command.metadata,"entry_fee": fee, "decision_id": command.decision_id},
            )
            self._positions.append(pos)
            self._executions.append({"decision_id": command.decision_id, "trade_id": trade_id, "action": command.action, "symbol": command.symbol, "price": price, "fee": fee, "stake": stake, "timestamp": pos.opened_at.isoformat()})
            self._save_state()
            return ExecutionResult(success=True, status="filled", provider_trade_id=trade_id)
        elif command.action in ("exit_long", "exit_short"):
            for p in self._positions:
                if p.symbol == command.symbol and p.status == "open" and p.side == ("long" if command.action == "exit_long" else "short"):
                    price = self._prices.get(command.symbol, p.entry_price)
                    if p.side == "long":
                        pnl = (price - p.entry_price) * p.quantity
                    else:
                        pnl = (p.entry_price - price) * p.quantity
                    p.status = "closed"
                    p.closed_at = datetime.fromisoformat(command.metadata['market_timestamp']) if command.metadata.get('market_timestamp') else datetime.now(timezone.utc)
                    p.exit_price = price
                    exit_fee = price * p.quantity * 0.001
                    net = pnl - p.metadata.get("entry_fee", 0) - exit_fee
                    p.current_price = price
                    p.profit_amount = net
                    p.profit_ratio = net / p.stake_amount if p.stake_amount > 0 else 0
                    self._realized_pnl += net
                    self._fees += exit_fee
                    balance = self._balances[0]
                    balance.used -= p.stake_amount
                    balance.free += p.stake_amount + pnl - exit_fee
                    balance.total = balance.free + balance.used
                    self._executions.append({"decision_id": command.decision_id, "trade_id": p.trade_id, "action": command.action, "symbol": command.symbol, "price": price, "pnl": net, "fee": exit_fee, "timestamp": p.closed_at.isoformat()})
                    self._save_state()
                    return ExecutionResult(success=True, status="filled", provider_trade_id=p.trade_id)
            return ExecutionResult(success=False, status="no_position", error=f"No open position for {command.symbol}")
        return ExecutionResult(success=False, status="unsupported", error=f"Unsupported action: {command.action}")

    async def cancel_order(self, command: ExecutionCommand) -> ExecutionResult:
        return ExecutionResult(success=True, status="cancelled")
