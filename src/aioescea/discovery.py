"""Find fireplaces on the LAN by broadcast."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import logging

from .exceptions import EsceaProtocolError
from .protocol import Command, decode_identity
from .transport import BROADCAST_ADDRESS, EsceaTransport

_LOGGER = logging.getLogger(__name__)

#: First three bytes of every Escea controller's MAC address. The last three
#: come from the fire's RF remote address. (Escea network summary, 2016.)
MAC_PREFIX = "AC:DE:48"


@dataclass(frozen=True, slots=True)
class DiscoveredFireplace:
    """A fireplace that answered SEARCH_FOR_FIRES."""

    host: str
    serial: int
    pin: int


async def async_discover(
    transport: EsceaTransport,
    broadcast_addresses: Iterable[str] = (BROADCAST_ADDRESS,),
    *,
    timeout: float = 5.0,
) -> list[DiscoveredFireplace]:
    """Broadcast SEARCH_FOR_FIRES and return every fireplace that answers.

    Pass the broadcast address of each network interface (for example
    ``192.168.1.255``) to search more than the default route's network.
    """
    found: dict[int, DiscoveredFireplace] = {}
    for host, frame in await transport.broadcast(
        Command.SEARCH_FOR_FIRES, broadcast_addresses, timeout=timeout
    ):
        try:
            identity = decode_identity(frame)
        except EsceaProtocolError as err:
            _LOGGER.debug("Bad discovery reply from %s: %s", host, err)
            continue
        found[identity.serial] = DiscoveredFireplace(
            host=host, serial=identity.serial, pin=identity.pin
        )
    return list(found.values())


async def async_find(
    transport: EsceaTransport,
    serial: int,
    broadcast_addresses: Iterable[str] = (BROADCAST_ADDRESS,),
    *,
    timeout: float = 5.0,
) -> DiscoveredFireplace | None:
    """Find one fireplace by serial number, for example after its IP changed."""
    for fire in await async_discover(transport, broadcast_addresses, timeout=timeout):
        if fire.serial == serial:
            return fire
    return None
