#!/usr/bin/env python3
"""
ft_download_data.py — Download historical OHLCV data for all configured pairs.

Reads the pair whitelist from the backtesting config and invokes
``freqtrade download-data`` so that every pair and timeframe is available
before running backtests or hyperopt.

Usage:
    # Download 365 days of 1h data (default)
    python scripts/ft_download_data.py

    # Download 90 days for specific pairs
    python scripts/ft_download_data.py --days 90 --pairs BTC/USDT ETH/USDT

    # Download multiple timeframes
    python scripts/ft_download_data.py --timeframes 1h 4h 1d

    # Use a custom config
    python scripts/ft_download_data.py --config configs/freqtrade_config_binance.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT       = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG  = REPO_ROOT / "configs" / "freqtrade_config_backtesting.json"
DEFAULT_DAYS    = 365
DEFAULT_TIMEFRAMES = ["1h"]


def load_pairs_from_config(config: Path) -> list[str]:
    """Return the pair whitelist defined in the Freqtrade config file."""
    with open(config, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    pairs: list[str] = (
        data.get("exchange", {}).get("pair_whitelist", [])
        or data.get("pairs", [])
    )
    if not pairs:
        raise ValueError(
            f"No pairs found in {config}. "
            "Check 'exchange.pair_whitelist' in the config."
        )
    return pairs


def download(pairs: list[str], timeframes: list[str], days: int, config: Path) -> int:
    """Run freqtrade download-data and return the process exit code."""
    cmd = [
        "freqtrade", "download-data",
        "--config", str(config),
        "--pairs"] + pairs + [
        "--timeframes"] + timeframes + [
        "--days", str(days),
    ]

    print(f"\n{'='*60}")
    print(f"Downloading data:")
    print(f"  Pairs      : {', '.join(pairs)}")
    print(f"  Timeframes : {', '.join(timeframes)}")
    print(f"  Days       : {days}")
    print(f"  Config     : {config}")
    print(f"  Command    : {' '.join(cmd)}")
    print(f"{'='*60}\n")

    result = subprocess.run(cmd, capture_output=False)
    return result.returncode


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Freqtrade historical data for all configured pairs"
    )
    parser.add_argument(
        "--pairs", nargs="+",
        help="Override pairs (default: read from config pair_whitelist)"
    )
    parser.add_argument(
        "--timeframes", nargs="+", default=DEFAULT_TIMEFRAMES,
        help=f"Timeframes to download (default: {' '.join(DEFAULT_TIMEFRAMES)})"
    )
    parser.add_argument(
        "--days", type=int, default=DEFAULT_DAYS,
        help=f"Number of days of history to download (default: {DEFAULT_DAYS})"
    )
    parser.add_argument(
        "--config", default=str(DEFAULT_CONFIG),
        help="Freqtrade config JSON (default: configs/freqtrade_config_backtesting.json)"
    )
    args = parser.parse_args()

    config = Path(args.config)
    if not config.is_file():
        print(f"ERROR: config not found: {config}", file=sys.stderr)
        sys.exit(1)

    if args.pairs:
        pairs = args.pairs
    else:
        try:
            pairs = load_pairs_from_config(config)
        except (OSError, ValueError, KeyError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit(1)

    rc = download(pairs, args.timeframes, args.days, config)
    if rc != 0:
        print("\nERROR: freqtrade download-data failed.", file=sys.stderr)
        sys.exit(rc)

    print(f"\nData download complete for {len(pairs)} pair(s).")
    print("You can now run backtests with: python scripts/ft_backtest.py")


if __name__ == "__main__":
    main()
