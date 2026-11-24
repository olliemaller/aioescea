"""Fixtures: a transport and a fake fire talking over loopback."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import socket

import pytest

from aioescea import EsceaConnectionError, EsceaFireplace, EsceaTransport

from .fake_fire import FakeFire

FIRE_HOST = "127.0.0.1"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class LoopbackTransport(EsceaTransport):
    """A transport that sends to the fake fire's own port.

    Real fires use port 3300 at both ends. Here the fire and the transport
    share 127.0.0.1 (the only loopback address macOS routes), so they need
    different ports.
    """

    def __init__(self, *, fire_port: int, port: int, bind_address: str) -> None:
        super().__init__(port=port, bind_address=bind_address)
        self.fire_port = fire_port

    def _send(self, frame: bytes, host: str) -> None:
        if self._transport is None or self._transport.is_closing():
            raise EsceaConnectionError("UDP socket is not open")
        self._transport.sendto(frame, (host, self.fire_port))


@pytest.fixture
def port() -> int:
    """A free UDP port standing in for 3300 on this host."""
    return _free_port()


@pytest.fixture
def fire_port() -> int:
    """A free UDP port standing in for 3300 on the fire."""
    return _free_port()


@pytest.fixture
async def transport(port: int, fire_port: int) -> AsyncIterator[EsceaTransport]:
    """A transport bound to 127.0.0.1:<port>, sending to <fire_port>."""
    async with LoopbackTransport(
        port=port, bind_address="127.0.0.1", fire_port=fire_port
    ) as transport:
        yield transport


@pytest.fixture
async def fake_fire(port: int, fire_port: int) -> AsyncIterator[FakeFire]:
    """A fake fire on 127.0.0.1:<fire_port> that replies to <port>."""
    loop = asyncio.get_running_loop()
    fire = FakeFire(reply_port=port)
    transport, _ = await loop.create_datagram_endpoint(
        lambda: fire, local_addr=(FIRE_HOST, fire_port)
    )
    yield fire
    transport.close()


@pytest.fixture
def fireplace(transport: EsceaTransport, fake_fire: FakeFire) -> EsceaFireplace:
    """An EsceaFireplace pointed at the fake fire."""
    return EsceaFireplace(FIRE_HOST, transport)
