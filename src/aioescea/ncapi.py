"""Escea Network Communication API (NCAPI) for Generation 3 Wi-Fi fires.

Source: "ESCEA Network Communication API Protocol (NCAPI)", Escea document
640051_0, 26 September 2024.

Generation 3 fires (from March 2024) have Wi-Fi instead of Ethernet and no
UDP protocol. They have an HTTP API on port 80 instead:

* ``GET /`` returns the fire's state as JSON.
* ``POST /?on=true&target_temp_deci_c=225`` changes it. Parameters can be
  combined; any invalid one rejects the whole request (HTTP 400).

The API is off by default. Turn on "Escea NCAPI Settings" under Fire Settings
in the Escea Smartheat app (fire firmware 1.5.9 or later). The fire needs a
DHCP reservation, since it can't be found by broadcast.

The fire answers HTTP 408 if it's asked too often, so requests are spaced
out and one is sent at a time.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
import logging
import time
from typing import Any

import aiohttp

from .exceptions import EsceaConnectionError, EsceaError, EsceaProtocolError

_LOGGER = logging.getLogger(__name__)

NCAPI_MIN_TEMPERATURE = 10.0
NCAPI_MAX_TEMPERATURE = 38.0
REQUEST_TIMEOUT = 10.0
#: Minimum gap between requests, to stay clear of HTTP 408.
MIN_REQUEST_GAP = 1.0
#: room_temp_deci_c when the fire hasn't heard from its remote since power up.
NO_ROOM_TEMPERATURE = -1000


class FireState(StrEnum):
    """What a Generation 3 fire is doing."""

    OFF = "off"
    STARTING = "starting"
    RETRYING = "retrying"
    IGNITING = "igniting"
    HEATING = "heating"
    BURN_OFF = "burn_off"
    COOL_DOWN = "cool_down"
    GAS_ADJUST = "gas_adjust"
    PILOT_ONLY = "pilot_only"
    SHUTTING_DOWN = "shutting_down"
    ERROR = "error"
    UNKNOWN = "unknown"


#: States in which the fire has been asked to be on and is running.
RUNNING_STATES = frozenset(
    {
        FireState.STARTING,
        FireState.RETRYING,
        FireState.IGNITING,
        FireState.HEATING,
        FireState.BURN_OFF,
        FireState.GAS_ADJUST,
        FireState.PILOT_ONLY,
    }
)


@dataclass(frozen=True, slots=True)
class NcapiStatus:
    """A Generation 3 fire's state."""

    state: FireState
    fan_boost: bool
    flame_effect: bool
    target_temperature: float
    #: None if the fire hasn't heard from its remote since it powered up.
    room_temperature: float | None
    remote_present: bool
    has_errors: bool
    serial: str
    fire_type: str
    protocol_version: str

    @property
    def is_on(self) -> bool:
        """Return True while the fire is running (starting through heating)."""
        return self.state in RUNNING_STATES

    @property
    def is_gas(self) -> bool:
        """Return True for gas fires (fan boost and flame effect are gas only)."""
        return self.fire_type == "gas"


def parse_status(data: dict[str, Any]) -> NcapiStatus:
    """Parse the JSON the fire returns for ``GET /``."""
    try:
        raw_state = str(data["state"]).lower()
        state = (
            FireState(raw_state)
            if raw_state in FireState._value2member_map_
            else FireState.UNKNOWN
        )
        room = int(data["room_temp_deci_c"])
        return NcapiStatus(
            state=state,
            fan_boost=bool(data.get("fan_boost", False)),
            flame_effect=bool(data.get("flame_effect", False)),
            target_temperature=int(data["target_temp_deci_c"]) / 10,
            room_temperature=None if room <= NO_ROOM_TEMPERATURE else room / 10,
            remote_present=bool(data.get("remote_present", False)),
            has_errors=bool(data.get("errors", False)),
            serial=str(data.get("serial_number", "")),
            fire_type=str(data.get("fire_type", "gas")).lower(),
            protocol_version=str(data.get("protocol_version", "")),
        )
    except (KeyError, TypeError, ValueError) as err:
        raise EsceaProtocolError(f"unexpected NCAPI state: {data!r}") from err


class EsceaNcapiError(EsceaError):
    """The fire refused an NCAPI request."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class EsceaNcapiClient:
    """Talk to a Generation 3 fire's NCAPI."""

    def __init__(self, session: aiohttp.ClientSession, host: str) -> None:
        self._session = session
        self._host = host
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._last_status: NcapiStatus | None = None

    @property
    def host(self) -> str:
        """Return the fire's IP address."""
        return self._host

    @host.setter
    def host(self, host: str) -> None:
        self._host = host

    @property
    def last_status(self) -> NcapiStatus | None:
        """Return the most recent state read, if any."""
        return self._last_status

    async def _request(self, method: str, params: dict[str, str] | None = None) -> Any:
        async with self._lock:
            wait = MIN_REQUEST_GAP - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                async with self._session.request(
                    method,
                    f"http://{self._host}/",
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                ) as response:
                    self._last_request = time.monotonic()
                    if response.status != 200:
                        raise _error_for(response.status)
                    if method == "GET":
                        try:
                            return await response.json(content_type=None)
                        except ValueError as err:
                            raise EsceaProtocolError(
                                f"{self._host} didn't answer with JSON"
                            ) from err
                    return None
            except (aiohttp.ClientError, TimeoutError) as err:
                self._last_request = time.monotonic()
                raise EsceaConnectionError(
                    f"NCAPI {method} to {self._host} failed: {err}"
                ) from err

    async def async_get_status(self) -> NcapiStatus:
        """Read the fire's state."""
        data = await self._request("GET")
        if not isinstance(data, dict):
            raise EsceaProtocolError(f"NCAPI returned {data!r}, not a JSON object")
        self._last_status = parse_status(data)
        return self._last_status

    async def async_set(
        self,
        *,
        on: bool | None = None,
        fan_boost: bool | None = None,
        flame_effect: bool | None = None,
        target_temperature: float | None = None,
    ) -> None:
        """Change one or more settings in a single request.

        Fan boost and flame effect can't both be turned on; the fire turns
        the other off when one is turned on.
        """
        params: dict[str, str] = {}
        if on is not None:
            params["on"] = _bool(on)
        if fan_boost is not None:
            params["fan_boost"] = _bool(fan_boost)
        if flame_effect is not None:
            params["flame_effect"] = _bool(flame_effect)
        if fan_boost and flame_effect:
            raise EsceaProtocolError("fan boost and flame effect can't both be on")
        if target_temperature is not None:
            deci = round(target_temperature * 10)
            if not NCAPI_MIN_TEMPERATURE * 10 <= deci <= NCAPI_MAX_TEMPERATURE * 10:
                raise EsceaProtocolError(
                    f"{target_temperature} °C is outside "
                    f"{NCAPI_MIN_TEMPERATURE:.0f}-{NCAPI_MAX_TEMPERATURE:.0f} °C"
                )
            params["target_temp_deci_c"] = str(deci)
        if not params:
            raise EsceaProtocolError("nothing to change")
        _LOGGER.debug("NCAPI POST to %s: %s", self._host, params)
        await self._request("POST", params)


def _bool(value: bool) -> str:
    return "true" if value else "false"


def _error_for(status: int) -> EsceaError:
    messages = {
        400: "the fire rejected the request (400 Bad Request)",
        408: "the fire is being asked too often (408); slow down",
        414: "request too long (414)",
        500: "the fire's web server failed (500)",
        503: "the fire hasn't finished starting up (503)",
    }
    message = messages.get(status, f"unexpected HTTP {status}")
    if status in (408, 500, 503):
        return EsceaConnectionError(message)
    return EsceaNcapiError(status, message)


async def async_probe(session: aiohttp.ClientSession, host: str) -> NcapiStatus | None:
    """Return the status if ``host`` answers as an NCAPI fire, otherwise None."""
    client = EsceaNcapiClient(session, host)
    try:
        return await client.async_get_status()
    except EsceaError:
        return None
