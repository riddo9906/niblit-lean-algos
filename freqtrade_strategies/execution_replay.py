"""
freqtrade_strategies/execution_replay.py — Execution Replay System.

Writes versioned, deterministically replayable JSONL traces for every
governed execution decision.  Each trace record captures:

    - schema_version        versioned trace format identifier
    - trace_id              causal_trace_id from the envelope
    - epoch_id              envelope epoch
    - event_type            entry_decision / exit_decision / sizing_decision
    - timestamp             wall-clock seconds
    - pair                  trading pair (e.g. "BTC/USDT")
    - regime                market regime from envelope
    - runtime_mode          effective governance mode at decision time
    - governance_decision   allow/deny reasons and overrides
    - consensus_state       model_consensus, strategy_disagreement, coalition
    - advisor_contributions normalized advisor vote breakdown
    - confidence_state      confidence, coherence, agreement, uncertainty
    - resource_state        cognitive_budget, attention_pressure, etc.
    - execution_reasoning   narrative list of factors contributing to decision
    - runtime_source        cloud / local / fallback

Traces are appended to a configurable JSONL file (default:
``/tmp/niblit_execution_trace.jsonl``) and are designed to be:

    - Self-contained         (no external references needed to replay)
    - Forward-compatible     (new fields do not break existing readers)
    - Causally ordered       (epoch_id + timestamp enable total ordering)
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

try:
    from .governance_contract import (
        NODE_IDENTITY,
        TRACE_SCHEMA_VERSION as _CONTRACT_TRACE_VERSION,
        compatibility_metadata,
        normalize_replay_metadata,
    )
except (ImportError, AttributeError):
    try:
        from governance_contract import (
            NODE_IDENTITY,
            compatibility_metadata,
            normalize_replay_metadata,
        )
        _CONTRACT_TRACE_VERSION = None
    except ImportError:
        NODE_IDENTITY = "niblit_lean_algos"
        _CONTRACT_TRACE_VERSION = None

        def compatibility_metadata(overrides=None):  # type: ignore[misc]
            return {"schema_version": "2.x", "event_contract_version": "omega-7", "node_identity": NODE_IDENTITY}

        def normalize_replay_metadata(payload):  # type: ignore[misc]
            return dict(payload or {})

logger = logging.getLogger(__name__)

_TRACE_FILE: str = os.environ.get(
    "NIBLIT_TRACE_FILE",
    os.path.join(os.environ.get("TMPDIR", "/tmp"), "runtime_traces", "execution_trace.jsonl"),
)

TRACE_SCHEMA_VERSION = "1.0"


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def build_execution_reasoning(
    allow: bool,
    reasons: List[str],
    confidence: float,
    coherence: float,
    model_consensus: float,
    strategy_disagreement: float,
    runtime_mode: str,
    regime: str,
    overrides: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Produce a human-readable list of factors that drove the decision."""
    narrative: List[str] = []
    overrides = overrides or {}

    if allow:
        narrative.append(
            f"Entry allowed: confidence={confidence:.2f}, coherence={coherence:.2f}, "
            f"consensus={model_consensus:.2f}"
        )
    else:
        narrative.append(
            f"Entry denied: confidence={confidence:.2f}, coherence={coherence:.2f}, "
            f"consensus={model_consensus:.2f}"
        )

    if strategy_disagreement > 0.50:
        narrative.append(f"Elevated advisor disagreement ({strategy_disagreement:.2f}) applied penalty.")

    if runtime_mode not in {"normal", ""}:
        narrative.append(f"Runtime governance mode: {runtime_mode}.")

    for reason in reasons:
        narrative.append(f"Governance flag: {reason}")

    pos_mult = overrides.get("position_multiplier", 1.0)
    if pos_mult < 1.0:
        narrative.append(f"Position multiplier reduced to {pos_mult:.2f}.")

    if regime not in {"ranging", "bullish", "trending"}:
        narrative.append(f"Non-standard regime '{regime}' influenced execution.")

    return narrative


# pylint: disable=too-many-arguments,too-many-locals
def write_execution_trace(
    event_type: str,
    pair: str,
    envelope: Dict[str, Any],
    governance_decision: Optional[Dict[str, Any]] = None,
    consensus_state: Optional[Dict[str, Any]] = None,
    trace_file: str = _TRACE_FILE,
) -> None:
    """Append one execution trace record to the JSONL trace file.

    Parameters
    ----------
    event_type:
        One of ``entry_decision``, ``exit_decision``, ``sizing_decision``.
    pair:
        The trading pair (e.g. ``"BTC/USDT"``).
    envelope:
        The full normalized cognitive execution envelope at decision time.
    governance_decision:
        Dict with allow/mode/reasons/overrides from GovernanceDecision.
    consensus_state:
        Dict with model_consensus, strategy_disagreement, coalition,
        vote_count from DebateConsensus.
    trace_file:
        Path to the JSONL trace output file.
    """
    governance_decision = governance_decision or {}
    consensus_state = consensus_state or {}
    allow: bool = bool(governance_decision.get("allow", True))
    reasons: List[str] = list(governance_decision.get("reasons", []))
    overrides: Dict[str, Any] = dict(governance_decision.get("overrides", {}))
    mode: str = str(governance_decision.get("mode", "normal"))

    temporal = envelope.get("temporal") or {}
    runtime = envelope.get("runtime") or {}
    resources = envelope.get("resources") or {}
    forecast = envelope.get("forecast_consensus") or {}
    trace_meta = envelope.get("trace") or {}
    advisors = envelope.get("advisors") or {}

    confidence = _clamp(envelope.get("confidence", 0.5))
    coherence = _clamp(temporal.get("coherence_score", 0.7))
    model_consensus = _clamp(consensus_state.get("model_consensus", envelope.get("model_consensus", confidence)))
    strategy_disagreement = _clamp(consensus_state.get("strategy_disagreement", envelope.get("strategy_disagreement", 0.0)))
    regime = str(envelope.get("market_regime", "ranging"))

    reasoning = build_execution_reasoning(
        allow=allow,
        reasons=reasons,
        confidence=confidence,
        coherence=coherence,
        model_consensus=model_consensus,
        strategy_disagreement=strategy_disagreement,
        runtime_mode=mode,
        regime=regime,
        overrides=overrides,
    )

    record: Dict[str, Any] = {
        "schema_version": TRACE_SCHEMA_VERSION,
        "trace_id": str(trace_meta.get("causal_trace_id", f"trace-{int(time.time())}")),
        "epoch_id": int(temporal.get("epoch_id", envelope.get("timestamp", 0))),
        "event_type": event_type,
        "timestamp": int(time.time()),
        "pair": pair,
        "regime": regime,
        "runtime_mode": mode,
        "runtime_source": str(envelope.get("_runtime_source", "unknown")),
        "governance_decision": {
            "allow": allow,
            "reasons": reasons,
            "overrides": {k: v for k, v in overrides.items() if k not in {"debate_coalition"}},
            "position_multiplier": _clamp(overrides.get("position_multiplier", 1.0)),
            "max_position_size": float(overrides.get("max_position_size", 0.02)),
        },
        "consensus_state": {
            "model_consensus": model_consensus,
            "strategy_disagreement": strategy_disagreement,
            "coalition": consensus_state.get("coalition", {}),
            "vote_count": int(consensus_state.get("vote_count", 0)),
            "debate_direction": str(consensus_state.get("direction", "HOLD")),
        },
        "advisor_contributions": {
            advisor_id: {
                "direction": str(v.get("direction", "HOLD")),
                "confidence": _clamp(v.get("confidence", 0.5)),
                "uncertainty": _clamp(v.get("uncertainty", 0.5)),
                "regime_view": str(v.get("regime_interpretation", regime)),
                "causal_hint": str(v.get("causal_hint", "")),
            }
            for advisor_id, v in (advisors.get("votes") or {}).items()
            if isinstance(v, dict)
        },
        "confidence_state": {
            "confidence": confidence,
            "coherence": coherence,
            "agreement": _clamp(forecast.get("agreement", confidence)),
            "uncertainty": _clamp(forecast.get("uncertainty", 1.0 - confidence)),
            # prefer temporal.coherence_drift (canonical per PR #219), fall back to top-level
            "coherence_drift": _clamp(temporal.get("coherence_drift", envelope.get("coherence_drift", 0.0))),
            "model_trust": _clamp(envelope.get("model_trust", 0.8)),
        },
        "resource_state": {
            "cognitive_budget": _clamp(resources.get("cognitive_budget", 1.0)),
            "attention_available": _clamp(resources.get("attention_available", 1.0)),
            "attention_pressure": _clamp(runtime.get("attention_pressure", 0.2)),
            "runtime_health": _clamp(runtime.get("runtime_health", 0.8)),
        },
        "execution_reasoning": reasoning,
        "causal_references": list(trace_meta.get("memory_reference_ids", [])),
        "subsystem_authority": str(trace_meta.get("subsystem_authority", NODE_IDENTITY)),
        "envelope_schema_version": str(envelope.get("schema_version", "unknown")),
        "compatibility": compatibility_metadata(),
    }

    try:
        os.makedirs(os.path.dirname(os.path.abspath(trace_file)), exist_ok=True)
        with open(trace_file, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, default=str) + "\n")
    except OSError as exc:
        logger.warning("execution_replay: unable to write trace to %s: %s", trace_file, exc)


class ExecutionReplayWriter:
    """Stateful replay writer bound to a specific trace file and pair context.

    Simplifies calling write_execution_trace from multiple decision points
    within a single strategy execution cycle.
    """

    def __init__(self, trace_file: str = _TRACE_FILE) -> None:
        self.trace_file = trace_file

    def record_entry_decision(
        self,
        pair: str,
        envelope: Dict[str, Any],
        governance_decision: Optional[Dict[str, Any]] = None,
        consensus_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        write_execution_trace(
            event_type="entry_decision",
            pair=pair,
            envelope=envelope,
            governance_decision=governance_decision,
            consensus_state=consensus_state,
            trace_file=self.trace_file,
        )

    def record_exit_decision(
        self,
        pair: str,
        envelope: Dict[str, Any],
        governance_decision: Optional[Dict[str, Any]] = None,
        consensus_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        write_execution_trace(
            event_type="exit_decision",
            pair=pair,
            envelope=envelope,
            governance_decision=governance_decision,
            consensus_state=consensus_state,
            trace_file=self.trace_file,
        )

    def record_sizing_decision(
        self,
        pair: str,
        envelope: Dict[str, Any],
        governance_decision: Optional[Dict[str, Any]] = None,
        consensus_state: Optional[Dict[str, Any]] = None,
    ) -> None:
        write_execution_trace(
            event_type="sizing_decision",
            pair=pair,
            envelope=envelope,
            governance_decision=governance_decision,
            consensus_state=consensus_state,
            trace_file=self.trace_file,
        )

    def status(self) -> Dict[str, Any]:
        """Return writer configuration for observability."""
        size = 0
        try:
            size = os.path.getsize(self.trace_file)
        except OSError:
            pass
        return {
            "trace_file": self.trace_file,
            "file_size_bytes": size,
            "node_identity": NODE_IDENTITY,
            "compatibility": compatibility_metadata(),
        }
