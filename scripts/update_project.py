#!/usr/bin/env python3
"""
scripts/update_project.py — Push updated algorithm files to existing
QuantConnect Cloud projects.

When you modify an algorithm locally, use this script (instead of
deploy_all_to_qc.py which creates new projects) to push the changes
to the projects you already deployed.

Usage
-----
    # Update all algorithms that appear in deployed_projects.json:
    python scripts/update_project.py

    # Update a single algorithm by its prefix number:
    python scripts/update_project.py --algo 01

    # Update a project by its QC project ID directly:
    python scripts/update_project.py --project-id 12345678

    # Update + immediately recompile and launch a new backtest:
    python scripts/update_project.py --backtest

    # Print what would be done without making any changes:
    python scripts/update_project.py --dry-run

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
_ALGOS_DIR     = _REPO_ROOT / "algorithms"
_DEPLOYED_FILE = _REPO_ROOT / "deployed_projects.json"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_deployed_projects(
    prefix_filter: Optional[str] = None,
    project_id_filter: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Load deployed project records from ``deployed_projects.json``."""
    if not _DEPLOYED_FILE.exists():
        print(f"❌ deployed_projects.json not found at {_DEPLOYED_FILE}")
        print("   Run scripts/deploy_all_to_qc.py first to create projects.")
        return []
    records: List[Dict[str, Any]] = json.loads(_DEPLOYED_FILE.read_text())
    if project_id_filter is not None:
        records = [r for r in records if r.get("project_id") == project_id_filter]
    if prefix_filter:
        records = [r for r in records if r.get("algo", "").startswith(prefix_filter)]
    return [r for r in records if r.get("status") == "deployed"]


def update_one(  # pylint: disable=too-many-positional-arguments
    client: QCClient,
    project_id: int,
    algo_name: str,
    main_py: Path,
    dry_run: bool,
    run_backtest: bool,
) -> Dict[str, Any]:
    """Upload a new version of main.py to an existing QC project."""
    content = main_py.read_text(encoding="utf-8")

    print(f"\n🔄 {algo_name}  (projectId={project_id})")
    print(f"   File size: {len(content)} bytes")

    if dry_run:
        print("   [DRY RUN — no changes made]")
        return {"algo": algo_name, "project_id": project_id, "status": "dry_run"}

    # Push the updated file (create if missing, overwrite if present)
    print("   Uploading main.py…", end=" ", flush=True)
    result = client.upsert_file(project_id, "main.py", content)
    if result.get("success") is False or "error" in result:
        print(f"❌  {result}")
        return {"algo": algo_name, "project_id": project_id, "status": "failed",
                "step": "upload"}
    print("✅")

    outcome: Dict[str, Any] = {
        "algo":       algo_name,
        "project_id": project_id,
        "status":     "updated",
    }

    if run_backtest:
        print("   Compiling…", end=" ", flush=True)
        compile_data = client.compile(project_id)
        compile_id = compile_data.get("compileId")
        if not compile_id:
            print(f"❌  {compile_data}")
            outcome["status"] = "partial"
            outcome["step"]   = "compile"
            return outcome
        print(f"✅  compileId={compile_id}")

        print("   Launching backtest…", end=" ", flush=True)
        bt_data = client.create_backtest(project_id, compile_id,
                                         f"niblit-update-{algo_name}")
        bt_id = bt_data.get("backtestId")
        if bt_id:
            print(f"✅  backtestId={bt_id}")
            outcome["compile_id"]  = compile_id
            outcome["backtest_id"] = bt_id
        else:
            print(f"⚠  {bt_data}")

        time.sleep(2)  # mild rate-limit courtesy

    return outcome


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Push updated algorithm files to existing QuantConnect projects"
    )
    parser.add_argument("--algo",       default=None,
                        help="Update only algorithms starting with this prefix (e.g. 01)")
    parser.add_argument("--project-id", default=None, type=int,
                        help="Update only this specific project ID")
    parser.add_argument("--backtest",   action="store_true",
                        help="Compile and launch a new backtest after uploading")
    parser.add_argument("--dry-run",    action="store_true",
                        help="Print what would be done without making changes")
    parser.add_argument("--save-ids",   default="update_results.json",
                        help="File to save update result IDs to")
    args = parser.parse_args()

    try:
        client = QCClient()
    except ValueError as exc:
        print(f"❌ {exc}")
        sys.exit(1)
    print(f"✅ Connected (user_id={client.user_id_prefix}…)")

    # ── Resolve which projects to update ──────────────────────────────────
    projects = load_deployed_projects(
        prefix_filter=args.algo,
        project_id_filter=args.project_id,
    )
    if not projects:
        print("No matching deployed projects found.")
        sys.exit(1)

    print(f"\nWill update {len(projects)} project(s):")
    for p in projects:
        print(f"  • {p.get('algo', '?')}  (projectId={p.get('project_id', '?')})")

    # ── Run updates ───────────────────────────────────────────────────────
    results = []
    for p in projects:
        pid  = int(p["project_id"])
        algo = p.get("algo", f"project-{pid}")
        main_py = _ALGOS_DIR / algo / "main.py"

        if not main_py.exists():
            print(f"\n⚠  {algo}: main.py not found at {main_py}, skipping.")
            results.append({"algo": algo, "project_id": pid, "status": "skipped"})
            continue

        result = update_one(
            client=client,
            project_id=pid,
            algo_name=algo,
            main_py=main_py,
            dry_run=args.dry_run,
            run_backtest=args.backtest,
        )
        results.append(result)
        if not args.dry_run:
            time.sleep(2)

    # ── Summary ───────────────────────────────────────────────────────────
    updated  = [r for r in results if r["status"] == "updated"]
    failed   = [r for r in results if r["status"] == "failed"]
    skipped  = [r for r in results if r["status"] == "skipped"]

    print(f"\n{'='*50}")
    print(f"Updated: {len(updated)}  Failed: {len(failed)}  Skipped: {len(skipped)}")

    if results and not args.dry_run:
        out_file = _REPO_ROOT / args.save_ids
        out_file.write_text(json.dumps(results, indent=2))
        print(f"Results saved to: {out_file}")

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
