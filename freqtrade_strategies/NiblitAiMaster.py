"""
Niblit AI Master — Freqtrade flagship strategy.

Advisor entry logic (all must align for a long signal contribution):
  1. Long-term trend:   close > EMA200     (macro bull filter)
  2. Medium trend:      close > EMA50      (intermediate trend)
  3. Short-term signal: EMA9 > EMA21       (fast crossover)
  4. Momentum gate:     ADX > adx_threshold (trending, not choppy)
  5. RSI range:         rsi_buy_min < RSI < rsi_buy_max (not oversold/overbought)
  6. Volume:            volume > 0

Exit signals:
  - EMA9 crosses below EMA21 AND close drops below EMA50  (clear reversal)
  - RSI > rsi_overbought                                  (momentum exhaustion)

Custom exits (live + backtest):
  - Dangerous regime (volatile/crash/bear) → immediate exit
  - Profit protection: if up >1% and EMA9 turns bearish   → lock in gains

Niblit AI role (live only):
  - Strategy contributes directional advice only.
  - Final execution authority is delegated to NiblitSignalMixin + TradeGovernanceGate.
  - Runtime governance modes (normal/cautious/survival/lockdown) can veto, resize, or force exits.

Timeframe: 1h (crypto, Binance).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Optional

import pandas as pd
import pandas_ta as ta  # type: ignore
from freqtrade.strategy import IStrategy, IntParameter

try:
    from .NiblitSignalMixin import NiblitSignalMixin
except ImportError:
    from NiblitSignalMixin import NiblitSignalMixin

logger = logging.getLogger(__name__)

_RESULTS_FILE = os.environ.get(
    "NIBLIT_RESULTS_FILE",
    os.path.join(os.environ.get("TMPDIR", "/tmp"), "niblit_ft_results.json"),
)
_REFLECTION_FILE = os.environ.get(
    "NIBLIT_REFLECTION_FILE",
    os.path.join(os.environ.get("TMPDIR", "/tmp"), "niblit_trade_reflection.jsonl"),
)
_EPISODES_FILE = os.environ.get(
    "NIBLIT_EPISODES_FILE",
    os.path.join(os.environ.get("TMPDIR", "/tmp"), "niblit_market_episodes.jsonl"),
)

try:
    from .governance_contract import (  # noqa: E402
        EVENT_TRADE_REFLECTION_INGESTED,
        EVENT_MARKET_EPISODE_INGESTED,
        EVENT_REFLECTION_COMPLETE,
    )
except ImportError:
    try:
        from governance_contract import (  # noqa: E402
            EVENT_TRADE_REFLECTION_INGESTED,
            EVENT_MARKET_EPISODE_INGESTED,
            EVENT_REFLECTION_COMPLETE,
        )
    except ImportError:
        EVENT_TRADE_REFLECTION_INGESTED = "trade_reflection.ingested"
        EVENT_MARKET_EPISODE_INGESTED = "market_episode.ingested"
        EVENT_REFLECTION_COMPLETE = "reflection.complete"


class NiblitAiMaster(NiblitSignalMixin, IStrategy):
    """Niblit AI Master — Freqtrade edition."""

    _ALIGNMENT_VETOED_BUT_POSITIVE = 0.35
    _ALIGNMENT_ALLOWED_BUT_NEGATIVE = 0.40
    _ALIGNMENT_SURVIVAL_WITH_LOW_HEALTH = 0.85
    _ALIGNMENT_DEFAULT = 0.70
    _LOW_RUNTIME_HEALTH = 0.5

    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = False

    # Staged ROI: take quick profits early, hold winners progressively longer
    minimal_roi = {"0": 0.05, "30": 0.03, "60": 0.02, "120": 0.01}

    # Tighter hard stop — pairs with -3% losses dominated the bad exits
    stoploss = -0.015

    # Trail after gains appear so winners don't round-trip to losses
    trailing_stop = True
    trailing_stop_positive = 0.01          # activate trailing once up 1%
    trailing_stop_positive_offset = 0.02   # lock floor at entry+1% when at +2%
    trailing_only_offset_is_reached = True

    # EMA200 needs 200 candles; set a buffer so indicators are fully warmed
    startup_candle_count: int = 200

    # ── hyperopt parameters ───────────────────────────────────────────────

    # ADX strength gate — filter out choppy, range-bound markets
    adx_threshold = IntParameter(15, 35, default=20, space="buy", optimize=True)

    # RSI entry window for longs — avoid both oversold traps and overbought entries
    rsi_buy_min = IntParameter(30, 50, default=40, space="buy", optimize=True)
    rsi_buy_max = IntParameter(55, 75, default=65, space="buy", optimize=True)

    # RSI overbought exit level
    rsi_overbought = IntParameter(70, 85, default=75, space="sell", optimize=True)

    # ── lifecycle ─────────────────────────────────────────────────────────

    def bot_start(self, **kwargs) -> None:
        self._trade_count: int   = 0
        self._win_count:   int   = 0
        self._total_pnl:   float = 0.0
        self._last_regime: Optional[str] = None

    # ── indicators ────────────────────────────────────────────────────────

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        # Short-term trend
        dataframe["ema9"]   = ta.ema(dataframe["close"], length=9)
        dataframe["ema21"]  = ta.ema(dataframe["close"], length=21)

        # Medium-term trend
        dataframe["ema50"]  = ta.ema(dataframe["close"], length=50)

        # Long-term macro trend filter
        dataframe["ema200"] = ta.ema(dataframe["close"], length=200)

        # Momentum / oscillator
        dataframe["rsi"]    = ta.rsi(dataframe["close"], length=14)

        # ADX — trend strength gate (avoids choppy, ranging markets)
        adx_result = ta.adx(dataframe["high"], dataframe["low"], dataframe["close"], length=14)
        if adx_result is not None and not adx_result.empty:
            adx_col = [c for c in adx_result.columns if c.upper().startswith("ADX_")]
            if adx_col:
                dataframe["adx"] = adx_result[adx_col[0]]
        if "adx" not in dataframe.columns:
            dataframe["adx"] = 0.0

        # Volatility — used in custom_exit profit protection
        dataframe["atr"]    = ta.atr(dataframe["high"], dataframe["low"],
                                     dataframe["close"], length=14)
        return dataframe

    # ── entry signals (fully vectorised) ─────────────────────────────────

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        # Long-term macro trend (EMA200 filter)
        macro_bull  = dataframe["close"] > dataframe["ema200"]
        # Medium trend alignment
        mid_bull    = dataframe["close"] > dataframe["ema50"]
        # Short-term EMA crossover
        ema_bull    = dataframe["ema9"]  > dataframe["ema21"]
        # Momentum gate — ADX must show a trending market
        adx_ok      = dataframe["adx"]   > self.adx_threshold.value
        # RSI in healthy buy zone — not a blind oversold bounce
        rsi_in_zone = (
            (dataframe["rsi"] > self.rsi_buy_min.value) &
            (dataframe["rsi"] < self.rsi_buy_max.value)
        )
        vol_ok      = dataframe["volume"] > 0

        dataframe.loc[
            macro_bull & mid_bull & ema_bull & adx_ok & rsi_in_zone & vol_ok,
            "enter_long"
        ] = 1

        # (Shorts disabled: can_short = False — column kept for interface compliance)
        return dataframe

    # ── exit signals (fully vectorised) ──────────────────────────────────

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        # Clear trend reversal: fast EMA crossed below slow AND price lost medium support
        ema_reversal  = (dataframe["ema9"] < dataframe["ema21"]) & \
                        (dataframe["close"] < dataframe["ema50"])
        # Momentum exhaustion: RSI overbought
        rsi_exhausted = dataframe["rsi"] > self.rsi_overbought.value

        dataframe.loc[ema_reversal | rsi_exhausted, "exit_long"] = 1

        return dataframe

    # ── live hooks ────────────────────────────────────────────────────────

    def confirm_trade_entry(self, pair: str, order_type: str, amount: float,
                            rate: float, time_in_force: str, current_time,
                            entry_tag, side: str, **kwargs) -> bool:
        return self.niblit_allow_entry(pair, side == "long")

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake: Optional[float],
                            max_stake: float, leverage: float,
                            entry_tag: Optional[str], side: str, **kwargs) -> float:
        return super().custom_stake_amount(
            pair=pair,
            current_time=current_time,
            current_rate=current_rate,
            proposed_stake=proposed_stake,
            min_stake=min_stake,
            max_stake=max_stake,
            leverage=leverage,
            entry_tag=entry_tag,
            side=side,
            **kwargs,
        )

    def custom_exit(self, pair: str, trade, current_time: datetime,
                    current_rate: float, current_profit: float,
                    **kwargs) -> Optional[str]:
        """Regime-based force-exit and profit-protection early exit."""
        is_long = not getattr(trade, "is_short", False)

        if is_long and self.niblit_should_force_exit(is_long=True):
            decision = getattr(self, "_niblit_last_decision", {})
            mode = decision.get("mode", "constrained")
            logger.info("NiblitAiMaster: force-exit long — governance_mode=%s pair=%s", mode, pair)
            return f"governance_{mode}"

        # Profit protection: if we're up and the fast EMA has turned against us, exit early
        # rather than waiting for the full trailing stop to fire.
        if is_long and current_profit > 0.01:
            try:
                dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
                if not dataframe.empty:
                    last = dataframe.iloc[-1]
                    ema9  = last.get("ema9",  float("nan"))
                    ema21 = last.get("ema21", float("nan"))
                    if ema9 < ema21:
                        logger.info(
                            "NiblitAiMaster: profit-protect exit — pair=%s profit=%.2f%%",
                            pair, current_profit * 100,
                        )
                        return "profit_protect"
            except (AttributeError, KeyError, IndexError, TypeError, ValueError):
                pass

        return None

    def confirm_trade_exit(self, pair: str, trade, order_type: str, amount: float,
                           rate: float, time_in_force: str, exit_reason: str,
                           current_time, **kwargs) -> bool:
        return True

    # ── results write-back ────────────────────────────────────────────────

    def bot_loop_start(self, current_time: datetime, **kwargs) -> None:
        """Periodically persist performance metrics so Niblit can read them."""
        try:
            snapshot = self.niblit_execution_snapshot()
            envelope = snapshot.get("envelope", {})
            governance = envelope.get("governance", {})
            temporal = envelope.get("temporal", {})
            forecast = envelope.get("forecast_consensus", {})
            runtime = envelope.get("runtime", {})
            resources = envelope.get("resources", {})
            reflection = envelope.get("reflection", {})
            trace = envelope.get("trace", {})

            results = {
                "source":      "freqtrade",
                "trade_count": getattr(self, "_trade_count", 0),
                "win_count":   getattr(self, "_win_count", 0),
                "total_pnl":   round(getattr(self, "_total_pnl", 0.0), 2),
                "timestamp":   current_time.isoformat() if hasattr(current_time, "isoformat") else str(current_time),
                "niblit_signal":   self.niblit_signal(),
                "niblit_regime":   self.niblit_regime(),
                "niblit_conf":     self.niblit_confidence(),
                "runtime_mode": runtime.get("mode", "normal"),
                "coherence_score": temporal.get("coherence_score", 0.0),
                "forecast_agreement": forecast.get("agreement", 0.0),
                "forecast_uncertainty": forecast.get("uncertainty", 1.0),
                "governance_authority": governance.get("authority", "unknown"),
                "governance_constitution_passed": governance.get("constitution_passed", True),
                "governance_mode": governance.get("governance_mode", runtime.get("mode", "normal")),
                "attention_pressure": runtime.get("attention_pressure", 0.0),
                "runtime_pressure": runtime.get("runtime_pressure", runtime.get("attention_pressure", 0.0)),
                "runtime_health": runtime.get("runtime_health", 0.8),
                "model_orchestration_state": runtime.get("model_orchestration_state", "unknown"),
                "cognitive_budget": resources.get("cognitive_budget", 1.0),
                "attention_available": resources.get("attention_available", 1.0),
                "model_consensus": envelope.get("model_consensus", forecast.get("agreement", 0.0)),
                "strategy_disagreement": envelope.get("strategy_disagreement", 0.0),
                "coherence_drift": envelope.get("coherence_drift", 0.0),
                "governance_confidence": envelope.get("governance_confidence", governance.get("governance_stability", 0.0)),
                "reflection_confidence": reflection.get("reflection_confidence", self.niblit_confidence()),
                "model_trust": envelope.get("model_trust", reflection.get("reflection_confidence", 0.0)),
                "execution_risk": envelope.get("execution_risk", (envelope.get("risk") or {}).get("emergence_risk", 0.0)),
                "causal_trace_id": trace.get("causal_trace_id"),
                "epoch_id": temporal.get("epoch_id"),
                "epoch_alignment": temporal.get("epoch_alignment", "aligned"),
                "governance_decision": snapshot.get("decision", {}),
                "envelope_schema_version": envelope.get("schema_version", "unknown"),
            }
            results["reconciliation"] = self._build_outcome_reconciliation(results)
            with open(_RESULTS_FILE, "w", encoding="utf-8") as fh:
                json.dump(results, fh, indent=2)
            self._emit_reflection_event(results)
            self._emit_regime_episode(results)
        except (OSError, ValueError, TypeError) as exc:
            logger.warning("Could not write results file: %s", exc)

    def _emit_reflection_event(self, results: dict) -> None:
        event = {
            "event": EVENT_TRADE_REFLECTION_INGESTED,
            "timestamp": results.get("timestamp"),
            "decision": results.get("governance_decision", {}),
            "regime": results.get("niblit_regime"),
            "confidence": results.get("niblit_conf"),
            "coherence": results.get("coherence_score"),
            "forecast_agreement": results.get("forecast_agreement"),
            "forecast_uncertainty": results.get("forecast_uncertainty"),
            "runtime_mode": results.get("runtime_mode"),
            "governance_mode": results.get("governance_mode"),
            "attention_pressure": results.get("attention_pressure"),
            "runtime_health": results.get("runtime_health"),
            "model_consensus": results.get("model_consensus"),
            "strategy_disagreement": results.get("strategy_disagreement"),
            "coherence_drift": results.get("coherence_drift"),
            "governance_confidence": results.get("governance_confidence"),
            "reflection_confidence": results.get("reflection_confidence"),
            "model_trust": results.get("model_trust"),
            "execution_risk": results.get("execution_risk"),
            "causal_trace_id": results.get("causal_trace_id"),
            "epoch_id": results.get("epoch_id"),
            "total_pnl": results.get("total_pnl"),
            "trade_count": results.get("trade_count"),
            "win_count": results.get("win_count"),
            "reconciliation": results.get("reconciliation", {}),
        }
        self._append_jsonl(_REFLECTION_FILE, event)
        self._append_jsonl(_EPISODES_FILE, {
            "event": EVENT_REFLECTION_COMPLETE,
            "timestamp": results.get("timestamp"),
            "regime": results.get("niblit_regime"),
            "causal_trace_id": results.get("causal_trace_id"),
            "epoch_id": results.get("epoch_id"),
            "reconciliation": results.get("reconciliation", {}),
        })

    def _emit_regime_episode(self, results: dict) -> None:
        regime = str(results.get("niblit_regime", "unknown"))
        if regime == self._last_regime:
            return
        self._last_regime = regime
        envelope = self.niblit_envelope() or {}
        advisors = envelope.get("advisors", {})
        governance = envelope.get("governance", {})
        episode = {
            "event": EVENT_MARKET_EPISODE_INGESTED,
            "timestamp": results.get("timestamp"),
            "regime": regime,
            "scenario": envelope.get("world_model", {}).get("scenario", "unknown"),
            "advisor_votes": advisors.get("votes", {}),
            "governance_authority": governance.get("authority", "unknown"),
            "governance_constitution_passed": governance.get("constitution_passed", True),
            "governance_mode": governance.get("governance_mode", "normal"),
            "model_consensus": envelope.get("model_consensus", 0.0),
            "strategy_disagreement": envelope.get("strategy_disagreement", 0.0),
            "attention_pressure": (envelope.get("runtime") or {}).get("attention_pressure", 0.0),
            "cognitive_budget": (envelope.get("resources") or {}).get("cognitive_budget", 1.0),
            "causal_trace_id": (envelope.get("trace") or {}).get("causal_trace_id"),
            "realized_total_pnl": results.get("total_pnl"),
        }
        self._append_jsonl(_EPISODES_FILE, episode)

    @staticmethod
    def _append_jsonl(path: str, payload: dict) -> None:
        try:
            dir_path = os.path.dirname(path)
            if dir_path:
                os.makedirs(dir_path, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload) + "\n")
        except OSError as exc:
            logger.warning("Unable to append event file at %s: %s", path, exc)

    def _build_outcome_reconciliation(self, results: dict) -> dict:
        """Reconcile predicted regime, action, and realized outcomes."""
        decision = results.get("governance_decision", {}) or {}
        reasons = decision.get("reasons", []) if isinstance(decision.get("reasons", []), list) else []
        predicted_regime = str(results.get("niblit_regime", "unknown"))
        runtime_state = {
            "runtime_mode": results.get("runtime_mode", "normal"),
            "governance_mode": results.get("governance_mode", "normal"),
            "runtime_health": float(results.get("runtime_health", 0.8)),
            "runtime_pressure": float(results.get("runtime_pressure", results.get("attention_pressure", 0.2))),
            "model_orchestration_state": results.get("model_orchestration_state", "unknown"),
        }

        executed_action = "enter_allowed" if decision.get("allow", True) else "entry_vetoed"
        if any(reason in {"survival_mode", "lockdown_mode"} for reason in reasons):
            executed_action = "survival_hardened"

        total_pnl = float(results.get("total_pnl", 0.0))
        trade_count = int(results.get("trade_count", 0))
        win_count = int(results.get("win_count", 0))
        downstream_volatility = float(results.get("forecast_uncertainty", 1.0))

        if trade_count <= 0:
            actual_outcome = "insufficient_data"
        elif total_pnl > 0:
            actual_outcome = "positive_realization"
        elif total_pnl < 0:
            actual_outcome = "negative_realization"
        else:
            actual_outcome = "flat_realization"

        alignment_score = 1.0
        if executed_action == "entry_vetoed" and total_pnl > 0:
            alignment_score = self._ALIGNMENT_VETOED_BUT_POSITIVE
        elif executed_action == "enter_allowed" and total_pnl < 0:
            alignment_score = self._ALIGNMENT_ALLOWED_BUT_NEGATIVE
        elif executed_action == "survival_hardened" and runtime_state["runtime_health"] < self._LOW_RUNTIME_HEALTH:
            alignment_score = self._ALIGNMENT_SURVIVAL_WITH_LOW_HEALTH
        else:
            alignment_score = self._ALIGNMENT_DEFAULT

        confidence_evolution = {
            "signal_confidence": float(results.get("niblit_conf", 0.5)),
            "reflection_confidence": float(results.get("reflection_confidence", 0.5)),
            "governance_confidence": float(results.get("governance_confidence", 0.5)),
            "model_trust": float(results.get("model_trust", 0.5)),
        }

        return {
            "predicted_regime": predicted_regime,
            "executed_action": executed_action,
            "actual_outcome": actual_outcome,
            "downstream_volatility": downstream_volatility,
            "runtime_state": runtime_state,
            "alignment_score": round(max(0.0, min(1.0, alignment_score)), 4),
            "win_rate": round((win_count / trade_count), 4) if trade_count > 0 else 0.0,
            "confidence_evolution": confidence_evolution,
            "veto_reasons": reasons,
            "governance_overrides": decision.get("overrides", {}),
        }
