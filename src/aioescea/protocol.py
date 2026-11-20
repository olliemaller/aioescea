"""Escea LAN wire protocol: pure framing, no I/O.

Every message to or from a fireplace is exactly 15 bytes::

    index  meaning
    0      start byte, always 0x47 ("G")
    1      command or response id
    2      data size
    3-12   data, zero filled
    13     CRC: sum of bytes 1 to 12, modulo 256
    14     end byte, always 0x46 ("F")

Reference: "Fireplace LAN Communications Protocol Specification", Kamahi
Electronics for Escea, version 0.3 (11 March 2014).

Everything here is side-effect free so it can be tested without a socket.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct

from .exceptions import EsceaProtocolError

MESSAGE_LENGTH = 15
START_BYTE = 0x47
END_BYTE = 0x46

_ID = 1
_SIZE = 2
_DATA = 3
_CRC = 13
_END = 14

#: Lowest and highest target temperature the fire accepts (°C). The spec
#: writes this as "3 < °C < 31", so both ends are exclusive.
MIN_TEMPERATURE = 4
MAX_TEMPERATURE = 30


class Command(IntEnum):
    """Commands a remote sends to a fireplace."""

    STATUS_PLEASE = 0x31
    FAN_BOOST_ON = 0x37
    FAN_BOOST_OFF = 0x38
    POWER_ON = 0x39
    POWER_OFF = 0x3A
    SEARCH_FOR_FIRES = 0x50
    FLAME_EFFECT_OFF = 0x55
    FLAME_EFFECT_ON = 0x56
    NEW_SET_TEMP = 0x57


class Response(IntEnum):
    """Responses a fireplace sends back."""

    FLAME_EFFECT_OFF_ACK = 0x60
    FLAME_EFFECT_ON_ACK = 0x61
    NEW_SET_TEMP_ACK = 0x66
    STATUS = 0x80
    FAN_BOOST_ON_ACK = 0x89
    FAN_BOOST_OFF_ACK = 0x8B
    POWER_ON_ACK = 0x8D
    POWER_OFF_ACK = 0x8F
    I_AM_A_FIRE = 0x90


#: The response that acknowledges each command. Any other reply is ignored,
#: so a stray datagram can never be taken as success.
EXPECTED_RESPONSE: dict[Command, Response] = {
    Command.STATUS_PLEASE: Response.STATUS,
    Command.FAN_BOOST_ON: Response.FAN_BOOST_ON_ACK,
    Command.FAN_BOOST_OFF: Response.FAN_BOOST_OFF_ACK,
    Command.POWER_ON: Response.POWER_ON_ACK,
    Command.POWER_OFF: Response.POWER_OFF_ACK,
    Command.SEARCH_FOR_FIRES: Response.I_AM_A_FIRE,
    Command.FLAME_EFFECT_OFF: Response.FLAME_EFFECT_OFF_ACK,
    Command.FLAME_EFFECT_ON: Response.FLAME_EFFECT_ON_ACK,
    Command.NEW_SET_TEMP: Response.NEW_SET_TEMP_ACK,
}


@dataclass(frozen=True, slots=True)
class Status:
    """A decoded STATUS response: the fire's whole live state."""

    has_new_timers: bool
    is_on: bool
    fan_boost: bool
    flame_effect: bool
    target_temperature: int
    room_temperature: int


@dataclass(frozen=True, slots=True)
class Identity:
    """A decoded I_AM_A_FIRE response, returned by discovery."""

    serial: int
    pin: int


def crc(frame: bytes | bytearray) -> int:
    """Return the checksum of a frame (sum of bytes 1 to 12, modulo 256)."""
    return sum(frame[_ID:_CRC]) % 256


def encode(command: int, data: bytes = b"") -> bytes:
    """Build the 15-byte frame for a command or response id carrying ``data``."""
    if len(data) > _CRC - _DATA:
        raise EsceaProtocolError(f"too much data for one frame: {len(data)} bytes")
    frame = bytearray(MESSAGE_LENGTH)
    frame[0] = START_BYTE
    frame[_ID] = command
    frame[_SIZE] = len(data)
    frame[_DATA : _DATA + len(data)] = data
    frame[_CRC] = crc(frame)
    frame[_END] = END_BYTE
    return bytes(frame)


def encode_set_temperature(temperature: int) -> bytes:
    """Build a NEW_SET_TEMP frame, checking the range first."""
    if not MIN_TEMPERATURE <= temperature <= MAX_TEMPERATURE:
        raise EsceaProtocolError(
            f"target temperature {temperature} °C is outside "
            f"{MIN_TEMPERATURE}-{MAX_TEMPERATURE} °C"
        )
    return encode(Command.NEW_SET_TEMP, bytes([temperature]))


def message_id(frame: bytes) -> int:
    """Validate framing and checksum and return the frame's message id."""
    if len(frame) != MESSAGE_LENGTH:
        raise EsceaProtocolError(f"bad length {len(frame)}: {frame.hex()}")
    if frame[0] != START_BYTE or frame[_END] != END_BYTE:
        raise EsceaProtocolError(f"bad start or end byte: {frame.hex()}")
    expected = crc(frame)
    if frame[_CRC] != expected:
        raise EsceaProtocolError(
            f"bad checksum {frame[_CRC]:#04x}, expected {expected:#04x}: {frame.hex()}"
        )
    return frame[_ID]


def decode_status(frame: bytes) -> Status:
    """Decode a STATUS frame."""
    if message_id(frame) != Response.STATUS:
        raise EsceaProtocolError(f"not a STATUS response: {frame.hex()}")
    if frame[_SIZE] != 6:
        raise EsceaProtocolError(f"STATUS data size {frame[_SIZE]}, expected 6")
    data = frame[_DATA:]
    return Status(
        has_new_timers=bool(data[0]),
        is_on=bool(data[1]),
        fan_boost=bool(data[2]),
        flame_effect=bool(data[3]),
        target_temperature=data[4],
        room_temperature=data[5],
    )


def decode_identity(frame: bytes) -> Identity:
    """Decode an I_AM_A_FIRE frame.

    Some firmware reports a data size of 4 here although the spec says 6 and
    the serial and PIN are both present, so the size is not checked.
    """
    if message_id(frame) != Response.I_AM_A_FIRE:
        raise EsceaProtocolError(f"not an I_AM_A_FIRE response: {frame.hex()}")
    serial, pin = struct.unpack_from(">IH", frame, _DATA)
    return Identity(serial=serial, pin=pin)
