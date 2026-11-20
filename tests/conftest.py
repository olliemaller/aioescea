"""Fixtures: a transport and a fake fire talking over loopback."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import socket

import pytest

from aioescea import EsceaFireplace, EsceaTransport

from .fake_fire import FakeFire

FIRE_HOST = "127.0.0.2"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def port() -> int:
    """A free UDP port standing in for 3300."""
    return _free_port()


@pytest.fixture
async def transport(port: int) -> AsyncIterator[EsceaTransport]:
    """A transport bound to 127.0.0.1:<port>."""
    async with EsceaTransport(port=port, bind_address="127.0.0.1") as transport:
        yield transport


@pytest.fixture
async def fake_fire(port: int) -> AsyncIterator[FakeFire]:
    """A fake fire on 127.0.0.2:<port> that replies to 127.0.0.1:<port>."""
    loop = asyncio.get_running_loop()
    fire = FakeFire(reply_port=port)
    transport, _ = await loop.create_datagram_endpoint(
        lambda: fire, local_addr=(FIRE_HOST, port)
    )
    yield fire
    transport.close()


@pytest.fixture
def fireplace(transport: EsceaTransport, fake_fire: FakeFire) -> EsceaFireplace:
    """An EsceaFireplace pointed at the fake fire."""
    return EsceaFireplace(FIRE_HOST, transport)
