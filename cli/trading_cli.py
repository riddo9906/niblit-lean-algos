"""CLI commands for the Niblit trading runtime."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

from services.trading_runtime import TradingRuntime


async def cmd_health(args: argparse.Namespace) -> int:
    runtime = TradingRuntime()
    await runtime.initialise()
    try:
        health = await runtime.health()
        print(f"Backend: {health.backend}")
        print(f"Healthy: {health.healthy}")
        if health.error:
            print(f"Error: {health.error}")
        if health.adapter_health:
            print(f"State: {health.adapter_health.runtime_state}")
            print(f"Version: {health.adapter_health.version}")
            print(f"Latency: {health.adapter_health.latency_ms:.1f}ms")
        return 0 if health.healthy else 1
    finally:
        await runtime.shutdown()


async def cmd_adapters(args: argparse.Namespace) -> int:
    print("Available adapters:")
    print("  - freqtrade (default)")
    print("  - lean (requires LeanAlgoManager)")
    return 0


async def cmd_freqtrade_ping(args: argparse.Namespace) -> int:
    runtime = TradingRuntime(backend="freqtrade")
    await runtime.initialise()
    try:
        health = await runtime.health()
        print(f"Freqtrade reachable: {health.adapter_health.reachable if health.adapter_health else False}")
        return 0 if health.healthy else 1
    finally:
        await runtime.shutdown()


async def cmd_freqtrade_status(args: argparse.Namespace) -> int:
    runtime = TradingRuntime(backend="freqtrade")
    await runtime.initialise()
    try:
        snapshot = await runtime.snapshot()
        print(f"Backend: {snapshot.backend}")
        print(f"Total equity: {snapshot.total_equity}")
        print(f"Available: {snapshot.available_capital}")
        print(f"Open positions: {len(snapshot.open_positions)}")
        return 0
    finally:
        await runtime.shutdown()


async def cmd_freqtrade_balance(args: argparse.Namespace) -> int:
    runtime = TradingRuntime(backend="freqtrade")
    await runtime.initialise()
    try:
        snapshot = await runtime.snapshot()
        for b in snapshot.balances:
            print(f"  {b.currency}: total={b.total} free={b.free} used={b.used}")
        return 0
    finally:
        await runtime.shutdown()


async def cmd_freqtrade_trades(args: argparse.Namespace) -> int:
    runtime = TradingRuntime(backend="freqtrade")
    await runtime.initialise()
    try:
        snapshot = await runtime.snapshot()
        for p in snapshot.open_positions:
            print(f"  {p.symbol} side={p.side} entry={p.entry_price} profit={p.profit_amount}")
        return 0
    finally:
        await runtime.shutdown()


async def cmd_portfolio(args: argparse.Namespace) -> int:
    return await cmd_freqtrade_status(args)


async def cmd_performance(args: argparse.Namespace) -> int:
    runtime = TradingRuntime(backend="freqtrade")
    await runtime.initialise()
    try:
        if runtime._adapter is None:
            print("Adapter not initialised")
            return 1
        perf = await runtime._adapter.get_performance()
        print(f"Total trades: {perf.total_trades}")
        print(f"Winning: {perf.winning_trades}")
        print(f"Losing: {perf.losing_trades}")
        print(f"Win rate: {perf.win_rate:.2%}")
        print(f"Total profit: {perf.total_profit}")
        return 0
    finally:
        await runtime.shutdown()


async def cmd_validate_decision(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    from domain.trading_decision import TradingDecision

    decision = TradingDecision(**data)
    runtime = TradingRuntime(backend="freqtrade")
    await runtime.initialise()
    try:
        result = await runtime.evaluate(decision)
        print(f"Valid: {result.valid}")
        if result.errors:
            print(f"Errors: {'; '.join(result.errors)}")
        if result.warnings:
            print(f"Warnings: {'; '.join(result.warnings)}")
        return 0 if result.valid else 1
    finally:
        await runtime.shutdown()


async def cmd_execute_decision(args: argparse.Namespace) -> int:
    data = json.loads(Path(args.file).read_text(encoding="utf-8"))
    from domain.trading_decision import TradingDecision

    decision = TradingDecision(**data)
    runtime = TradingRuntime(backend="freqtrade")
    await runtime.initialise()
    try:
        result = await runtime.execute(decision)
        print(f"Success: {result.success}")
        print(f"Status: {result.status}")
        if result.error:
            print(f"Error: {result.error}")
        if result.provider_trade_id:
            print(f"Trade ID: {result.provider_trade_id}")
        return 0 if result.success else 1
    finally:
        await runtime.shutdown()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="niblit-algos", description="Niblit trading runtime CLI")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("health", help="Check trading runtime health")
    sub.add_parser("adapters", help="List available adapters")

    ft = sub.add_parser("freqtrade", help="Freqtrade adapter commands")
    ft_sub = ft.add_subparsers(dest="ft_command")
    ft_sub.add_parser("ping", help="Ping Freqtrade")
    ft_sub.add_parser("status", help="Show Freqtrade status")
    ft_sub.add_parser("balance", help="Show balances")
    ft_sub.add_parser("trades", help="Show open trades")

    sub.add_parser("portfolio", help="Show portfolio snapshot")
    sub.add_parser("performance", help="Show performance summary")

    vd = sub.add_parser("validate-decision", help="Validate a trading decision from JSON file")
    vd.add_argument("file", help="Path to decision JSON file")

    ed = sub.add_parser("execute-decision", help="Execute a trading decision from JSON file")
    ed.add_argument("file", help="Path to decision JSON file")
    ed.add_argument("--confirm-live", action="store_true", help="Confirm live execution")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    commands = {
        "health": cmd_health,
        "adapters": cmd_adapters,
        "portfolio": cmd_portfolio,
        "performance": cmd_performance,
        "validate-decision": cmd_validate_decision,
        "execute-decision": cmd_execute_decision,
    }

    if args.command == "freqtrade":
        ft_commands = {
            "ping": cmd_freqtrade_ping,
            "status": cmd_freqtrade_status,
            "balance": cmd_freqtrade_balance,
            "trades": cmd_freqtrade_trades,
        }
        handler = ft_commands.get(args.ft_command)
        if handler is None:
            parser.print_help()
            return 1
        return asyncio.run(handler(args))

    handler = commands.get(args.command)
    if handler is None:
        parser.print_help()
        return 1
    return asyncio.run(handler(args))


if __name__ == "__main__":
    sys.exit(main())