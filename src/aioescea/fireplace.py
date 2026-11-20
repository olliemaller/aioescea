"""High-level control of one Escea fireplace over UDP."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
import math
import time

from .exceptions import EsceaBusyError, EsceaStateError
from .protocol import (
    MAX_TEMPERATURE,
    MIN_TEMPERATURE,
    Command,
    Status,
    decode_status,
    encode_set_temperature,
)
from .transport import EsceaTransport

_LOGGER = logging.getLogger(__name__)

#: After POWER_ON or POWER_OFF the fire ignores commands while it lights or
#: purges. Measured from the Escea remote by the pescea author.
POWER_CHANGE_BUSY_SECONDS = 66.0


class FireMode(StrEnum):
    """How the fire runs while it is on.

    The fire has two independent switches, flame effect and fan boost. Only
    three combinations are useful, so they are offered as one choice.
    """

    #: Normal heating: both switches off.
    NORMAL = "normal"
    #: Fan boost on: maximum heat output.
    BOOST = "boost"
    #: Flame effect on: flames for looks, very little heat.
    FLAME_EFFECT = "flame_effect"


def round_half_up(value: float) -> int:
    """Round to the nearest whole degree, halves up (22.5 -> 23, not 22)."""
    return math.floor(value + 0.5)


def mode_from_status(status: Status) -> FireMode:
    """Return the mode a status describes. Fan boost wins if both are on."""
    if status.fan_boost:
        return FireMode.BOOST
    if status.flame_effect:
        return FireMode.FLAME_EFFECT
    return FireMode.NORMAL


@dataclass(frozen=True, slots=True)
class FireplaceState:
    """A status reading plus what the library knows about the busy window."""

    status: Status
    busy_remaining: float

    @property
    def mode(self) -> FireMode:
        """Return the running mode."""
        return mode_from_status(self.status)


class EsceaFireplace:
    """One fireplace on the LAN.

    Every method sends exactly the commands it needs, when it is called, and
    nothing else. There is no background loop and no buffered state, so a
    missed reply can never cause an old command to be replayed later.
    """

    def __init__(
        self,
        host: str,
        transport: EsceaTransport,
        *,
        serial: int | None = None,
    ) -> None:
        self._host = host
        self._transport = transport
        self.serial = serial
        self._busy_until = 0.0
        self._last_status: Status | None = None

    @property
    def host(self) -> str:
        """Return the fireplace's IP address."""
        return self._host

    @host.setter
    def host(self, host: str) -> None:
        self._host = host

    @property
    def transport(self) -> EsceaTransport:
        """Return the shared UDP transport."""
        return self._transport

    @property
    def last_status(self) -> Status | None:
        """Return the most recent status read, if any."""
        return self._last_status

    @property
    def busy_remaining(self) -> float:
        """Seconds left before the fire accepts commands after a power change."""
        return max(0.0, self._busy_until - time.monotonic())

    async def async_get_status(self) -> Status:
        """Read the fire's live status."""
        frame = await self._transport.request(self._host, Command.STATUS_PLEASE)
        self._last_status = decode_status(frame)
        return self._last_status

    async def async_get_state(self) -> FireplaceState:
        """Read the status and return it with the busy countdown."""
        status = await self.async_get_status()
        return FireplaceState(status=status, busy_remaining=self.busy_remaining)

    def _check_ready(self) -> None:
        remaining = self.busy_remaining
        if remaining > 0:
            raise EsceaBusyError(remaining)

    async def _send(self, command: Command, frame: bytes | None = None) -> None:
        _LOGGER.debug("Sending %s to %s", command.name, self._host)
        await self._transport.request(self._host, command, frame)

    async def async_turn_on(self) -> None:
        """Light the fire. Does nothing if it is already on."""
        self._check_ready()
        status = await self.async_get_status()
        if status.is_on:
            return
        await self._send(Command.POWER_ON)
        self._busy_until = time.monotonic() + POWER_CHANGE_BUSY_SECONDS

    async def async_turn_off(self) -> None:
        """Turn the fire off.

        Never refused, even while the fire is busy lighting: turning a gas
        fire off must always be possible. If the fire is mid-ignition it may
        only act on this once it is ready, so callers should read the status
        again afterwards.
        """
        was_off = self._last_status is not None and not self._last_status.is_on
        await self._send(Command.POWER_OFF)
        # An already-off fire has nothing to shut down, so it isn't busy. Without
        # this, a routine "make sure it's off" would block turning it on for 66 s.
        if not was_off:
            self._busy_until = time.monotonic() + POWER_CHANGE_BUSY_SECONDS

    async def async_set_temperature(self, temperature: float) -> None:
        """Set the target room temperature (whole °C, 4 to 30)."""
        self._check_ready()
        degrees = round_half_up(temperature)
        if not MIN_TEMPERATURE <= degrees <= MAX_TEMPERATURE:
            raise EsceaStateError(
                f"{temperature} °C is outside {MIN_TEMPERATURE}-{MAX_TEMPERATURE} °C"
            )
        await self._send(Command.NEW_SET_TEMP, encode_set_temperature(degrees))

    async def async_set_mode(
        self, mode: FireMode, *, allow_ignite: bool = False
    ) -> None:
        """Set normal, boost or flame effect.

        On Escea fires, turning flame effect or fan boost on while the fire is
        off also lights it. That is refused unless ``allow_ignite`` is True.
        Only the switches that need to change are sent.
        """
        self._check_ready()
        status = await self.async_get_status()
        want_boost = mode is FireMode.BOOST
        want_flame = mode is FireMode.FLAME_EFFECT
        if not status.is_on and (want_boost or want_flame) and not allow_ignite:
            raise EsceaStateError(
                "the fire is off; turning on boost or flame effect would light it"
            )
        # Turn the unwanted switch off first so both are never on at once.
        if status.fan_boost and not want_boost:
            await self._send(Command.FAN_BOOST_OFF)
        if status.flame_effect and not want_flame:
            await self._send(Command.FLAME_EFFECT_OFF)
        if want_boost and not status.fan_boost:
            await self._send(Command.FAN_BOOST_ON)
        if want_flame and not status.flame_effect:
            await self._send(Command.FLAME_EFFECT_ON)
        if not status.is_on and (want_boost or want_flame):
            self._busy_until = time.monotonic() + POWER_CHANGE_BUSY_SECONDS
