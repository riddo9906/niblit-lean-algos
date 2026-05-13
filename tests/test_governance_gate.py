import time
import unittest

from freqtrade_strategies.trade_governance import TradeGovernanceGate


def _base_envelope():
    return {
        "schema_version": "2.0",
        "signal": "BUY",
        "confidence": 0.8,
        "timestamp": int(time.time()),
        "market_regime": "ranging",
        "forecast_consensus": {"direction": "UP", "agreement": 0.8, "uncertainty": 0.1},
        "governance": {
            "constitution_passed": True,
            "governance_mode": "normal",
            "survival_mode": False,
            "governance_stability": 0.9,
            "current_drawdown_pct": 0.01,
            "max_drawdown_pct": 0.2,
        },
        "execution": {"max_position_size": 0.05, "hold_only": False, "runtime_stability": 0.8},
        "temporal": {"coherence_score": 0.9, "epoch_id": int(time.time())},
        "runtime": {"mode": "normal", "attention_pressure": 0.2, "runtime_health": 0.9},
        "risk": {"emergence_risk": 0.1},
        "resources": {"cognitive_budget": 0.8, "attention_available": 0.8},
        "model_consensus": 0.8,
        "strategy_disagreement": 0.2,
    }


class GovernanceGateTests(unittest.TestCase):
    def test_lockdown_blocks_execution(self):
        gate = TradeGovernanceGate()
        env = _base_envelope()
        env["runtime"]["mode"] = "lockdown"
        decision = gate.evaluate(env, is_long=True)
        self.assertFalse(decision.allow)
        self.assertEqual(decision.mode, "lockdown")
        self.assertIn("lockdown_mode", decision.reasons)

    def test_attention_saturation_forces_cautious(self):
        gate = TradeGovernanceGate()
        env = _base_envelope()
        env["runtime"]["attention_pressure"] = 0.95
        decision = gate.evaluate(env, is_long=True)
        self.assertEqual(decision.overrides.get("runtime_mode"), "cautious")
        self.assertIn("attention_saturation", decision.reasons)

    def test_survival_mode_blocks_execution(self):
        gate = TradeGovernanceGate()
        env = _base_envelope()
        env["governance"]["survival_mode"] = True
        decision = gate.evaluate(env, is_long=True)
        self.assertFalse(decision.allow)
        self.assertIn("survival_mode", decision.reasons)

    def test_runtime_pressure_reduces_position(self):
        gate = TradeGovernanceGate()
        env = _base_envelope()
        env["runtime"]["runtime_pressure"] = 0.95
        decision = gate.evaluate(env, is_long=True)
        self.assertIn("runtime_pressure_high", decision.reasons)
        self.assertLess(decision.overrides.get("position_multiplier", 1.0), 1.0)

    def test_confidence_decay_under_instability_can_block(self):
        gate = TradeGovernanceGate()
        env = _base_envelope()
        env["confidence"] = 0.2
        env["runtime"]["runtime_health"] = 0.2
        env["runtime"]["runtime_pressure"] = 0.9
        env["coherence_drift"] = 0.5
        decision = gate.evaluate(env, is_long=True)
        self.assertIn("confidence_decay_under_instability", decision.reasons)
        self.assertFalse(decision.allow)

    def test_high_disagreement_penalizes_position(self):
        gate = TradeGovernanceGate()
        env = _base_envelope()
        env["strategy_disagreement"] = 0.9
        decision = gate.evaluate(env, is_long=True)
        self.assertIn("high_strategy_disagreement", decision.reasons)
        self.assertLess(decision.overrides.get("position_multiplier", 1.0), 1.0)


if __name__ == "__main__":
    unittest.main()
