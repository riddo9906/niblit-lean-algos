#!/usr/bin/env python3
# pylint: disable=duplicate-code
"""
scripts/list_projects.py — List all Niblit LEAN algorithm projects on QuantConnect Cloud.

Reads deployed_projects.json (produced by deploy_all_to_qc.py) and fetches
live status from the QuantConnect API, showing project names, compilation
status, and any running backtests or live algorithms.

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

_SCRIPT_DIR    = Path(__file__).resolve().parent
_REPO_ROOT     = _SCRIPT_DIR.parent
_NIBLIT_ROOT   = _REPO_ROOT.parent
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


def _get_project(project_id: int, user_id: str, api_cred: str) -> Dict[str, Any]:
    return _api("GET", f"projects/read?projectId={project_id}", None, user_id, api_cred)


def _list_backtests(project_id: int, user_id: str, api_cred: str) -> List[Dict[str, Any]]:
    data = _api("GET", f"backtests/list?projectId={project_id}", None, user_id, api_cred)
    return data.get("backtests", []) if data and "backtests" in data else []


def _list_live_algorithms(project_id: int, user_id: str, api_cred: str) -> List[Dict[str, Any]]:
    data = _api("GET", f"live/list?projectId={project_id}", None, user_id, api_cred)
    return data.get("live", []) if data and "live" in data else []


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

    user_id, api_cred = _load_credentials()
    if not user_id or not api_cred:
        print("❌ QC_USER_ID and QC_API_CRED must be set.")
        sys.exit(1)
    print(f"✅ Credentials loaded (user_id={user_id[:4]}...)\n")

    deployed = load_deployed_projects()
    if not deployed:
        sys.exit(1)

    print(f"{'#':<4} {'Algorithm':<35} {'ProjectID':<12} {'Status':<12} {'QC Name'}")
    print("-" * 90)

    all_details = []
    for i, record in enumerate(deployed, 1):
        algo       = record.get("algo", "?")
        project_id = record.get("project_id")
        status     = record.get("status", "?")
        qc_name    = record.get("qc_name", "?")

        detail = {**record}

        if project_id and (args.backtests or args.live):
            if args.backtests:
                bts = _list_backtests(int(project_id), user_id, api_cred)
                detail["backtests"] = bts
                time.sleep(0.3)
            if args.live:
                lives = _list_live_algorithms(int(project_id), user_id, api_cred)
                detail["live_algorithms"] = lives
                time.sleep(0.3)

        print(f"{i:<4} {algo:<35} {str(project_id):<12} {status:<12} {qc_name}")

        if args.backtests and detail.get("backtests"):
            for bt in detail["backtests"][:3]:
                bt_id   = bt.get("backtestId", "?")
                bt_name = bt.get("name", "?")
                bt_stat = "✅" if bt.get("completed") else "⏳"
                sharpe  = bt.get("result", {}).get("Statistics", {}).get("Sharpe Ratio", "?")
                print(f"     └─ {bt_stat} Backtest {bt_id[:8]}... '{bt_name}'  Sharpe={sharpe}")

        if args.live and detail.get("live_algorithms"):
            for live in detail["live_algorithms"][:2]:
                la_id    = live.get("deployId", "?")
                la_state = live.get("status", "?")
                print(f"     └─ 🟢 Live {la_id[:8]}...  state={la_state}")

        all_details.append(detail)

    print("-" * 90)
    deployed_count = sum(1 for r in deployed if r.get("status") == "deployed")
    print(f"\nTotal: {len(deployed)} records  |  Deployed: {deployed_count}")

    if args.json:
        print("\n" + json.dumps(all_details, indent=2))


if __name__ == "__main__":
    main()
