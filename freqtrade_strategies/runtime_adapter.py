"""
freqtrade_strategies/runtime_adapter.py — Cognitive Runtime Adapter Layer.

Provides a uniform RuntimeState snapshot by probing runtime sources in
priority order:

    1. Cloud Niblit runtime  (NIBLIT_CLOUD_RUNTIME_URL)    — highest authority
    2. Local Niblit signal file (NIBLIT_SIGNAL_FILE)        — default
    3. Hardcoded fallback defaults                          — graceful degradation

The adapter exposes:
    - runtime_mode      (normal / cautious / survival / lockdown)
    - governance_mode   (mirrors runtime_mode unless overridden)
    - coherence_score   (0–1 temporal coherence)
    - coherence_drift   (rate of coherence change, 0–1)
    - runtime_health    (0–1 subsystem health composite)
    - attention_pressure (0–1 cognitive load)
    - cognitive_budget  (0–1 remaining budget)
    - model_trust       (0–1 trustworthiness of advisor outputs)
    - execution_risk    (0–1 composite execution risk)
    - source            (cloud / local / fallback)
    - epoch_id          (timestamp of the source snapshot)

Downstream consumers (NiblitSignalMixin, TradeGovernanceGate) call
``RuntimeAdapter.get_state()`` to enrich execution envelopes with live
runtime context before governance evaluation.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ── environment configuration ─────────────────────────────────────────────────
_CLOUD_URL: Optional[str] = os.environ.get("NIBLIT_CLOUD_RUNTIME_URL", "")
_CLOUD_TIMEOUT_S: float = float(os.environ.get("NIBLIT_CLOUD_RUNTIME_TIMEOUT", "3.0"))
_CLOUD_MAX_AGE_S: int = int(os.environ.get("NIBLIT_CLOUD_RUNTIME_MAX_AGE", "120"))
_LOCAL_SIGNAL_FILE: str = os.environ.get(
    "NIBLIT_SIGNAL_FILE",
    os.path.join(os.environ.get("TMPDIR", "/tmp"), "niblit_lean_signal.json"),
)
_LOCAL_MAX_AGE_S: int = int(os.environ.get("NIBLIT_SIGNAL_MAX_AGE", "300"))
_REFRESH_INTERVAL_S: float = float(os.environ.get("NIBLIT_ADAPTER_REFRESH_S", "5.0"))

# Coherence drift: if coherence drops more than this between consecutive
# readings, flag it as coherence_drift so governance can react.
_COHERENCE_DRIFT_THRESHOLD: float = float(
    os.environ.get("NIBLIT_COHERENCE_DRIFT_THRESHOLD", "0.10")
)


@dataclass
class RuntimeState:
    """Normalized runtime snapshot consumed by governance and mixin."""

    source: str = "fallback"
    runtime_mode: str = "normal"
    governance_mode: str = "normal"
    coherence_score: float = 0.7
    coherence_drift: float = 0.0
    runtime_health: float = 0.8
    attention_pressure: float = 0.2
    cognitive_budget: float = 1.0
    attention_available: float = 1.0
    model_trust: float = 0.8
    execution_risk: float = 0.2
    runtime_pressure: float = 0.2
    model_orchestration_state: str = "unknown"
    survival_mode: bool = False
    constitution_passed: bool = True
    epoch_id: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)

    def is_healthy(self) -> bool:
        return self.runtime_health >= 0.4 and not self.survival_mode

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "runtime_mode": self.runtime_mode,
            "governance_mode": self.governance_mode,
            "coherence_score": self.coherence_score,
            "coherence_drift": self.coherence_drift,
            "runtime_health": self.runtime_health,
            "attention_pressure": self.attention_pressure,
            "cognitive_budget": self.cognitive_budget,
            "attention_available": self.attention_available,
            "model_trust": self.model_trust,
            "execution_risk": self.execution_risk,
            "runtime_pressure": self.runtime_pressure,
            "model_orchestration_state": self.model_orchestration_state,
            "survival_mode": self.survival_mode,
            "constitution_passed": self.constitution_passed,
            "epoch_id": self.epoch_id,
        }


_FALLBACK_STATE = RuntimeState(source="fallback")


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _parse_envelope_into_state(payload: Dict[str, Any], source: str) -> RuntimeState:
    """Extract a RuntimeState from a normalized envelope payload."""
    runtime = payload.get("runtime") or {}
    governance = payload.get("governance") or {}
    temporal = payload.get("temporal") or {}
    resources = payload.get("resources") or {}
    reflection = payload.get("reflection") or {}
    risk = payload.get("risk") or {}

    runtime_mode = str(runtime.get("mode", governance.get("governance_mode", "normal"))).lower()
    if runtime_mode == "constrained":
        runtime_mode = "cautious"

    governance_mode = str(governance.get("governance_mode", runtime_mode)).lower()
    if governance_mode == "constrained":
        governance_mode = "cautious"

    coherence = _clamp(temporal.get("coherence_score", 0.7))
    coherence_drift_raw = payload.get("coherence_drift", 0.0)
    coherence_drift = _clamp(coherence_drift_raw)

    runtime_health = _clamp(runtime.get("runtime_health", 0.8))
    attention_pressure = _clamp(runtime.get("attention_pressure", 0.2))
    runtime_pressure = _clamp(runtime.get("runtime_pressure", (attention_pressure + _clamp(runtime.get("instability", 0.0))) / 2.0))
    cognitive_budget = _clamp(resources.get("cognitive_budget", 1.0))
    attention_available = _clamp(resources.get("attention_available", 1.0))
    model_trust = _clamp(payload.get("model_trust", reflection.get("reflection_confidence", 0.8)))
    execution_risk = _clamp(payload.get("execution_risk", risk.get("emergence_risk", 0.2)))
    model_orchestration_state = str(runtime.get("model_orchestration_state", payload.get("model_orchestration_state", "unknown")))
    survival_mode = bool(governance.get("survival_mode", runtime_mode in {"survival", "lockdown"}))
    constitution_passed = bool(governance.get("constitution_passed", True))
    epoch_id = int(temporal.get("epoch_id", payload.get("timestamp", 0)))

    return RuntimeState(
        source=source,
        runtime_mode=runtime_mode,
        governance_mode=governance_mode,
        coherence_score=coherence,
        coherence_drift=coherence_drift,
        runtime_health=runtime_health,
        attention_pressure=attention_pressure,
        runtime_pressure=runtime_pressure,
        cognitive_budget=cognitive_budget,
        attention_available=attention_available,
        model_trust=model_trust,
        execution_risk=execution_risk,
        model_orchestration_state=model_orchestration_state,
        survival_mode=survival_mode,
        constitution_passed=constitution_passed,
        epoch_id=epoch_id,
        raw=payload,
    )


class RuntimeAdapter:
    """Multi-source runtime state adapter with cloud/local/fallback priority.

    Parameters
    ----------
    cloud_url:
        Optional URL that returns an envelope-compatible JSON payload
        (e.g. a Niblit runtime coordination endpoint).  When empty or
        unreachable, the adapter falls through to the local signal file.
    local_signal_file:
        Path to the local Niblit signal JSON sidecar.
    cloud_timeout_s:
        HTTP request timeout when polling the cloud endpoint.
    cloud_max_age_s:
        Maximum age (seconds) for a cloud snapshot to be considered fresh.
    local_max_age_s:
        Maximum age (seconds) for a local file snapshot.
    refresh_interval_s:
        How often to re-poll sources (inter-call caching window).
    """

    def __init__(
        self,
        cloud_url: str = _CLOUD_URL or "",
        local_signal_file: str = _LOCAL_SIGNAL_FILE,
        cloud_timeout_s: float = _CLOUD_TIMEOUT_S,
        cloud_max_age_s: int = _CLOUD_MAX_AGE_S,
        local_max_age_s: int = _LOCAL_MAX_AGE_S,
        refresh_interval_s: float = _REFRESH_INTERVAL_S,
    ) -> None:
        self.cloud_url = cloud_url.rstrip("/") if cloud_url else ""
        self.local_signal_file = local_signal_file
        self.cloud_timeout_s = cloud_timeout_s
        self.cloud_max_age_s = cloud_max_age_s
        self.local_max_age_s = local_max_age_s
        self.refresh_interval_s = refresh_interval_s

        self._cached_state: Optional[RuntimeState] = None
        self._last_refresh: float = 0.0
        self._prev_coherence: Optional[float] = None

    # ── public API ────────────────────────────────────────────────────────────

    def get_state(self) -> RuntimeState:
        """Return the current RuntimeState, refreshing if the cache is stale."""
        now = time.time()
        if (
            self._cached_state is not None
            and (now - self._last_refresh) < self.refresh_interval_s
        ):
            return self._cached_state

        state = self._probe()
        state = self._apply_coherence_drift(state)
        self._cached_state = state
        self._last_refresh = now
        return state

    def enrich_envelope(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        """Merge live runtime state into an existing envelope, returning a copy.

        Fields already present in the envelope take priority unless the
        adapter source is ``cloud`` (highest authority).
        """
        state = self.get_state()
        env = dict(envelope)

        cloud_authority = state.source == "cloud"

        def _set_if_missing_or_cloud(key: str, value: Any) -> None:
            if cloud_authority or key not in env:
                env[key] = value

        _set_if_missing_or_cloud("coherence_drift", state.coherence_drift)
        _set_if_missing_or_cloud("model_trust", state.model_trust)
        _set_if_missing_or_cloud("execution_risk", state.execution_risk)

        runtime = dict(env.get("runtime") or {})
        if cloud_authority or "runtime_health" not in runtime:
            runtime["runtime_health"] = state.runtime_health
        if cloud_authority or "attention_pressure" not in runtime:
            runtime["attention_pressure"] = state.attention_pressure
        if cloud_authority or "runtime_pressure" not in runtime:
            runtime["runtime_pressure"] = state.runtime_pressure
        if cloud_authority or "model_orchestration_state" not in runtime:
            runtime["model_orchestration_state"] = state.model_orchestration_state
        if cloud_authority or "mode" not in runtime or runtime["mode"] == "normal":
            # Escalate runtime mode if adapter suggests higher pressure
            existing_mode = str(runtime.get("mode", "normal")).lower()
            if _mode_rank(state.runtime_mode) > _mode_rank(existing_mode):
                runtime["mode"] = state.runtime_mode
        env["runtime"] = runtime

        resources = dict(env.get("resources") or {})
        if cloud_authority or "cognitive_budget" not in resources:
            resources["cognitive_budget"] = state.cognitive_budget
        if cloud_authority or "attention_available" not in resources:
            resources["attention_available"] = state.attention_available
        env["resources"] = resources

        temporal = dict(env.get("temporal") or {})
        if cloud_authority or temporal.get("epoch_id", 0) == 0:
            if state.epoch_id > 0:
                temporal["epoch_id"] = state.epoch_id
        env["temporal"] = temporal

        governance = dict(env.get("governance") or {})
        if cloud_authority:
            governance["governance_mode"] = state.governance_mode
            governance["survival_mode"] = state.survival_mode
            governance["constitution_passed"] = state.constitution_passed
        env["governance"] = governance

        env["_runtime_source"] = state.source
        return env

    def status(self) -> Dict[str, Any]:
        """Expose adapter configuration and last known state for observability."""
        state = self._cached_state or _FALLBACK_STATE
        return {
            "cloud_url": self.cloud_url or "(none)",
            "cloud_timeout_s": self.cloud_timeout_s,
            "cloud_max_age_s": self.cloud_max_age_s,
            "local_max_age_s": self.local_max_age_s,
            "refresh_interval_s": self.refresh_interval_s,
            "last_state": state.to_dict(),
        }

    # ── internal probing ──────────────────────────────────────────────────────

    def _probe(self) -> RuntimeState:
        """Probe sources in priority order and return the first fresh result."""
        if self.cloud_url:
            state = self._probe_cloud()
            if state is not None:
                logger.debug("runtime_adapter: using cloud source %s", self.cloud_url)
                return state

        state = self._probe_local()
        if state is not None:
            logger.debug("runtime_adapter: using local signal file")
            return state

        logger.debug("runtime_adapter: falling back to defaults")
        return RuntimeState(source="fallback")

    def _probe_cloud(self) -> Optional[RuntimeState]:
        """Fetch envelope-compatible JSON from the configured cloud URL."""
        url = f"{self.cloud_url}/niblit/runtime" if self.cloud_url else ""
        if not url:
            return None
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self.cloud_timeout_s) as resp:
                if resp.status != 200:
                    return None
                payload: Dict[str, Any] = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
            return None

        if not isinstance(payload, dict):
            return None

        ts = int(payload.get("timestamp", 0))
        if ts and (time.time() - ts) > self.cloud_max_age_s:
            logger.debug("runtime_adapter: cloud snapshot stale (%ds)", int(time.time() - ts))
            return None

        try:
            return _parse_envelope_into_state(payload, source="cloud")
        except (KeyError, TypeError, ValueError):
            return None

    def _probe_local(self) -> Optional[RuntimeState]:
        """Read and parse the local signal file."""
        path = self.local_signal_file
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload: Dict[str, Any] = json.load(handle)
        except (OSError, ValueError, json.JSONDecodeError):
            return None

        if not isinstance(payload, dict):
            return None

        ts = int(payload.get("timestamp", 0))
        if ts and (time.time() - ts) > self.local_max_age_s:
            return None

        try:
            return _parse_envelope_into_state(payload, source="local")
        except (KeyError, TypeError, ValueError):
            return None

    def _apply_coherence_drift(self, state: RuntimeState) -> RuntimeState:
        """Compute and annotate coherence drift relative to the previous reading."""
        prev = self._prev_coherence
        current = state.coherence_score
        if prev is not None:
            drift = max(0.0, prev - current)
            self._prev_coherence = current
            if drift >= _COHERENCE_DRIFT_THRESHOLD:
                logger.info(
                    "runtime_adapter: coherence drift detected %.3f -> %.3f (drift=%.3f)",
                    prev,
                    current,
                    drift,
                )
            # Replace state with updated drift value
            return RuntimeState(
                source=state.source,
                runtime_mode=state.runtime_mode,
                governance_mode=state.governance_mode,
                coherence_score=state.coherence_score,
                coherence_drift=_clamp(drift),
                runtime_health=state.runtime_health,
                attention_pressure=state.attention_pressure,
                runtime_pressure=state.runtime_pressure,
                cognitive_budget=state.cognitive_budget,
                attention_available=state.attention_available,
                model_trust=state.model_trust,
                execution_risk=state.execution_risk,
                model_orchestration_state=state.model_orchestration_state,
                survival_mode=state.survival_mode,
                constitution_passed=state.constitution_passed,
                epoch_id=state.epoch_id,
                raw=state.raw,
            )
        self._prev_coherence = current
        return state


# ── utility helpers ───────────────────────────────────────────────────────────

_MODE_RANK = {"normal": 0, "cautious": 1, "constrained": 1, "survival": 2, "lockdown": 3}


def _mode_rank(mode: str) -> int:
    return _MODE_RANK.get(str(mode).lower(), 0)


# Module-level singleton (used by NiblitSignalMixin).
_GLOBAL_ADAPTER: Optional[RuntimeAdapter] = None


def get_global_adapter() -> RuntimeAdapter:
    """Return (or create) the module-level RuntimeAdapter singleton."""
    global _GLOBAL_ADAPTER  # pylint: disable=global-statement
    if _GLOBAL_ADAPTER is None:
        _GLOBAL_ADAPTER = RuntimeAdapter()
    return _GLOBAL_ADAPTER
