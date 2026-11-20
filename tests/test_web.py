"""Tests for the web page client against a fake web page."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import time
import hashlib

import aiohttp
from aiohttp.test_utils import TestServer
import pytest

from aioescea import EsceaAuthError, EsceaError, EsceaWebClient, Timer

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
