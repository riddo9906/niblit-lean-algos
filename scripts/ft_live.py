#!/usr/bin/env python3
"""
ft_live.py — Manage Freqtrade live / dry-run trading for Niblit strategies.

Sub-commands:
    start       Start the bot (dry-run or live)
    stop        Stop the bot via REST API
    status      Show bot status via REST API
    trades      Show recent trades via REST API
    balance     Show current balance via REST API

Environment variables:
    BINANCE_API_KEY      Binance exchange API key
    BINANCE_API_SECRET   Binance exchange API secret
    FT_API_HOST          Freqtrade REST API host (default: 127.0.0.1)
    FT_API_PORT          Freqtrade REST API port (default: 8080)
    FT_API_USER          Freqtrade REST API username (default: freqtrader)
    FT_API_PASS          Freqtrade REST API password

Usage:
    # Start dry-run with EmaTripleCross strategy
    python scripts/ft_live.py start --strategy EmaTripleCross --dry-run

    # Start live trading (real Binance keys required)
    python scripts/ft_live.py start --strategy NiblitAiMaster

    # Check bot status
    python scripts/ft_live.py status

    # List recent trades
    python scripts/ft_live.py trades --limit 20

    # Stop the bot
    python scripts/ft_live.py stop
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

_FT_HOST = os.environ.get("FT_API_HOST", "127.0.0.1")
_FT_PORT = os.environ.get("FT_API_PORT", "8080")
_FT_USER = os.environ.get("FT_API_USER", "freqtrader")
_FT_PASS = os.environ.get("FT_API_PASS", "")
_FT_BASE = f"http://{_FT_HOST}:{_FT_PORT}/api/v1"


def _ft_request(method: str, path: str,
                payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Make an authenticated request to the Freqtrade REST API."""
    url = f"{_FT_BASE}{path}"
    creds = base64.b64encode(f"{_FT_USER}:{_FT_PASS}".encode()).decode()
    headers = {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
    }
    data = json.dumps(payload).encode() if payload else None
    req  = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()
        print(f"HTTP {exc.code}: {body}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"Cannot reach Freqtrade API at {url}: {exc.reason}", file=sys.stderr)
        sys.exit(1)


def cmd_start(args: argparse.Namespace) -> None:
    config = (REPO_ROOT / "configs" / "freqtrade_config_dry_run.json"
              if args.dry_run
              else REPO_ROOT / "configs" / "freqtrade_config_binance.json")

    if not config.is_file():
        print(f"ERROR: config not found: {config}", file=sys.stderr)
        sys.exit(1)

    # Inject exchange keys from environment into a temporary config override
    env = os.environ.copy()
    if not args.dry_run:
        key    = os.environ.get("BINANCE_API_KEY", "")
        secret = os.environ.get("BINANCE_API_SECRET", "")
        if not key or not secret:
            print("ERROR: BINANCE_API_KEY and BINANCE_API_SECRET must be set "
                  "for live trading.", file=sys.stderr)
            sys.exit(1)
        env["FREQTRADE__EXCHANGE__KEY"]    = key
        env["FREQTRADE__EXCHANGE__SECRET"] = secret

    cmd = [
        "freqtrade", "trade",
        "--strategy", args.strategy,
        "--strategy-path", str(REPO_ROOT / "freqtrade_strategies"),
        "--config", str(config),
    ]
    if args.logfile:
        cmd += ["--logfile", args.logfile]

    print(f"Starting Freqtrade: {' '.join(cmd)}")
    os.execvpe("freqtrade", cmd, env)  # replace current process


def cmd_stop(args: argparse.Namespace) -> None:
    result = _ft_request("POST", "/forceexit", {"tradeid": "all"})
    print(json.dumps(result, indent=2))
    stop = _ft_request("POST", "/stopbuy")
    print(json.dumps(stop, indent=2))


def cmd_status(args: argparse.Namespace) -> None:
    result = _ft_request("GET", "/status")
    print(json.dumps(result, indent=2))


def cmd_trades(args: argparse.Namespace) -> None:
    result = _ft_request("GET", f"/trades?limit={args.limit}")
    print(json.dumps(result, indent=2))


def cmd_balance(args: argparse.Namespace) -> None:
    result = _ft_request("GET", "/balance")
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description="Niblit Freqtrade live manager")
    sub    = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="Start Freqtrade bot")
    p_start.add_argument("--strategy", default="NiblitAiMaster")
    p_start.add_argument("--dry-run", action="store_true",
                         help="Use dry-run config (no real funds)")
    p_start.add_argument("--logfile", default="",
                         help="Path to log file")
    p_start.set_defaults(func=cmd_start)

    p_stop = sub.add_parser("stop", help="Stop the running bot")
    p_stop.set_defaults(func=cmd_stop)

    p_status = sub.add_parser("status", help="Show bot open trades")
    p_status.set_defaults(func=cmd_status)

    p_trades = sub.add_parser("trades", help="Show trade history")
    p_trades.add_argument("--limit", type=int, default=20)
    p_trades.set_defaults(func=cmd_trades)

    p_balance = sub.add_parser("balance", help="Show balance")
    p_balance.set_defaults(func=cmd_balance)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
