#!/usr/bin/env python3
# pylint: disable=duplicate-code
"""
scripts/sync_signal.py — write/read a Niblit cognitive execution envelope.

Default behavior writes schema_version=2.0 envelopes while preserving
legacy compatibility fields (signal/confidence/regime/risk_pct/timestamp).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

_SIGNAL_FILE = os.environ.get(
    "NIBLIT_SIGNAL_FILE",
    os.path.join(os.environ.get("TMPDIR", "/tmp"), "niblit_lean_signal.json"),
)


def write_signal(  # pylint: disable=too-many-positional-arguments,too-many-locals
    signal: str,
    confidence: float = 0.7,
    symbol: str = "SPY",
    price: float = 0.0,
    regime: str = "bullish",
    risk_pct: float = 0.02,
    rsi: Optional[float] = None,
    macd: Optional[float] = None,
    intent: str = "trend_following",
    coherence: float = 0.8,
    agreement: float = 0.7,
    uncertainty: float = 0.2,
    emergence_risk: float = 0.1,
    runtime_mode: str = "normal",
    hold_only: bool = False,
    constitution_passed: bool = True,
    legacy: bool = False,
) -> None:
    """Write a Niblit cognitive envelope JSON to the shared signal file."""
    signal = signal.upper()
    if signal not in ("BUY", "SELL", "HOLD"):
        print(f"❌ Invalid signal '{signal}'. Must be BUY, SELL, or HOLD.")
        sys.exit(1)

    confidence = max(0.0, min(1.0, confidence))
    coherence = max(0.0, min(1.0, coherence))
    agreement = max(0.0, min(1.0, agreement))
    uncertainty = max(0.0, min(1.0, uncertainty))
    emergence_risk = max(0.0, min(1.0, emergence_risk))

    ts = int(time.time())

    payload: Dict[str, Any] = {
        "signal": signal,
        "confidence": round(confidence, 4),
        "symbol": symbol,
        "price": price,
        "timestamp": ts,
        "indicators": {},
        "regime": regime,
        "risk_pct": round(risk_pct, 4),
    }

    if rsi is not None:
        payload["indicators"]["rsi"] = round(rsi, 2)
    if macd is not None:
        payload["indicators"]["macd"] = round(macd, 6)

    if not legacy:
        payload.update({
            "schema_version": "2.0",
            "epoch": ts,
            "intent": intent,
            "market_regime": regime,
            "forecast_consensus": {
                "direction": "UP" if signal == "BUY" else "DOWN" if signal == "SELL" else "NEUTRAL",
                "agreement": round(agreement, 4),
                "uncertainty": round(uncertainty, 4),
            },
            "governance": {
                "constitution_passed": constitution_passed,
                "risk_tier": "medium",
                "authority": "trading_brain",
                "survival_mode": runtime_mode == "survival",
                "governance_stability": round(max(0.0, min(1.0, 1.0 - emergence_risk)), 4),
                "current_drawdown_pct": 0.0,
                "max_drawdown_pct": 0.12,
            },
            "execution": {
                "max_position_size": round(risk_pct, 4),
                "stoploss_override": None,
                "allow_scale_in": False,
                "hold_only": hold_only or signal == "HOLD",
                "runtime_stability": round(max(0.0, min(1.0, 1.0 - emergence_risk)), 4),
            },
            "world_model": {
                "predicted_horizon": "12h",
                "scenario": "manual_injection",
            },
            "temporal": {
                "epoch_id": ts,
                "coherence_score": round(coherence, 4),
            },
            "runtime": {
                "mode": runtime_mode,
                "health": "manual",
                "instability": round(emergence_risk, 4),
            },
            "risk": {
                "emergence_risk": round(emergence_risk, 4),
            },
            "advisors": {
                "votes": {},
            },
        })

    path = Path(_SIGNAL_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"✅ Signal written to {path}")
    _print_signal(payload)


def read_signal() -> None:
    """Read and print the current signal/envelope from the signal file."""
    path = Path(_SIGNAL_FILE)
    if not path.exists():
        print(f"⚠  No signal file found at {path}")
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"❌ Could not read signal file: {exc}")
        return

    ts = int(payload.get("timestamp", 0) or 0)
    age = int(time.time()) - ts if ts else 0
    stale = age > 300
    stale_tag = f"  ⚠ STALE ({age}s old)" if stale else f"  ✅ fresh ({age}s old)"
    print(f"\n📡 Current Niblit signal ({path}){stale_tag}")
    _print_signal(payload)


def _print_signal(payload: Dict[str, Any]) -> None:
    sig = payload.get("signal", "?")
    conf = float(payload.get("confidence", 0.0) or 0.0)
    sym = payload.get("symbol", "?")
    price = payload.get("price", 0.0)
    reg = payload.get("market_regime", payload.get("regime", "?"))
    risk = float(payload.get("risk_pct", 0.0) or 0.0)
    ts = int(payload.get("timestamp", 0) or 0)
    color = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(sig, "⚪")

    print(f"  {color} Signal      : {sig}")
    print(f"     Confidence  : {conf:.1%}")
    print(f"     Symbol      : {sym}")
    print(f"     Price       : {price}")
    print(f"     Regime      : {reg}")
    print(f"     Risk %      : {risk:.2%}")

    if "schema_version" in payload:
        temporal = payload.get("temporal", {}) if isinstance(payload.get("temporal"), dict) else {}
        forecast = payload.get("forecast_consensus", {}) if isinstance(payload.get("forecast_consensus"), dict) else {}
        runtime = payload.get("runtime", {}) if isinstance(payload.get("runtime"), dict) else {}
        governance = payload.get("governance", {}) if isinstance(payload.get("governance"), dict) else {}
        print(f"     Schema      : {payload.get('schema_version')}")
        print(f"     Coherence   : {float(temporal.get('coherence_score', 0.0)):.2f}")
        print(f"     Agreement   : {float(forecast.get('agreement', conf)):.2f}")
        print(f"     Uncertainty : {float(forecast.get('uncertainty', 1.0 - conf)):.2f}")
        print(f"     Runtime     : {runtime.get('mode', 'normal')}")
        print(f"     Constitution: {governance.get('constitution_passed', True)}")

    if ts > 0:
        print(f"     Timestamp   : {ts}  ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))})")


def clear_signal() -> None:
    """Remove the signal file."""
    path = Path(_SIGNAL_FILE)
    if path.exists():
        path.unlink()
        print(f"✅ Signal file deleted: {path}")
    else:
        print(f"⚠  Signal file not found: {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inject / inspect Niblit cognitive execution envelopes"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--signal", choices=["BUY", "SELL", "HOLD", "buy", "sell", "hold"],
                       help="Signal to write (BUY / SELL / HOLD)")
    group.add_argument("--read", action="store_true",
                       help="Print the current signal from the signal file")
    group.add_argument("--clear", action="store_true",
                       help="Delete the signal file")

    parser.add_argument("--confidence", type=float, default=0.7,
                        help="Signal confidence 0–1 (default: 0.7)")
    parser.add_argument("--symbol", default="SPY",
                        help="Trading symbol (default: SPY)")
    parser.add_argument("--price", type=float, default=0.0,
                        help="Current price (0 = unknown)")
    parser.add_argument("--regime", default="bullish",
                        help="Market regime label (default: bullish)")
    parser.add_argument("--risk-pct", type=float, default=0.02,
                        help="Suggested max position size fraction (default: 0.02)")
    parser.add_argument("--rsi", type=float, default=None,
                        help="RSI indicator value")
    parser.add_argument("--macd", type=float, default=None,
                        help="MACD indicator value")

    parser.add_argument("--intent", default="trend_following",
                        help="Execution intent (default: trend_following)")
    parser.add_argument("--coherence", type=float, default=0.8,
                        help="Temporal coherence score 0-1 (default: 0.8)")
    parser.add_argument("--agreement", type=float, default=0.7,
                        help="Forecast consensus agreement 0-1 (default: 0.7)")
    parser.add_argument("--uncertainty", type=float, default=0.2,
                        help="Forecast uncertainty 0-1 (default: 0.2)")
    parser.add_argument("--emergence-risk", type=float, default=0.1,
                        help="Emergence risk 0-1 (default: 0.1)")
    parser.add_argument("--runtime-mode", choices=["normal", "constrained", "survival"], default="normal",
                        help="Runtime mode (default: normal)")
    parser.add_argument("--hold-only", action="store_true",
                        help="Set hold-only execution constraint")
    parser.add_argument("--constitution-failed", action="store_true",
                        help="Mark constitution as failed")
    parser.add_argument("--legacy", action="store_true",
                        help="Write legacy schema only (signal/confidence/regime)")

    args = parser.parse_args()

    if args.read:
        read_signal()
    elif args.clear:
        clear_signal()
    else:
        write_signal(
            signal=args.signal,
            confidence=args.confidence,
            symbol=args.symbol,
            price=args.price,
            regime=args.regime,
            risk_pct=args.risk_pct,
            rsi=args.rsi,
            macd=args.macd,
            intent=args.intent,
            coherence=args.coherence,
            agreement=args.agreement,
            uncertainty=args.uncertainty,
            emergence_risk=args.emergence_risk,
            runtime_mode=args.runtime_mode,
            hold_only=args.hold_only,
            constitution_passed=not args.constitution_failed,
            legacy=args.legacy,
        )


if __name__ == "__main__":
    main()
