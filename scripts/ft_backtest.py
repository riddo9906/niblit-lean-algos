#!/usr/bin/env python3
"""
ft_backtest.py — Run Freqtrade backtests for Niblit strategies.

Usage:
    python scripts/ft_backtest.py [--strategy StrategyName] [--days N]
                                   [--pairs BTC/USDT ETH/USDT ...]
                                   [--config configs/freqtrade_config_backtesting.json]
                                   [--results-dir backtest_results/]

Examples:
    # Backtest all strategies (one at a time)
    python scripts/ft_backtest.py

    # Backtest a single strategy for 90 days
    python scripts/ft_backtest.py --strategy EmaTripleCross --days 90

    # Use custom pairs
    python scripts/ft_backtest.py --strategy MacdMomentum --pairs BTC/USDT ETH/USDT
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT    = Path(__file__).resolve().parent.parent
STRATEGIES   = [
    "EmaTripleCross",
    "MacdMomentum",
    "RsiMeanReversion",
    "BollingerSqueeze",
    "SupertrendAtr",
    "NiblitAiMaster",
]
DEFAULT_CONFIG   = REPO_ROOT / "configs" / "freqtrade_config_backtesting.json"
DEFAULT_DAYS     = 180
DEFAULT_RESULTS  = REPO_ROOT / "backtest_results"


def run_backtest(strategy: str, config: Path, timerange: str,
                 pairs: list[str] | None, results_dir: Path) -> dict:
    """Run a single Freqtrade backtest and return parsed results summary."""
    results_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        "freqtrade", "backtesting",
        "--strategy", strategy,
        "--strategy-path", str(REPO_ROOT / "freqtrade_strategies"),
        "--config", str(config),
        "--timerange", timerange,
        "--export", "trades",
        "--export-filename", str(results_dir / f"{strategy}_backtest.json"),
        "--cache", "none",
    ]
    if pairs:
        cmd += ["--pairs"] + pairs

    print(f"\n{'='*60}")
    print(f"Backtesting: {strategy}")
    print(f"Command:     {' '.join(cmd)}")
    print(f"{'='*60}")

    result = subprocess.run(cmd, capture_output=False, text=True)
    return {"strategy": strategy, "returncode": result.returncode,
            "timerange": timerange}


def main() -> None:
    parser = argparse.ArgumentParser(description="Freqtrade backtest runner")
    parser.add_argument("--strategy", help="Strategy name (default: all)")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"Backtest period in days (default: {DEFAULT_DAYS})")
    parser.add_argument("--pairs", nargs="+",
                        help="Pairs to test e.g. BTC/USDT ETH/USDT")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG),
                        help="Freqtrade config JSON")
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS),
                        help="Directory for backtest result files")
    args = parser.parse_args()

    config = Path(args.config)
    if not config.is_file():
        print(f"ERROR: config not found: {config}", file=sys.stderr)
        sys.exit(1)

    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=args.days)
    timerange = f"{start.strftime('%Y%m%d')}-{end.strftime('%Y%m%d')}"

    strategies = [args.strategy] if args.strategy else STRATEGIES
    results    = []
    failures   = []

    for strat in strategies:
        res = run_backtest(strat, config, timerange, args.pairs,
                           Path(args.results_dir))
        results.append(res)
        if res["returncode"] != 0:
            failures.append(strat)

    # Summary
    summary_path = Path(args.results_dir) / "backtest_summary.json"
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nSummary written to {summary_path}")

    if failures:
        print(f"\nFAILED strategies: {failures}", file=sys.stderr)
        sys.exit(1)
    print(f"\nAll {len(strategies)} backtests completed successfully.")


if __name__ == "__main__":
    main()
