#!/usr/bin/env python3
"""
ft_live.py — Manage Freqtrade live / dry-run trading for Niblit strategies.

Sub-commands:
    start       Start the bot (dry-run or live)
    stop        Stop the bot via REST API
    status      Show bot status via REST API
    trades      Show recent trades via REST API
    balance     Show current balance via REST API
    mode        Show cognitive runtime mode from Niblit envelope
    health      Show cognitive health snapshot from Niblit envelope

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
_NIBLIT_SIGNAL_FILE = os.environ.get(
    "NIBLIT_SIGNAL_FILE",
    os.path.join(os.environ.get("TMPDIR", "/tmp"), "niblit_lean_signal.json"),
)


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
    result = _ft_request("POST", "/stop")
    print(json.dumps(result, indent=2))


def cmd_status(args: argparse.Namespace) -> None:
    result = _ft_request("GET", "/status")
    print(json.dumps(result, indent=2))


def cmd_trades(args: argparse.Namespace) -> None:
    result = _ft_request("GET", f"/trades?limit={args.limit}")
    print(json.dumps(result, indent=2))


def cmd_balance(args: argparse.Namespace) -> None:
    result = _ft_request("GET", "/balance")
    print(json.dumps(result, indent=2))


def _load_signal_payload() -> Dict[str, Any]:
    path = Path(_NIBLIT_SIGNAL_FILE)
    if not path.is_file():
        return {"status": "missing_signal_file", "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "invalid_signal_file", "path": str(path), "error": str(exc)}

    runtime = payload.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    temporal = payload.get("temporal")
    temporal = temporal if isinstance(temporal, dict) else {}
    forecast = payload.get("forecast_consensus")
    forecast = forecast if isinstance(forecast, dict) else {}
    governance = payload.get("governance")
    governance = governance if isinstance(governance, dict) else {}
    execution = payload.get("execution")
    execution = execution if isinstance(execution, dict) else {}

    return {
        "status": "ok",
        "path": str(path),
        "schema_version": payload.get("schema_version", "legacy"),
        "signal": payload.get("signal"),
        "confidence": payload.get("confidence"),
        "market_regime": payload.get("market_regime", payload.get("regime", "unknown")),
        "runtime_mode": runtime.get("mode", "normal"),
        "runtime_health": runtime.get("health", "unknown"),
        "coherence_score": temporal.get("coherence_score"),
        "forecast_agreement": forecast.get("agreement"),
        "forecast_uncertainty": forecast.get("uncertainty"),
        "constitution_passed": governance.get("constitution_passed", True),
        "survival_mode": governance.get("survival_mode", False),
        "hold_only": execution.get("hold_only", False),
        "max_position_size": execution.get("max_position_size", payload.get("risk_pct")),
    }


def cmd_mode(args: argparse.Namespace) -> None:
    print(json.dumps(_load_signal_payload(), indent=2))


def cmd_health(args: argparse.Namespace) -> None:
    payload = _load_signal_payload()
    if payload.get("status") != "ok":
        print(json.dumps(payload, indent=2))
        return
    health = {
        "runtime_mode": payload.get("runtime_mode"),
        "runtime_health": payload.get("runtime_health"),
        "coherence_score": payload.get("coherence_score"),
        "forecast_agreement": payload.get("forecast_agreement"),
        "forecast_uncertainty": payload.get("forecast_uncertainty"),
        "constitution_passed": payload.get("constitution_passed"),
        "survival_mode": payload.get("survival_mode"),
        "hold_only": payload.get("hold_only"),
    }
    print(json.dumps(health, indent=2))


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

    p_mode = sub.add_parser("mode", help="Show cognitive runtime mode from signal envelope")
    p_mode.set_defaults(func=cmd_mode)

    p_health = sub.add_parser("health", help="Show cognitive runtime health snapshot")
    p_health.set_defaults(func=cmd_health)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
