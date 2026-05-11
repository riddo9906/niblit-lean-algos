"""
NiblitSignalMixin — cognitive envelope bridge for Freqtrade execution strategies.

Freqtrade strategies act as advisors and generate candidate entries/exits.
Final authority comes from Niblit's external cognitive envelope, normalized by
execution adapter rules and enforced by TradeGovernanceGate.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

try:
    from .advisor_protocol import summarize_debate
    from .cognitive_envelope import read_envelope_file
    from .trade_governance import TradeGovernanceGate
except ImportError:
    from advisor_protocol import summarize_debate
    from cognitive_envelope import read_envelope_file
    from trade_governance import TradeGovernanceGate

logger = logging.getLogger(__name__)

_DEFAULT_SIGNAL_FILE = os.environ.get(
    "NIBLIT_SIGNAL_FILE",
    os.path.join(os.environ.get("TMPDIR", "/tmp"), "niblit_lean_signal.json"),
)
_MAX_SIGNAL_AGE_SECS: int = int(os.environ.get("NIBLIT_SIGNAL_MAX_AGE", "300"))
_NIBLIT_MIN_CONF: float = float(os.environ.get("NIBLIT_MIN_CONF", "0.55"))


class NiblitSignalMixin:
    """Mixin for Freqtrade IStrategy subclasses using governed execution envelopes."""

    _niblit_last_read: float = 0.0
    _niblit_last_data: Optional[Dict[str, Any]] = None
    _niblit_lock: threading.Lock = threading.Lock()

    niblit_min_conf: float = _NIBLIT_MIN_CONF
    niblit_signal_file: str = _DEFAULT_SIGNAL_FILE
    niblit_max_age: int = _MAX_SIGNAL_AGE_SECS
    niblit_weight_confidence: float = float(os.environ.get("NIBLIT_WEIGHT_CONFIDENCE", "1.0"))
    niblit_weight_coherence: float = float(os.environ.get("NIBLIT_WEIGHT_COHERENCE", "1.0"))
    niblit_weight_agreement: float = float(os.environ.get("NIBLIT_WEIGHT_AGREEMENT", "1.0"))
    niblit_weight_runtime_stability: float = float(os.environ.get("NIBLIT_WEIGHT_RUNTIME_STABILITY", "1.0"))
    niblit_weight_governance_stability: float = float(os.environ.get("NIBLIT_WEIGHT_GOVERNANCE_STABILITY", "1.0"))
    niblit_weight_emergence_inverse: float = float(os.environ.get("NIBLIT_WEIGHT_EMERGENCE_INVERSE", "1.0"))
    niblit_min_health_multiplier: float = float(os.environ.get("NIBLIT_MIN_HEALTH_MULTIPLIER", "0.05"))

    def _niblit_gate(self) -> TradeGovernanceGate:
        if not hasattr(self, "__niblit_gate"):
            gate = TradeGovernanceGate()
            gate.survival_coherence_threshold = float(
                os.environ.get("NIBLIT_SURVIVAL_COHERENCE", gate.survival_coherence_threshold)
            )
            gate.constrained_coherence_threshold = float(
                os.environ.get("NIBLIT_CONSTRAINED_COHERENCE", gate.constrained_coherence_threshold)
            )
            gate.cautious_coherence_threshold = float(
                os.environ.get("NIBLIT_CAUTIOUS_COHERENCE", gate.cautious_coherence_threshold)
            )
            gate.max_attention_pressure = float(
                os.environ.get("NIBLIT_MAX_ATTENTION_PRESSURE", gate.max_attention_pressure)
            )
            gate.min_cognitive_budget = float(
                os.environ.get("NIBLIT_MIN_COGNITIVE_BUDGET", gate.min_cognitive_budget)
            )
            self.__niblit_gate = gate
        return self.__niblit_gate

    def niblit_envelope(self) -> Optional[Dict[str, Any]]:
        return self._niblit_read()

    def niblit_signal(self) -> Optional[str]:
        data = self._niblit_read()
        return data.get("signal") if data else None

    def niblit_confidence(self) -> float:
        data = self._niblit_read()
        return float(data.get("confidence", 0.5)) if data else 0.5

    def niblit_regime(self) -> str:
        data = self._niblit_read()
        return str(data.get("market_regime", "ranging")) if data else "ranging"

    def niblit_risk_pct(self, default: float = 0.02) -> float:
        data = self._niblit_read()
        if not data:
            return default
        execution = data.get("execution", {})
        return float(execution.get("max_position_size", default))

    def niblit_runtime_mode(self) -> str:
        data = self._niblit_read()
        if not data:
            return "normal"
        runtime = data.get("runtime", {})
        return str(runtime.get("mode", "normal"))

    def niblit_block_entry(self, pair: str, is_long: bool) -> bool:
        """Backward-compatible alias: True when governed gate rejects entry."""
        return not self.niblit_allow_entry(pair, is_long)

    def niblit_allow_entry(self, pair: str, is_long: bool) -> bool:
        """Evaluate cognitive governance before any entry order is submitted."""
        envelope = self._niblit_read()
        if envelope is None:
            return True

        debate = summarize_debate(envelope)
        envelope["model_consensus"] = debate.model_consensus
        envelope["strategy_disagreement"] = debate.strategy_disagreement
        if debate.direction in {"BUY", "SELL"}:
            envelope["forecast_consensus"]["direction"] = "UP" if debate.direction == "BUY" else "DOWN"

        confidence = float(envelope.get("confidence", 0.5))
        if confidence < self.niblit_min_conf:
            self._niblit_set_decision(
                allow=False,
                reasons=["confidence_below_min_conf"],
                overrides={
                    "position_multiplier": 0.0,
                    "debate_vote_count": debate.vote_count,
                    "model_consensus": debate.model_consensus,
                    "strategy_disagreement": debate.strategy_disagreement,
                },
                mode="constrained",
            )
            self._log_governance_decision(pair)
            return False

        decision = self._niblit_gate().evaluate(envelope, is_long=is_long)
        decision.overrides["debate_vote_count"] = debate.vote_count
        decision.overrides["debate_coalition"] = debate.coalition
        decision.overrides["model_consensus"] = debate.model_consensus
        decision.overrides["strategy_disagreement"] = debate.strategy_disagreement
        self._niblit_set_decision(
            allow=decision.allow,
            reasons=decision.reasons,
            overrides=decision.overrides,
            mode=decision.mode,
        )
        self._log_governance_decision(pair)
        return decision.allow

    def niblit_should_force_exit(self, is_long: bool = True) -> bool:
        """Return True when governance indicates survival/hold-only behavior."""
        envelope = self._niblit_read()
        if envelope is None:
            return False
        debate = summarize_debate(envelope)
        envelope["model_consensus"] = debate.model_consensus
        envelope["strategy_disagreement"] = debate.strategy_disagreement
        decision = self._niblit_gate().evaluate(envelope, is_long=is_long)
        return (not decision.allow) and any(
            reason in {"survival_mode", "hold_only", "regime_blocks_trading", "lockdown_mode"}
            for reason in decision.reasons
        )

    # pylint: disable=too-many-arguments,unused-argument
    def custom_stake_amount(
        self,
        pair: str,
        current_time,
        current_rate: float,
        proposed_stake: float,
        min_stake: Optional[float],
        max_stake: float,
        leverage: float,
        entry_tag: Optional[str],
        side: str,
        **kwargs,
    ) -> float:
        """Centralized adaptive position sizing from cognitive envelope health."""
        envelope = self._niblit_read()
        if envelope is None:
            return max(proposed_stake, min_stake or 0)

        governance = envelope.get("governance", {})
        forecast = envelope.get("forecast_consensus", {})
        temporal = envelope.get("temporal", {})
        execution = envelope.get("execution", {})
        risk = envelope.get("risk", {})

        confidence = float(envelope.get("confidence", 0.5))
        coherence = float(temporal.get("coherence_score", 0.7))
        agreement = float(forecast.get("agreement", confidence))
        runtime_stability = float(execution.get("runtime_stability", 0.8))
        governance_stability = float(governance.get("governance_stability", 0.8))
        emergence_risk = float(risk.get("emergence_risk", 0.0))
        runtime = envelope.get("runtime", {})
        resources = envelope.get("resources", {})
        attention_pressure = float(runtime.get("attention_pressure", 0.2))
        cognitive_budget = float(resources.get("cognitive_budget", 1.0))
        attention_available = float(resources.get("attention_available", 1.0))
        debate = summarize_debate(envelope)
        envelope["model_consensus"] = debate.model_consensus
        envelope["strategy_disagreement"] = debate.strategy_disagreement

        decision = self._niblit_gate().evaluate(envelope, is_long=(side == "long"))
        self._niblit_set_decision(
            allow=decision.allow,
            reasons=decision.reasons,
            overrides=decision.overrides,
            mode=decision.mode,
        )

        if not decision.allow:
            return min_stake or 0

        # Multiplicative blend intentionally suppresses size when any cognition
        # health dimension degrades; clamp with a floor to avoid zeroing due to
        # a single noisy input while still remaining conservative.
        health_multiplier = self._niblit_health_multiplier(
            envelope=envelope,
            confidence=confidence,
            coherence=coherence,
            agreement=agreement,
            runtime_stability=runtime_stability,
            governance_stability=governance_stability,
            emergence_risk=emergence_risk,
            attention_pressure=attention_pressure,
            cognitive_budget=cognitive_budget,
            attention_available=attention_available,
            model_consensus=debate.model_consensus,
            disagreement=debate.strategy_disagreement,
        )

        position_multiplier = float(decision.overrides.get("position_multiplier", 1.0))
        max_position_size = float(decision.overrides.get(
            "max_position_size",
            execution.get("max_position_size", 0.02),
        ))

        base = proposed_stake * max(0.0, min(1.0, health_multiplier))
        sized = base * max(0.0, min(1.0, position_multiplier))

        if max_stake and max_stake > 0:
            sized = min(sized, max_stake * max_position_size)

        if min_stake is not None:
            sized = max(sized, min_stake)

        return max(0.0, sized)

    # pylint: disable=too-many-arguments
    def _niblit_health_multiplier(
        self,
        envelope: Dict[str, Any],
        confidence: float,
        coherence: float,
        agreement: float,
        runtime_stability: float,
        governance_stability: float,
        emergence_risk: float,
        attention_pressure: float,
        cognitive_budget: float,
        attention_available: float,
        model_consensus: float,
        disagreement: float,
    ) -> float:
        ts = int(envelope.get("timestamp", 0))
        cache = getattr(self, "_niblit_health_cache", None)
        if cache and cache.get("timestamp") == ts:
            return float(cache.get("value", 1.0))

        value = (
            (confidence ** self.niblit_weight_confidence)
            * (coherence ** self.niblit_weight_coherence)
            * (agreement ** self.niblit_weight_agreement)
            * (runtime_stability ** self.niblit_weight_runtime_stability)
            * (governance_stability ** self.niblit_weight_governance_stability)
            * (max(0.0, 1.0 - emergence_risk) ** self.niblit_weight_emergence_inverse)
            * max(0.0, min(1.0, 1.0 - attention_pressure))
            * max(0.0, min(1.0, cognitive_budget))
            * max(0.0, min(1.0, attention_available))
            * max(0.0, min(1.0, model_consensus))
            * max(0.0, min(1.0, 1.0 - disagreement))
        )
        # Keep a small configurable floor to avoid accidental zero sizing from
        # one noisy factor while still keeping sizing strongly defensive.
        value = max(self.niblit_min_health_multiplier, min(1.0, value))
        self._niblit_health_cache = {"timestamp": ts, "value": value}
        return value

    def niblit_execution_snapshot(self) -> Dict[str, Any]:
        envelope = self._niblit_read() or {}
        return {
            "envelope": envelope,
            "decision": getattr(self, "_niblit_last_decision", {}),
            "timestamp": int(time.time()),
        }

    def _niblit_read(self) -> Optional[Dict[str, Any]]:
        now = time.time()
        with NiblitSignalMixin._niblit_lock:
            if now - NiblitSignalMixin._niblit_last_read < 5.0 and \
                    NiblitSignalMixin._niblit_last_data is not None:
                return NiblitSignalMixin._niblit_last_data

            path = getattr(self, "niblit_signal_file", _DEFAULT_SIGNAL_FILE)
            max_age = int(getattr(self, "niblit_max_age", _MAX_SIGNAL_AGE_SECS))
            data = read_envelope_file(signal_file=path, max_age_secs=max_age)
            NiblitSignalMixin._niblit_last_read = now
            NiblitSignalMixin._niblit_last_data = data
            return data

    def _niblit_set_decision(
        self,
        allow: bool,
        reasons: List[str],
        overrides: Optional[Dict[str, Any]],
        mode: str,
    ) -> None:
        envelope = self._niblit_read() or {}
        trace = envelope.get("trace", {}) if isinstance(envelope.get("trace"), dict) else {}
        temporal = envelope.get("temporal", {}) if isinstance(envelope.get("temporal"), dict) else {}
        self._niblit_last_decision = {
            "allow": bool(allow),
            "reasons": list(reasons or []),
            "overrides": dict(overrides or {}),
            "mode": mode,
            "causal_trace_id": trace.get("causal_trace_id"),
            "epoch_id": temporal.get("epoch_id"),
            "timestamp": int(time.time()),
        }

    def _log_governance_decision(self, pair: str) -> None:
        decision = getattr(self, "_niblit_last_decision", {})
        if not decision:
            return
        log_payload = {
            "event": "trade_governance_gate",
            "pair": pair,
            "allow": decision.get("allow", True),
            "mode": decision.get("mode", "normal"),
            "reasons": decision.get("reasons", []),
            "overrides": decision.get("overrides", {}),
        }
        logger.info("niblit_governance=%s", log_payload)

    def niblit_status(self) -> Dict[str, Any]:
        """Expose current envelope + decision + gate thresholds for observability."""
        return {
            "runtime_mode": self.niblit_runtime_mode(),
            "regime": self.niblit_regime(),
            "confidence": self.niblit_confidence(),
            "decision": getattr(self, "_niblit_last_decision", {}),
            "gate": self._niblit_gate().status(),
        }
