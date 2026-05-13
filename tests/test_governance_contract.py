"""Tests for governance_contract.py cross-repo contract alignment.

Validates canonical mode contract, event constants, telemetry normalization,
compatibility metadata, and anti-drift detection aligned with:
  - riddo9906/Niblit PR #219  (shared/governance_contract/)
  - riddo9906/Niblit-cloud-server PR #4 (tools/lib/runtime_profiles.py)
"""

import unittest

from freqtrade_strategies.governance_contract import (
    CANONICAL_EVENTS,
    EVENT_MARKET_EPISODE_INGESTED,
    EVENT_REFLECTION_COMPLETE,
    EVENT_TRADE_REFLECTION_INGESTED,
    GOVERNANCE_RUNTIME_MODES,
    NODE_IDENTITY,
    anti_drift_report,
    compatibility_metadata,
    mode_rank,
    normalize_runtime_mode,
    normalize_replay_metadata,
    normalize_telemetry,
    validate_compatibility,
    validate_envelope_contract,
)


class CanonicalModesTests(unittest.TestCase):
    def test_four_canonical_modes(self):
        self.assertEqual(set(GOVERNANCE_RUNTIME_MODES), {"normal", "cautious", "survival", "lockdown"})

    def test_normalize_canonical_passthrough(self):
        for mode in GOVERNANCE_RUNTIME_MODES:
            self.assertEqual(normalize_runtime_mode(mode), mode)

    def test_normalize_constrained_alias(self):
        # backward-compat: "constrained" maps to "cautious" (legacy cloud-server alias)
        self.assertEqual(normalize_runtime_mode("constrained"), "cautious")

    def test_normalize_minimal_alias(self):
        # cloud-server PR #4 alias
        self.assertEqual(normalize_runtime_mode("minimal"), "cautious")

    def test_normalize_unknown_falls_back(self):
        self.assertEqual(normalize_runtime_mode("unknown_mode"), "normal")
        self.assertEqual(normalize_runtime_mode(None), "normal")

    def test_mode_rank_ordering(self):
        self.assertLess(mode_rank("normal"), mode_rank("cautious"))
        self.assertLess(mode_rank("cautious"), mode_rank("survival"))
        self.assertLess(mode_rank("survival"), mode_rank("lockdown"))


class CanonicalEventTests(unittest.TestCase):
    def test_canonical_event_names_match_pr219(self):
        self.assertEqual(EVENT_TRADE_REFLECTION_INGESTED, "trade_reflection.ingested")
        self.assertEqual(EVENT_MARKET_EPISODE_INGESTED, "market_episode.ingested")
        self.assertEqual(EVENT_REFLECTION_COMPLETE, "reflection.complete")

    def test_canonical_events_frozenset(self):
        self.assertIn("execution_envelope.published", CANONICAL_EVENTS)
        self.assertIn("trade_reflection.ingested", CANONICAL_EVENTS)
        self.assertIn("market_episode.ingested", CANONICAL_EVENTS)
        self.assertIn("reflection.complete", CANONICAL_EVENTS)
        self.assertIn("runtime_mode.changed", CANONICAL_EVENTS)

    def test_unknown_event_detected_in_anti_drift_report(self):
        report = anti_drift_report(observed_events=["some.unknown.event"])
        self.assertIn("unknown_events_detected", report["drift_factors"])
        self.assertIn("some.unknown.event", report["unknown_events"])

    def test_canonical_events_not_flagged(self):
        report = anti_drift_report(
            observed_events=list(CANONICAL_EVENTS),
        )
        self.assertNotIn("unknown_events_detected", report["drift_factors"])


class CompatibilityMetadataTests(unittest.TestCase):
    def test_compatibility_metadata_keys(self):
        meta = compatibility_metadata()
        self.assertEqual(meta["schema_version"], "2.x")
        self.assertEqual(meta["event_contract_version"], "omega-7")
        self.assertEqual(meta["governance_contract_version"], "1.x")
        self.assertEqual(meta["advisor_protocol_version"], "2.x")
        self.assertEqual(meta["runtime_mode_contract"], "2026.05")
        self.assertEqual(meta["node_identity"], NODE_IDENTITY)

    def test_node_identity(self):
        self.assertEqual(NODE_IDENTITY, "niblit_lean_algos")

    def test_validate_compatibility_pass(self):
        meta = compatibility_metadata()
        result = validate_compatibility(meta)
        self.assertTrue(result["compatible"])
        self.assertEqual(result["mismatches"], {})

    def test_validate_compatibility_mismatch_detected(self):
        result = validate_compatibility({"schema_version": "1.x"})
        self.assertFalse(result["compatible"])
        self.assertIn("schema_version", result["mismatches"])


class TelemetryNormalizationTests(unittest.TestCase):
    def test_normalize_telemetry_defaults(self):
        out = normalize_telemetry({})
        self.assertEqual(out["runtime_mode"], "normal")
        self.assertEqual(out["governance_mode"], "normal")
        self.assertEqual(out["coherence_score"], 1.0)
        self.assertEqual(out["coherence_drift"], 0.0)
        self.assertEqual(out["runtime_health"], 1.0)
        self.assertEqual(out["model_trust"], 0.5)

    def test_normalize_telemetry_clamps(self):
        out = normalize_telemetry({"coherence_score": 99, "runtime_health": -5})
        self.assertEqual(out["coherence_score"], 1.0)
        self.assertEqual(out["runtime_health"], 0.0)

    def test_normalize_telemetry_mode_alias(self):
        out = normalize_telemetry({"runtime_mode": "constrained"})
        self.assertEqual(out["runtime_mode"], "cautious")

    def test_normalize_replay_metadata(self):
        out = normalize_replay_metadata({"causal_trace_id": "t-1", "memory_reference_ids": ["m1"]})
        self.assertEqual(out["trace_id"], "t-1")
        self.assertEqual(out["causal_references"], ["m1"])
        self.assertEqual(out["node_identity"], NODE_IDENTITY)


class EnvelopeContractValidationTests(unittest.TestCase):
    def _valid_envelope(self):
        return {
            "schema_version": "2.0",
            "signal": "BUY",
            "confidence": 0.7,
            "timestamp": 1000,
            "forecast_consensus": {"agreement": 0.7, "uncertainty": 0.3},
            "governance": {"governance_mode": "normal", "constitution_passed": True},
            "runtime": {"mode": "normal", "runtime_health": 1.0},
            "temporal": {"coherence_score": 0.8, "coherence_drift": 0.05},
            "resources": {"cognitive_budget": 1.0, "attention_available": 1.0},
        }

    def test_valid_envelope_passes(self):
        result = validate_envelope_contract(self._valid_envelope())
        self.assertTrue(result["valid"])
        self.assertEqual(result["issues"], [])

    def test_missing_field_detected(self):
        env = self._valid_envelope()
        del env["resources"]
        result = validate_envelope_contract(env)
        self.assertFalse(result["valid"])
        self.assertIn("missing:resources", result["issues"])

    def test_mode_mismatch_detected(self):
        env = self._valid_envelope()
        env["runtime"]["mode"] = "cautious"
        env["governance"]["governance_mode"] = "normal"
        result = validate_envelope_contract(env)
        self.assertFalse(result["valid"])
        self.assertIn("mode_mismatch:runtime_vs_governance", result["issues"])

    def test_invalid_advisors_detected(self):
        env = self._valid_envelope()
        env["advisors"] = "not_a_dict"
        result = validate_envelope_contract(env)
        self.assertFalse(result["valid"])
        self.assertIn("advisor_protocol_invalid", result["issues"])


class AntiDriftReportTests(unittest.TestCase):
    def test_clean_report_is_low_risk(self):
        env = {
            "schema_version": "2.0", "signal": "BUY", "confidence": 0.7, "timestamp": 1000,
            "forecast_consensus": {}, "governance": {"governance_mode": "normal"},
            "runtime": {"mode": "normal"}, "temporal": {}, "resources": {},
        }
        report = anti_drift_report(envelope=env)
        self.assertEqual(report["drift_risk"], "low")
        self.assertEqual(report["node_identity"], NODE_IDENTITY)

    def test_multiple_drift_factors_is_high_risk(self):
        report = anti_drift_report(
            envelope=None,
            compatibility={"schema_version": "1.x"},
            observed_events=["unknown.event"],
        )
        self.assertEqual(report["drift_risk"], "high")
        self.assertGreater(len(report["drift_factors"]), 1)


if __name__ == "__main__":
    unittest.main()
