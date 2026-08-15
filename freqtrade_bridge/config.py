"""Config module for Freqtrade-Lean bridge with environment-based defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ExecutionConfig(BaseModel):
    """Execution settings for the bridge."""
    mode: str = Field(default="dry_run", description="Execution mode: dry_run, paper, or live")
    exchange: str = Field(default="binance", description="Exchange to use")
    dry_run_wallet: float = Field(default=1000.0, description="Virtual wallet size for dry-run")
    max_slippage_bps: float = Field(default=10.0, description="Maximum slippage in basis points")
    order_timeout_secs: int = Field(default=30, description="Order timeout in seconds")


class FreqtradeConfig(BaseModel):
    """Freqtrade connection settings."""
    host: str = Field(default="127.0.0.1", description="Freqtrade API host")
    port: int = Field(default=8080, description="Freqtrade API port")
    username: Optional[str] = Field(default=None, description="Freqtrade API username")
    password: Optional[str] = Field(default=None, description="Freqtrade API password")
    use_ssl: bool = Field(default=False, description="Use HTTPS for Freqtrade API")


class LeanConfig(BaseModel):
    """LEAN signal transport settings."""
    signal_transport: str = Field(default="file", description="Transport: file, websocket, or zmq")
    signal_file: str = Field(default="/tmp/niblit_lean_signal.json", description="Signal file path")
    signal_host: str = Field(default="127.0.0.1", description="WebSocket/ZMQ host")
    signal_port: int = Field(default=9000, description="WebSocket/ZMQ port")
    max_signal_age_secs: int = Field(default=300, description="Max signal staleness in seconds")


class RiskConfig(BaseModel):
    """Risk management settings."""
    max_drawdown: float = Field(default=0.10, description="Maximum portfolio drawdown (0-1)")
    max_daily_loss: float = Field(default=0.03, description="Maximum daily loss (0-1)")
    max_position_size: float = Field(default=0.10, description="Maximum position size (0-1)")
    max_leverage: float = Field(default=3.0, description="Maximum leverage multiplier")
    use_kelly_sizing: bool = Field(default=False, description="Enable Kelly criterion sizing")
    use_volatility_sizing: bool = Field(default=True, description="Enable volatility-adjusted sizing")


class AIConfig(BaseModel):
    """AI-assisted signal settings."""
    confidence_threshold: float = Field(default=0.70, description="Minimum confidence to execute")
    use_ai_stance: bool = Field(default=True, description="Consider AI stance in decisions")
    cooldown_secs: int = Field(default=60, description="Cooldown between similar trades")


class PrometheusConfig(BaseModel):
    """Prometheus metrics settings."""
    enabled: bool = Field(default=True, description="Enable Prometheus metrics")
    port: int = Field(default=9090, description="Metrics server port")
    endpoint: str = Field(default="/metrics", description="Metrics endpoint path")


class BridgeConfig(BaseModel):
    """Main configuration for the Freqtrade-Lean bridge."""
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    freqtrade: FreqtradeConfig = Field(default_factory=FreqtradeConfig)
    lean: LeanConfig = Field(default_factory=LeanConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    prometheus: PrometheusConfig = Field(default_factory=PrometheusConfig)

    @classmethod
    def from_yaml(cls, path: str) -> "BridgeConfig":
        """Load configuration from YAML file."""
        try:
            import yaml
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
            return cls(**data) if data else cls()
        except Exception:
            return cls()

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        """Load configuration from environment variables."""
        return cls(
            execution=ExecutionConfig(
                mode=os.getenv("NIBLIT_EXECUTION_MODE", "dry_run"),
                exchange=os.getenv("NIBLIT_EXCHANGE", "binance"),
                dry_run_wallet=float(os.getenv("NIBLIT_DRY_RUN_WALLET", "1000.0")),
                max_slippage_bps=float(os.getenv("NIBLIT_MAX_SLIPPAGE_BPS", "10.0")),
            ),
            freqtrade=FreqtradeConfig(
                host=os.getenv("FREQTRADE_HOST", "127.0.0.1"),
                port=int(os.getenv("FREQTRADE_PORT", "8080")),
            ),
            lean=LeanConfig(
                signal_transport=os.getenv("NIBLIT_SIGNAL_TRANSPORT", "file"),
                signal_file=os.getenv("NIBLIT_SIGNAL_FILE", "/tmp/niblit_lean_signal.json"),
            ),
            risk=RiskConfig(
                max_drawdown=float(os.getenv("NIBLIT_MAX_DRAWDOWN", "0.10")),
                max_daily_loss=float(os.getenv("NIBLIT_MAX_DAILY_LOSS", "0.03")),
                max_position_size=float(os.getenv("NIBLIT_MAX_POSITION_SIZE", "0.10")),
            ),
            ai=AIConfig(
                confidence_threshold=float(os.getenv("NIBLIT_CONFIDENCE_THRESHOLD", "0.70")),
            ),
        )

    def to_yaml(self, path: str) -> None:
        """Save configuration to YAML file."""
        import yaml
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(self.model_dump(), f, default_flow_style=False)