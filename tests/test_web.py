"""Tests for the web page client against a fake web page."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import time
import hashlib

import aiohttp
from aiohttp.test_utils import TestServer
import pytest

from aioescea import (
    EsceaAuthError,
    EsceaConnectionError,
    EsceaError,
    EsceaStateError,
    EsceaWebClient,
    Timer,
)

from .fake_web import FakeWeb


@pytest.fixture
def fake_web() -> FakeWeb:
    return FakeWeb()


@pytest.fixture
async def host(fake_web: FakeWeb) -> AsyncIterator[str]:
    server = TestServer(fake_web.app(), host="127.0.0.1")
    await server.start_server()
    yield f"127.0.0.1:{server.port}"
    await server.close()


@pytest.fixture
async def web(host: str) -> AsyncIterator[EsceaWebClient]:
    async with aiohttp.ClientSession() as session:
        yield EsceaWebClient(session, host, username="123456", password="1234")


async def test_home_page(web: EsceaWebClient) -> None:
    page = await web.async_get_home_page()
    assert page.clock == time(17, 12)
    assert page.room_temperature == 18
    assert page.target_temperature == 23
    assert page.timers[1] == Timer(1, time(6, 55), time(8, 0), True)
    assert page.timers[2] == Timer(2, time(16, 0), time(22, 0), False)


async def test_diagnostics(web: EsceaWebClient, fake_web: FakeWeb) -> None:
    diag = await web.async_get_diagnostics()
    assert diag.processor_temperature == 41
    assert diag.duct_temperature is None
    assert diag.thermal_cutout_power is True
    assert diag.flue_fan_drive == 99
    assert diag.gas_drive == 23
    assert diag.ignition_power is False
    assert diag.solenoid_current == 305
    assert diag.valve_current == 69
    assert diag.power_b3 is None
    assert fake_web.logins == [
        {
            "user": hashlib.md5(b"123456").hexdigest(),
            "password": hashlib.md5(b"1234").hexdigest(),
        }
    ]
    await web.async_get_diagnostics()
    assert len(fake_web.logins) == 1  # the token is reused


async def test_bad_login(web: EsceaWebClient) -> None:
    web.set_credentials("123456", "0000")
    with pytest.raises(EsceaAuthError):
        await web.async_validate_login()


async def test_expired_token_logs_in_again(
    web: EsceaWebClient, fake_web: FakeWeb
) -> None:
    await web.async_get_diagnostics()
    fake_web.expire_next_token = True
    assert (await web.async_get_diagnostics()).flue_fan_drive == 99
    assert len(fake_web.logins) == 2


async def test_set_timer_form_order(web: EsceaWebClient, fake_web: FakeWeb) -> None:
    fake_web.fire_is_on = True
    current = (await web.async_get_home_page()).timers
    page = await web.async_set_timer(
        Timer(1, time(6, 30), None, None), fire_is_on=True, current=current
    )
    assert page.timers[1] == Timer(1, time(6, 30), time(8, 0), True)
    assert fake_web.saves == [
        [
            ("ON_OFF_SWITCH", "On"),
            ("TIMER1_ON_TIME", "06:30"),
            ("TIMER1_OFF_TIME", "08:00"),
            ("TIMER2_ON_TIME", "16:00"),
            ("TIMER2_OFF_TIME", "22:00"),
            ("TIMER1_SW", "On"),
            ("TIMER2_SW", "Off"),
        ]
    ]
    assert fake_web.fire_is_on is True  # power left as it was


async def test_set_timer_detects_unsaved(
    web: EsceaWebClient, fake_web: FakeWeb
) -> None:
    fake_web.ignore_saves = True
    current = (await web.async_get_home_page()).timers
    with pytest.raises(EsceaError, match="did not save"):
        await web.async_set_timer(
            Timer(2, None, None, True), fire_is_on=False, current=current
        )


async def test_set_timer_needs_login(web: EsceaWebClient) -> None:
    current = (await web.async_get_home_page()).timers
    web.set_credentials(None, None)
    with pytest.raises(EsceaAuthError):
        await web.async_set_timers(current, fire_is_on=False)


async def test_incomplete_timer_refused(web: EsceaWebClient) -> None:
    with pytest.raises(EsceaError, match="not fully known"):
        await web.async_set_timers(
            {1: Timer(1, None, None, None), 2: Timer(2, None, None, None)},
            fire_is_on=False,
        )


async def test_unreachable() -> None:
    async with aiohttp.ClientSession() as session:
        web = EsceaWebClient(session, "127.0.0.1:9")
        with pytest.raises(EsceaError):
            await web.async_get_home_page()


async def test_set_timer_after_token_expired(
    web: EsceaWebClient, fake_web: FakeWeb
) -> None:
    """A save with an expired token logs in again and resends."""
    await web.async_validate_login()
    current = (await web.async_get_home_page()).timers
    fake_web.expire_next_token = True
    page = await web.async_set_timer(
        Timer(2, None, None, True), fire_is_on=False, current=current
    )
    assert page.timers[2].enabled is True
    assert len(fake_web.saves) == 2
    assert len(fake_web.logins) == 2


async def test_set_timer_fire_turned_off_during_login(
    web: EsceaWebClient, fake_web: FakeWeb, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The power is read after the login, so a stale "on" is never sent."""
    fake_web.fire_is_on = True
    login = web._login

    async def slow_login() -> str:
        token = await login()
        fake_web.fire_is_on = False  # turned off with the remote meanwhile
        return token

    async def read_power() -> bool:
        return fake_web.fire_is_on

    monkeypatch.setattr(web, "_login", slow_login)
    with pytest.raises(EsceaStateError, match="turned off"):
        await web.async_set_timer(
            Timer(1, time(6, 30), None, None), fire_is_on=True, read_power=read_power
        )
    assert fake_web.saves == []
    assert fake_web.fire_is_on is False


async def test_set_timer_resend_reads_power_again(
    web: EsceaWebClient, fake_web: FakeWeb
) -> None:
    """The resend after an expired token checks the power again."""
    await web.async_validate_login()
    fake_web.expire_next_token = True
    reads = iter([False, True])  # turned on between the two POSTs

    async def read_power() -> bool:
        return next(reads)

    with pytest.raises(EsceaStateError, match="turned on"):
        await web.async_set_timer(
            Timer(2, None, None, True), fire_is_on=False, read_power=read_power
        )
    assert len(fake_web.saves) == 1  # only the ignored one
    assert fake_web.fire_is_on is False


async def test_set_timer_reads_power_before_each_post(
    web: EsceaWebClient, fake_web: FakeWeb
) -> None:
    await web.async_validate_login()
    fake_web.expire_next_token = True
    reads: list[bool] = []

    async def read_power() -> bool:
        reads.append(fake_web.fire_is_on)
        return fake_web.fire_is_on

    page = await web.async_set_timer(
        Timer(2, None, None, True), fire_is_on=False, read_power=read_power
    )
    assert page.timers[2].enabled is True
    assert reads == [False, False]
    assert len(fake_web.saves) == 2


async def test_set_timer_reads_other_timer_fresh(
    web: EsceaWebClient, fake_web: FakeWeb
) -> None:
    """Without ``current``, the other timer comes from the fire, not a cache."""
    await web.async_get_home_page()
    fake_web.timers[2] = ["17:00", "23:00", True]  # changed in the app
    page = await web.async_set_timer(
        Timer(1, time(6, 30), None, None), fire_is_on=False
    )
    assert page.timers[1] == Timer(1, time(6, 30), time(8, 0), True)
    assert page.timers[2] == Timer(2, time(17, 0), time(23, 0), True)


# The fire's own server: one read per request, and it doesn't always close. ---

LOGIN_REPLY = (
    b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n"
    b'<html><body><input type="hidden" name="token" id="token" value="abc123">'
    b"</body></html>"
)


async def _one_read_server(
    reply: bytes, *, close: bool, seen: list[bytes]
) -> tuple[asyncio.Server, str]:
    """A server like the fire's: one recv per request, maybe never closing."""

    async def handle(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        request = await reader.read(65536)
        seen.append(request)
        head, _, body = request.partition(b"\r\n\r\n")
        length = next(
            (
                int(line.split(b":")[1])
                for line in head.lower().split(b"\r\n")
                if line.startswith(b"content-length:")
            ),
            0,
        )
        if len(body) < length:
            await asyncio.sleep(3600)  # waits for a body it never reads
        writer.write(reply)
        await writer.drain()
        if close:
            writer.close()
        else:
            await asyncio.sleep(3600)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"127.0.0.1:{port}"


@pytest.mark.parametrize("close", [True, False])
async def test_login_sent_in_one_packet_and_read_until_complete(close: bool) -> None:
    seen: list[bytes] = []
    server, host = await _one_read_server(LOGIN_REPLY, close=close, seen=seen)
    try:
        async with aiohttp.ClientSession() as session:
            client = EsceaWebClient(session, host, username="123456", password="1234")
            async with asyncio.timeout(5):
                await client.async_validate_login()
        assert client._token == "abc123"
        head, _, body = seen[0].partition(b"\r\n\r\n")
        assert head.startswith(b"POST /doLogin.html HTTP/1.1")
        assert body.startswith(b"user=")
        assert b"&password=" in body
    finally:
        server.close()


async def test_chunked_and_rejected_replies() -> None:
    seen: list[bytes] = []
    chunked = (
        b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"4a\r\n"
        b'<input type="hidden" name="token" id="token" value="tok">'
        + b" "
        * 17
        + b"\r\n"
        b"0\r\n\r\n"
    )
    server, host = await _one_read_server(chunked, close=False, seen=seen)
    try:
        async with aiohttp.ClientSession() as session:
            client = EsceaWebClient(session, host, username="u", password="p")
            async with asyncio.timeout(5):
                await client.async_validate_login()
            assert client._token == "tok"
    finally:
        server.close()

    server, host = await _one_read_server(
        b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n", close=True, seen=seen
    )
    try:
        async with aiohttp.ClientSession() as session:
            client = EsceaWebClient(session, host, username="u", password="p")
            with pytest.raises(EsceaAuthError):
                await client.async_validate_login()
    finally:
        server.close()


async def test_post_timeout_and_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from aioescea import web as web_module

    monkeypatch.setattr(web_module, "LOGIN_TIMEOUT", 0.3)
    seen: list[bytes] = []
    server, host = await _one_read_server(
        b"HTTP/1.1 200 OK\r\n\r\n<html><body>still going", close=False, seen=seen
    )
    try:
        async with aiohttp.ClientSession() as session:
            client = EsceaWebClient(session, host, username="u", password="p")
            with pytest.raises(EsceaConnectionError, match="no complete reply"):
                await client.async_validate_login()
    finally:
        server.close()

    async with aiohttp.ClientSession() as session:
        client = EsceaWebClient(session, "127.0.0.1:1", username="u", password="p")
        with pytest.raises(EsceaConnectionError):
            await client.async_validate_login()


def test_reply_helpers() -> None:
    from aioescea.web import _body, _dechunk, _status

    assert _status(b"garbage") is None
    assert _dechunk(b"zz\r\nhello") == b"zz\r\nhello"
    assert _body(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nhi") == "hi"
