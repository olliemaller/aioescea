"""The fireplace's built-in web page (TCP port 80).

The UDP protocol has no commands for the fire's two daily timers, its clock
or its diagnostics. The controller's web page does, so this client reads and
writes them.

The page was reverse engineered from the fire's own web interface by Derek
Clapham (derekclapham/ha-escea, MIT). Rules this client follows, from that work:

* The web server is single threaded, handles one connection at a time and
  stops answering under load until the fire is power cycled. So every
  request is serialised, sent with ``Connection: close``, and callers should
  poll it rarely (minutes, not seconds).
* Logging in posts MD5 hashes of the username and password to
  ``doLogin.html`` and returns a token, sent back as a ``token`` cookie. On
  most fires the username is the serial number and the password is the PIN.
* The timer form also carries the fire's power switch. It must be sent with
  the fire's current power state or saving a timer turns the fire on or off.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import time as dt_time
import hashlib
import html as html_lib
import logging
import re

import aiohttp

from .exceptions import EsceaAuthError, EsceaConnectionError, EsceaError

_LOGGER = logging.getLogger(__name__)

# The fire's web server is slow: pages often take 10-20 s and the login can
# take longer. Shorter timeouts gave up on fires that were working, and an
# abandoned request keeps the single-threaded server busy for the next one.
READ_TIMEOUT = 30.0
LOGIN_TIMEOUT = 60.0
WRITE_TIMEOUT = 60.0
TIMER_NUMBERS = (1, 2)

_TOKEN_INPUT_RE = re.compile(r"<input\b[^>]*\b(?:name|id)=[\"']token[\"'][^>]*>", re.I)
_VALUE_RE = re.compile(r"\bvalue=[\"']([^\"']*)[\"']", re.I)
_ROW_RE = re.compile(r"<td>\s*([^<]+?)\s*:\s*</td>\s*<td[^>]*>(.*?)</td>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


@dataclass(frozen=True, slots=True)
class Timer:
    """One of the fire's two daily timers."""

    number: int
    on_time: dt_time | None
    off_time: dt_time | None
    enabled: bool | None

    @property
    def is_complete(self) -> bool:
        """Return True if every field was read."""
        return (
            self.on_time is not None
            and self.off_time is not None
            and self.enabled is not None
        )


@dataclass(frozen=True, slots=True)
class HomePage:
    """What the public home page shows (no login needed)."""

    clock: dt_time | None
    room_temperature: int | None
    target_temperature: int | None
    timers: dict[int, Timer]


@dataclass(frozen=True, slots=True)
class Diagnostics:
    """The login-protected diagnostics page.

    Any value the page does not show (or shows as "DX Model Only!") is None.
    """

    processor_temperature: int | None = None
    duct_temperature: int | None = None
    thermal_cutout_power: bool | None = None
    pilot_valve_power: bool | None = None
    power_b2_sv: bool | None = None
    power_b3: bool | None = None
    aux_switch: bool | None = None
    automation_switch: bool | None = None
    flue_fan_drive: int | None = None
    room_fan_drive: int | None = None
    gas_drive: int | None = None
    ignition_power: bool | None = None
    flue_fan_override: bool | None = None
    solenoid_current: int | None = None
    valve_current: int | None = None


#: Diagnostics page label (lower case, without the colon) -> (field, kind).
_DIAGNOSTIC_LABELS: dict[str, tuple[str, str]] = {
    "internal processor temperature": ("processor_temperature", "number"),
    "dx duct temperature": ("duct_temperature", "number"),
    "power detected through thermal cut-out": ("thermal_cutout_power", "bool"),
    "power detected pilot valve": ("pilot_valve_power", "bool"),
    "power to b2 (dl), sv (dx)": ("power_b2_sv", "bool"),
    "power to b3 (dl)": ("power_b3", "bool"),
    "auxiliary switch state": ("aux_switch", "bool"),
    "automation switch state": ("automation_switch", "bool"),
    "current flue fan drive": ("flue_fan_drive", "number"),
    "current room fan drive": ("room_fan_drive", "number"),
    "current dx gas drive": ("gas_drive", "number"),
    "ignition module power switch": ("ignition_power", "bool"),
    "flue fan override switch state": ("flue_fan_override", "bool"),
    "solenoid current sd01/02": ("solenoid_current", "number"),
    "variable valve current vdc24": ("valve_current", "number"),
}


def _md5(value: str) -> str:
    return hashlib.md5(value.encode()).hexdigest()  # noqa: S324 - the device's own scheme


def _cell_text(raw: str) -> str:
    return html_lib.unescape(_TAG_RE.sub("", raw)).strip()


def _parse_time(text: str) -> dt_time | None:
    match = _HHMM_RE.match(text.strip())
    return dt_time(int(match[1]), int(match[2])) if match else None


def _parse_number(text: str) -> int | None:
    match = re.search(r"-?\d+", text)
    return int(match[0]) if match else None


def _parse_bool(text: str) -> bool | None:
    upper = text.strip().upper()
    if upper in ("ON", "OFF"):
        return upper == "ON"
    return None


def _rows(page: str) -> dict[str, str]:
    """Return every ``<td>Label:</td><td>value</td>`` row, labels in lower case."""
    return {label.lower(): _cell_text(value) for label, value in _ROW_RE.findall(page)}


def parse_home_page(page: str) -> HomePage:
    """Parse ``index.html``."""
    rows = _rows(page)
    timers: dict[int, Timer] = {}
    for number in TIMER_NUMBERS:
        status = rows.get(f"timer {number} status")
        timers[number] = Timer(
            number=number,
            on_time=_parse_time(rows.get(f"timer {number} on time", "")),
            off_time=_parse_time(rows.get(f"timer {number} off time", "")),
            enabled=_parse_bool(status) if status is not None else None,
        )
    return HomePage(
        clock=_parse_time(rows.get("time", "")),
        room_temperature=_parse_number(rows.get("current room temperature", "")),
        target_temperature=_parse_number(rows.get("set room temperature", "")),
        timers=timers,
    )


def parse_diagnostics(page: str) -> Diagnostics:
    """Parse ``diagnostics.html``."""
    values: dict[str, int | bool | None] = {}
    for label, text in _rows(page).items():
        if label not in _DIAGNOSTIC_LABELS:
            continue
        field, kind = _DIAGNOSTIC_LABELS[label]
        values[field] = _parse_bool(text) if kind == "bool" else _parse_number(text)
    return Diagnostics(**values)  # type: ignore[arg-type]


def _is_login_page(page: str) -> bool:
    """Return True if the fire served a page for visitors who are not logged in.

    Logged-in pages carry a "Log out" menu item; the public ones don't.
    """
    return "logout" not in page.lower()


class EsceaWebClient:
    """Read and write the fire's web page."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        *,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self._session = session
        self._host = host
        self._username = username
        self._password = password
        self._token: str | None = None
        self._lock = asyncio.Lock()

    @property
    def host(self) -> str:
        """Return the fireplace's IP address."""
        return self._host

    @host.setter
    def host(self, host: str) -> None:
        self._host = host
        self._token = None

    @property
    def has_credentials(self) -> bool:
        """Return True if a username and password are set."""
        return bool(self._username) and bool(self._password)

    def set_credentials(self, username: str | None, password: str | None) -> None:
        """Change the login and forget the old token."""
        self._username = username
        self._password = password
        self._token = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        data: dict[str, str] | None = None,
        token: str | None = None,
        timeout: float = READ_TIMEOUT,
    ) -> str:
        """Send one request. Callers must hold ``self._lock``."""
        cookies = {"token": token} if token else None
        try:
            async with self._session.request(
                method,
                f"http://{self._host}/{path}",
                data=data,
                cookies=cookies,
                headers={"Connection": "close"},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                if response.status in (401, 403):
                    raise EsceaAuthError(f"{method} /{path}: HTTP {response.status}")
                response.raise_for_status()
                return await response.text(errors="replace")
        except (aiohttp.ClientError, TimeoutError) as err:
            raise EsceaConnectionError(f"{method} /{path} failed: {err}") from err

    async def _login(self) -> str:
        if not self.has_credentials:
            raise EsceaAuthError("no web login set")
        page = await self._request(
            "POST",
            "doLogin.html",
            data={
                "user": _md5(self._username or ""),
                "password": _md5(self._password or ""),
            },
            timeout=LOGIN_TIMEOUT,
        )
        tag = _TOKEN_INPUT_RE.search(page)
        value = _VALUE_RE.search(tag[0]) if tag else None
        if not value or not value[1]:
            raise EsceaAuthError("the fireplace rejected the web login")
        return value[1]

    async def _authed_get(self, path: str) -> str:
        for attempt in range(2):
            if self._token is None or attempt:
                self._token = await self._login()
            page = await self._request("GET", path, token=self._token)
            if not _is_login_page(page):
                return page
            self._token = None
        raise EsceaAuthError(f"/{path} still asks for a login after logging in")

    async def async_validate_login(self) -> None:
        """Log in now, raising EsceaAuthError if the login is wrong."""
        async with self._lock:
            self._token = await self._login()

    async def async_get_home_page(self) -> HomePage:
        """Read the clock, temperatures and timers (no login needed)."""
        async with self._lock:
            return parse_home_page(await self._request("GET", "index.html"))

    async def async_get_diagnostics(self) -> Diagnostics:
        """Read the diagnostics page (login needed)."""
        async with self._lock:
            return parse_diagnostics(await self._authed_get("diagnostics.html"))

    async def async_set_timers(
        self, timers: dict[int, Timer], *, fire_is_on: bool
    ) -> HomePage:
        """Write both timers in one request and return the page read back.

        ``timers`` must hold both timers, complete. ``fire_is_on`` must be
        the fire's current power state: the device applies the form's power
        field too, so a wrong value turns the fire on or off.
        """
        form: dict[str, str] = {"ON_OFF_SWITCH": "On" if fire_is_on else "Off"}
        for number in TIMER_NUMBERS:
            timer = timers.get(number)
            if timer is None or not timer.is_complete:
                raise EsceaError(f"timer {number} is not fully known; read it first")
            assert timer.on_time is not None
            assert timer.off_time is not None
            form[f"TIMER{number}_ON_TIME"] = timer.on_time.strftime("%H:%M")
            form[f"TIMER{number}_OFF_TIME"] = timer.off_time.strftime("%H:%M")
        # The enable switches must come last, in this order, as the device's
        # own page sends them; otherwise they are accepted and ignored.
        for number in TIMER_NUMBERS:
            form[f"TIMER{number}_SW"] = "On" if timers[number].enabled else "Off"

        async with self._lock:
            if not self.has_credentials:
                raise EsceaAuthError("no web login set")
            for attempt in range(2):
                if self._token is None or attempt:
                    self._token = await self._login()
                try:
                    reply = await self._request(
                        "POST",
                        "index.html",
                        data=form,
                        token=self._token,
                        timeout=WRITE_TIMEOUT,
                    )
                except EsceaConnectionError:
                    # The fire can be slow to answer a save (it writes to
                    # flash). A lost reply doesn't mean a lost save, so check
                    # by reading back below.
                    _LOGGER.debug("No reply to timer save; checking by reading back")
                    break
                if not _is_login_page(reply):
                    break
                # The token had expired, so the fire ignored the form and
                # served its public page. Log in again and resend once.
                _LOGGER.debug("Timer save needed a fresh login")
                self._token = None
            await asyncio.sleep(1)
            page = parse_home_page(await self._request("GET", "index.html"))

        for number in TIMER_NUMBERS:
            if page.timers.get(number) != timers[number]:
                self._token = None
                raise EsceaError(f"timer {number} did not save")
        return page

    async def async_set_timer(
        self, timer: Timer, *, fire_is_on: bool, current: dict[int, Timer]
    ) -> HomePage:
        """Change one timer, keeping the other as it is in ``current``."""
        merged = dict(current)
        old = current.get(timer.number)
        if old is not None:
            timer = replace(
                old,
                on_time=timer.on_time or old.on_time,
                off_time=timer.off_time or old.off_time,
                enabled=old.enabled if timer.enabled is None else timer.enabled,
            )
        merged[timer.number] = timer
        return await self.async_set_timers(merged, fire_is_on=fire_is_on)
