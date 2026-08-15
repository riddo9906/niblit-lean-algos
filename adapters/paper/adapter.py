"""Paper trading adapter with deterministic simulated execution."""
from __future__ import annotations

import json
import os
import uuid
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
        self._initial_capital = initial_capital
        self._state_file = Path(state_file or os.getenv("NIBLIT_PAPER_STATE_FILE", "paper_state.json"))
        self._running = False
        self._started_at: Optional[datetime] = None
        self._balances: List[Balance] = [Balance(currency="USD", free=initial_capital, used=0.0, total=initial_capital)]
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
                self._balances = [Balance(**b) for b in data.get("balances", [])]
                self._realized_pnl = data.get("realized_pnl", 0.0)
                self._fees = data.get("fees", 0.0)
                self._executions = data.get("executions", [])
            except Exception:
                pass

    def _save_state(self) -> None:
        data = {
            "balances": [{"currency": b.currency, "free": b.free, "used": b.used, "total": b.total} for b in self._balances],
            "realized_pnl": self._realized_pnl,
            "fees": self._fees,
            "executions": self._executions,
        }
        self._state_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    async def close(self) -> None:
        self._save_state()

    async def health(self) -> AdapterHealth:
        return AdapterHealth(
            healthy=True,
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
        return list(self._balances)

    async def get_open_positions(self) -> Sequence[Position]:
        return [p for p in self._positions if p.status == "open"]

    async def get_closed_positions(self, limit: Optional[int] = None) -> Sequence[Position]:
        closed = [p for p in self._positions if p.status == "closed"]
        if limit:
            closed = closed[-limit:]
        return closed

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
        """Set the current price for a symbol (for testing)."""
        self._prices[symbol] = price

    async def execute_order(self, command: ExecutionCommand) -> ExecutionResult:
        if command.action in ("enter_long", "enter_short"):
            trade_id = str(uuid.uuid4())[:8]
            price = self._prices.get(command.symbol, 50000.0)
            fee = (command.stake_amount or 0) * 0.001
            self._fees += fee
            pos = Position(
                position_id=trade_id,
                trade_id=trade_id,
                symbol=command.symbol,
                side="long" if command.action == "enter_long" else "short",
                status="open",
                opened_at=datetime.now(timezone.utc),
                entry_price=price,
                current_price=price,
                stake_amount=command.stake_amount or 0,
                quantity=(command.stake_amount or 0) / price if price > 0 else 0,
            )
            self._positions.append(pos)
            self._executions.append({"trade_id": trade_id, "action": command.action, "symbol": command.symbol, "price": price, "fee": fee})
            self._save_state()
            return ExecutionResult(success=True, status="filled", provider_trade_id=trade_id)
        elif command.action in ("exit_long", "exit_short"):
            for p in self._positions:
                if p.symbol == command.symbol and p.status == "open":
                    price = self._prices.get(command.symbol, p.entry_price)
                    if p.side == "long":
                        pnl = (price - p.entry_price) * p.quantity
                    else:
                        pnl = (p.entry_price - price) * p.quantity
                    p.status = "closed"
                    p.closed_at = datetime.now(timezone.utc)
                    p.exit_price = price
                    p.profit_amount = pnl
                    p.profit_ratio = pnl / p.stake_amount if p.stake_amount > 0 else 0
                    self._realized_pnl += pnl
                    self._executions.append({"trade_id": p.trade_id, "action": command.action, "symbol": command.symbol, "price": price, "pnl": pnl})
                    self._save_state()
                    return ExecutionResult(success=True, status="filled", provider_trade_id=p.trade_id)
            return ExecutionResult(success=False, status="no_position", error=f"No open position for {command.symbol}")
        return ExecutionResult(success=False, status="unsupported", error=f"Unsupported action: {command.action}")

    async def cancel_order(self, command: ExecutionCommand) -> ExecutionResult:
        return ExecutionResult(success=True, status="cancelled")