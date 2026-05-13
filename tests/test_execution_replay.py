import json
import os
import tempfile
import unittest

from freqtrade_strategies.execution_replay import write_execution_trace


class ExecutionReplayTests(unittest.TestCase):
    def test_write_execution_trace_contains_governance_and_consensus(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace_file = os.path.join(tmp, "trace.jsonl")
            envelope = {
                "schema_version": "2.0",
                "timestamp": 111,
                "market_regime": "volatile_breakout",
                "confidence": 0.72,
                "model_consensus": 0.68,
                "strategy_disagreement": 0.21,
                "runtime": {"attention_pressure": 0.6, "runtime_health": 0.75},
                "resources": {"cognitive_budget": 0.8, "attention_available": 0.7},
                "temporal": {"epoch_id": 11, "coherence_score": 0.77},
                "forecast_consensus": {"agreement": 0.66, "uncertainty": 0.3},
                "trace": {"causal_trace_id": "trace-1", "memory_reference_ids": ["m1"]},
                "advisors": {
                    "votes": {
                        "trend": {
                            "direction": "BUY",
                            "confidence": 0.8,
                            "uncertainty": 0.2,
                            "regime_interpretation": "volatile_breakout",
                        }
                    }
                },
                "_runtime_source": "local",
            }
            governance_decision = {
                "allow": False,
                "mode": "cautious",
                "reasons": ["runtime_pressure_high"],
                "overrides": {"position_multiplier": 0.5, "max_position_size": 0.02},
            }
            consensus = {
                "model_consensus": 0.68,
                "strategy_disagreement": 0.21,
                "coalition": {"BUY": 0.7, "SELL": 0.2, "HOLD": 0.1},
                "vote_count": 3,
                "direction": "BUY",
            }
            write_execution_trace(
                event_type="entry_decision",
                pair="BTC/USDT",
                envelope=envelope,
                governance_decision=governance_decision,
                consensus_state=consensus,
                trace_file=trace_file,
            )

            with open(trace_file, "r", encoding="utf-8") as handle:
                record = json.loads(handle.readline())

            self.assertEqual(record["event_type"], "entry_decision")
            self.assertEqual(record["runtime_mode"], "cautious")
            self.assertEqual(record["runtime_source"], "local")
            self.assertIn("runtime_pressure_high", record["governance_decision"]["reasons"])
            self.assertEqual(record["consensus_state"]["vote_count"], 3)
            self.assertIn("trend", record["advisor_contributions"])


if __name__ == "__main__":
    unittest.main()
