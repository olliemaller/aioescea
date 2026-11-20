"""Command-line tool: ``python -m aioescea`` or ``aioescea``.

Examples::

    aioescea discover
    aioescea status 192.168.1.50
    aioescea on 192.168.1.50
    aioescea temp 192.168.1.50 22
    aioescea mode 192.168.1.50 boost
    aioescea timers 192.168.1.50
    aioescea diagnostics 192.168.1.50 --username 123456 --password 1234
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
    EsceaTransport,
    EsceaWebClient,
    FireMode,
    async_discover,
)


def _print(value: Any) -> None:
    print(json.dumps(value, indent=2, default=str))


async def _run(args: argparse.Namespace) -> None:
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

    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING)
    try:
        asyncio.run(_run(args))
    except EsceaError as err:
        print(f"error: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
