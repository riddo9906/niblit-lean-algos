import json
import os
import tempfile
import unittest

from scripts import sync_signal


class SyncSignalSchemaTests(unittest.TestCase):
    def test_write_signal_includes_extended_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal_path = os.path.join(tmp, "signal.json")
            sync_signal._SIGNAL_FILE = signal_path  # pylint: disable=protected-access
            sync_signal.write_signal(
                signal="BUY",
                confidence=0.8,
                runtime_mode="cautious",
                governance_mode="cautious",
                attention_pressure=0.6,
                runtime_pressure=0.5,
                cognitive_budget=0.7,
                model_consensus=0.75,
                strategy_disagreement=0.3,
                coherence_drift=0.1,
                governance_confidence=0.74,
                model_trust=0.71,
                execution_risk=0.22,
                memory_reference_ids="mem-1,mem-2",
            )
            with open(signal_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload.get("schema_version"), "2.0")
            self.assertIn("resources", payload)
            self.assertIn("trace", payload)
            self.assertIn("model_consensus", payload)
            self.assertEqual(payload.get("runtime", {}).get("mode"), "cautious")
            self.assertEqual(payload.get("governance", {}).get("governance_mode"), "cautious")
            self.assertEqual(payload.get("trace", {}).get("memory_reference_ids"), ["mem-1", "mem-2"])
            self.assertIn("runtime_pressure", payload.get("runtime", {}))
            self.assertIn("coherence_drift", payload)
            self.assertIn("model_trust", payload)


if __name__ == "__main__":
    unittest.main()
