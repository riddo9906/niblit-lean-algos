#!/usr/bin/env python3
"""
scripts/download_qc_projects.py — Download all QuantConnect Cloud projects
to a local ``quantconnect-projects/`` directory.

Uses the shared QCClient (HMAC-SHA256 auth) from qc_client.py.

Usage
-----
    python scripts/download_qc_projects.py
    python scripts/download_qc_projects.py --output ./my-qc-backup
    python scripts/download_qc_projects.py --project-id 12345678

Environment variables required:
    QC_USER_ID   — QuantConnect numeric user ID
    QC_API_CRED  — QuantConnect API token
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Allow running as `python scripts/download_qc_projects.py` from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from qc_client import QCClient  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def write_project_files(
    base_dir: Path,
    project_name: str,
    files: List[Dict[str, Any]],
) -> int:
    """Write a project's files to ``base_dir/<project_name>/``."""
    project_dir = base_dir / project_name
    project_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for f in files:
        rel_path = f.get("name") or f.get("path") or "unnamed.py"
        content  = f.get("content", "")
        dest = project_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
        print(f"    Wrote {dest}")
        written += 1
    return written


def download_project(
    client: QCClient,
    project_id: int,
    project_name: str,
    output_dir: Path,
) -> bool:
    """Download all files for one project. Returns True on success."""
    print(f"  Fetching files for '{project_name}' (id={project_id})…", end=" ", flush=True)
    files = client.read_files(project_id)
    if not files and isinstance(files, list):
        print("0 files (empty project)")
        return True
    count = write_project_files(output_dir, project_name, files)
    print(f"✅  {count} file(s) written")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download QuantConnect Cloud projects to a local directory"
    )
    parser.add_argument(
        "--output", default="quantconnect-projects",
        help="Local directory to write downloaded projects (default: quantconnect-projects/)"
    )
    parser.add_argument(
        "--project-id", type=int, default=None,
        help="Download only this single project ID (omit to download all)"
    )
    parser.add_argument(
        "--prefix", default=None,
        help="Only download projects whose name starts with this string"
    )
    args = parser.parse_args()

    try:
        client = QCClient()
    except ValueError as exc:
        print(f"❌ {exc}")
        print("   Set QC_USER_ID and QC_API_CRED as environment variables.")
        sys.exit(1)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"✅ Output directory: {output_dir.resolve()}")

    # ── Resolve project list ───────────────────────────────────────────────
    if args.project_id:
        project_data = client.read_project(args.project_id)
        projects: List[Dict[str, Any]] = project_data.get("projects", [])
        if not projects:
            print(f"❌ Could not read project {args.project_id}: {project_data}")
            sys.exit(1)
    else:
        print("Listing all projects…")
        projects = client.list_projects()
        if not projects:
            print("No projects found on QuantConnect Cloud.")
            sys.exit(0)
        if args.prefix:
            projects = [p for p in projects if p.get("name", "").startswith(args.prefix)]
        print(f"Found {len(projects)} project(s) to download.\n")

    # ── Download each project ──────────────────────────────────────────────
    success_count = 0
    for proj in projects:
        project_id: Optional[int] = proj.get("projectId") or proj.get("id")
        project_name: str = proj.get("name") or f"project_{project_id}"

        if project_id is None:
            print(f"⚠  Skipping project with missing ID: {proj}", file=sys.stderr)
            continue

        ok = download_project(client, int(project_id), project_name, output_dir)
        if ok:
            success_count += 1

    print(f"\n{'='*50}")
    print(f"Downloaded {success_count}/{len(projects)} project(s) → {output_dir.resolve()}")


if __name__ == "__main__":
    main()
