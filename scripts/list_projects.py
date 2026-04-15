#!/usr/bin/env python3
"""
scripts/list_projects.py — List all Niblit LEAN algorithm projects on QuantConnect Cloud.

Reads deployed_projects.json (produced by deploy_all_to_qc.py) and fetches
live status from the QuantConnect API, showing project names, compilation
status, and any running backtests or live algorithms.

Uses the shared QCClient (scripts/qc_client.py) for authenticated API access,
including automatic .env loading from the repo root.

Usage
-----
    python scripts/list_projects.py             # list all deployed projects
    python scripts/list_projects.py --backtests # also show backtest history
    python scripts/list_projects.py --live      # show live algorithm status
    python scripts/list_projects.py --json      # output raw JSON

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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from qc_client import QCClient  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────

_SCRIPT_DIR    = Path(__file__).resolve().parent
_REPO_ROOT     = _SCRIPT_DIR.parent
_DEPLOYED_FILE = _REPO_ROOT / "deployed_projects.json"


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def load_deployed_projects() -> List[Dict[str, Any]]:
    if not _DEPLOYED_FILE.exists():
        print(f"❌ No deployed_projects.json found at {_DEPLOYED_FILE}")
        print("   Run scripts/deploy_all_to_qc.py first.")
        return []
    return json.loads(_DEPLOYED_FILE.read_text())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="List Niblit LEAN algorithm projects on QuantConnect"
    )
    parser.add_argument("--backtests", action="store_true",
                        help="Fetch and display backtest history for each project")
    parser.add_argument("--live",      action="store_true",
                        help="Fetch and display live algorithm status")
    parser.add_argument("--json",      action="store_true",
                        help="Output full details as JSON")
    args = parser.parse_args()

    try:
        client = QCClient()
    except ValueError as exc:
        print(f"❌ {exc}")
        sys.exit(1)
    print(f"✅ Credentials loaded (user_id={client.user_id_prefix}…)\n")

    deployed = load_deployed_projects()
    if not deployed:
        sys.exit(1)

    print(f"{'#':<4} {'Algorithm':<35} {'ProjectID':<12} {'Status':<12} {'QC Name'}")
    print("-" * 90)

    all_details = []
    for i, record in enumerate(deployed, 1):
        algo       = record.get("algo", "?")
        project_id: Optional[int] = record.get("project_id")
        status     = record.get("status", "?")
        qc_name    = record.get("qc_name", "?")

        detail: Dict[str, Any] = {**record}

        if project_id and (args.backtests or args.live):
            if args.backtests:
                detail["backtests"] = client.list_backtests(int(project_id))
                time.sleep(0.3)
            if args.live:
                live_data = client.read_live(int(project_id))
                detail["live_algorithms"] = live_data.get("liveAlgorithms", [])
                time.sleep(0.3)

        print(f"{i:<4} {algo:<35} {str(project_id):<12} {status:<12} {qc_name}")

        if args.backtests and detail.get("backtests"):
            for bt in detail["backtests"][:3]:
                bt_id   = bt.get("backtestId", "?")
                bt_name = bt.get("name", "?")
                bt_stat = "✅" if bt.get("completed") else "⏳"
                sharpe  = bt.get("result", {}).get("Statistics", {}).get("Sharpe Ratio", "?")
                print(f"     └─ {bt_stat} Backtest {str(bt_id)[:8]}... '{bt_name}'  Sharpe={sharpe}")

        if args.live and detail.get("live_algorithms"):
            for live in detail["live_algorithms"][:2]:
                la_id    = live.get("deployId", "?")
                la_state = live.get("status", "?")
                print(f"     └─ 🟢 Live {str(la_id)[:8]}...  state={la_state}")

        all_details.append(detail)

    print("-" * 90)
    deployed_count = sum(1 for r in deployed if r.get("status") == "deployed")
    print(f"\nTotal: {len(deployed)} records  |  Deployed: {deployed_count}")

    if args.json:
        print("\n" + json.dumps(all_details, indent=2))


if __name__ == "__main__":
    main()
