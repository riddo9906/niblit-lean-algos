"""Balance domain model."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
@dataclass
class Balance:
    currency: str
    free: float
    used: float
    total: float
    usd_value: Optional[float] = None