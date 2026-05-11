"""Helpers for reading and normalizing Niblit cognitive execution envelopes."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

_DEFAULT_SIGNAL_FILE = os.environ.get(
    "NIBLIT_SIGNAL_FILE",
    os.path.join(os.environ.get("TMPDIR", "/tmp"), "niblit_lean_signal.json"),
)
_MAX_SIGNAL_AGE_SECS: int = int(os.environ.get("NIBLIT_SIGNAL_MAX_AGE", "300"))
_DEFAULT_SCHEMA_VERSION = "2.0"

_REQUIRED_V2_FIELDS = (
    "schema_version",
    "signal",
    "confidence",
    "timestamp",
    "forecast_consensus",
    "governance",
    "execution",
    "temporal",
)

logger = logging.getLogger(__name__)


# pylint: disable=too-many-branches
def normalize_envelope(payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Normalize either legacy or v2 envelope payload into a stable shape."""
    if not isinstance(payload, dict):
        return None

    source = dict(payload)
    schema_version = str(source.get("schema_version") or "").strip()

    if schema_version and schema_version.startswith("2"):
        missing = [field for field in _REQUIRED_V2_FIELDS if field not in source]
        if missing:
            logger.warning("Invalid cognitive envelope: missing required fields %s", missing)
            return None
        normalized = source
    else:
        signal = str(source.get("signal", "HOLD")).upper()
        normalized = {
            "schema_version": _DEFAULT_SCHEMA_VERSION,
            "epoch": int(source.get("timestamp", 0)),
            "intent": str(source.get("intent", "legacy_signal")),
            "market_regime": str(source.get("market_regime") or source.get("regime", "ranging")),
            "signal": signal,
            "confidence": float(source.get("confidence", 0.5)),
            "timestamp": int(source.get("timestamp", 0)),
            "forecast_consensus": {
                "direction": "UP" if signal == "BUY" else "DOWN" if signal == "SELL" else "NEUTRAL",
                "agreement": float(source.get("confidence", 0.5)),
                "uncertainty": float(1.0 - float(source.get("confidence", 0.5))),
            },
            "governance": {
                "constitution_passed": True,
                "risk_tier": "medium",
                "authority": "legacy_signal_bridge",
                "survival_mode": False,
                "governance_mode": "normal",
                "governance_stability": 0.8,
                "current_drawdown_pct": 0.0,
                "max_drawdown_pct": 0.12,
            },
            "execution": {
                "max_position_size": float(source.get("risk_pct", 0.02)),
                "stoploss_override": None,
                "allow_scale_in": False,
                "hold_only": signal == "HOLD",
                "runtime_stability": 0.8,
                "execution_priority": "normal",
            },
            "world_model": {
                "predicted_horizon": "unknown",
                "scenario": "legacy_mode",
                "forecast_uncertainty": float(1.0 - float(source.get("confidence", 0.5))),
            },
            "temporal": {
                "epoch_id": int(source.get("timestamp", 0)),
                "coherence_score": 0.7,
                "epoch_alignment": "aligned",
            },
            "runtime": {
                "mode": "normal",
                "health": "unknown",
                "instability": 0.0,
                "attention_pressure": 0.2,
                "runtime_health": 0.8,
            },
            "risk": {
                "emergence_risk": 0.0,
            },
            "resources": {
                "cognitive_budget": 1.0,
                "attention_available": 1.0,
            },
            # Canonical source field is advisor_votes; advisor_vote is legacy.
            "advisors": {
                "votes": source.get("advisor_votes", source.get("advisor_vote", {})),
            },
            "reflection": {
                "reflection_confidence": float(source.get("confidence", 0.5)),
            },
            "trace": {
                "causal_trace_id": f"legacy-{int(source.get('timestamp', 0))}",
                "memory_reference_ids": [],
                "subsystem_authority": "legacy_signal_bridge",
            },
            "legacy": source,
        }

    signal = str(normalized.get("signal", "HOLD")).upper()
    if signal not in {"BUY", "SELL", "HOLD"}:
        signal = "HOLD"

    confidence = max(0.0, min(1.0, float(normalized.get("confidence", 0.5))))
    market_regime = str(normalized.get("market_regime") or normalized.get("regime", "ranging"))

    forecast = normalized.get("forecast_consensus")
    if not isinstance(forecast, dict):
        forecast = {}
    governance = normalized.get("governance")
    if not isinstance(governance, dict):
        governance = {}
    execution = normalized.get("execution")
    if not isinstance(execution, dict):
        execution = {}
    temporal = normalized.get("temporal")
    if not isinstance(temporal, dict):
        temporal = {}
    runtime = normalized.get("runtime")
    if not isinstance(runtime, dict):
        runtime = {}
    risk = normalized.get("risk")
    if not isinstance(risk, dict):
        risk = {}

    out: Dict[str, Any] = {
        **normalized,
        "schema_version": str(normalized.get("schema_version") or _DEFAULT_SCHEMA_VERSION),
        "signal": signal,
        "confidence": confidence,
        "timestamp": int(normalized.get("timestamp", 0)),
        "market_regime": market_regime,
        "forecast_consensus": {
            "direction": str(forecast.get("direction", "NEUTRAL")).upper(),
            "agreement": max(0.0, min(1.0, float(forecast.get("agreement", confidence)))),
            "uncertainty": max(0.0, min(1.0, float(forecast.get("uncertainty", 1.0 - confidence)))),
        },
        "governance": {
            "constitution_passed": bool(governance.get("constitution_passed", True)),
            "risk_tier": str(governance.get("risk_tier", "medium")),
            "authority": str(governance.get("authority", "trading_brain")),
            "survival_mode": bool(governance.get("survival_mode", False)),
            "governance_mode": str(governance.get("governance_mode", "normal")).lower(),
            "governance_stability": max(0.0, min(1.0, float(governance.get("governance_stability", 0.8)))),
            "current_drawdown_pct": max(0.0, float(governance.get("current_drawdown_pct", 0.0))),
            "max_drawdown_pct": max(0.0, float(governance.get("max_drawdown_pct", 0.12))),
        },
        "execution": {
            "max_position_size": max(0.0, min(1.0, float(execution.get("max_position_size", 0.02)))),
            "stoploss_override": execution.get("stoploss_override"),
            "allow_scale_in": bool(execution.get("allow_scale_in", False)),
            "hold_only": bool(execution.get("hold_only", signal == "HOLD")),
            "runtime_stability": max(0.0, min(1.0, float(execution.get("runtime_stability", 0.8)))),
            "execution_priority": str(execution.get("execution_priority", "normal")).lower(),
        },
        "temporal": {
            "epoch_id": int(temporal.get("epoch_id", normalized.get("epoch", normalized.get("timestamp", 0)))),
            "coherence_score": max(0.0, min(1.0, float(temporal.get("coherence_score", 0.7)))),
            "epoch_alignment": str(temporal.get("epoch_alignment", "aligned")),
        },
        "runtime": {
            "mode": str(runtime.get("mode", "normal")).lower(),
            "health": str(runtime.get("health", "unknown")),
            "instability": max(0.0, min(1.0, float(runtime.get("instability", 0.0)))),
            "attention_pressure": max(0.0, min(1.0, float(runtime.get("attention_pressure", 0.2)))),
            "runtime_health": max(0.0, min(1.0, float(runtime.get("runtime_health", 0.8)))),
        },
        "risk": {
            "emergence_risk": max(0.0, min(1.0, float(risk.get("emergence_risk", 0.0)))),
        },
        "resources": {
            "cognitive_budget": max(
                0.0,
                min(1.0, float((normalized.get("resources") or {}).get("cognitive_budget", 1.0))),
            ),
            "attention_available": max(
                0.0,
                min(1.0, float((normalized.get("resources") or {}).get("attention_available", 1.0))),
            ),
        },
        "reflection": {
            "reflection_confidence": max(
                0.0,
                min(1.0, float((normalized.get("reflection") or {}).get("reflection_confidence", confidence))),
            ),
        },
        "trace": {
            "causal_trace_id": str((normalized.get("trace") or {}).get("causal_trace_id", f"trace-{int(normalized.get('timestamp', 0))}")),
            "memory_reference_ids": list((normalized.get("trace") or {}).get("memory_reference_ids", [])),
            "subsystem_authority": str((normalized.get("trace") or {}).get("subsystem_authority", "trading_brain")),
        },
        "model_consensus": max(0.0, min(1.0, float(normalized.get("model_consensus", confidence)))),
        "strategy_disagreement": max(0.0, min(1.0, float(normalized.get("strategy_disagreement", 0.0)))),
        "governance_mode": str(normalized.get("governance_mode", (governance or {}).get("governance_mode", "normal"))).lower(),
    }

    return out


def read_envelope_file(
    signal_file: str = _DEFAULT_SIGNAL_FILE,
    max_age_secs: int = _MAX_SIGNAL_AGE_SECS,
) -> Optional[Dict[str, Any]]:
    """Read envelope from disk, validate staleness, and normalize shape."""
    try:
        if not os.path.isfile(signal_file):
            return None
        with open(signal_file, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, json.JSONDecodeError):
        return None

    envelope = normalize_envelope(payload)
    if envelope is None:
        return None

    ts = envelope.get("timestamp", 0)
    if ts and (time.time() - float(ts)) > max_age_secs:
        return None
    return envelope
