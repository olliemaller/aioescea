"""Command-line tool: ``python -m aioescea`` or ``aioescea``.

Examples::

    aioescea discover
    aioescea status 192.168.1.50
    aioescea on 192.168.1.50
    aioescea temp 192.168.1.50 22
    aioescea mode 192.168.1.50 boost
    aioescea timers 192.168.1.50
    aioescea diagnostics 192.168.1.50 --username 123456 --password 1234
    aioescea g3-status 192.168.1.60
    aioescea g3-set 192.168.1.60 --on --mode flame_effect --temp 22.5
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
import logging
import sys
from typing import Any

import aiohttp

from . import (
    EsceaError,
    EsceaFireplace,
    EsceaNcapiClient,
    EsceaTransport,
    EsceaWebClient,
    FireMode,
    async_discover,
)


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str))


async def _run_g3(args: argparse.Namespace) -> None:
    async with aiohttp.ClientSession() as session:
        fire = EsceaNcapiClient(session, args.host)
        if args.command == "g3-set":
            mode = FireMode(args.mode) if args.mode else None
            await fire.async_set(
                on=args.on,
                fan_boost=None if mode is None else mode is FireMode.BOOST,
                flame_effect=None if mode is None else mode is FireMode.FLAME_EFFECT,
                target_temperature=args.temp,
            )
        _print(asdict(await fire.async_get_status()))


async def _run(args: argparse.Namespace) -> None:
    if args.command.startswith("g3-"):
        await _run_g3(args)
        return
    if args.command in ("timers", "diagnostics"):
        async with aiohttp.ClientSession() as session:
            web = EsceaWebClient(
                session, args.host, username=args.username, password=args.password
            )
            if args.command == "timers":
                _print(asdict(await web.async_get_home_page()))
            else:
                _print(asdict(await web.async_get_diagnostics()))
        return

    async with EsceaTransport() as transport:
        if args.command == "discover":
            fires = await async_discover(
                transport, args.broadcast or ["255.255.255.255"], timeout=args.timeout
            )
            _print([asdict(fire) for fire in fires])
            return
        fire = EsceaFireplace(args.host, transport)
        if args.command == "on":
            await fire.async_turn_on()
        elif args.command == "off":
            await fire.async_turn_off()
        elif args.command == "temp":
            await fire.async_set_temperature(args.temperature)
        elif args.command == "mode":
            await fire.async_set_mode(
                FireMode(args.mode), allow_ignite=args.allow_ignite
            )
        _print(asdict(await fire.async_get_status()))


def main() -> None:
    """Run the command-line tool."""
    parser = argparse.ArgumentParser(
        prog="aioescea", description=__doc__.splitlines()[0]
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    discover = sub.add_parser("discover", help="find fireplaces by broadcast")
    discover.add_argument("--broadcast", action="append", help="broadcast address")
    discover.add_argument("--timeout", type=float, default=5.0)

    for name, text in (
        ("status", "read the live status"),
        ("on", "light the fire"),
        ("off", "turn the fire off"),
    ):
        sub.add_parser(name, help=text).add_argument("host")

    temp = sub.add_parser("temp", help="set the target temperature")
    temp.add_argument("host")
    temp.add_argument("temperature", type=int)

    mode = sub.add_parser("mode", help="set normal, boost or flame_effect")
    mode.add_argument("host")
    mode.add_argument("mode", choices=[m.value for m in FireMode])
    mode.add_argument("--allow-ignite", action="store_true")

    for name, text in (
        ("timers", "read clock, temperatures and timers from the web page"),
        ("diagnostics", "read the diagnostics web page"),
    ):
        web = sub.add_parser(name, help=text)
        web.add_argument("host")
        web.add_argument("--username")
        web.add_argument("--password")

    sub.add_parser("g3-status", help="read a Generation 3 (Wi-Fi) fire").add_argument(
        "host"
    )
    g3 = sub.add_parser("g3-set", help="change a Generation 3 (Wi-Fi) fire")
    g3.add_argument("host")
    power = g3.add_mutually_exclusive_group()
    power.add_argument("--on", dest="on", action="store_true", default=None)
    power.add_argument("--off", dest="on", action="store_false")
    g3.add_argument("--mode", choices=[m.value for m in FireMode])
    g3.add_argument("--temp", type=float)

    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    try:
        asyncio.run(_run(args))
    except EsceaError as err:
        print(f"error: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
