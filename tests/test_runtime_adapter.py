import json
import os
import tempfile
import time
import unittest

from freqtrade_strategies.runtime_adapter import RuntimeAdapter


class RuntimeAdapterTests(unittest.TestCase):
    def test_fallback_when_no_sources_available(self):
        missing = os.path.join(tempfile.gettempdir(), "niblit-nonexistent-signal.json")
        adapter = RuntimeAdapter(cloud_url="", local_signal_file=missing, refresh_interval_s=0.0)
        state = adapter.get_state()
        self.assertEqual(state.source, "fallback")
        self.assertEqual(state.runtime_mode, "normal")

    def test_local_source_and_enrichment(self):
        with tempfile.TemporaryDirectory() as tmp:
            signal_path = os.path.join(tmp, "signal.json")
            payload = {
                "timestamp": int(time.time()),
                "temporal": {"coherence_score": 0.81, "epoch_id": 123},
                "runtime": {"mode": "cautious", "attention_pressure": 0.6, "runtime_health": 0.7},
                "governance": {"governance_mode": "cautious", "constitution_passed": True},
                "resources": {"cognitive_budget": 0.4, "attention_available": 0.5},
                "reflection": {"reflection_confidence": 0.65},
                "risk": {"emergence_risk": 0.3},
            }
            with open(signal_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)

            adapter = RuntimeAdapter(cloud_url="", local_signal_file=signal_path, refresh_interval_s=0.0)
            state = adapter.get_state()
            self.assertEqual(state.source, "local")
            self.assertEqual(state.runtime_mode, "cautious")

            env = {"runtime": {}, "resources": {}, "temporal": {}, "governance": {}}
            enriched = adapter.enrich_envelope(env)
            self.assertEqual(enriched["_runtime_source"], "local")
            self.assertIn("runtime_pressure", enriched["runtime"])
            self.assertEqual(enriched["temporal"]["epoch_id"], 123)


if __name__ == "__main__":
    unittest.main()
