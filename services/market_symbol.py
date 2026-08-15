"""Market symbol normalization utilities."""
from __future__ import annotations


class MarketSymbolError(ValueError):
    """Raised when a market symbol is malformed or unsupported."""


def normalize_market_symbol(
    symbol: str,
    *,
    provider: str = "ccxt",
    market_type: str = "swap",
) -> str:
    """Normalize a trading pair for a market-data provider.

    Contract:
      * Futures/swap symbols keep settlement: ``BTC/USDT:USDT``
      * Spot symbols remain ``BASE/QUOTE`` when market_type is spot
      * Malformed values raise :class:`MarketSymbolError`
      * Futures symbols are never silently stripped to spot unless the
        provider and market_type explicitly require that conversion

    Supported provider formats today:
      * ``ccxt`` — preserves CCXT unified symbols including settlement
      * ``mock`` — preserves the input shape after structural validation
    """
    if not symbol or not isinstance(symbol, str):
        raise MarketSymbolError(f"Malformed symbol: {symbol!r}")

    s = symbol.strip().upper()
    if not s:
        raise MarketSymbolError(f"Malformed symbol: {symbol!r}")

    if "/" not in s:
        raise MarketSymbolError(
            f"Malformed symbol: {symbol!r} — expected BASE/QUOTE[:SETTLE]"
        )

    base, quote_part = s.split("/", 1)
    if not base or not quote_part:
        raise MarketSymbolError(f"Malformed symbol: {symbol!r}")

    if ":" in quote_part:
        quote, settle = quote_part.split(":", 1)
        if not quote:
            raise MarketSymbolError(f"Malformed symbol: {symbol!r} — missing quote")
        if not settle:
            raise MarketSymbolError(
                f"Malformed symbol: {symbol!r} — missing settlement currency"
            )
        normalized = f"{base}/{quote}:{settle}"
        if market_type in ("swap", "future", "futures", "linear", "delivery"):
            return normalized
        if market_type == "spot":
            # Spot providers that do not accept settlement keep BASE/QUOTE
            # only when market_type is explicitly spot.
            if provider == "ccxt":
                return f"{base}/{quote}"
            return normalized
        raise MarketSymbolError(
            f"Unsupported market_type={market_type!r} for settled symbol {symbol!r}"
        )

    quote = quote_part
    if not quote:
        raise MarketSymbolError(f"Malformed symbol: {symbol!r}")

    if market_type in ("swap", "future", "futures", "linear") and provider == "ccxt":
        # Allow spot-shaped input for swap only when caller did not provide
        # settlement; do not invent a settlement currency.
        return f"{base}/{quote}"

    return f"{base}/{quote}"


def normalize_freqtrade_symbol_for_provider(symbol: str) -> str:
    """Backward-compatible alias for Freqtrade → CCXT swap symbols."""
    return normalize_market_symbol(symbol, provider="ccxt", market_type="swap")
