"""Compatibility wrapper for the trading REST API.

``niblit_lean_algos.api.trading_api`` re-exports the public surface of the
authoritative repository-root ``api.trading_api`` module. No logic is
duplicated here — the root module remains the single source of truth.
"""

from __future__ import annotations

from api.trading_api import (  # noqa: F401
    DecisionRequest,
    create_app,
)

__all__ = ["create_app", "DecisionRequest"]