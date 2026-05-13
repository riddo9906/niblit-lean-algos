"""
freqtrade_strategies/governance_contract.py — Cross-repo governance contract.

Mirrors the canonical definitions from riddo9906/Niblit PR #219
(shared/governance_contract/) so that niblit-lean-algos can do local
anti-drift validation, emit canonical event names, and expose consistent
compatibility metadata without a direct Niblit package dependency.

Authority boundaries:
  - Niblit              : cognitive coordination, governance orchestration
  - Niblit-cloud-server : runtime orchestration, topology coordination
  - niblit-lean-algos   : execution cognition, governed trade execution

This module MUST NOT duplicate orchestration authority owned by the
above repos.  It only re-declares canonical constants and lightweight
normalization helpers.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

# ── node identity ─────────────────────────────────────────────────────────────

NODE_IDENTITY = "niblit_lean_algos"
NODE_DESCRIPTION = "governed cognitive execution node"

# ── canonical 4-mode runtime/governance contract ──────────────────────────────

GOVERNANCE_RUNTIME_MODES = ("normal", "cautious", "survival", "lockdown")
_MODE_ALIASES: Dict[str, str] = {
    "constrained": "cautious",   # backward-compat alias
    "minimal": "cautious",       # cloud-server edge-runtime alias
}


def normalize_runtime_mode(mode: object, default: str = "normal") -> str:
    """Normalize any runtime mode string to the canonical 4-mode contract."""
    candidate = str(mode or default).strip().lower()
    candidate = _MODE_ALIASES.get(candidate, candidate)
    if candidate not in GOVERNANCE_RUNTIME_MODES:
        return default
    return candidate


def mode_rank(mode: object) -> int:
    """Return escalating risk rank for canonical mode."""
    return {
        "normal": 0,
        "cautious": 1,
        "survival": 2,
        "lockdown": 3,
    }.get(normalize_runtime_mode(mode), 0)


# ── canonical event constants (mirrors Niblit PR #219) ────────────────────────

EVENT_EXECUTION_ENVELOPE_PUBLISHED = "execution_envelope.published"
EVENT_TRADE_REFLECTION_INGESTED = "trade_reflection.ingested"
EVENT_MARKET_EPISODE_INGESTED = "market_episode.ingested"
EVENT_RUNTIME_MODE_CHANGED = "runtime_mode.changed"
EVENT_ATTENTION_ALLOCATED = "attention.allocated"
EVENT_RESOURCE_ADAPTED = "resource.adapted"
EVENT_WORLD_MODEL_UPDATED = "world_model.updated"
EVENT_REFLECTION_COMPLETE = "reflection.complete"
EVENT_STATE_UPDATED = "state.updated"

CANONICAL_EVENTS = frozenset({
    EVENT_EXECUTION_ENVELOPE_PUBLISHED,
    EVENT_TRADE_REFLECTION_INGESTED,
    EVENT_MARKET_EPISODE_INGESTED,
    EVENT_RUNTIME_MODE_CHANGED,
    EVENT_ATTENTION_ALLOCATED,
    EVENT_RESOURCE_ADAPTED,
    EVENT_WORLD_MODEL_UPDATED,
    EVENT_REFLECTION_COMPLETE,
    EVENT_STATE_UPDATED,
})

# ── compatibility metadata (mirrors Niblit PR #219 + cloud-server PR #4) ──────

CANONICAL_COMPATIBILITY: Dict[str, str] = {
    "schema_version": "2.x",
    "event_contract_version": "omega-7",
    "governance_contract_version": "1.x",
    "advisor_protocol_version": "2.x",
    "runtime_mode_contract": "2026.05",
}


def compatibility_metadata(overrides: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """Return canonical compatibility metadata for this execution node."""
    meta: Dict[str, str] = dict(CANONICAL_COMPATIBILITY)
    meta["node_identity"] = NODE_IDENTITY
    if overrides:
        meta.update({str(k): str(v) for k, v in overrides.items()})
    return meta


def validate_compatibility(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Check that an incoming payload's compatibility metadata aligns."""
    incoming = dict(payload or {})
    expected = CANONICAL_COMPATIBILITY
    mismatches: Dict[str, Dict[str, str]] = {}
    for key, expected_value in expected.items():
        got = str(incoming.get(key, "")).strip()
        if got and got != expected_value:
            mismatches[key] = {"expected": str(expected_value), "received": got}
    return {
        "compatible": len(mismatches) == 0,
        "mismatches": mismatches,
        "expected": expected,
    }


# ── telemetry normalization (mirrors Niblit PR #219) ──────────────────────────

def normalize_telemetry(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize runtime telemetry to stable cross-repo fields."""
    src: Dict[str, Any] = dict(payload or {})
    return {
        "timestamp": int(src.get("timestamp", time.time())),
        "runtime_mode": normalize_runtime_mode(src.get("runtime_mode", "normal")),
        "governance_mode": normalize_runtime_mode(src.get("governance_mode", "normal")),
        "epoch_id": int(src.get("epoch_id", 0)),
        "coherence_score": max(0.0, min(1.0, float(src.get("coherence_score", 1.0)))),
        "coherence_drift": max(0.0, min(1.0, float(src.get("coherence_drift", 0.0)))),
        "attention_pressure": max(0.0, min(1.0, float(src.get("attention_pressure", 0.0)))),
        "runtime_health": max(0.0, min(1.0, float(src.get("runtime_health", 1.0)))),
        "model_trust": max(0.0, min(1.0, float(src.get("model_trust", 0.5)))),
        "execution_risk": max(0.0, min(1.0, float(src.get("execution_risk", 0.0)))),
        "source": str(src.get("source", "unknown")),
    }


def normalize_replay_metadata(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize replay metadata for causal/temporal reconstruction."""
    src: Dict[str, Any] = dict(payload or {})
    return {
        "trace_id": str(src.get("trace_id", src.get("causal_trace_id", f"trace-{int(time.time())}"))),
        "decision_lineage": list(src.get("decision_lineage", [])),
        "confidence_evolution": list(src.get("confidence_evolution", [])),
        "governance_replay": dict(src.get("governance_replay", {})),
        "causal_references": list(src.get("causal_references", src.get("memory_reference_ids", []))),
        "node_identity": NODE_IDENTITY,
    }


# ── anti-drift validation ─────────────────────────────────────────────────────

def validate_envelope_contract(envelope: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Validate schema, runtime/governance mode alignment, and advisor contract."""
    env: Dict[str, Any] = dict(envelope or {})
    issues: List[str] = []

    required_fields = (
        "schema_version", "signal", "confidence", "timestamp",
        "forecast_consensus", "governance", "runtime", "temporal", "resources",
    )
    for field in required_fields:
        if field not in env:
            issues.append(f"missing:{field}")

    runtime_mode = normalize_runtime_mode((env.get("runtime") or {}).get("mode", "normal"))
    governance_mode = normalize_runtime_mode((env.get("governance") or {}).get("governance_mode", "normal"))
    if runtime_mode != governance_mode:
        issues.append("mode_mismatch:runtime_vs_governance")

    raw_advisors = env.get("advisors")
    if raw_advisors is not None and not isinstance(raw_advisors, dict):
        issues.append("advisor_protocol_invalid")

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "runtime_mode": runtime_mode,
        "governance_mode": governance_mode,
        "node_identity": NODE_IDENTITY,
    }


def anti_drift_report(
    *,
    envelope: Optional[Dict[str, Any]] = None,
    compatibility: Optional[Dict[str, Any]] = None,
    observed_events: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Return semantic drift assessment for governance validation."""
    contract_check = validate_envelope_contract(envelope)
    compat_check = validate_compatibility(compatibility)

    observed = set(observed_events or [])
    unknown_events = sorted(list(observed - CANONICAL_EVENTS))

    drift_factors: List[str] = []
    if not contract_check["valid"]:
        drift_factors.append("runtime_contract_invalid")
    if not compat_check["compatible"]:
        drift_factors.append("compatibility_mismatch")
    if unknown_events:
        drift_factors.append("unknown_events_detected")

    if len(drift_factors) == 0:
        drift_risk = "low"
    elif len(drift_factors) == 1:
        drift_risk = "medium"
    else:
        drift_risk = "high"

    return {
        "drift_risk": drift_risk,
        "drift_factors": drift_factors,
        "unknown_events": unknown_events,
        "contract": contract_check,
        "compatibility": compat_check,
        "node_identity": NODE_IDENTITY,
    }
