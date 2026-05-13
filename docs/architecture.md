# Cognitive Execution Architecture

## Overview

`niblit-lean-algos` now operates as a governed cognitive execution node for Freqtrade strategies.
Core execution flow:

1. `scripts/sync_signal.py` (or external Niblit runtime) writes schema-v2 cognitive envelopes.
2. `freqtrade_strategies/cognitive_envelope.py` normalizes legacy and v2 payloads.
3. `freqtrade_strategies/runtime_adapter.py` enriches envelopes with distributed runtime state from:
   - cloud runtime (`NIBLIT_CLOUD_RUNTIME_URL`)
   - local runtime sidecar (`NIBLIT_SIGNAL_FILE`)
   - fallback runtime defaults
4. `freqtrade_strategies/advisor_protocol.py` computes debate consensus/disagreement.
5. `freqtrade_strategies/trade_governance.py` arbitrates allow/deny and sizing overrides.
6. `freqtrade_strategies/NiblitSignalMixin.py` applies governance to entry/exit/sizing hooks.
7. `freqtrade_strategies/execution_replay.py` emits replay traces to `runtime_traces/execution_trace.jsonl`.
8. `freqtrade_strategies/NiblitAiMaster.py` emits reflection + reconciliation sidecars.

## Runtime Coordination

The runtime adapter adds governance-relevant state:

- runtime mode / governance mode
- coherence score + coherence drift
- runtime pressure / runtime health
- model orchestration state
- resource state (budget + attention availability)
- model trust and execution risk
- source authority (`cloud`, `local`, `fallback`)

Cloud snapshots have highest authority and can override local runtime fields.

## Replay and Explainability

Replay records include:

- governance decision (allow, reasons, overrides)
- advisor contributions and consensus state
- runtime source and runtime mode
- confidence evolution (confidence, coherence, agreement, uncertainty)
- causal references (`trace.causal_trace_id`, memory references)

These traces support deterministic after-the-fact reconstruction of decision context.

## Sidecars

- `NIBLIT_RESULTS_FILE`: current strategy + governance snapshot
- `NIBLIT_REFLECTION_FILE`: reflection events
- `NIBLIT_EPISODES_FILE`: market episodes and reconciliation episodes
- `NIBLIT_TRACE_FILE`: governed execution replay traces
