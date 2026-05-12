import json
import os
import tempfile
import time
import unittest

from freqtrade_strategies.cognitive_envelope import normalize_envelope, read_envelope_file


class CognitiveEnvelopeTests(unittest.TestCase):
    def test_normalize_legacy_payload_adds_v2_fields(self):
        payload = {
            "signal": "BUY",
            "confidence": 0.8,
            "regime": "volatile_breakout",
            "timestamp": int(time.time()),
            "risk_pct": 0.03,
            "advisor_vote": {"momentum": {"direction": "BUY", "confidence": 0.8}},
        }
        out = normalize_envelope(payload)
        self.assertIsNotNone(out)
        self.assertEqual(out["schema_version"], "2.0")
        self.assertIn("governance", out)
        self.assertIn("resources", out)
        self.assertIn("trace", out)
        self.assertIn("model_consensus", out)
        self.assertIn("coherence_drift", out)
        self.assertIn("model_trust", out)
        self.assertIn("execution_risk", out)
        self.assertIn("runtime_pressure", out["runtime"])

    def test_stale_envelope_rejected(self):
        stale = {
            "signal": "BUY",
            "confidence": 0.9,
            "timestamp": int(time.time()) - 1000,
            "regime": "ranging",
            "risk_pct": 0.02,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "signal.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(stale, handle)
            self.assertIsNone(read_envelope_file(path, max_age_secs=30))


if __name__ == "__main__":
    unittest.main()
