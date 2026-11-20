"""A fake Escea web page, served by aiohttp on localhost, for tests."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib

from aiohttp import web

TOKEN = "0123456789abcdef0123456789abcdef"


def _row(label: str, value: str) -> str:
    return f'<tr><td>{label}:</td><td class ="t_right">{value}</td></tr>'


def _on_off(value: bool) -> str:
    colour, text = ("red", "ON") if value else ("green", "OFF")
    return f'<font color="{colour}">{text}</font>'


@dataclass
class FakeWeb:
    """State and request log of a fake fire's web page."""

    username: str = "123456"
    password: str = "1234"
    timers: dict[int, list[object]] = field(
        default_factory=lambda: {
            1: ["06:55", "08:00", True],
            2: ["16:00", "22:00", False],
        }
    )
    clock: str = "17:12"
    fire_is_on: bool = False
    logins: list[dict[str, str]] = field(default_factory=list)
    saves: list[list[tuple[str, str]]] = field(default_factory=list)
    ignore_saves: bool = False
    expire_next_token: bool = False

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/index.html", self.index)
        app.router.add_post("/index.html", self.save)
        app.router.add_post("/doLogin.html", self.login)
        app.router.add_get("/diagnostics.html", self.diagnostics)
        return app

    def _logged_in(self, request: web.Request) -> bool:
        if request.cookies.get("token") != TOKEN:
            return False
        if self.expire_next_token:
            self.expire_next_token = False
            return False
        return True

    def _page(self, body: str, logged_in: bool) -> web.Response:
        menu = (
            '<a class="button" href="#" onClick="logout();">Log out</a>'
            if logged_in
            else '<a class="button" href="login.html">Login</a>'
        )
        return web.Response(
            text=f"<html><body>{menu}<table>{body}</table></body></html>",
            content_type="text/html",
        )

    async def index(self, request: web.Request) -> web.Response:
        return self._index(self._logged_in(request))

    def _index(self, logged_in: bool) -> web.Response:
        rows = [
            _row("Time", self.clock),
            _row("Current Room Temperature", " 18 &deg;C"),
            _row("Set Room Temperature", " 23 &deg;C"),
        ]
        for n in (1, 2):
            rows.append(_row(f"Timer {n} on time", str(self.timers[n][0])))
            rows.append(_row(f"Timer {n} off time", str(self.timers[n][1])))
        for n in (1, 2):
            rows.append(_row(f"Timer {n} status", _on_off(bool(self.timers[n][2]))))
        return self._page("".join(rows), logged_in)

    async def login(self, request: web.Request) -> web.Response:
        form = dict(await request.post())
        self.logins.append({k: str(v) for k, v in form.items()})
        good = (
            form.get("user") == hashlib.md5(self.username.encode()).hexdigest()
            and form.get("password") == hashlib.md5(self.password.encode()).hexdigest()
        )
        token = TOKEN if good else ""
        return web.Response(
            text=f'<input type="hidden" name="token" id="token" value="{token}">',
            content_type="text/html",
        )

    async def save(self, request: web.Request) -> web.Response:
        form = list((await request.post()).items())
        self.saves.append([(k, str(v)) for k, v in form])
        if self._logged_in(request) and not self.ignore_saves:
            values = dict(self.saves[-1])
            for n in (1, 2):
                self.timers[n] = [
                    values[f"TIMER{n}_ON_TIME"],
                    values[f"TIMER{n}_OFF_TIME"],
                    values[f"TIMER{n}_SW"] == "On",
                ]
            self.fire_is_on = values["ON_OFF_SWITCH"] == "On"
        return await self.index(request)

    async def diagnostics(self, request: web.Request) -> web.Response:
        if not self._logged_in(request):
            return self._index(False)
        rows = "".join(
            [
                _row("Internal Processor Temperature", "  41 &deg;C"),
                _row("DX Duct Temperature", "DX Model Only!"),
                _row("Power detected through thermal cut-out", _on_off(True)),
                _row("Power Detected Pilot Valve", _on_off(True)),
                _row("Current Flue Fan Drive", " 99&#37;"),
                _row("Current DX Gas Drive", " 23&#37;"),
                _row("Ignition module Power Switch", _on_off(False)),
                _row("Solenoid current SD01/02", "305 mA"),
                _row("Variable Valve current VDC24", "69 mA"),
            ]
        )
        return self._page(rows, True)
