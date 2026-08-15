"""Adapter health domain model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class AdapterHealth:
    """Health status of a trading adapter."""

    healthy: bool
    backend: str
    reachable: bool
    authenticated: bool
    runtime_state: str
    version: Optional[str] = None
    latency_ms: Optional[float] = None
    capabilities: list[str] = field(default_factory=list)
    error: Optional[str] = None
    checked_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
