# Governance Model

## Governance-First Execution

`TradeGovernanceGate` is the final authority for execution permission and stake scaling.
Strategies provide directional intent; governance decides execution.

## Arbitration Inputs

The gate evaluates:

- constitutional state (`constitution_passed`)
- drawdown constraints
- runtime mode (`normal`, `cautious`, `survival`, `lockdown`)
- coherence score and coherence drift
- runtime pressure, runtime health, attention pressure
- advisor consensus and disagreement
- governance confidence and model trust
- execution risk and emergence risk
- resource availability (cognitive budget, attention availability)
- regime-specific position caps

## Arbitration Outputs

`GovernanceDecision` includes:

- `allow`: final allow/deny
- `mode`: effective governance mode
- `reasons`: veto/throttle reasons
- `overrides`: position multiplier, max size, adjusted confidence, runtime influence

## Hard Blocks

Execution is denied when any of the following applies:

- constitution failure
- drawdown breach
- hold-only or survival/lockdown behavior
- insufficient consensus or model trust
- direction conflict
- confidence floor breach / confidence decay under instability
- regime hard block

## Explainability Guarantees

Decision artifacts expose:

- veto reasons
- advisor contributions
- consensus/disagreement
- governance overrides
- runtime influence fields
- causal trace references

## Outcome Reconciliation

`NiblitAiMaster` writes reconciliation episodes linking:

- predicted regime
- executed action
- actual outcome
- downstream volatility
- runtime state and confidence evolution

This supports post-trade reflection loops and cross-runtime learning.
