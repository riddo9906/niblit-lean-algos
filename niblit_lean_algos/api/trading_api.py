"""Compatibility wrapper for the trading REST API.

``niblit_lean_algos.api.trading_api`` re-exports ``create_app`` from the authoritative
repository-root ``api.trading_api`` module. No logic is duplicated here.
"""

from __future__ import annotations

from api.trading_api import create_app  # noqa: F401

__all__ = ["create_app"]