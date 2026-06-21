# niblit-lean-algos architecture alignment

This layer remains responsible for market execution and broker-facing workflows only.
It should consume approved decisions from the cognitive core and emit execution telemetry back through the shared event contract.

## Boundary

- Inbound: market events and execution signals
- Outbound: execution proposals, approvals, fills, and performance updates
- Forbidden: high-level reasoning or AI decision logic beyond normalized execution policy enforcement
