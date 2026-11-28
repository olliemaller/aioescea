# aioescea

[![PyPI](https://img.shields.io/pypi/v/aioescea?style=for-the-badge&color=FFFFFF&labelColor=000000)](https://pypi.org/project/aioescea/)
[![Checks](https://img.shields.io/github/actions/workflow/status/olliemaller/aioescea/ci.yml?branch=main&style=for-the-badge&label=checks&labelColor=000000)](https://github.com/olliemaller/aioescea/actions/workflows/ci.yml)
[![Licence](https://img.shields.io/badge/licence-MIT-FFFFFF?style=for-the-badge&labelColor=000000)](https://github.com/olliemaller/aioescea/blob/main/LICENSE)
[![Read the write-up](https://img.shields.io/badge/Read-the%20write--up-FFFFFF?style=for-the-badge&labelColor=000000)](https://maller.com.au/projects/escea-fireplace/)
[![maller.com.au](https://img.shields.io/badge/Site-maller.com.au-FFFFFF?style=for-the-badge&labelColor=000000)](https://maller.com.au/)

**Async Python client for Escea gas fireplaces on your local network.** No cloud.

It supports both generations of Escea network controller:

| | Generation 2 (Ethernet) | Generation 3 (Wi-Fi, from 2024) |
|---|---|---|
| Control | UDP port 3300 | NCAPI: HTTP and JSON on port 80 |
| On/off, temperature, boost, flame effect | ✓ (4-30 °C) | ✓ (10-38 °C, 0.1 °C steps) |
| Room temperature | ✓ | ✓ |
| What the fire is doing (starting, igniting, heating, shutting down, error) | | ✓ |
| Remote present, stored errors | | ✓ |
| Found by broadcast | ✓ | needs its IP address |
| Built-in timers, clock, diagnostics | ✓ (from its web page) | |

It powers the [Escea Fireplace](https://github.com/olliemaller/ha-escea-fireplace)
integration for Home Assistant.

> [!IMPORTANT]
> **Unofficial.** Not affiliated with or endorsed by Escea. "Escea" is a
> trademark of its owner, used here only to describe compatibility.

## Install

```sh
pip install aioescea
```

Python 3.12 or later.

## Use it

```python
import asyncio
from aioescea import EsceaFireplace, EsceaTransport, FireMode, async_discover


async def main() -> None:
    async with EsceaTransport() as transport:
        for found in await async_discover(transport):
            print(found.host, found.serial, found.pin)

        fire = EsceaFireplace("192.168.1.50", transport)
        status = await fire.async_get_status()
        print(status.is_on, status.room_temperature, status.target_temperature)

        await fire.async_turn_on()  # lights the fire
        # The fire ignores commands for 66 s while it lights. Commands sent
        # in that time raise EsceaBusyError; turning off is always allowed.
        await asyncio.sleep(fire.busy_remaining)
        await fire.async_set_temperature(22)
        await fire.async_set_mode(FireMode.BOOST)
        await fire.async_turn_off()


asyncio.run(main())
```

### Generation 3 (Wi-Fi) fires

Turn on **Escea NCAPI Settings** under Fire Settings in the Escea Smartheat
app first (fire firmware 1.5.9 or later), and give the fire a DHCP
reservation.

```python
import aiohttp
from aioescea import EsceaNcapiClient

async with aiohttp.ClientSession() as session:
    fire = EsceaNcapiClient(session, "192.168.1.60")
    status = await fire.async_get_status()
    print(status.state, status.room_temperature, status.remote_present)
    await fire.async_set(on=True, flame_effect=True, target_temperature=22.5)
```

### The web page (Generation 2: timers, clock, diagnostics)

```python
import aiohttp
from datetime import time
from aioescea import EsceaWebClient, Timer

async with aiohttp.ClientSession() as session:
    web = EsceaWebClient(session, "192.168.1.50", username="123456", password="1234")
    page = await web.async_get_home_page()  # no login needed
    print(page.clock, page.timers)
    print(await web.async_get_diagnostics())  # login needed

    # fire is an EsceaFireplace for the same fire, as above.
    async def read_power() -> bool:  # whether the fire is on right now
        return (await fire.async_get_status()).is_on

    # Reads the other timer from the fire, and checks the power again right
    # before sending, because the save form also switches the fire on or off.
    await web.async_set_timer(
        Timer(number=1, on_time=time(6, 30), off_time=time(8, 0), enabled=True),
        fire_is_on=await read_power(),
        read_power=read_power,
    )
```

On most fires the web login is the **serial number** and the **PIN**, both of
which discovery returns.

### Command line

```sh
aioescea discover
aioescea status 192.168.1.50
aioescea on 192.168.1.50
aioescea temp 192.168.1.50 22
aioescea mode 192.168.1.50 boost
aioescea off 192.168.1.50
aioescea timers 192.168.1.50
aioescea diagnostics 192.168.1.50 --username 123456 --password 1234
aioescea g3-status 192.168.1.60
aioescea g3-set 192.168.1.60 --on --mode flame_effect --temp 22.5
```

| Command | What it does |
|---|---|
| `discover [--broadcast ADDRESS] [--timeout SECONDS]` | Find Generation 2 fires by broadcast. Repeat `--broadcast` for more than one address (default 255.255.255.255); `--timeout` defaults to 5 s. |
| `status HOST`, `on HOST`, `off HOST` | Read the live status, light the fire, or turn it off. |
| `temp HOST DEGREES` | Set the target temperature (whole degrees). |
| `mode HOST {normal,boost,flame_effect} [--allow-ignite]` | Set the mode. Boost or flame effect on a fire that's off is refused unless you add `--allow-ignite`, because it would light the fire. |
| `timers HOST` | Read the clock, temperatures and timers from the web page. |
| `diagnostics HOST --username U --password P` | Read the diagnostics web page. |
| `g3-status HOST` | Read a Generation 3 (Wi-Fi) fire. |
| `g3-set HOST [--on \| --off] [--mode MODE] [--temp DEGREES]` | Change a Generation 3 fire. `--temp` takes decimals, such as 22.5. |

Add `-v` (or `--verbose`) before the command for debug logging, for example
`aioescea -v status 192.168.1.50`.

## How it behaves

- **It never sends anything you didn't ask for.** No background loop, no
  buffered commands. A missed reply can't cause an old command to be re-sent,
  so it can't re-light a fire someone turned off with the remote or the app.
  ([The bug this avoids](https://github.com/lazdavila/pescea/issues/8).)
- **Changing the mode while the fire is off would light it**, because that's
  how the fire works. `async_set_mode` refuses unless you pass
  `allow_ignite=True`.
- **Turning off is never refused**, even during the 66 s lighting window.
- **One request per fire at a time, retried**, because the controller is
  single threaded and UDP drops packets.
- **Port 3300 is not shared.** Fires always reply to port 3300, so only one
  program per computer can talk to them. If another program has the port,
  `EsceaPortInUseError` says so instead of silently missing replies.
- **The web page is fragile.** It answers one connection at a time and can
  stop answering until the fire is power cycled. The client sends one request
  at a time with `Connection: close`; poll it every few minutes at most.

## Protocol

- [Fireplace LAN Communications Protocol Specification v0.3](https://github.com/olliemaller/aioescea/blob/main/docs/protocol.md)
  (Kamahi Electronics for Escea, 2014): the UDP protocol.
- [NCAPI](https://github.com/olliemaller/aioescea/blob/main/docs/protocol.md#generation-3-ncapi) (Escea document 640051_0,
  2024): the Generation 3 Wi-Fi HTTP API.
- [Network summary](https://github.com/olliemaller/aioescea/blob/main/docs/protocol.md#controller-network-summary) (Tussock
  Innovation for Escea, 2016): the controller's ports, DHCP behaviour and
  MAC address prefix (`AC:DE:48`).

## Credits

- [snikch/escea](https://github.com/snikch/escea) by Mal Curtis: the first
  Python client, and the captured frames used in the tests.
- [lazdavila/pescea](https://github.com/lazdavila/pescea) by Laz Davila: the
  66 s busy window and the flame-effect quirk.
- [derekclapham/ha-escea](https://github.com/derekclapham/ha-escea) by Derek
  Clapham: the web page's login, timer and diagnostics formats.

## Licence

[MIT](https://github.com/olliemaller/aioescea/blob/main/LICENSE)
