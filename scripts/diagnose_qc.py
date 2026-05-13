#!/usr/bin/env python3
"""
scripts/diagnose_qc.py — Diagnose QuantConnect API connectivity and auth.

Runs a series of checks and prints clear pass/fail for each, helping you
identify whether credentials, network access, or the API itself is the
source of failures in deploy_all_to_qc.py.

Usage
-----
    python scripts/diagnose_qc.py

Environment variables required:
    QC_USER_ID   — QuantConnect numeric user ID
    QC_API_CRED  — QuantConnect API token
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

try:
    from scripts.qc_client import QCClient
except ImportError:
    from qc_client import QCClient


def _ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def _fail(msg: str) -> None:
    print(f"  ❌ {msg}")


def _info(msg: str) -> None:
    print(f"  ℹ️  {msg}")


def main() -> None:
    print("=" * 60)
    print("Niblit QC Diagnostics")
    print("=" * 60)

    # ── 1. Credential loading ──────────────────────────────────────────────
    print("\n[1] Loading credentials…")
    try:
        client = QCClient()
        _ok(f"Credentials loaded (user_id={client.user_id_prefix}…)")
    except ValueError as exc:
        _fail(str(exc))
        print("\n  ➡  Set QC_USER_ID and QC_API_CRED as environment variables,")
        print("     or add them to .env in the repo root,")
        print("     or add them to niblit_params.json in the Niblit root.")
        sys.exit(1)

    # ── 2. Raw /authenticate endpoint ─────────────────────────────────────
    print("\n[2] Testing /authenticate endpoint…")
    auth_resp = client.request("GET", "authenticate")
    if auth_resp.get("success"):
        _ok("Authentication succeeded")
    else:
        _fail(f"Authentication FAILED — raw response: {json.dumps(auth_resp, indent=4)}")
        print("\n  ➡  This almost always means QC_USER_ID or QC_API_CRED is wrong.")
        print("     Double-check both values at https://www.quantconnect.com/account")
        print("     and ensure QC_API_CRED is your API Token (not your password).")
        sys.exit(1)

    # ── 3. List projects (GET /projects/read) ─────────────────────────────
    print("\n[3] Listing existing projects…")
    projects = client.list_projects()
    if isinstance(projects, list):
        _ok(f"Found {len(projects)} project(s) on QC Cloud")
        for p in projects[:5]:
            _info(f"  projectId={p.get('projectId')}  name={p.get('name')}")
        if len(projects) > 5:
            _info(f"  … and {len(projects) - 5} more")
    else:
        _fail(f"list_projects returned unexpected value: {projects}")

    # ── 4. Create a scratch project (POST /projects/create) ────────────────
    print("\n[4] Creating a temporary test project…")
    test_name = f"niblit-diag-{int(time.time())}"
    create_resp = client.create_project(test_name)
    project_id = create_resp.get("projectId")
    if project_id:
        _ok(f"Project created — projectId={project_id}  name={test_name}")
    else:
        _fail(f"Project creation FAILED — raw response: {json.dumps(create_resp, indent=4)}")
        print("\n  ➡  Possible causes:")
        print("     • Your QC account plan does not allow creating more projects")
        print("     • The project name contains disallowed characters")
        print("     • Temporary QC service issue — try again in a few minutes")
        sys.exit(1)

    # ── 5. Upload a file (POST /files/create) ──────────────────────────────
    print("\n[5] Uploading a test file…")
    sample_code = (
        "from AlgorithmImports import *\n"
        "class DiagAlgorithm(QCAlgorithm):\n"
        "    def Initialize(self): pass\n"
        "    def OnData(self, data): pass\n"
    )
    upload_resp = client.create_file(project_id, "main.py", sample_code)
    if upload_resp.get("success") is False or "error" in upload_resp:
        _fail(f"File upload FAILED — raw response: {json.dumps(upload_resp, indent=4)}")
    else:
        _ok("File uploaded successfully")

    # ── 6. Delete the scratch project ─────────────────────────────────────
    print("\n[6] Cleaning up — deleting test project…")
    del_resp = client.delete_project(project_id)
    if del_resp.get("success") is False or "error" in del_resp:
        _fail(f"Delete failed (safe to ignore) — {del_resp.get('error', del_resp)}")
    else:
        _ok(f"Test project {project_id} deleted")

    # ── Summary ────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("All checks passed — your credentials and API access look good.")
    print("You should be able to run scripts/deploy_all_to_qc.py now.")
    print("If deploy still fails, re-run with:  --verbose")
    print("=" * 60)


if __name__ == "__main__":
    main()
