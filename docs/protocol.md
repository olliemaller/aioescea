# Escea fireplace protocol notes

A summary of what Escea's documents say and what has been learned from real
fires since. Escea's own PDFs are not reproduced here.

## UDP protocol

Source: *Fireplace LAN Communications Protocol Specification*, Kamahi
Electronics Ltd for Escea, version 0.3, 11 March 2014.

- UDP, port 3300, both ways. Fires reply to port 3300 whatever port the
  request came from.
- Discovery: broadcast `SEARCH_FOR_FIRES` on the local subnet. Each fire
  answers `I_AM_A_FIRE` with its serial number and PIN. Replies usually take
  under 3 seconds.
- The spec suggests a remote shows the user the serial number and asks for
  the PIN before talking to the fire.

### Frame (15 bytes)

| Byte (from 0) | Meaning |
|---|---|
| 0 | Start, `0x47` ("G") |
| 1 | Command or response id |
| 2 | Data size |
| 3-12 | Data, zero filled |
| 13 | CRC: sum of bytes 1-12, modulo 256 |
| 14 | End, `0x46` ("F") |

### Commands

| Command | Id | Data | Reply |
|---|---|---|---|
| `STATUS_PLEASE` | `0x31` | none | `STATUS` `0x80` |
| `POWER_ON` | `0x39` | none | `0x8D` |
| `POWER_OFF` | `0x3A` | none | `0x8F` |
| `SEARCH_FOR_FIRES` | `0x50` | none | `I_AM_A_FIRE` `0x90` |
| `FAN_BOOST_ON` | `0x37` | none | `0x89` |
| `FAN_BOOST_OFF` | `0x38` | none | `0x8B` |
| `FLAME_EFFECT_ON` | `0x56` | none | `0x61` |
| `FLAME_EFFECT_OFF` | `0x55` | none | `0x60` |
| `NEW_SET_TEMP` | `0x57` | 1 byte, °C, 4-30 | `0x66` |

### `STATUS` data (6 bytes)

| Data byte | Meaning |
|---|---|
| 0 | Fire has new timers |
| 1 | Fire is on |
| 2 | Fan boost is on |
| 3 | Flame effect is on |
| 4 | Target temperature, °C |
| 5 | Room temperature, °C |

### `I_AM_A_FIRE` data

Bytes 0-3: serial number (unsigned, big endian). Bytes 4-5: PIN (unsigned,
big endian). Real firmware reports a data size of 4 here, not 6, but both
values are present.

### Learned from real fires

- After `POWER_ON` or `POWER_OFF`, the fire ignores commands for about
  **66 seconds** while it lights or purges (pescea, measured with the Escea
  remote).
- Turning **flame effect or fan boost on while the fire is off lights it**.
- The fire can also be changed by its RF remote and the Escea app, so a
  client must never assume its last command is still in effect.

## Controller network summary

Source: *Escea Controller Network Communications Summary*, Mark Butler,
Tussock Innovation, 16 March 2016.

- Microchip ENC28J60, 10 Mbit Ethernet. No Wi-Fi.
- **MAC address `AC:DE:48:xx:xx:xx`.** The last three bytes come from the RF
  remote address, which is derived from the serial number.
- Listens on: DHCP client UDP 68, NBNS UDP 137, mDNS UDP 5353, app
  protocol UDP 3300, web page TCP 80.
- Connects out to Escea's Smart Heat server on TCP 8001 (source ports
  29152-29202), for the app away from home. It reconnects every 20 s if that
  fails.
- DHCP: 5 attempts 10 s apart, then a fallback static address, then one
  attempt every 2 minutes. Renews 20 s before the lease runs out.
- mDNS name `escea-<serial>.local`; NBNS name `ESCEA-<serial>`.
- **Network monitor: it pings the default gateway every 50 s and resets its
  network stack if there's no reply within 5 s.** If the router doesn't answer
  pings from the LAN, the fire keeps dropping off the network.

## Web page (port 80)

Reverse engineered by Derek Clapham (derekclapham/ha-escea).

- `GET /index.html`, no login: the fire's clock, room and target
  temperature, both timers' on and off times and whether each is on.
- `POST /doLogin.html` with `user` and `password` set to the **MD5 hex** of
  the username and password. The reply page holds a `token` input; send it
  back as a `token` cookie. Usually username = serial, password = PIN.
- `GET /diagnostics.html` (login): processor temperature, duct temperature
  (DX only), pilot valve, thermal cut-out, ignition, fan and gas drive, and
  valve currents.
- `POST /index.html` (login) saves both timers at once with
  `ON_OFF_SWITCH`, `TIMER1_ON_TIME`, `TIMER1_OFF_TIME`, `TIMER2_ON_TIME`,
  `TIMER2_OFF_TIME`, `TIMER1_SW`, `TIMER2_SW`, in that order. `ON_OFF_SWITCH`
  is the fire's power and is applied, so send the current state.
- The web server handles one connection at a time and can stop answering
  under load until the fire is power cycled.
