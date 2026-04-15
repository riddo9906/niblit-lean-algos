#!/usr/bin/env python3
"""
scripts/backtest_all.py — Run backtests for all (or selected) Niblit LEAN algorithms.

Launches a backtest on QuantConnect Cloud for every algorithm that has already
been deployed (i.e. appears in deployed_projects.json) or for a single
algorithm by project ID.

Uses the shared QCClient (scripts/qc_client.py) for authenticated API access,
including automatic .env loading from the repo root.

Usage
-----
    python scripts/backtest_all.py                          # backtest all deployed
    python scripts/backtest_all.py --algo 01                # match prefix
    python scripts/backtest_all.py --project-id 12345678    # single project
    python scripts/backtest_all.py --wait                   # poll until complete

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
# Main logic
# ─────────────────────────────────────────────────────────────────────────────

def load_deployed_projects(prefix_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load deployed project records from deployed_projects.json."""
    if not _DEPLOYED_FILE.exists():
        print(f"❌ No deployed_projects.json found at {_DEPLOYED_FILE}")
        print("   Run scripts/deploy_all_to_qc.py first.")
        return []
    records: List[Dict[str, Any]] = json.loads(_DEPLOYED_FILE.read_text())
    if prefix_filter:
        records = [r for r in records if r.get("algo", "").startswith(prefix_filter)]
    return records


def backtest_project(
    client: QCClient,
    project_id: int,
    algo_name: str,
    wait: bool = False,
) -> Dict[str, Any]:
    print(f"\n🔬 {algo_name}  (projectId={project_id})")

    print("   Compiling...", end=" ", flush=True)
    compile_data = client.compile(project_id)
    compile_id = compile_data.get("compileId")
    if not compile_id:
        print(f"❌  {compile_data}")
        return {"algo": algo_name, "status": "failed", "step": "compile"}
    print(f"✅  compileId={compile_id}")

    print("   Launching backtest...", end=" ", flush=True)
    bt_data = client.create_backtest(project_id, compile_id, "niblit-backtest")
    bt_id = bt_data.get("backtestId")
    if not bt_id:
        print(f"❌  {bt_data}")
        return {"algo": algo_name, "status": "failed", "step": "backtest_launch"}
    print(f"✅  backtestId={bt_id}")

    result: Dict[str, Any] = {
        "algo":        algo_name,
        "project_id":  project_id,
        "compile_id":  compile_id,
        "backtest_id": bt_id,
        "status":      "launched",
    }

    if wait:
        print("   Polling for completion...", end=" ", flush=True)
        for _ in range(60):
            time.sleep(10)
            status_data = client.read_backtest(project_id, bt_id)
            bt_entry = status_data.get("backtests", [{}])[0]
            progress  = bt_entry.get("progress", 0)
            completed = bt_entry.get("completed", False)
            print(f"\r   Polling... {progress*100:.0f}%  ", end="", flush=True)
            if completed:
                print("✅  complete")
                result["status"]     = "completed"
                result["statistics"] = bt_entry.get("result", {}).get("Statistics", {})
                break
        else:
            print("⚠  timed out (backtest still running)")
            result["status"] = "timeout"

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run backtests for deployed Niblit LEAN algorithms on QuantConnect"
    )
    parser.add_argument("--algo",       default=None,
                        help="Only backtest algorithms starting with this prefix (e.g. 01)")
    parser.add_argument("--project-id", default=None, type=int,
                        help="Run a single backtest for this project ID")
    parser.add_argument("--wait",       action="store_true",
                        help="Poll until each backtest completes")
    parser.add_argument("--save-ids",   default="backtest_results.json",
                        help="File to save backtest result IDs to")
    args = parser.parse_args()

    try:
        client = QCClient()
    except ValueError as exc:
        print(f"❌ {exc}")
        sys.exit(1)
    print(f"✅ Credentials loaded (user_id={client.user_id_prefix}…)")

    if args.project_id:
        projects: List[Dict[str, Any]] = [
            {"project_id": args.project_id, "algo": f"project-{args.project_id}",
             "status": "deployed"}
        ]
    else:
        projects = load_deployed_projects(args.algo)
        if not projects:
            sys.exit(1)
        projects = [p for p in projects if p.get("status") == "deployed"]

    print(f"\nFound {len(projects)} project(s) to backtest:")
    for p in projects:
        print(f"  • {p.get('algo', '?')}  (projectId={p.get('project_id', '?')})")

    results = []
    for p in projects:
        r = backtest_project(
            client=client,
            project_id=int(p["project_id"]),
            algo_name=p.get("algo", "unknown"),
            wait=args.wait,
        )
        results.append(r)
        time.sleep(2)

    launched  = [r for r in results if r["status"] in ("launched", "completed")]
    failed    = [r for r in results if r["status"] == "failed"]
    completed = [r for r in results if r["status"] == "completed"]

    print(f"\n{'='*50}")
    print(f"Launched: {len(launched)}  Completed: {len(completed)}  Failed: {len(failed)}")

    if results:
        out_file = _REPO_ROOT / args.save_ids
        out_file.write_text(json.dumps(results, indent=2))
        print(f"Results saved to: {out_file}")

    if completed:
        print("\n📊 Summary of completed backtests:")
        for r in completed:
            stats  = r.get("statistics", {})
            sharpe = stats.get("Sharpe Ratio", "?")
            ret    = stats.get("Compounding Annual Return", "?")
            dd     = stats.get("Drawdown", "?")
            print(f"  {r['algo']}: Sharpe={sharpe}  AnnRet={ret}  MaxDD={dd}")


if __name__ == "__main__":
    main()
