#!/usr/bin/env python3
# pylint: disable=duplicate-code
"""
scripts/sync_signal.py — Write a Niblit-format trading signal to the shared
signal file so that LEAN algorithms running locally can read it via NiblitBridge.

This is the counterpart to niblit_bridge/connector.py.  In a production setup
Niblit's TradingBrain writes this file continuously.  This script lets you
inject a manual signal during local testing or CI.

Usage
-----
    # Inject a BUY signal with 80% confidence
    python scripts/sync_signal.py --signal BUY --confidence 0.8

    # SELL with regime and indicators
    python scripts/sync_signal.py --signal SELL --confidence 0.65 --regime bearish

    # HOLD
    python scripts/sync_signal.py --signal HOLD

    # Read and display the current signal
    python scripts/sync_signal.py --read

    # Clear / delete the signal file
    python scripts/sync_signal.py --clear

Environment variables:
    NIBLIT_SIGNAL_FILE   — path to write the signal JSON (default: /tmp/niblit_lean_signal.json)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Default signal file path — same default as niblit_bridge/connector.py
_SIGNAL_FILE = os.environ.get(
    "NIBLIT_SIGNAL_FILE",
    os.path.join(os.environ.get("TMPDIR", "/tmp"), "niblit_lean_signal.json"),
)


# ─────────────────────────────────────────────────────────────────────────────
# Signal read / write
# ─────────────────────────────────────────────────────────────────────────────

def write_signal(  # pylint: disable=too-many-positional-arguments
    signal:     str,
    confidence: float = 0.7,
    symbol:     str   = "SPY",
    price:      float = 0.0,
    regime:     str   = "bullish",
    risk_pct:   float = 0.02,
    rsi:        Optional[float] = None,
    macd:       Optional[float] = None,
) -> None:
    """Write a Niblit-format signal JSON to the shared signal file."""
    signal = signal.upper()
    if signal not in ("BUY", "SELL", "HOLD"):
        print(f"❌ Invalid signal '{signal}'. Must be BUY, SELL, or HOLD.")
        sys.exit(1)

    confidence = max(0.0, min(1.0, confidence))

    payload: Dict[str, Any] = {
        "signal":     signal,
        "confidence": round(confidence, 4),
        "symbol":     symbol,
        "price":      price,
        "timestamp":  int(time.time()),
        "indicators": {},
        "regime":     regime,
        "risk_pct":   round(risk_pct, 4),
    }

    if rsi is not None:
        payload["indicators"]["rsi"] = round(rsi, 2)
    if macd is not None:
        payload["indicators"]["macd"] = round(macd, 6)

    path = Path(_SIGNAL_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"✅ Signal written to {path}")
    _print_signal(payload)


def read_signal() -> None:
    """Read and print the current signal from the signal file."""
    path = Path(_SIGNAL_FILE)
    if not path.exists():
        print(f"⚠  No signal file found at {path}")
        return
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"❌ Could not read signal file: {exc}")
        return

    ts = payload.get("timestamp", 0)
    age = int(time.time()) - ts
    stale = age > 300
    stale_tag = f"  ⚠ STALE ({age}s old)" if stale else f"  ✅ fresh ({age}s old)"
    print(f"\n📡 Current Niblit signal ({path}){stale_tag}")
    _print_signal(payload)


def _print_signal(payload: Dict[str, Any]) -> None:
    sig   = payload.get("signal", "?")
    conf  = payload.get("confidence", 0.0)
    sym   = payload.get("symbol", "?")
    price = payload.get("price", 0.0)
    reg   = payload.get("regime", "?")
    risk  = payload.get("risk_pct", 0.0)
    inds  = payload.get("indicators", {})
    ts    = payload.get("timestamp", 0)
    color = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}.get(sig, "⚪")

    print(f"  {color} Signal    : {sig}")
    print(f"     Confidence: {conf:.1%}")
    print(f"     Symbol    : {sym}")
    print(f"     Price     : {price}")
    print(f"     Regime    : {reg}")
    print(f"     Risk %    : {risk:.2%}")
    if inds:
        print(f"     Indicators: {inds}")
    print(f"     Timestamp : {ts}  ({time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(ts))})")


def clear_signal() -> None:
    """Remove the signal file."""
    path = Path(_SIGNAL_FILE)
    if path.exists():
        path.unlink()
        print(f"✅ Signal file deleted: {path}")
    else:
        print(f"⚠  Signal file not found: {path}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inject / inspect Niblit trading signals for local LEAN testing"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--signal",  choices=["BUY", "SELL", "HOLD", "buy", "sell", "hold"],
                       help="Signal to write (BUY / SELL / HOLD)")
    group.add_argument("--read",    action="store_true",
                       help="Print the current signal from the signal file")
    group.add_argument("--clear",   action="store_true",
                       help="Delete the signal file")

    parser.add_argument("--confidence", type=float, default=0.7,
                        help="Signal confidence 0–1 (default: 0.7)")
    parser.add_argument("--symbol",     default="SPY",
                        help="Trading symbol (default: SPY)")
    parser.add_argument("--price",      type=float, default=0.0,
                        help="Current price (0 = unknown)")
    parser.add_argument("--regime",
                        choices=["bullish", "bearish", "ranging", "volatile"],
                        default="bullish",
                        help="Market regime (default: bullish)")
    parser.add_argument("--risk-pct",   type=float, default=0.02,
                        help="Suggested position risk as fraction (default: 0.02)")
    parser.add_argument("--rsi",        type=float, default=None,
                        help="RSI indicator value")
    parser.add_argument("--macd",       type=float, default=None,
                        help="MACD indicator value")

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
        )


if __name__ == "__main__":
    main()
