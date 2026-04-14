#!/usr/bin/env python3
# pylint: disable=duplicate-code
"""
scripts/backtest_all.py — Run backtests for all (or selected) Niblit LEAN algorithms.

Launches a backtest on QuantConnect Cloud for every algorithm that has already
been deployed (i.e. appears in deployed_projects.json) or for a single
algorithm by project ID.

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
import base64
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import urllib.request
import urllib.error

# ─────────────────────────────────────────────────────────────────────────────
# Paths
# ─────────────────────────────────────────────────────────────────────────────

_SCRIPT_DIR  = Path(__file__).resolve().parent
_REPO_ROOT   = _SCRIPT_DIR.parent
_NIBLIT_ROOT = _REPO_ROOT.parent
_DEPLOYED_FILE = _REPO_ROOT / "deployed_projects.json"
_QC_API_BASE   = "https://www.quantconnect.com/api/v2"

# ─────────────────────────────────────────────────────────────────────────────
# Credentials
# ─────────────────────────────────────────────────────────────────────────────

def _load_credentials() -> tuple[str, str]:
    user_id  = os.environ.get("QC_USER_ID", "").strip()
    api_cred = os.environ.get("QC_API_CRED", "").strip()
    if not user_id or not api_cred:
        params_file = _NIBLIT_ROOT / "niblit_params.json"
        if params_file.exists():
            try:
                params   = json.loads(params_file.read_text())
                user_id  = user_id  or str(params.get("QC_USER_ID", "")).strip()
                api_cred = api_cred or str(params.get("QC_API_CRED", "")).strip()
            except (ValueError, OSError):
                pass
    return user_id, api_cred


def _auth_headers(user_id: str, api_cred: str) -> Dict[str, str]:
    ts = str(int(time.time()))
    digest_input = f"{ts}:{api_cred}".encode()
    hash_hex = hashlib.sha256(digest_input).hexdigest()
    raw = f"{user_id}:{hash_hex}"
    encoded = base64.b64encode(raw.encode()).decode()
    return {
        "Authorization": f"Basic {encoded}",
        "Timestamp": ts,
        "Content-Type": "application/json",
    }


# ─────────────────────────────────────────────────────────────────────────────
# API helpers
# ─────────────────────────────────────────────────────────────────────────────

def _api(method: str, endpoint: str, payload: Optional[Dict[str, Any]],
         user_id: str, api_cred: str) -> Dict[str, Any]:
    url = f"{_QC_API_BASE}/{endpoint.lstrip('/')}"
    headers = _auth_headers(user_id, api_cred)
    body = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.reason}"}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"error": str(exc)}


def _compile_project(project_id: int, user_id: str, api_cred: str) -> Optional[str]:
    data = _api("POST", "compile/create", {"projectId": project_id}, user_id, api_cred)
    if "error" in data:
        print(f"  ❌ Compile failed: {data['error']}")
        return None
    return data.get("compileId")


def _launch_backtest(project_id: int, compile_id: str,
                     user_id: str, api_cred: str) -> Optional[str]:
    data = _api("POST", "backtests/create", {
        "projectId":    project_id,
        "compileId":    compile_id,
        "backtestName": "niblit-backtest",
    }, user_id, api_cred)
    if "error" in data:
        print(f"  ❌ Backtest launch failed: {data['error']}")
        return None
    return data.get("backtestId")


def _get_backtest_status(project_id: int, backtest_id: str,
                         user_id: str, api_cred: str) -> Dict[str, Any]:
    return _api("GET", f"backtests/read?projectId={project_id}&backtestId={backtest_id}",
                None, user_id, api_cred)


# ─────────────────────────────────────────────────────────────────────────────
# Main logic
# ─────────────────────────────────────────────────────────────────────────────

def load_deployed_projects(prefix_filter: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load deployed project records from deployed_projects.json."""
    if not _DEPLOYED_FILE.exists():
        print(f"❌ No deployed_projects.json found at {_DEPLOYED_FILE}")
        print("   Run scripts/deploy_all_to_qc.py first.")
        return []
    records = json.loads(_DEPLOYED_FILE.read_text())
    if prefix_filter:
        records = [r for r in records if r.get("algo", "").startswith(prefix_filter)]
    return records


def backtest_project(project_id: int, algo_name: str,
                     user_id: str, api_cred: str,
                     wait: bool = False) -> Dict[str, Any]:
    print(f"\n🔬 {algo_name}  (projectId={project_id})")

    print("   Compiling...", end=" ", flush=True)
    compile_id = _compile_project(project_id, user_id, api_cred)
    if not compile_id:
        return {"algo": algo_name, "status": "failed", "step": "compile"}
    print(f"✅  compileId={compile_id}")

    print("   Launching backtest...", end=" ", flush=True)
    bt_id = _launch_backtest(project_id, compile_id, user_id, api_cred)
    if not bt_id:
        return {"algo": algo_name, "status": "failed", "step": "backtest_launch"}
    print(f"✅  backtestId={bt_id}")

    result = {
        "algo": algo_name,
        "project_id": project_id,
        "compile_id": compile_id,
        "backtest_id": bt_id,
        "status": "launched",
    }

    if wait:
        print("   Polling for completion...", end=" ", flush=True)
        for _ in range(60):
            time.sleep(10)
            status_data = _get_backtest_status(project_id, bt_id, user_id, api_cred)
            progress = status_data.get("backtests", [{}])[0].get("progress", 0)
            completed = status_data.get("backtests", [{}])[0].get("completed", False)
            print(f"\r   Polling... {progress*100:.0f}%  ", end="", flush=True)
            if completed:
                print("✅  complete")
                bt_data = status_data.get("backtests", [{}])[0]
                result["status"] = "completed"
                result["statistics"] = bt_data.get("result", {}).get("Statistics", {})
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

    user_id, api_cred = _load_credentials()
    if not user_id or not api_cred:
        print("❌ QC_USER_ID and QC_API_CRED must be set.")
        sys.exit(1)
    print(f"✅ Credentials loaded (user_id={user_id[:4]}...)")

    if args.project_id:
        projects = [{"project_id": args.project_id, "algo": f"project-{args.project_id}",
                     "status": "deployed"}]
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
            project_id=int(p["project_id"]),
            algo_name=p.get("algo", "unknown"),
            user_id=user_id,
            api_cred=api_cred,
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
            stats = r.get("statistics", {})
            sharpe = stats.get("Sharpe Ratio", "?")
            ret    = stats.get("Compounding Annual Return", "?")
            dd     = stats.get("Drawdown", "?")
            print(f"  {r['algo']}: Sharpe={sharpe}  AnnRet={ret}  MaxDD={dd}")


if __name__ == "__main__":
    main()
