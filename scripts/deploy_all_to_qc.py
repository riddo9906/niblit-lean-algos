#!/usr/bin/env python3
"""
scripts/deploy_all_to_qc.py — Deploy all Niblit LEAN algorithms to QuantConnect Cloud.

Uses the shared QCClient (scripts/qc_client.py) to create projects on
QuantConnect, upload algorithm code, and optionally run an initial backtest
to validate each algorithm.

Usage
-----
    # From the niblit-lean-algos directory:
    python scripts/deploy_all_to_qc.py

    # With options:
    python scripts/deploy_all_to_qc.py --dry-run          # Print only, don't deploy
    python scripts/deploy_all_to_qc.py --backtest         # Also launch backtests
    python scripts/deploy_all_to_qc.py --algo 01          # Deploy specific algo by prefix

Environment variables required:
    QC_USER_ID   — QuantConnect numeric user ID
    QC_API_CRED  — QuantConnect API token

Both can also be set in a .env file in the repo root, or in niblit_params.json
in the Niblit root directory.
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

_SCRIPT_DIR  = Path(__file__).resolve().parent
_REPO_ROOT   = _SCRIPT_DIR.parent
_ALGOS_DIR   = _REPO_ROOT / "algorithms"
_NIBLIT_ROOT = _REPO_ROOT.parent  # sibling: .../Niblit/


# ─────────────────────────────────────────────────────────────────────────────
# Main deployment logic
# ─────────────────────────────────────────────────────────────────────────────

def discover_algorithms(prefix_filter: Optional[str] = None) -> List[tuple[str, Path]]:
    """Return [(algo_name, main_py_path), ...] sorted by prefix number."""
    algos = []
    if not _ALGOS_DIR.exists():
        print(f"❌ Algorithms directory not found: {_ALGOS_DIR}")
        return algos
    for d in sorted(_ALGOS_DIR.iterdir()):
        if not d.is_dir():
            continue
        if prefix_filter and not d.name.startswith(prefix_filter):
            continue
        main_py = d / "main.py"
        if main_py.exists():
            algos.append((d.name, main_py))
    return algos


def deploy_algorithm(  # pylint: disable=too-many-positional-arguments
    client: QCClient,
    algo_name: str,
    main_py: Path,
    dry_run: bool,
    run_backtest: bool,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Deploy a single algorithm. Returns a result dict."""
    qc_name = f"niblit-{algo_name}"
    content = main_py.read_text(encoding="utf-8")

    print(f"\n🚀 {algo_name}")
    print(f"   QC Project name: {qc_name}")
    print(f"   File size: {len(content)} bytes")

    if dry_run:
        print("   [DRY RUN — no changes made]")
        return {"algo": algo_name, "status": "dry_run"}

    # 1. Create project
    print("   Creating project…", end=" ", flush=True)
    project_data = client.create_project(qc_name)
    project_id = project_data.get("projectId")
    if not project_id:
        err = project_data.get("error", project_data.get("errors", project_data))
        print(f"\n   ❌ Create project failed: {err}")
        if verbose:
            print(f"      Raw response: {project_data}")
        return {"algo": algo_name, "status": "failed", "step": "create", "error": str(err)}
    print(f"✅ projectId={project_id}")

    # 2. Upload main.py
    print("   Uploading main.py…", end=" ", flush=True)
    upload_result = client.create_file(project_id, "main.py", content)
    if upload_result.get("success") is False or "error" in upload_result:
        err = upload_result.get("error", upload_result)
        print(f"\n   ❌ Upload failed: {err}")
        if verbose:
            print(f"      Raw response: {upload_result}")
        return {"algo": algo_name, "status": "failed", "step": "upload", "error": str(err)}
    print("✅")

    # 3. Compile
    print("   Compiling…", end=" ", flush=True)
    compile_data = client.compile(project_id)
    compile_id = compile_data.get("compileId")
    if not compile_id:
        err = compile_data.get("error", compile_data.get("errors", compile_data))
        print(f"\n   ❌ Compile failed: {err}")
        if verbose:
            print(f"      Raw response: {compile_data}")
        return {"algo": algo_name, "status": "failed", "step": "compile", "error": str(err)}
    print(f"✅ compileId={compile_id}")

    result: Dict[str, Any] = {
        "algo": algo_name,
        "status": "deployed",
        "project_id": project_id,
        "compile_id": compile_id,
        "qc_name": qc_name,
    }

    # 4. Optional backtest
    if run_backtest:
        print("   Launching backtest…", end=" ", flush=True)
        bt_data = client.create_backtest(project_id, compile_id, "niblit-initial")
        bt_id = bt_data.get("backtestId")
        if bt_id:
            print(f"✅ backtestId={bt_id}")
            result["backtest_id"] = bt_id
        else:
            print(f"⚠  {bt_data}")
        # Small delay between backtests to avoid rate limits
        time.sleep(2)

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Deploy Niblit LEAN algorithms to QuantConnect Cloud")
    parser.add_argument("--dry-run",   action="store_true", help="Print plan without deploying")
    parser.add_argument("--backtest",  action="store_true", help="Launch initial backtest after deploy")
    parser.add_argument("--algo",      default=None, help="Deploy only algorithms starting with this prefix")
    parser.add_argument("--save-ids",  default="deployed_projects.json", help="Save project IDs to this file")
    parser.add_argument("--verbose",   action="store_true", help="Print raw API responses on failure")
    args = parser.parse_args()

    try:
        client = QCClient()
    except ValueError as exc:
        print(f"❌ {exc}")
        sys.exit(1)
    print(f"✅ Credentials loaded (user_id={client.user_id_prefix}…)")

    algos = discover_algorithms(args.algo)
    if not algos:
        print(f"❌ No algorithms found in {_ALGOS_DIR}")
        sys.exit(1)

    print(f"\nFound {len(algos)} algorithm(s) to deploy:")
    for name, _ in algos:
        print(f"  • {name}")

    results = []
    for algo_name, main_py in algos:
        r = deploy_algorithm(
            client=client,
            algo_name=algo_name,
            main_py=main_py,
            dry_run=args.dry_run,
            run_backtest=args.backtest,
            verbose=args.verbose,
        )
        results.append(r)
        # Rate-limit: 1 project per 3 seconds
        if not args.dry_run:
            time.sleep(3)

    # Summary
    deployed = [r for r in results if r["status"] == "deployed"]
    failed   = [r for r in results if r["status"] == "failed"]
    print(f"\n{'='*50}")
    print(f"Deployed: {len(deployed)}  Failed: {len(failed)}  Dry-run: {args.dry_run}")

    if failed:
        print("\nFailed algorithms:")
        for r in failed:
            step = r.get("step", "?")
            err  = r.get("error", "unknown error")
            print(f"  ✗ {r['algo']}  (step={step})  {err}")

    if deployed and not args.dry_run:
        out_file = _REPO_ROOT / args.save_ids
        out_file.write_text(json.dumps(results, indent=2))
        print(f"Project IDs saved to: {out_file}")

        # Also write niblit_lean_deployed_projects.json at the Niblit root in
        # the format LeanAlgoManager._load_deployed_ids() expects: {algo_name: project_id}.
        lean_ids: Dict[str, int] = {
            r["algo"]: r["project_id"] for r in deployed if isinstance(r.get("project_id"), int)
        }
        niblit_ids_file = _NIBLIT_ROOT / "niblit_lean_deployed_projects.json"
        try:
            niblit_ids_file.write_text(json.dumps(lean_ids, indent=2))
            print(f"LeanAlgoManager IDs written to: {niblit_ids_file}")
        except OSError as exc:
            print(f"⚠️  Could not write {niblit_ids_file}: {exc}")

        print("\nTo start live practice trading, inside Niblit:")
        for r in deployed[:3]:
            print(f"  lean algo start {r['project_id']}")
        print("  (use the project IDs above)")


if __name__ == "__main__":
    main()
