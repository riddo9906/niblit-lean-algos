"""Freqtrade adapter error classes."""
from __future__ import annotations


class FreqtradeAdapterError(Exception):
    """Base error for Freqtrade adapter."""


class AdapterUnavailableError(FreqtradeAdapterError):
    """Raised when the Freqtrade endpoint is unreachable."""


class AuthenticationError(FreqtradeAdapterError):
    """Raised when authentication with Freqtrade fails."""


class UnsupportedOperationError(FreqtradeAdapterError):
    """Raised when the Freqtrade version does not support an endpoint."""


class InvalidProviderResponseError(FreqtradeAdapterError):
    """Raised when Freqtrade returns malformed or unexpected data."""


class ExecutionDisabledError(FreqtradeAdapterError):
    """Raised when execution is attempted while disabled."""


class AmbiguousExecutionError(FreqtradeAdapterError):
    """Raised when execution outcome cannot be confirmed."""