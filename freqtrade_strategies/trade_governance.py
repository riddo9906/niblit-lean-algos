"""Trade governance gate for cognitive execution envelopes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class GovernanceDecision:
    """Decision returned by TradeGovernanceGate."""

    allow: bool
    mode: str
    reasons: List[str] = field(default_factory=list)
    overrides: Dict[str, Any] = field(default_factory=dict)


class TradeGovernanceGate:
    """Applies constitutional and runtime safety controls before order execution."""

    survival_coherence_threshold: float = 0.30
    cautious_coherence_threshold: float = 0.52
    constrained_coherence_threshold: float = 0.45
    max_uncertainty: float = 0.55
    min_agreement: float = 0.40
    max_attention_pressure: float = 0.85
    max_runtime_pressure: float = 0.80
    min_cognitive_budget: float = 0.10
    max_coherence_drift: float = 0.20
    min_model_trust: float = 0.35

    # Regime-aware execution caps:
    # - 0.0 means force HOLD (no new risk) for extreme instability regimes.
    # - 0.01/0.02/0.03 progressively constrain exposure as uncertainty rises.
    # - Keys include richer world-model identities published by the envelope.
    _REGIME_POSITION_CAP = {
        "volatile_breakout": 0.03,
        "liquidity_trap": 0.01,
        "mean_reversion_compression": 0.02,
        "panic_capitulation": 0.0,
        "distribution_phase": 0.02,
        "late_trend_exhaustion": 0.02,
        "synthetic_pump": 0.01,
        "news_driven_instability": 0.01,
        "volatile": 0.0,
        "crash": 0.0,
        "bear": 0.0,
        "ranging": 0.02,
        "sideways": 0.02,
    }

    def status(self) -> Dict[str, float]:
        """Expose active governance thresholds for runtime observability."""
        return {
            "survival_coherence_threshold": self.survival_coherence_threshold,
            "cautious_coherence_threshold": self.cautious_coherence_threshold,
            "constrained_coherence_threshold": self.constrained_coherence_threshold,
            "max_uncertainty": self.max_uncertainty,
            "min_agreement": self.min_agreement,
            "max_attention_pressure": self.max_attention_pressure,
            "max_runtime_pressure": self.max_runtime_pressure,
            "min_cognitive_budget": self.min_cognitive_budget,
            "max_coherence_drift": self.max_coherence_drift,
            "min_model_trust": self.min_model_trust,
        }

    # pylint: disable=too-many-locals,too-many-branches
    def evaluate(self, envelope: Dict[str, Any], is_long: bool) -> GovernanceDecision:
        signal = str(envelope.get("signal", "HOLD")).upper()
        confidence = float(envelope.get("confidence", 0.5))
        regime = str(envelope.get("market_regime", "ranging"))

        governance = envelope.get("governance", {})
        forecast = envelope.get("forecast_consensus", {})
        temporal = envelope.get("temporal", {})
        runtime = envelope.get("runtime", {})
        execution = envelope.get("execution", {})
        risk = envelope.get("risk", {})
        resources = envelope.get("resources", {})

        coherence = float(temporal.get("coherence_score", 0.7))
        agreement = float(forecast.get("agreement", confidence))
        uncertainty = float(forecast.get("uncertainty", 1.0 - confidence))
        emergence_risk = float(risk.get("emergence_risk", 0.0))
        current_drawdown = float(governance.get("current_drawdown_pct", 0.0))
        max_drawdown = float(governance.get("max_drawdown_pct", 0.12))
        constitution_passed = bool(governance.get("constitution_passed", True))
        survival_mode = bool(governance.get("survival_mode", False))
        hold_only = bool(execution.get("hold_only", False))
        runtime_mode = str(runtime.get("mode", governance.get("governance_mode", "normal"))).lower()
        # Backward-compat alias: legacy "constrained" maps to current "cautious".
        if runtime_mode == "constrained":
            runtime_mode = "cautious"

        model_consensus = float(envelope.get("model_consensus", agreement))
        strategy_disagreement = float(envelope.get("strategy_disagreement", 0.0))
        attention_pressure = float(runtime.get("attention_pressure", 0.2))
        runtime_health = float(runtime.get("runtime_health", 0.8))
        runtime_pressure = float(runtime.get("runtime_pressure", (attention_pressure + float(runtime.get("instability", 0.0))) / 2.0))
        coherence_drift = float(envelope.get("coherence_drift", 0.0))
        governance_confidence = float(envelope.get("governance_confidence", (float(governance.get("governance_stability", 0.8)) + confidence) / 2.0))
        model_trust = float(envelope.get("model_trust", confidence))
        execution_risk = float(envelope.get("execution_risk", emergence_risk))
        cognitive_budget = float(resources.get("cognitive_budget", 1.0))
        attention_available = float(resources.get("attention_available", 1.0))

        reasons: List[str] = []
        overrides: Dict[str, Any] = {
            "position_multiplier": 1.0,
            "max_position_size": float(execution.get("max_position_size", 0.02)),
            "runtime_mode": runtime_mode,
            "governance_mode": runtime_mode,
            "required_consensus": self.min_agreement,
            "runtime_pressure": runtime_pressure,
            "coherence_drift": coherence_drift,
            "model_trust": model_trust,
            "execution_risk": execution_risk,
        }

        if not constitution_passed:
            reasons.append("constitution_failed")

        if current_drawdown > max_drawdown:
            reasons.append("drawdown_limit_exceeded")

        if hold_only or signal == "HOLD":
            reasons.append("hold_only")

        if strategy_disagreement > 0.70:
            reasons.append("high_strategy_disagreement")
            overrides["position_multiplier"] *= 0.5

        if governance_confidence < self.min_agreement:
            reasons.append("governance_confidence_too_low")
            overrides["position_multiplier"] *= 0.6

        if model_consensus < self.min_agreement:
            reasons.append("model_consensus_too_low")

        if model_trust < self.min_model_trust:
            reasons.append("model_trust_too_low")
            overrides["position_multiplier"] *= 0.5

        if attention_pressure >= self.max_attention_pressure:
            reasons.append("attention_saturation")
            overrides["position_multiplier"] *= 0.5
            overrides["runtime_mode"] = "cautious"

        if runtime_pressure >= self.max_runtime_pressure:
            reasons.append("runtime_pressure_high")
            overrides["position_multiplier"] *= 0.5
            overrides["runtime_mode"] = "cautious"

        if coherence_drift >= self.max_coherence_drift:
            reasons.append("coherence_drift_high")
            overrides["position_multiplier"] *= 0.65
            overrides["runtime_mode"] = "cautious"

        if cognitive_budget <= self.min_cognitive_budget or attention_available <= self.min_cognitive_budget:
            reasons.append("insufficient_cognitive_budget")
            overrides["position_multiplier"] *= 0.5
            overrides["runtime_mode"] = "cautious"

        if runtime_health < 0.35:
            reasons.append("runtime_instability")
            overrides["runtime_mode"] = "survival"

        if survival_mode or runtime_mode in {"survival", "lockdown"} or coherence < self.survival_coherence_threshold:
            reasons.append("survival_mode")
            overrides["runtime_mode"] = "survival"

        if coherence < self.cautious_coherence_threshold:
            reasons.append("cautious_mode")
            overrides["runtime_mode"] = "cautious"
            overrides["position_multiplier"] *= 0.75

        if coherence < self.constrained_coherence_threshold:
            reasons.append("low_coherence")
            overrides["runtime_mode"] = "survival"
            overrides["position_multiplier"] *= 0.5

        if uncertainty > self.max_uncertainty and agreement < self.min_agreement:
            reasons.append("insufficient_consensus")

        if emergence_risk > 0.70:
            reasons.append("high_emergence_risk")
            overrides["position_multiplier"] *= 0.5

        if execution_risk > 0.70:
            reasons.append("high_execution_risk")
            overrides["position_multiplier"] *= 0.5

        regime_cap = self._REGIME_POSITION_CAP.get(regime)
        if regime_cap is not None:
            overrides["max_position_size"] = min(overrides["max_position_size"], regime_cap)
            if regime_cap <= 0.0:
                reasons.append("regime_blocks_trading")

        if is_long and signal == "SELL":
            reasons.append("signal_direction_conflict")
        if not is_long and signal == "BUY":
            reasons.append("signal_direction_conflict")

        if confidence < 0.10:
            reasons.append("confidence_too_low")
        elif runtime_health < 0.50 or runtime_pressure > self.max_runtime_pressure or coherence_drift > self.max_coherence_drift:
            adjusted_confidence = confidence * max(0.25, runtime_health) * max(0.4, 1.0 - coherence_drift)
            overrides["adjusted_confidence"] = adjusted_confidence
            if adjusted_confidence < 0.15:
                reasons.append("confidence_decay_under_instability")

        if runtime_mode == "lockdown" or str(governance.get("governance_mode", "normal")).lower() == "lockdown":
            reasons.append("lockdown_mode")
            overrides["runtime_mode"] = "lockdown"
            overrides["position_multiplier"] = 0.0
            overrides["max_position_size"] = 0.0

        mode = str(overrides.get("runtime_mode", "normal")).lower()
        overrides["governance_mode"] = mode

        deny_reasons = {
            "constitution_failed",
            "drawdown_limit_exceeded",
            "hold_only",
            "survival_mode",
            "insufficient_consensus",
            "regime_blocks_trading",
            "signal_direction_conflict",
            "confidence_too_low",
            "confidence_decay_under_instability",
            "model_consensus_too_low",
            "model_trust_too_low",
            "lockdown_mode",
        }
        allow = not any(reason in deny_reasons for reason in reasons)

        return GovernanceDecision(allow=allow, mode=mode, reasons=reasons, overrides=overrides)
