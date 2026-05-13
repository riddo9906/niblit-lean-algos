#!/usr/bin/env python3
"""
scripts/live_trading.py — Manage live (and paper) trading algorithms on
QuantConnect Cloud.

Sub-commands
------------
    start       Deploy and start a live algorithm
    stop        Stop a running live algorithm (positions remain open)
    liquidate   Liquidate all positions and stop the algorithm
    status      Show the current live status for one or all projects
    portfolio   Show live portfolio (holdings, cash, open orders)
    orders      Show recent live order history
    log         Tail the live algorithm log

Usage examples
--------------
    # Start live paper trading for algorithm 01:
    python scripts/live_trading.py start --algo 01

    # Start live with a real brokerage (requires broker-specific settings):
    python scripts/live_trading.py start --project-id 12345678 \\
        --brokerage InteractiveBrokersBrokerage

    # Check status of all deployed live algorithms:
    python scripts/live_trading.py status

    # Stop a live algorithm gracefully (no liquidation):
    python scripts/live_trading.py stop --project-id 12345678

    # Liquidate positions and stop:
    python scripts/live_trading.py liquidate --project-id 12345678

    # Show live portfolio:
    python scripts/live_trading.py portfolio --project-id 12345678

    # Tail live log:
    python scripts/live_trading.py log --project-id 12345678

Environment variables required:
    QC_USER_ID   — QuantConnect numeric user ID
    QC_API_CRED  — QuantConnect API token
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from scripts.qc_client import QCClient
except ImportError:
    from qc_client import QCClient

# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────

_SCRIPT_DIR    = Path(__file__).resolve().parent
_REPO_ROOT     = _SCRIPT_DIR.parent
_DEPLOYED_FILE = _REPO_ROOT / "deployed_projects.json"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_deployed(
    prefix_filter: Optional[str] = None,
    project_id_filter: Optional[int] = None,
) -> List[Dict[str, Any]]:
    if not _DEPLOYED_FILE.exists():
        print(f"❌ {_DEPLOYED_FILE} not found.  Run deploy_all_to_qc.py first.")
        return []
    records: List[Dict[str, Any]] = json.loads(_DEPLOYED_FILE.read_text())
    if project_id_filter is not None:
        records = [r for r in records if r.get("project_id") == project_id_filter]
    if prefix_filter:
        records = [r for r in records if r.get("algo", "").startswith(prefix_filter)]
    return [r for r in records if r.get("status") == "deployed"]


def _resolve_projects(args: argparse.Namespace) -> List[Dict[str, Any]]:
    """Return a list of project records matching the CLI flags."""
    project_id: Optional[int] = getattr(args, "project_id", None)
    algo:       Optional[str] = getattr(args, "algo", None)

    if project_id:
        return [{"project_id": project_id, "algo": f"project-{project_id}",
                 "status": "deployed"}]
    return _load_deployed(prefix_filter=algo, project_id_filter=project_id)


def _compile_and_get_id(client: QCClient, project_id: int) -> Optional[str]:
    """Compile a project and return the compile ID, or None on failure."""
    print("   Compiling…", end=" ", flush=True)
    result = client.compile(project_id)
    compile_id = result.get("compileId")
    if not compile_id:
        print(f"❌  {result}")
        return None
    print(f"✅  compileId={compile_id}")
    return compile_id


# ─────────────────────────────────────────────────────────────────────────────
# Sub-command: start
# ─────────────────────────────────────────────────────────────────────────────

def cmd_start(client: QCClient, args: argparse.Namespace) -> None:
    """Compile and deploy a live algorithm."""
    projects = _resolve_projects(args)
    if not projects:
        print("No matching projects found.")
        sys.exit(1)

    for p in projects:
        pid  = int(p["project_id"])
        algo = p.get("algo", f"project-{pid}")
        print(f"\n🚀 Starting live: {algo}  (projectId={pid})")

        compile_id = _compile_and_get_id(client, pid)
        if not compile_id:
            continue

        print(f"   Deploying live ({args.brokerage})…", end=" ", flush=True)
        result = client.create_live(
            project_id=pid,
            compile_id=compile_id,
            brokerage=args.brokerage,
            server_type=args.server_type,
            automatic_redeploy=args.auto_redeploy,
        )
        deploy_id = result.get("deployId") or result.get("liveAlgorithm", {}).get("deployId")
        if deploy_id:
            print(f"✅  deployId={deploy_id}")
        else:
            print(f"❌  {result}")
        time.sleep(1)


# ─────────────────────────────────────────────────────────────────────────────
# Sub-command: stop
# ─────────────────────────────────────────────────────────────────────────────

def cmd_stop(client: QCClient, args: argparse.Namespace) -> None:
    """Stop live algorithms (no liquidation)."""
    projects = _resolve_projects(args)
    if not projects:
        print("No matching projects found.")
        sys.exit(1)

    for p in projects:
        pid  = int(p["project_id"])
        algo = p.get("algo", f"project-{pid}")
        print(f"\n⏹  Stopping {algo}  (projectId={pid})…", end=" ", flush=True)
        result = client.stop_live(pid)
        if result.get("success") is not False:
            print("✅")
        else:
            print(f"❌  {result}")


# ─────────────────────────────────────────────────────────────────────────────
# Sub-command: liquidate
# ─────────────────────────────────────────────────────────────────────────────

def cmd_liquidate(client: QCClient, args: argparse.Namespace) -> None:
    """Liquidate positions and stop live algorithms."""
    projects = _resolve_projects(args)
    if not projects:
        print("No matching projects found.")
        sys.exit(1)

    for p in projects:
        pid  = int(p["project_id"])
        algo = p.get("algo", f"project-{pid}")
        if not args.yes:
            answer = input(f"  Liquidate ALL positions for {algo} (projectId={pid})? [y/N] ")
            if answer.strip().lower() not in ("y", "yes"):
                print("  Skipped.")
                continue
        print(f"\n💥 Liquidating {algo}  (projectId={pid})…", end=" ", flush=True)
        result = client.liquidate_live(pid)
        if result.get("success") is not False:
            print("✅")
        else:
            print(f"❌  {result}")


# ─────────────────────────────────────────────────────────────────────────────
# Sub-command: status
# ─────────────────────────────────────────────────────────────────────────────

def cmd_status(client: QCClient, args: argparse.Namespace) -> None:
    """Show the live status of deployed projects."""
    projects = _resolve_projects(args)

    if not projects:
        # Fall back to listing all live algorithms from the API
        print("Listing all running live algorithms…")
        lives = client.list_live(status="")
        if not lives:
            print("  (none found)")
            return
        _print_live_table(lives)
        return

    print(f"\n{'#':<4} {'Algorithm':<35} {'ProjectID':<12} {'Status':<15} {'DeployID'}")
    print("-" * 90)
    for i, p in enumerate(projects, 1):
        pid  = int(p["project_id"])
        algo = p.get("algo", f"project-{pid}")
        live_data = client.read_live(pid)
        live_nodes: List[Dict[str, Any]] = live_data.get("live", [])
        if not live_nodes:
            print(f"{i:<4} {algo:<35} {str(pid):<12} {'not deployed':<15}")
            continue
        for node in live_nodes:
            state    = node.get("status", "?")
            deploy_id = node.get("deployId", "?")[:16]
            print(f"{i:<4} {algo:<35} {str(pid):<12} {state:<15} {deploy_id}")
        time.sleep(0.3)
    print("-" * 90)


def _print_live_table(lives: List[Dict[str, Any]]) -> None:
    print(f"\n{'ProjectID':<12} {'Status':<15} {'DeployID':<20} {'Started'}")
    print("-" * 75)
    for node in lives:
        pid      = node.get("projectId", "?")
        state    = node.get("status", "?")
        did      = (node.get("deployId") or "?")[:18]
        started  = node.get("launched", "?")
        print(f"{str(pid):<12} {state:<15} {did:<20} {started}")
    print("-" * 75)


# ─────────────────────────────────────────────────────────────────────────────
# Sub-command: portfolio
# ─────────────────────────────────────────────────────────────────────────────

def cmd_portfolio(client: QCClient, args: argparse.Namespace) -> None:
    """Show live portfolio (holdings + cash) for a project."""
    pid = _require_project_id(args)
    data = client.read_live_portfolio(pid)

    cash     = data.get("cash", {})
    holdings = data.get("holdings", {})
    orders   = data.get("openOrders", [])

    print(f"\n💼 Portfolio for projectId={pid}")
    if cash:
        print(f"  Cash: {cash}")
    if holdings:
        print("\n  Holdings:")
        for sym, h in holdings.items():
            qty     = h.get("quantity", 0)
            price   = h.get("marketPrice", 0)
            value   = h.get("marketValue", 0)
            unrealised = h.get("unrealizedPnL", 0)
            print(f"    {sym:<12} qty={qty:<12} price={price:<12.4f} "
                  f"value={value:<12.2f} unrealized_pnl={unrealised:.2f}")
    else:
        print("  Holdings: (none)")

    if orders:
        print(f"\n  Open orders: {len(orders)}")
        for o in orders[:5]:
            print(f"    {o}")

    if args.json:
        print("\n" + json.dumps(data, indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# Sub-command: orders
# ─────────────────────────────────────────────────────────────────────────────

def cmd_orders(client: QCClient, args: argparse.Namespace) -> None:
    """Show recent order history for a live project."""
    pid  = _require_project_id(args)
    data = client.read_live_orders(pid)
    orders: List[Dict[str, Any]] = data.get("orders", [])

    print(f"\n📋 Orders for projectId={pid}  ({len(orders)} total)")
    for o in orders[:20]:
        symbol    = o.get("symbol", {}).get("value", "?")
        direction = o.get("direction", "?")
        qty       = o.get("quantity", "?")
        price     = o.get("price", "?")
        status    = o.get("status", "?")
        created   = o.get("createdTime", "?")
        print(f"  {created}  {symbol:<10} {direction:<6} qty={qty:<12} "
              f"price={price:<12} status={status}")

    if args.json:
        print("\n" + json.dumps(data, indent=2))


# ─────────────────────────────────────────────────────────────────────────────
# Sub-command: log
# ─────────────────────────────────────────────────────────────────────────────

def cmd_log(client: QCClient, args: argparse.Namespace) -> None:
    """Tail the live algorithm log for a project."""
    pid = _require_project_id(args)

    # Determine algorithm ID from live status
    live_data  = client.read_live(pid)
    live_nodes: List[Dict[str, Any]] = live_data.get("live", [])
    if not live_nodes:
        print(f"❌ No live algorithm found for projectId={pid}")
        sys.exit(1)
    algo_id = live_nodes[0].get("deployId", "")

    print(f"\n📜 Live log for projectId={pid}  algorithmId={algo_id}")
    start = 0
    while True:
        log_data = client.read_live_log(pid, algo_id, start=start)
        entries: List[str] = log_data.get("log", []) or log_data.get("logs", [])
        for entry in entries:
            print(entry)
        if entries:
            start += len(entries)
        if not args.follow:
            break
        time.sleep(5)


# ─────────────────────────────────────────────────────────────────────────────
# Util
# ─────────────────────────────────────────────────────────────────────────────

def _require_project_id(args: argparse.Namespace) -> int:
    """Return project_id from args, exiting if not provided."""
    pid = getattr(args, "project_id", None)
    if not pid:
        print("❌ --project-id is required for this command.")
        sys.exit(1)
    return int(pid)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:  # pylint: disable=too-many-statements
    parser = argparse.ArgumentParser(
        description="Manage live trading algorithms on QuantConnect Cloud",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    subs = parser.add_subparsers(dest="command", required=True)

    # ── start ────────────────────────────────────────────────────────────────
    p_start = subs.add_parser("start",
                               help="Compile and deploy a live algorithm")
    p_start.add_argument("--algo",         default=None,
                          help="Match algorithm by prefix (e.g. 20)")
    p_start.add_argument("--project-id",   dest="project_id", type=int, default=None,
                          help="Use a specific project ID")
    p_start.add_argument("--brokerage",    default="PaperBrokerage",
                          help="Brokerage name (default: PaperBrokerage)")
    p_start.add_argument("--server-type",  dest="server_type", default="Server512",
                          choices=["Server512", "Server1024", "Server2048"],
                          help="Cloud server tier (default: Server512)")
    p_start.add_argument("--auto-redeploy", dest="auto_redeploy",
                          action="store_true",
                          help="Automatically redeploy if the algorithm crashes")

    # ── stop ─────────────────────────────────────────────────────────────────
    p_stop = subs.add_parser("stop", help="Stop a running live algorithm")
    p_stop.add_argument("--algo",       default=None)
    p_stop.add_argument("--project-id", dest="project_id", type=int, default=None)

    # ── liquidate ────────────────────────────────────────────────────────────
    p_liq = subs.add_parser("liquidate",
                             help="Liquidate positions and stop the algorithm")
    p_liq.add_argument("--algo",       default=None)
    p_liq.add_argument("--project-id", dest="project_id", type=int, default=None)
    p_liq.add_argument("--yes", "-y",  action="store_true",
                        help="Skip confirmation prompt")

    # ── status ───────────────────────────────────────────────────────────────
    p_status = subs.add_parser("status", help="Show live status of algorithms")
    p_status.add_argument("--algo",       default=None)
    p_status.add_argument("--project-id", dest="project_id", type=int, default=None)

    # ── portfolio ─────────────────────────────────────────────────────────────
    p_port = subs.add_parser("portfolio", help="Show live portfolio")
    p_port.add_argument("--project-id", dest="project_id", type=int, required=True)
    p_port.add_argument("--json", action="store_true", help="Output raw JSON")

    # ── orders ───────────────────────────────────────────────────────────────
    p_ord = subs.add_parser("orders", help="Show live order history")
    p_ord.add_argument("--project-id", dest="project_id", type=int, required=True)
    p_ord.add_argument("--json", action="store_true", help="Output raw JSON")

    # ── log ──────────────────────────────────────────────────────────────────
    p_log = subs.add_parser("log", help="Tail the live algorithm log")
    p_log.add_argument("--project-id", dest="project_id", type=int, required=True)
    p_log.add_argument("--follow", "-f", action="store_true",
                        help="Poll and stream new log entries every 5s")

    args = parser.parse_args()

    try:
        client = QCClient()
    except ValueError as exc:
        print(f"❌ {exc}")
        sys.exit(1)
    print(f"✅ Connected (user_id={client.user_id_prefix}…)")

    dispatch = {
        "start":     cmd_start,
        "stop":      cmd_stop,
        "liquidate": cmd_liquidate,
        "status":    cmd_status,
        "portfolio": cmd_portfolio,
        "orders":    cmd_orders,
        "log":       cmd_log,
    }
    dispatch[args.command](client, args)


if __name__ == "__main__":
    main()
