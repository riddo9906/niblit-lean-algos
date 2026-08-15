"""Async REST client for the Freqtrade API."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional

import httpx

from .config import FreqtradeAdapterConfig
from .errors import (
    AdapterUnavailableError,
    AuthenticationError,
    FreqtradeAdapterError,
    InvalidProviderResponseError,
)


class FreqtradeClient:
    """Minimal async client for the Freqtrade REST API.

    Supports both username/password (Basic auth) and bearer token auth.
    Retries transient failures. Never logs credentials or auth headers.
    """

    def __init__(
        self,
        config: Optional[FreqtradeAdapterConfig] = None,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ):
        self._config = config or FreqtradeAdapterConfig.from_env()
        self._client = httpx.AsyncClient(
            timeout=self._config.timeout_seconds,
            verify=self._config.verify_ssl,
            transport=transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> FreqtradeClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    @property
    def base_url(self) -> str:
        return self._config.normalized_url

    @property
    def api_base(self) -> str:
        return f"{self.base_url}/api/v1"

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.api_base}{path}"
        headers = self._headers()

        last_error: Optional[Exception] = None
        for attempt in range(self._config.max_retries + 1):
            try:
                response = await self._client.request(method, url, headers=headers, **kwargs)
                if response.status_code in (401, 403):
                    raise AuthenticationError(
                        f"Freqtrade authentication failed at {self.base_url} (status {response.status_code})"
                    )
                if response.status_code == 404:
                    raise FreqtradeAdapterError(f"Freqtrade endpoint not found: {path}")
                if 400 <= response.status_code < 500:
                    raise FreqtradeAdapterError(
                        f"Freqtrade client error {response.status_code} for {path}: {response.text[:200]}"
                    )
                if response.status_code >= 500:
                    raise FreqtradeAdapterError(
                        f"Freqtrade server error {response.status_code} for {path}: {response.text[:200]}"
                    )
                try:
                    return response.json()
                except ValueError as exc:
                    raise InvalidProviderResponseError(
                        f"Freqtrade returned invalid JSON for {path}"
                    ) from exc
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
                last_error = exc
                await asyncio.sleep(self._config.retry_backoff_seconds * (attempt + 1))
            except FreqtradeAdapterError:
                raise

        raise AdapterUnavailableError(
            f"Freqtrade endpoint unreachable at {self.base_url}: {last_error}"
        )

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self._config.api_token:
            headers["Authorization"] = f"Bearer {self._config.api_token}"
        elif self._config.username is not None:
            headers["Authorization"] = self._basic_auth()
        return headers

    def _basic_auth(self) -> str:
        import base64

        token = f"{self._config.username}:{self._config.password or ''}"
        encoded = base64.b64encode(token.encode("utf-8")).decode("ascii")
        return f"Basic {encoded}"

    # ── typed endpoints ──────────────────────────────────────────────────────

    async def ping(self) -> Dict[str, Any]:
        return await self._request("GET", "/ping")

    async def version(self) -> Dict[str, Any]:
        return await self._request("GET", "/version")

    async def show_config(self) -> Dict[str, Any]:
        return await self._request("GET", "/show_config")

    async def status(self) -> Any:
        return await self._request("GET", "/status")

    async def balance(self) -> Any:
        return await self._request("GET", "/balance")

    async def profit(self) -> Dict[str, Any]:
        return await self._request("GET", "/profit")

    async def performance(self) -> Any:
        return await self._request("GET", "/performance")

    async def count(self) -> Dict[str, Any]:
        return await self._request("GET", "/count")

    async def trades(self, limit: Optional[int] = None) -> Any:
        params = {}
        if limit is not None:
            params["limit"] = limit
        return await self._request("GET", "/trades", params=params)

    async def trade(self, trade_id: int) -> Dict[str, Any]:
        return await self._request("GET", f"/trade/{trade_id}")

    async def whitelist(self) -> Dict[str, Any]:
        return await self._request("GET", "/whitelist")

    async def blacklist(self) -> Dict[str, Any]:
        return await self._request("GET", "/blacklist")

    async def locks(self) -> Dict[str, Any]:
        return await self._request("GET", "/locks")

    async def logs(self, limit: int = 100) -> Any:
        return await self._request("GET", "/logs", params={"limit": limit})

    async def start(self) -> Dict[str, Any]:
        return await self._request("POST", "/start")

    async def stop(self) -> Dict[str, Any]:
        return await self._request("POST", "/stop")

    async def reload_config(self) -> Dict[str, Any]:
        return await self._request("POST", "/reload_config")

    async def forceenter(self, pair: str, side: str = "long") -> Dict[str, Any]:
        payload = {"pair": pair, "side": side}
        return await self._request("POST", "/forceenter", json=payload)

    async def forceexit(self, trade_id: int) -> Dict[str, Any]:
        return await self._request("POST", "/forceexit", json={"tradeid": trade_id})

    async def delete_trade(self, trade_id: int) -> Dict[str, Any]:
        return await self._request("DELETE", f"/trades/{trade_id}")

    async def cancel_open_order(self, trade_id: int) -> Dict[str, Any]:
        return await self._request("DELETE", f"/trades/{trade_id}/open-order")

    async def measure_latency(self) -> float:
        """Measure latency by making a real lightweight request to /ping.

        Returns latency in milliseconds (consistent with the ``latency_ms``
        domain field).
        """
        start = time.perf_counter()
        try:
            await self.ping()
        except Exception:
            pass
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return round(elapsed_ms, 2)
