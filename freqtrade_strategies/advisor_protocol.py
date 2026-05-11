"""Advisor-output protocol and consensus helpers for governed execution."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass(frozen=True)
class AdvisorVote:
    """Normalized advisor output used by the debate/consensus layer."""

    advisor_id: str
    direction: str
    confidence: float
    uncertainty: float
    risk_estimate: float
    rationale: str
    expected_horizon: str
    regime_view: str
    causal_hint: str


@dataclass(frozen=True)
class DebateConsensus:
    """Consensus summary extracted from advisor votes."""

    direction: str
    model_consensus: float
    strategy_disagreement: float
    vote_count: int
    coalition: Dict[str, float]


def _clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    return max(lower, min(upper, float(value)))


def parse_advisor_votes(envelope: Dict[str, Any]) -> List[AdvisorVote]:
    """Parse advisor votes from envelope into a normalized list."""
    advisors = envelope.get("advisors", {})
    if not isinstance(advisors, dict):
        return []

    raw_votes = advisors.get("votes", {})
    if not isinstance(raw_votes, dict):
        return []

    votes: List[AdvisorVote] = []
    for advisor_id, payload in raw_votes.items():
        if not isinstance(payload, dict):
            continue
        direction = str(payload.get("direction", "HOLD")).upper()
        if direction not in {"BUY", "SELL", "HOLD"}:
            direction = "HOLD"
        votes.append(
            AdvisorVote(
                advisor_id=str(advisor_id),
                direction=direction,
                confidence=_clamp(payload.get("confidence", 0.5)),
                uncertainty=_clamp(payload.get("uncertainty", 0.5)),
                risk_estimate=_clamp(payload.get("risk_estimate", 0.5)),
                rationale=str(payload.get("rationale", "")),
                expected_horizon=str(payload.get("expected_horizon", "unknown")),
                regime_view=str(payload.get("regime_interpretation", envelope.get("market_regime", "unknown"))),
                causal_hint=str(payload.get("causal_hint", "")),
            )
        )
    return votes


def summarize_debate(envelope: Dict[str, Any]) -> DebateConsensus:
    """Compute model consensus and disagreement from advisor votes."""
    votes = parse_advisor_votes(envelope)
    if not votes:
        return DebateConsensus(
            direction="HOLD",
            model_consensus=float(envelope.get("model_consensus", 0.5)),
            strategy_disagreement=float(envelope.get("strategy_disagreement", 0.0)),
            vote_count=0,
            coalition={},
        )

    weighted: Dict[str, float] = {"BUY": 0.0, "SELL": 0.0, "HOLD": 0.0}
    for vote in votes:
        influence = vote.confidence * max(0.0, 1.0 - vote.uncertainty)
        weighted[vote.direction] += influence

    total = sum(weighted.values()) or 1.0
    coalition = {k: v / total for k, v in weighted.items()}
    ordered = sorted(coalition.items(), key=lambda item: item[1], reverse=True)
    top_direction, top_score = ordered[0]
    second_score = ordered[1][1] if len(ordered) > 1 else 0.0

    disagreement = _clamp(1.0 - max(0.0, top_score - second_score))
    return DebateConsensus(
        direction=top_direction,
        model_consensus=_clamp(top_score),
        strategy_disagreement=disagreement,
        vote_count=len(votes),
        coalition=coalition,
    )
