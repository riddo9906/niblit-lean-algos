"""Risk policy configuration for the Niblit trading runtime."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class RiskPolicy:
    """Deterministic risk limits for trading decisions."""

    maximum_stake_per_trade: Optional[float] = None
    maximum_stake_percentage: float = 0.10
    maximum_total_exposure: float = 0.50
    maximum_open_positions: int = 5
    maximum_positions_per_symbol: int = 1
    maximum_daily_loss: float = 0.03
    maximum_drawdown: float = 0.15
    maximum_leverage: float = 1.0
    minimum_confidence: float = 0.50
    maximum_decision_age_seconds: float = 300.0
    cooldown_after_loss_seconds: float = 300.0
    allowed_pairs: List[str] = field(default_factory=list)
    blocked_pairs: List[str] = field(default_factory=list)
    allowed_actions: List[str] = field(
        default_factory=lambda: ["hold", "enter_long", "exit_long", "enter_short", "exit_short", "cancel"]
    )

    @classmethod
    def from_env(cls) -> RiskPolicy:
        return cls(
            maximum_stake_per_trade=(
                float(os.getenv("NIBLIT_MAX_STAKE_PER_TRADE")) if os.getenv("NIBLIT_MAX_STAKE_PER_TRADE") else None
            ),
            maximum_stake_percentage=float(os.getenv("NIBLIT_MAX_STAKE_PERCENTAGE", "0.10")),
            maximum_total_exposure=float(os.getenv("NIBLIT_MAX_TOTAL_EXPOSURE", "0.50")),
            maximum_open_positions=int(os.getenv("NIBLIT_MAX_OPEN_POSITIONS", "5")),
            maximum_positions_per_symbol=int(os.getenv("NIBLIT_MAX_POSITIONS_PER_SYMBOL", "1")),
            maximum_daily_loss=float(os.getenv("NIBLIT_MAX_DAILY_LOSS", "0.03")),
            maximum_drawdown=float(os.getenv("NIBLIT_MAX_DRAWDOWN", "0.15")),
            maximum_leverage=float(os.getenv("NIBLIT_MAX_LEVERAGE", "1.0")),
            minimum_confidence=float(os.getenv("NIBLIT_MIN_CONFIDENCE", "0.50")),
            maximum_decision_age_seconds=float(os.getenv("NIBLIT_MAX_DECISION_AGE_SECONDS", "300.0")),
            cooldown_after_loss_seconds=float(os.getenv("NIBLIT_COOLDOWN_AFTER_LOSS_SECONDS", "300.0")),
            allowed_pairs=[p.strip() for p in os.getenv("NIBLIT_ALLOWED_PAIRS", "").split(",") if p.strip()],
            blocked_pairs=[p.strip() for p in os.getenv("NIBLIT_BLOCKED_PAIRS", "").split(",") if p.strip()],
            allowed_actions=[
                a.strip() for a in os.getenv("NIBLIT_ALLOWED_ACTIONS", "hold,enter_long,exit_long,enter_short,exit_short,cancel").split(",") if a.strip()
            ],
        )