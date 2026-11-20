"""Tests for EsceaFireplace and the transport against a fake fire."""

from __future__ import annotations

import pytest

from aioescea import (
    EsceaBusyError,
    EsceaConnectionError,
    EsceaFireplace,
    EsceaPortInUseError,
    EsceaStateError,
    EsceaTransport,
    FireMode,
    async_discover,
    async_find,
)
from aioescea.protocol import Command
import aioescea.transport as transport_module

from .conftest import FIRE_HOST
from .fake_fire import FakeFire


@pytest.fixture(autouse=True)
def fast_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep retry tests quick."""
    monkeypatch.setattr(transport_module, "DEFAULT_TIMEOUT", 0.2)


async def test_status(fireplace: EsceaFireplace, fake_fire: FakeFire) -> None:
    """A status read decodes the fake fire's state."""
    fake_fire.state.room = 19
    status = await fireplace.async_get_status()
    assert status.is_on is False
    assert status.target_temperature == 21
    assert status.room_temperature == 19
    assert fireplace.last_status == status


async def test_turn_on_then_busy(
    fireplace: EsceaFireplace, fake_fire: FakeFire
) -> None:
    """Lighting starts the 66 s busy window; other commands are refused."""
    await fireplace.async_turn_on()
    assert fake_fire.state.is_on
    assert fireplace.busy_remaining > 60
    with pytest.raises(EsceaBusyError):
        await fireplace.async_set_temperature(22)
    with pytest.raises(EsceaBusyError):
        await fireplace.async_turn_on()


async def test_turn_off_never_refused(
    fireplace: EsceaFireplace, fake_fire: FakeFire
) -> None:
    """Turning off works even inside the busy window."""
    await fireplace.async_turn_on()
    await fireplace.async_turn_off()
    assert fake_fire.state.is_on is False


async def test_turn_on_when_already_on(
    fireplace: EsceaFireplace, fake_fire: FakeFire
) -> None:
    """No POWER_ON (and no busy window) if the fire is already burning."""
    fake_fire.state.is_on = True
    await fireplace.async_turn_on()
    assert Command.POWER_ON not in fake_fire.received
    assert fireplace.busy_remaining == 0


async def test_set_temperature(fireplace: EsceaFireplace, fake_fire: FakeFire) -> None:
    """Target temperature is rounded and range checked."""
    await fireplace.async_set_temperature(23.4)
    assert fake_fire.state.target == 23
    with pytest.raises(EsceaStateError):
        await fireplace.async_set_temperature(31)


@pytest.mark.parametrize(
    ("start", "mode", "boost", "flame"),
    [
        ((False, False), FireMode.BOOST, True, False),
        ((False, False), FireMode.FLAME_EFFECT, False, True),
        ((True, False), FireMode.FLAME_EFFECT, False, True),
        ((False, True), FireMode.BOOST, True, False),
        ((True, False), FireMode.NORMAL, False, False),
    ],
)
async def test_set_mode(
    fireplace: EsceaFireplace,
    fake_fire: FakeFire,
    start: tuple[bool, bool],
    mode: FireMode,
    boost: bool,
    flame: bool,
) -> None:
    """Modes switch the right flags and never leave both on."""
    fake_fire.state.is_on = True
    fake_fire.state.fan_boost, fake_fire.state.flame_effect = start
    await fireplace.async_set_mode(mode)
    assert (fake_fire.state.fan_boost, fake_fire.state.flame_effect) == (boost, flame)


async def test_set_mode_refuses_to_light(
    fireplace: EsceaFireplace, fake_fire: FakeFire
) -> None:
    """Boost while off would light the fire, so it is refused by default."""
    with pytest.raises(EsceaStateError):
        await fireplace.async_set_mode(FireMode.BOOST)
    assert fake_fire.state.is_on is False
    assert Command.FAN_BOOST_ON not in fake_fire.received
    await fireplace.async_set_mode(FireMode.BOOST, allow_ignite=True)
    assert fake_fire.state.is_on is True
    assert fireplace.busy_remaining > 60


async def test_normal_mode_while_off_is_safe(
    fireplace: EsceaFireplace, fake_fire: FakeFire
) -> None:
    """Choosing normal while off sends nothing that could light the fire."""
    await fireplace.async_set_mode(FireMode.NORMAL)
    assert fake_fire.state.is_on is False


async def test_retry_after_dropped_packet(
    fireplace: EsceaFireplace, fake_fire: FakeFire
) -> None:
    """One lost reply is retried."""
    fake_fire.drop = 1
    await fireplace.async_get_status()
    assert fake_fire.received.count(Command.STATUS_PLEASE) == 2


async def test_unreachable(fireplace: EsceaFireplace, fake_fire: FakeFire) -> None:
    """After every retry fails, EsceaConnectionError."""
    fake_fire.drop = 10
    with pytest.raises(EsceaConnectionError):
        await fireplace.async_get_status()
    assert fake_fire.received.count(Command.STATUS_PLEASE) == 3


async def test_missed_status_never_replays_commands(
    fireplace: EsceaFireplace, fake_fire: FakeFire
) -> None:
    """The pescea #8 scenario: nothing is re-sent after a missed status.

    The fire is on, someone turns it off with the Escea app, and a status
    poll is lost. The next poll must only read, never send POWER_ON.
    """
    fake_fire.state.is_on = True
    await fireplace.async_get_status()
    fake_fire.state.is_on = False  # turned off with the app
    fake_fire.drop = 10
    with pytest.raises(EsceaConnectionError):
        await fireplace.async_get_status()
    fake_fire.drop = 0
    fake_fire.received.clear()
    status = await fireplace.async_get_status()
    assert status.is_on is False
    assert fake_fire.received == [Command.STATUS_PLEASE]


async def test_discover(transport: EsceaTransport, fake_fire: FakeFire) -> None:
    """Discovery returns serial and PIN."""
    fires = await async_discover(transport, [FIRE_HOST], timeout=0.3)
    assert len(fires) == 1
    assert fires[0].host == FIRE_HOST
    assert fires[0].serial == 123456
    assert fires[0].pin == 1234
    assert await async_find(transport, 123456, [FIRE_HOST], timeout=0.3)
    assert await async_find(transport, 1, [FIRE_HOST], timeout=0.3) is None


async def test_port_in_use(port: int, transport: EsceaTransport) -> None:
    """A second transport on the same port fails loudly."""
    second = EsceaTransport(port=port, bind_address="127.0.0.1")
    with pytest.raises(EsceaPortInUseError):
        await second.open()


async def test_closed_transport_reopens(
    transport: EsceaTransport, fireplace: EsceaFireplace
) -> None:
    """A request on a closed transport opens it again."""
    await transport.close()
    assert not transport.is_open
    await fireplace.async_get_status()
    assert transport.is_open
