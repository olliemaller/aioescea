"""Async client for Escea gas fireplaces on the local network."""

from __future__ import annotations

from .discovery import MAC_PREFIX, DiscoveredFireplace, async_discover, async_find
from .exceptions import (
    EsceaAuthError,
    EsceaBusyError,
    EsceaConnectionError,
    EsceaError,
    EsceaPortInUseError,
    EsceaProtocolError,
    EsceaStateError,
)
from .fireplace import (
    POWER_CHANGE_BUSY_SECONDS,
    EsceaFireplace,
    FireMode,
    FireplaceState,
    mode_from_status,
)
from .protocol import MAX_TEMPERATURE, MIN_TEMPERATURE, Status
from .transport import UDP_PORT, EsceaTransport
from .web import Diagnostics, EsceaWebClient, HomePage, Timer

__version__ = "0.1.0"

__all__ = [
    "MAC_PREFIX",
    "MAX_TEMPERATURE",
    "MIN_TEMPERATURE",
    "POWER_CHANGE_BUSY_SECONDS",
    "UDP_PORT",
    "Diagnostics",
    "DiscoveredFireplace",
    "EsceaAuthError",
    "EsceaBusyError",
    "EsceaConnectionError",
    "EsceaError",
    "EsceaFireplace",
    "EsceaPortInUseError",
    "EsceaProtocolError",
    "EsceaStateError",
    "EsceaTransport",
    "EsceaWebClient",
    "FireMode",
    "FireplaceState",
    "HomePage",
    "Status",
    "Timer",
    "__version__",
    "async_discover",
    "async_find",
    "mode_from_status",
]
