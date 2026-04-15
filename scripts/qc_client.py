#!/usr/bin/env python3
"""
scripts/qc_client.py — Shared QuantConnect REST API v2 client.

Stdlib-only (no third-party packages) so it runs everywhere: local dev,
GitHub Actions, and inside a QuantConnect Cloud algorithm container.

Authentication
--------------
QuantConnect uses HMAC-SHA256 Basic auth with a per-request timestamp:

    timestamp = str(int(time.time()))
    hash_hex  = sha256(f"{timestamp}:{api_token}").hexdigest()
    header    = "Basic " + base64(f"{user_id}:{hash_hex}")

Both the ``Authorization`` header AND a ``Timestamp: <epoch>`` header must
be sent on every request.

Reference: https://www.quantconnect.com/docs/v2/our-platform/api-reference

Usage
-----
    from scripts.qc_client import QCClient

    client = QCClient()          # reads QC_USER_ID / QC_API_CRED from env
    projects = client.list_projects()
    pid = client.create_project("my-algo", language="Py")["projectId"]
    client.create_file(pid, "main.py", source_code)
    compile_id = client.compile(pid)["compileId"]
    bt = client.create_backtest(pid, compile_id, "initial-backtest")
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
import urllib.error
import urllib.request

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

QC_API_BASE = "https://www.quantconnect.com/api/v2"

# How many seconds to wait before giving up on a single HTTP request.
_DEFAULT_TIMEOUT = 30


# ─────────────────────────────────────────────────────────────────────────────
# Credential helpers  (shared across all scripts)
# ─────────────────────────────────────────────────────────────────────────────

def load_credentials(
    niblit_params_file: Optional[Path] = None,
) -> tuple[str, str]:
    """Return ``(user_id, api_token)`` from env vars or niblit_params.json.

    Resolution order (first non-empty value wins):
    1. ``QC_USER_ID`` / ``QC_API_CRED`` environment variables.
    2. ``niblit_params.json`` in the Niblit root directory (sibling of this
       repo when checked out next to the main Niblit project).

    Raises
    ------
    ValueError
        When credentials cannot be found in either source.
    """
    user_id  = os.environ.get("QC_USER_ID", "").strip()
    api_cred = os.environ.get("QC_API_CRED", "").strip()

    if not user_id or not api_cred:
        # Try niblit_params.json (Niblit root is the parent of this repo)
        if niblit_params_file is None:
            niblit_params_file = (
                Path(__file__).resolve().parent.parent.parent / "niblit_params.json"
            )
        if niblit_params_file.exists():
            try:
                params   = json.loads(niblit_params_file.read_text(encoding="utf-8"))
                user_id  = user_id  or str(params.get("QC_USER_ID", "")).strip()
                api_cred = api_cred or str(params.get("QC_API_CRED", "")).strip()
            except (ValueError, OSError):
                pass

    if not user_id or not api_cred:
        raise ValueError(
            "QuantConnect credentials not found.  "
            "Set QC_USER_ID and QC_API_CRED as environment variables, "
            "or add them to niblit_params.json in the Niblit root directory."
        )

    return user_id, api_cred


# ─────────────────────────────────────────────────────────────────────────────
# QCClient
# ─────────────────────────────────────────────────────────────────────────────

class QCClient:
    """Thin wrapper around the QuantConnect REST API v2.

    Parameters
    ----------
    user_id:
        QuantConnect numeric user ID.  If omitted, read from ``QC_USER_ID``
        environment variable.
    api_token:
        QuantConnect API token.  If omitted, read from ``QC_API_CRED``
        environment variable.
    timeout:
        HTTP request timeout in seconds (default: 30).
    """

    def __init__(
        self,
        user_id: Optional[str] = None,
        api_token: Optional[str] = None,
        timeout: int = _DEFAULT_TIMEOUT,
    ) -> None:
        if user_id and api_token:
            self._user_id  = user_id.strip()
            self._api_token = api_token.strip()
        else:
            self._user_id, self._api_token = load_credentials()
        self._timeout = timeout

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _auth_headers(self) -> Dict[str, str]:
        """Build HMAC-SHA256 authentication headers for one request."""
        ts = str(int(time.time()))
        digest = hashlib.sha256(f"{ts}:{self._api_token}".encode()).hexdigest()
        encoded = base64.b64encode(f"{self._user_id}:{digest}".encode()).decode()
        return {
            "Authorization": f"Basic {encoded}",
            "Timestamp": ts,
            "Content-Type": "application/json",
        }

    # ── Low-level request ─────────────────────────────────────────────────────

    def request(
        self,
        method: str,
        endpoint: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send an authenticated request to the QC API.

        Parameters
        ----------
        method:   HTTP verb (``"GET"``, ``"POST"``, ``"DELETE"``).
        endpoint: API path, e.g. ``"projects/create"`` or
                  ``"files/read?projectId=12345"``.
        payload:  Optional JSON body (only for POST / DELETE with body).

        Returns
        -------
        Parsed JSON response dict.  If the HTTP request fails, returns a
        dict with a single ``"error"`` key describing the problem.
        """
        url  = f"{QC_API_BASE}/{endpoint.lstrip('/')}"
        body = json.dumps(payload).encode() if payload is not None else None
        req  = urllib.request.Request(
            url,
            data=body,
            headers=self._auth_headers(),
            method=method.upper(),
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            body_text = ""
            try:
                body_text = exc.read().decode()
            except Exception:  # pylint: disable=broad-except
                pass
            return {"success": False, "error": f"HTTP {exc.code}: {exc.reason}",
                    "details": body_text}
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return {"success": False, "error": str(exc)}

    # ── Project endpoints ─────────────────────────────────────────────────────

    def create_project(self, name: str, language: str = "Py") -> Dict[str, Any]:
        """Create a new project.  Returns the first project dict."""
        data = self.request("POST", "projects/create",
                            {"name": name, "language": language})
        projects = data.get("projects", [])
        return projects[0] if projects else data

    def read_project(self, project_id: int) -> Dict[str, Any]:
        """Read a single project by ID."""
        return self.request("GET", f"projects/read?projectId={project_id}")

    def list_projects(self) -> List[Dict[str, Any]]:
        """Return a list of all projects for this user."""
        data = self.request("GET", "projects/read")
        return data.get("projects", [])

    def delete_project(self, project_id: int) -> Dict[str, Any]:
        """Delete a project."""
        return self.request("DELETE", "projects/delete",
                            {"projectId": project_id})

    # ── File endpoints ────────────────────────────────────────────────────────

    def create_file(
        self, project_id: int, filename: str, content: str
    ) -> Dict[str, Any]:
        """Upload a new file to a project."""
        return self.request("POST", "files/create", {
            "projectId": project_id,
            "name":      filename,
            "content":   content,
        })

    def update_file(
        self, project_id: int, filename: str, content: str
    ) -> Dict[str, Any]:
        """Update (overwrite) an existing file in a project."""
        return self.request("POST", "files/update", {
            "projectId": project_id,
            "name":      filename,
            "content":   content,
        })

    def read_files(self, project_id: int) -> List[Dict[str, Any]]:
        """Return a list of all files in a project."""
        data = self.request("GET", f"files/read?projectId={project_id}")
        return data.get("files", [])

    def delete_file(self, project_id: int, filename: str) -> Dict[str, Any]:
        """Delete a file from a project."""
        return self.request("DELETE", "files/delete", {
            "projectId": project_id,
            "name":      filename,
        })

    def upsert_file(
        self, project_id: int, filename: str, content: str
    ) -> Dict[str, Any]:
        """Create or update a file: tries ``update`` first, falls back to ``create``."""
        result = self.update_file(project_id, filename, content)
        if result.get("success") is False or "error" in result:
            result = self.create_file(project_id, filename, content)
        return result

    # ── Compile endpoints ─────────────────────────────────────────────────────

    def compile(self, project_id: int) -> Dict[str, Any]:
        """Compile a project.  Returns a dict with ``compileId``."""
        return self.request("POST", "compile/create", {"projectId": project_id})

    def read_compile(self, project_id: int, compile_id: str) -> Dict[str, Any]:
        """Read compile status."""
        return self.request(
            "GET",
            f"compile/read?projectId={project_id}&compileId={compile_id}",
        )

    # ── Backtest endpoints ────────────────────────────────────────────────────

    def create_backtest(
        self,
        project_id: int,
        compile_id: str,
        name: str = "niblit-backtest",
    ) -> Dict[str, Any]:
        """Launch a backtest.  Returns a dict with ``backtestId``."""
        return self.request("POST", "backtests/create", {
            "projectId":   project_id,
            "compileId":   compile_id,
            "backtestName": name,
        })

    def read_backtest(
        self, project_id: int, backtest_id: str
    ) -> Dict[str, Any]:
        """Read backtest status / results."""
        return self.request(
            "GET",
            f"backtests/read?projectId={project_id}&backtestId={backtest_id}",
        )

    def list_backtests(self, project_id: int) -> List[Dict[str, Any]]:
        """List all backtests for a project."""
        data = self.request("GET", f"backtests/list?projectId={project_id}")
        return data.get("backtests", [])

    def delete_backtest(
        self, project_id: int, backtest_id: str
    ) -> Dict[str, Any]:
        """Delete a backtest result."""
        return self.request("DELETE", "backtests/delete", {
            "projectId":  project_id,
            "backtestId": backtest_id,
        })

    # ── Live trading endpoints ────────────────────────────────────────────────

    def create_live(
        self,
        project_id: int,
        compile_id: str,
        brokerage: str = "PaperBrokerage",
        *,
        brokerage_settings: Optional[Dict[str, Any]] = None,
        version_id: str = "-1",
        server_type: str = "Server512",
        automatic_redeploy: bool = False,
    ) -> Dict[str, Any]:
        """Deploy a live trading algorithm.

        Parameters
        ----------
        project_id:
            ID of the QC project.
        compile_id:
            Compile ID from a recent successful ``compile()``.
        brokerage:
            Brokerage name string, e.g. ``"PaperBrokerage"``,
            ``"InteractiveBrokersBrokerage"``, ``"TDAmeritradeBrokerage"``.
        brokerage_settings:
            Dict of brokerage-specific settings.  Pass ``None`` or an empty
            dict for paper trading.
        version_id:
            LEAN engine version (``"-1"`` = latest).
        server_type:
            Cloud server tier: ``"Server512"``, ``"Server1024"``,
            ``"Server2048"`` (more RAM).
        automatic_redeploy:
            Automatically redeploy on market open if the algorithm crashes.
        """
        payload: Dict[str, Any] = {
            "projectId":          project_id,
            "compileId":          compile_id,
            "nodeId":             "",
            "brokerageSettings":  brokerage_settings or {"id": brokerage},
            "versionId":          version_id,
            "serverType":         server_type,
            "automaticRedeploy":  automatic_redeploy,
            "language":           "Py",
        }
        return self.request("POST", "live/create", payload)

    def read_live(self, project_id: int) -> Dict[str, Any]:
        """Read the live trading status for a project."""
        return self.request("GET", f"live/read?projectId={project_id}")

    def list_live(
        self, status: str = "Running"
    ) -> List[Dict[str, Any]]:
        """List live trading algorithms, optionally filtered by status.

        Parameters
        ----------
        status:
            ``"Running"``, ``"Stopped"``, ``"RuntimeError"``, ``"Liquidated"``
            or ``""`` for all.
        """
        endpoint = "live/list"
        if status:
            endpoint = f"live/list?status={status}"
        data = self.request("GET", endpoint)
        return data.get("live", [])

    def stop_live(self, project_id: int) -> Dict[str, Any]:
        """Stop (but do NOT liquidate) a running live algorithm."""
        return self.request("POST", "live/update/stop", {"projectId": project_id})

    def liquidate_live(self, project_id: int) -> Dict[str, Any]:
        """Liquidate all positions and stop a running live algorithm."""
        return self.request("POST", "live/update/liquidate",
                            {"projectId": project_id})

    def read_live_log(
        self, project_id: int, algorithm_id: str, start: int = 0
    ) -> Dict[str, Any]:
        """Read live algorithm log entries.

        Parameters
        ----------
        project_id:   QC project ID.
        algorithm_id: Live deploy / algorithm ID (``deployId``).
        start:        Log line offset (0 = from the beginning).
        """
        return self.request(
            "GET",
            f"live/read/log?projectId={project_id}"
            f"&algorithmId={algorithm_id}&start={start}",
        )

    def read_live_portfolio(self, project_id: int) -> Dict[str, Any]:
        """Read live portfolio (cash, holdings, open orders) for a project."""
        return self.request("GET", f"live/read/portfolio?projectId={project_id}")

    def read_live_orders(self, project_id: int, start: int = 0) -> Dict[str, Any]:
        """Read live order history for a project."""
        return self.request(
            "GET",
            f"live/read/orders?projectId={project_id}&start={start}",
        )

    # ── Node endpoints ────────────────────────────────────────────────────────

    def list_nodes(self) -> Dict[str, Any]:
        """List all compute nodes (backtesting + live) for this account."""
        return self.request("GET", "nodes/read")

    # ── Organisation/account ─────────────────────────────────────────────────

    def read_account(self) -> Dict[str, Any]:
        """Read account/organisation details (credit balance, etc.)."""
        return self.request("GET", "account/read")

    # ── Convenience ──────────────────────────────────────────────────────────

    def compile_and_backtest(
        self,
        project_id: int,
        backtest_name: str = "niblit-backtest",
    ) -> Dict[str, Any]:
        """Compile a project then immediately launch a backtest.

        Returns a dict with ``compileId`` and ``backtestId`` on success,
        or an ``error`` key if either step fails.
        """
        compile_result = self.compile(project_id)
        compile_id = compile_result.get("compileId")
        if not compile_id:
            return {"error": f"compile failed: {compile_result}"}

        bt_result = self.create_backtest(project_id, compile_id, backtest_name)
        backtest_id = bt_result.get("backtestId")
        if not backtest_id:
            return {"error": f"backtest launch failed: {bt_result}",
                    "compileId": compile_id}

        return {
            "projectId":  project_id,
            "compileId":  compile_id,
            "backtestId": backtest_id,
        }
