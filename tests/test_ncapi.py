"""Tests for the Generation 3 NCAPI client against a fake fire."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from aioescea import (
    EsceaConnectionError,
    EsceaNcapiClient,
    EsceaNcapiError,
    EsceaProtocolError,
    FireState,
    async_probe,
)
import aioescea.ncapi as ncapi_module


@dataclass
class FakeG3:
    """A Generation 3 fire's NCAPI, following Escea's document 640051_0."""

    state: dict[str, Any] = field(
        default_factory=lambda: {
            "protocol_version": "1",
            "fire_type": "gas",
            "state": "off",
            "fan_boost": False,
            "flame_effect": False,
            "target_temp_deci_c": 220,
            "room_temp_deci_c": 185,
            "remote_present": True,
            "serial_number": "999997",
            "errors": False,
        }
    )
    posts: list[dict[str, str]] = field(default_factory=list)
    status: int = 200

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/", self.get)
        app.router.add_post("/", self.post)
        return app

    async def get(self, request: web.Request) -> web.Response:
        if request.query:
            return web.Response(status=400)
        if self.status != 200:
            return web.Response(status=self.status)
        return web.json_response(self.state)

    async def post(self, request: web.Request) -> web.Response:
        params = {k.lower(): v.lower() for k, v in request.query.items()}
        self.posts.append(params)
        if not params or len(params) != len(request.query):
            return web.Response(status=400)
        allowed = {"on", "fan_boost", "flame_effect", "target_temp_deci_c"}
        if set(params) - allowed:
            return web.Response(status=400)
        if params.get("fan_boost") == "true" and params.get("flame_effect") == "true":
            return web.Response(status=400)
        if "target_temp_deci_c" in params:
            value = int(params["target_temp_deci_c"])
            if not 100 <= value <= 380:
                return web.Response(status=400)
            self.state["target_temp_deci_c"] = value
        if "on" in params:
            self.state["state"] = (
                "starting" if params["on"] == "true" else "shutting_down"
            )
        if params.get("fan_boost") == "true":
            self.state.update(fan_boost=True, flame_effect=False)
        elif params.get("fan_boost") == "false":
            self.state["fan_boost"] = False
        if params.get("flame_effect") == "true":
            self.state.update(flame_effect=True, fan_boost=False)
        elif params.get("flame_effect") == "false":
            self.state["flame_effect"] = False
        return web.Response(status=200)


@pytest.fixture(autouse=True)
def no_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ncapi_module, "MIN_REQUEST_GAP", 0)


@pytest.fixture
def fake() -> FakeG3:
    return FakeG3()


@pytest.fixture
async def client(fake: FakeG3) -> AsyncIterator[EsceaNcapiClient]:
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    async with aiohttp.ClientSession() as session:
        yield EsceaNcapiClient(session, f"127.0.0.1:{server.port}")
    await server.close()


async def test_status(client: EsceaNcapiClient) -> None:
    status = await client.async_get_status()
    assert status.state is FireState.OFF
    assert status.is_on is False
    assert status.target_temperature == 22.0
    assert status.room_temperature == 18.5
    assert status.serial == "999997"
    assert status.is_gas
    assert status.remote_present


@pytest.mark.parametrize(
    ("state", "is_on"),
    [
        ("starting", True),
        ("igniting", True),
        ("heating", True),
        ("pilot_only", True),
        ("shutting_down", False),
        ("cool_down", False),
        ("error", False),
        ("something_new", False),
    ],
)
async def test_states(
    client: EsceaNcapiClient, fake: FakeG3, state: str, is_on: bool
) -> None:
    fake.state["state"] = state
    status = await client.async_get_status()
    assert status.is_on is is_on


async def test_no_room_temperature_yet(client: EsceaNcapiClient, fake: FakeG3) -> None:
    fake.state["room_temp_deci_c"] = -1000
    assert (await client.async_get_status()).room_temperature is None


async def test_turn_on_with_settings(client: EsceaNcapiClient, fake: FakeG3) -> None:
    await client.async_set(on=True, flame_effect=True, target_temperature=24.5)
    assert fake.posts == [
        {"on": "true", "flame_effect": "true", "target_temp_deci_c": "245"}
    ]
    status = await client.async_get_status()
    assert status.is_on
    assert status.flame_effect
    assert status.target_temperature == 24.5


async def test_boost_turns_flame_effect_off(
    client: EsceaNcapiClient, fake: FakeG3
) -> None:
    fake.state["flame_effect"] = True
    await client.async_set(fan_boost=True)
    status = await client.async_get_status()
    assert status.fan_boost
    assert not status.flame_effect


async def test_rejects_bad_requests_locally(client: EsceaNcapiClient) -> None:
    with pytest.raises(EsceaProtocolError):
        await client.async_set()
    with pytest.raises(EsceaProtocolError):
        await client.async_set(fan_boost=True, flame_effect=True)
    with pytest.raises(EsceaProtocolError):
        await client.async_set(target_temperature=40)


@pytest.mark.parametrize(
    ("status", "error"),
    [(408, EsceaConnectionError), (503, EsceaConnectionError), (400, EsceaNcapiError)],
)
async def test_http_errors(
    client: EsceaNcapiClient, fake: FakeG3, status: int, error: type[Exception]
) -> None:
    fake.status = status
    with pytest.raises(error):
        await client.async_get_status()


async def test_probe(fake: FakeG3) -> None:
    server = TestServer(fake.app(), host="127.0.0.1")
    await server.start_server()
    async with aiohttp.ClientSession() as session:
        status = await async_probe(session, f"127.0.0.1:{server.port}")
        assert status is not None
        assert status.serial == "999997"
        assert await async_probe(session, "127.0.0.1:9") is None
    await server.close()


async def test_probe_html_page_is_not_ncapi() -> None:
    """A Generation 2 fire's web page is HTML, not NCAPI."""
    app = web.Application()

    async def index(request: web.Request) -> web.Response:
        return web.Response(text="<html>escea</html>", content_type="text/html")

    app.router.add_get("/", index)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    async with aiohttp.ClientSession() as session:
        assert await async_probe(session, f"127.0.0.1:{server.port}") is None
    await server.close()
