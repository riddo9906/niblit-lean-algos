"""Freqtrade adapter configuration."""
from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FreqtradeAdapterConfig:
    base_url: str = "http://127.0.0.1:8080"
    username: Optional[str] = None
    password: Optional[str] = None
    api_token: Optional[str] = None
    timeout_seconds: int = 30
    dry_run: bool = True
    verify_ssl: bool = True
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0
    allow_execution: bool = False

    @classmethod
    def from_env(cls) -> FreqtradeAdapterConfig:
        return cls(
            base_url=os.getenv("NIBLIT_FREQTRADE_BASE_URL", "http://127.0.0.1:8080"),
            username=os.getenv("NIBLIT_FREQTRADE_USERNAME"),
            password=os.getenv("NIBLIT_FREQTRADE_PASSWORD"),
            api_token=os.getenv("NIBLIT_FREQTRADE_TOKEN"),
            timeout_seconds=int(os.getenv("NIBLIT_FREQTRADE_TIMEOUT_SECONDS", "30")),
            dry_run=os.getenv("NIBLIT_FREQTRADE_DRY_RUN", "true").lower() == "true",
            verify_ssl=os.getenv("NIBLIT_FREQTRADE_VERIFY_SSL", "true").lower() == "true",
            max_retries=int(os.getenv("NIBLIT_FREQTRADE_MAX_RETRIES", "3")),
            retry_backoff_seconds=float(os.getenv("NIBLIT_FREQTRADE_RETRY_BACKOFF_SECONDS", "1.0")),
            allow_execution=os.getenv("NIBLIT_FREQTRADE_ALLOW_EXECUTION", "false").lower() == "true",
        )

    @property
    def normalized_url(self) -> str:
        url = self.base_url.rstrip("/")
        if url.endswith("/api/v1"):
            url = url[:-7]
        return url