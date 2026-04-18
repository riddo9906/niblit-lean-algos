#!/usr/bin/env python3
"""
ft_hyperopt.py — Run Freqtrade hyperopt for Niblit strategies.

Usage:
    python scripts/ft_hyperopt.py [--strategy StrategyName]
                                   [--epochs N] [--spaces buy sell]
                                   [--days N]
                                   [--config configs/freqtrade_config_backtesting.json]

Examples:
    # Hyperopt EmaTripleCross for 200 epochs on buy + sell space
    python scripts/ft_hyperopt.py --strategy EmaTripleCross --epochs 200

    # Hyperopt all strategies with 100 epochs each
    python scripts/ft_hyperopt.py --epochs 100

    # Optimize only entry (buy) parameters
    python scripts/ft_hyperopt.py --strategy RsiMeanReversion --spaces buy
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT     = Path(__file__).resolve().parent.parent
STRATEGIES    = [
    "EmaTripleCross",
    "MacdMomentum",
    "RsiMeanReversion",
    "BollingerSqueeze",
    "SupertrendAtr",
    "NiblitAiMaster",
]
DEFAULT_CONFIG  = REPO_ROOT / "configs" / "freqtrade_config_backtesting.json"
DEFAULT_EPOCHS  = 200
DEFAULT_DAYS    = 180
VALID_SPACES    = {"all", "buy", "sell", "roi", "stoploss", "trailing", "protection"}


def run_hyperopt(strategy: str, config: Path, epochs: int,
                 spaces: list[str], timerange: str) -> int:
    cmd = [
        "freqtrade", "hyperopt",
        "--strategy", strategy,
        "--strategy-path", str(REPO_ROOT / "freqtrade_strategies"),
        "--config", str(config),
        "--hyperopt-loss", "SharpeHyperOptLoss",
        "--epochs", str(epochs),
        "--spaces"] + spaces + [
        "--timerange", timerange,
        "--cache", "none",
        "--jobs", "-1",
    ]

    print(f"\n{'='*60}")
    print(f"Hyperopt: {strategy}  epochs={epochs}  spaces={spaces}")
    print(f"Command:  {' '.join(cmd)}")
    print(f"{'='*60}")

    result = subprocess.run(cmd, capture_output=False, text=True)
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description="Freqtrade hyperopt runner")
    parser.add_argument("--strategy", help="Strategy name (default: all)")
    parser.add_argument("--epochs", type=int, default=DEFAULT_EPOCHS,
                        help=f"Hyperopt epochs per strategy (default: {DEFAULT_EPOCHS})")
    parser.add_argument("--spaces", nargs="+", default=["buy", "sell"],
                        choices=sorted(VALID_SPACES),
                        help="Hyperopt spaces (default: buy sell)")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = parser.parse_args()

    config = Path(args.config)
    if not config.is_file():
        print(f"ERROR: config not found: {config}", file=sys.stderr)
        sys.exit(1)

    end   = datetime.utcnow()
    start = end - timedelta(days=args.days)
    timerange = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"

    strategies = [args.strategy] if args.strategy else STRATEGIES
    failures   = []

    for strat in strategies:
        rc = run_hyperopt(strat, config, args.epochs, args.spaces, timerange)
        if rc != 0:
            failures.append(strat)

    if failures:
        print(f"\nFAILED hyperopt: {failures}", file=sys.stderr)
        sys.exit(1)
    print(f"\nHyperopt complete for {len(strategies)} strategies.")


if __name__ == "__main__":
    main()
