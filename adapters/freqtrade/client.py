"""Async REST client for the Freqtrade API."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import httpx

from .config import FreqtradeAdapterConfig
from .errors import (
    AdapterUnavailableError,
    AmbiguousExecutionError,
    AuthenticationError,
    FreqtradeAdapterError,
    InvalidProviderResponseError,
)


@dataclass
class ForceEntryRequest:
    """Typed request payload for the Freqtrade 2026.6 ``/api/v1/forceenter``.

    Only fields the provider actually accepts are carried. Unsupported /
    uncertain fields are excluded rather than fabricated. Freqtrade force-entry
    accepts (per the 2026.6 REST contract):

      pair          — exchange pair, e.g. BTC/USDT:USDT
      side          — long | short
      price         — optional limit price
      ordertype     — optional order type (limit | market)
      stakeamount   — optional stake amount
      leverage      — optional leverage (spot=1)
      entry_tag     — optional provenance tag

    ``trade_id`` resolves *after* the response; it is not a requested payload
    field for forceenter (Freqtrade assigns the trade id).
    """

    pair: str
    side: str = "long"
    price: Optional[float] = None
    ordertype: Optional[str] = None
    stakeamount: Optional[float] = None
    leverage: Optional[float] = None
    entry_tag: Optional[str] = None

    def as_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"pair": self.pair, "side": self.side}
        if self.price is not None:
            payload["price"] = self.price
        if self.ordertype is not None:
            payload["ordertype"] = self.ordertype
        if self.stakeamount is not None:
            payload["stakeamount"] = self.stakeamount
        if self.leverage is not None:
            payload["leverage"] = self.leverage
        if self.entry_tag is not None:
            payload["entry_tag"] = self.entry_tag
        return payload


def build_force_entry_request(
    *,
    pair: str,
    side: str,
    stake_amount: Optional[float] = None,
    order_type: Optional[str] = None,
    limit_price: Optional[float] = None,
    leverage: Optional[float] = None,
    entry_tag: Optional[str] = None,
) -> ForceEntryRequest:
    """Build a typed :class:`ForceEntryRequest`.

    ``market`` is the Freqtrade-safe default when an ``order_type`` of "market"
    is requested (map "market" -> "market", "limit" -> "limit", otherwise omit
    ``ordertype`` and let Freqtrade use its configured default).
    """
    ordertype: Optional[str] = None
    if order_type is not None:
        ot = str(order_type).strip().lower()
        if ot in ("market", "limit"):
            ordertype = ot
        else:
            ordertype = None
    return ForceEntryRequest(
        pair=pair,
        side=side,
        price=limit_price,
        ordertype=ordertype,
        stakeamount=stake_amount,
        leverage=leverage,
        entry_tag=entry_tag,
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

    async def _request_execution(self, method: str, path: str, **kwargs: Any) -> Any:
        """Provider execution request that NEVER blindly retries on timeout.

        For execution endpoints (forceenter / forceexit / cancel), a
        connection or read timeout leaves provider acceptance UNKNOWN. We must
        not automatically replay a trade submission. On such a timeout we raise
        :class:`AmbiguousExecutionError` so the caller marks the decision
        ``ambiguous`` and triggers reconciliation instead of retrying.
        """
        url = f"{self.api_base}{path}"
        headers = self._headers()
        try:
            response = await self._client.request(method, url, headers=headers, **kwargs)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            raise AmbiguousExecutionError(
                f"Provider execution outcome unknown for {path} (timeout): {exc}"
            ) from exc
        except httpx.RequestError as exc:
            raise FreqtradeAdapterError(
                f"Provider execution request failed for {path}: {exc}"
            ) from exc

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

    async def forceenter_ext(
        self, request: ForceEntryRequest
    ) -> Dict[str, Any]:
        """Submit a typed force-entry request (no auto-retry on timeout)."""
        return await self._request_execution(
            "POST", "/forceenter", json=request.as_payload()
        )

    async def forceenter(self, pair: str, side: str = "long") -> Dict[str, Any]:
        payload = {"pair": pair, "side": side}
        return await self._request_execution("POST", "/forceenter", json=payload)

    async def forceexit(self, trade_id: int) -> Dict[str, Any]:
        return await self._request_execution(
            "POST", "/forceexit", json={"tradeid": trade_id}
        )

    async def delete_trade(self, trade_id: int) -> Dict[str, Any]:
        return await self._request_execution("DELETE", f"/trades/{trade_id}")

    async def cancel_open_order(self, trade_id: int) -> Dict[str, Any]:
        return await self._request_execution(
            "DELETE", f"/trades/{trade_id}/open-order"
        )

    # ── Remote execution-readiness gates (fail closed on uncertainty) ───────

    async def remote_dry_run_status(self) -> Dict[str, Any]:
        """Return verified remote Freqtrade dry-run configuration.

        Returns ``{"runmode":..., "dry_run":...}`` derived from the live
        ``/show_config`` response. Never trusts an env var — the provider is
        the source of truth.
        """
        show = await self.show_config()
        if not isinstance(show, dict):
            raise InvalidProviderResponseError("show_config did not return an object")
        runmode = str(show.get("runmode", "")).strip().lower()
        dry_run_raw = show.get("dry_run")
        dry_run = dry_run_raw if isinstance(dry_run_raw, bool) else None
        if dry_run is None and runmode:
            dry_run = runmode == "dry_run"
        return {
            "runmode": runmode,
            "dry_run": bool(dry_run),
            "dry_run_determined": dry_run is not None,
        }

    async def remote_force_entry_enabled(self) -> bool:
        """Return provider force_entry_enable, raising if unknown/absent."""
        show = await self.show_config()
        if not isinstance(show, dict):
            raise InvalidProviderResponseError("show_config did not return an object")
        fe = show.get("force_entry_enable")
        if not isinstance(fe, bool):
            raise InvalidProviderResponseError(
                "force_entry_enable not present in show_config"
            )
        return fe

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
