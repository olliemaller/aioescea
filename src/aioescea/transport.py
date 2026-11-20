"""UDP transport shared by every fireplace on a host.

Fireplaces listen on UDP 3300 and always reply to port 3300, whatever port the
request came from. So one socket bound to 3300 receives every reply, and this
module routes each reply to whoever is waiting for it, keyed by the sender's
IP address and the response id.

Rules this transport follows, learned from the hardware:

* One request per fireplace at a time. The controller is single threaded.
* Replies can take up to about 3 seconds (the spec says "in most cases under
  3 seconds") and UDP drops packets, so requests are retried.
* Nothing is ever sent on a timer. A command is only sent because a caller
  asked for it, so a missed reply can never cause an old command to be
  re-sent later.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
import contextlib
import errno
import logging
import socket

from .exceptions import EsceaConnectionError, EsceaPortInUseError, EsceaProtocolError
from .protocol import EXPECTED_RESPONSE, Command, encode, message_id

_LOGGER = logging.getLogger(__name__)

UDP_PORT = 3300
DEFAULT_TIMEOUT = 3.5
DEFAULT_RETRIES = 2
BROADCAST_ADDRESS = "255.255.255.255"


class _Protocol(asyncio.DatagramProtocol):
    def __init__(self, owner: EsceaTransport) -> None:
        self._owner = owner

    def datagram_received(self, data: bytes, addr: tuple[str | object, ...]) -> None:
        self._owner._datagram_received(data, str(addr[0]))

    def error_received(self, exc: Exception) -> None:
        _LOGGER.debug("UDP error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        self._owner._connection_lost()


class EsceaTransport:
    """A UDP socket on port 3300 that routes replies to waiting requests.

    Create one per process and share it between fireplaces::

        async with EsceaTransport() as transport:
            fire = EsceaFireplace("192.168.1.50", transport)
    """

    def __init__(self, *, port: int = UDP_PORT, bind_address: str = "0.0.0.0") -> None:
        self._port = port
        self._bind_address = bind_address
        self._transport: asyncio.DatagramTransport | None = None
        self._waiters: dict[tuple[str, int], asyncio.Future[bytes]] = {}
        self._collectors: list[asyncio.Queue[tuple[str, bytes]]] = []
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._start_lock = asyncio.Lock()
        self._closed = asyncio.Event()

    @property
    def is_open(self) -> bool:
        """Return True while the socket is open."""
        return self._transport is not None and not self._transport.is_closing()

    async def open(self) -> None:
        """Bind the socket. Safe to call more than once."""
        async with self._start_lock:
            if self.is_open:
                return
            loop = asyncio.get_running_loop()
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            try:
                # No SO_REUSEADDR or SO_REUSEPORT on purpose. With either, a
                # second program can share the port and the kernel then hands
                # every reply from a given fire to just one of the sockets, so
                # the other program silently stops hearing the fire. Failing
                # loudly is better.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.setblocking(False)
                sock.bind((self._bind_address, self._port))
            except OSError as err:
                sock.close()
                if err.errno in (errno.EADDRINUSE, errno.EACCES):
                    raise EsceaPortInUseError(
                        f"UDP port {self._port} is already in use on this host"
                    ) from err
                raise EsceaConnectionError(f"cannot open UDP socket: {err}") from err
            self._closed.clear()
            transport, _ = await loop.create_datagram_endpoint(
                lambda: _Protocol(self), sock=sock
            )
            self._transport = transport
            _LOGGER.debug("Listening on UDP %s:%s", self._bind_address, self._port)

    async def close(self) -> None:
        """Close the socket, wait until it is released, fail pending requests."""
        transport = self._transport
        if transport is not None:
            transport.close()
            # The socket is released on a later loop iteration; wait for it so
            # the port can be bound again straight away.
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(1):
                    await self._closed.wait()
        self._connection_lost()

    async def __aenter__(self) -> EsceaTransport:
        await self.open()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    def _connection_lost(self) -> None:
        self._transport = None
        self._closed.set()
        for future in self._waiters.values():
            if not future.done():
                future.set_exception(EsceaConnectionError("UDP socket closed"))
        self._waiters.clear()

    def _datagram_received(self, data: bytes, host: str) -> None:
        try:
            response_id = message_id(data)
        except EsceaProtocolError as err:
            _LOGGER.debug("Ignoring bad datagram from %s: %s", host, err)
            return
        for queue in self._collectors:
            queue.put_nowait((host, data))
        future = self._waiters.get((host, response_id))
        if future is not None and not future.done():
            future.set_result(data)

    def _send(self, frame: bytes, host: str) -> None:
        if not self.is_open:
            raise EsceaConnectionError("UDP socket is not open")
        assert self._transport is not None
        try:
            self._transport.sendto(frame, (host, self._port))
        except OSError as err:
            raise EsceaConnectionError(f"cannot send to {host}: {err}") from err

    async def request(
        self,
        host: str,
        command: Command,
        frame: bytes | None = None,
        *,
        timeout: float | None = None,
        retries: int = DEFAULT_RETRIES,
    ) -> bytes:
        """Send ``command`` to ``host`` and return the matching reply frame.

        ``frame`` overrides the encoded command (for commands that carry
        data). The request is sent up to ``retries + 1`` times.
        """
        await self.open()
        timeout = DEFAULT_TIMEOUT if timeout is None else timeout
        frame = frame if frame is not None else encode(command)
        expected = EXPECTED_RESPONSE[command]
        key = (host, int(expected))
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        loop = asyncio.get_running_loop()
        async with lock:
            for attempt in range(retries + 1):
                future: asyncio.Future[bytes] = loop.create_future()
                self._waiters[key] = future
                try:
                    self._send(frame, host)
                    async with asyncio.timeout(timeout):
                        return await future
                except TimeoutError:
                    _LOGGER.debug(
                        "No %s from %s (attempt %s of %s)",
                        expected.name,
                        host,
                        attempt + 1,
                        retries + 1,
                    )
                finally:
                    if self._waiters.get(key) is future:
                        del self._waiters[key]
        raise EsceaConnectionError(
            f"{host} did not answer {command.name} after {retries + 1} attempts"
        )

    async def broadcast(
        self,
        command: Command,
        addresses: Iterable[str] = (BROADCAST_ADDRESS,),
        *,
        timeout: float = 5.0,
    ) -> list[tuple[str, bytes]]:
        """Broadcast ``command`` and collect every matching reply for ``timeout``."""
        await self.open()
        expected = int(EXPECTED_RESPONSE[command])
        queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue()
        self._collectors.append(queue)
        replies: list[tuple[str, bytes]] = []
        try:
            frame = encode(command)
            for address in dict.fromkeys(addresses):
                try:
                    self._send(frame, address)
                except EsceaConnectionError as err:
                    _LOGGER.debug("Broadcast to %s failed: %s", address, err)
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(timeout):
                    while True:
                        host, data = await queue.get()
                        if data[1] == expected:
                            replies.append((host, data))
        finally:
            self._collectors.remove(queue)
        return replies
