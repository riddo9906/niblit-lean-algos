"""Deterministic risk engine for the Niblit trading runtime."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import List, Optional, Sequence

from domain import (
    Position,
    RiskAssessment,
    TradingDecision,
)

from .policy import RiskPolicy


class RiskEngine:
    """Evaluates trading decisions against a deterministic risk policy.

    The LLM may propose a trade, but risk policy always wins.
    """

    def __init__(self, policy: Optional[RiskPolicy] = None):
        self._policy = policy or RiskPolicy.from_env()

    def assess(
        self,
        decision: TradingDecision,
        portfolio_value: float,
        open_positions: Sequence[Position],
        realized_pnl_today: float = 0.0,
    ) -> RiskAssessment:
        violations: List[str] = []
        warnings: List[str] = []
        adjusted_stake: Optional[float] = None
        risk_score = 0.0

        # Confidence
        if not 0.0 <= decision.confidence <= 1.0:
            violations.append("confidence_out_of_range")
        elif decision.confidence < self._policy.minimum_confidence:
            violations.append("confidence_below_minimum")

        # Allowed actions
        if decision.action not in self._policy.allowed_actions:
            violations.append(f"action_not_allowed:{decision.action}")

        # Pair policy
        if self._policy.blocked_pairs and decision.symbol in self._policy.blocked_pairs:
            violations.append("symbol_blacklisted")
        if self._policy.allowed_pairs and decision.symbol not in self._policy.allowed_pairs:
            violations.append("symbol_not_allowed")

        # Decision age
        age_seconds = (datetime.now(timezone.utc) - decision.created_at).total_seconds()
        if age_seconds > self._policy.maximum_decision_age_seconds:
            violations.append("decision_expired")

        # Leverage
        leverage = decision.leverage or 1.0
        if leverage > self._policy.maximum_leverage:
            violations.append("leverage_exceeds_limit")

        # Open positions
        if len(open_positions) >= self._policy.maximum_open_positions:
            violations.append("maximum_open_positions_exceeded")

        # Per-symbol exposure
        symbol_positions = [p for p in open_positions if p.symbol == decision.symbol]
        if len(symbol_positions) >= self._policy.maximum_positions_per_symbol:
            violations.append("maximum_positions_per_symbol_exceeded")

        # Stake sizing
        stake = decision.stake_amount or 0.0
        if stake <= 0:
            violations.append("stake_amount_non_positive")
        else:
            stake_pct = stake / portfolio_value if portfolio_value > 0 else 1.0
            max_stake = portfolio_value * self._policy.maximum_stake_percentage
            if self._policy.maximum_stake_per_trade and stake > self._policy.maximum_stake_per_trade:
                adjusted_stake = self._policy.maximum_stake_per_trade
                if adjusted_stake > max_stake:
                    adjusted_stake = max_stake
                warnings.append(f"stake_reduced_to:{adjusted_stake}")
            elif stake > max_stake:
                adjusted_stake = max_stake
                warnings.append(f"stake_reduced_to:{adjusted_stake}")

        # Daily loss
        if portfolio_value > 0 and realized_pnl_today < -portfolio_value * self._policy.maximum_daily_loss:
            violations.append("daily_loss_exceeded")

        # Compute risk score (deterministic)
        if stake and portfolio_value > 0:
            risk_score = min(1.0, (stake / portfolio_value) / self._policy.maximum_stake_percentage)
        risk_score = risk_score * 0.5 + (decision.confidence * 0.5)

        return RiskAssessment(
            approved=len(violations) == 0,
            risk_score=round(risk_score, 4),
            violations=violations,
            warnings=warnings,
            adjusted_stake_amount=adjusted_stake,
            evaluated_at=datetime.now(timezone.utc),
        )