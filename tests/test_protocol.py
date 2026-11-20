"""Tests for the wire format, checked against the frames in the Escea spec."""

from __future__ import annotations

import pytest

from aioescea.exceptions import EsceaProtocolError
from aioescea.protocol import (
    Command,
    decode_identity,
    decode_status,
    encode,
    encode_set_temperature,
    message_id,
)


def test_status_request_matches_spec() -> None:
    """The spec's example STATUS_PLEASE frame."""
    assert encode(Command.STATUS_PLEASE).hex() == "473100000000000000000000003146"


def test_status_response_from_spec() -> None:
    """The spec's example STATUS reply: on, 27 °C wanted, 24 °C in the room."""
    status = decode_status(bytes.fromhex("478006000100001B1800000000BA46"))
    assert status.is_on is True
    assert status.fan_boost is False
    assert status.flame_effect is False
    assert status.has_new_timers is False
    assert status.target_temperature == 27
    assert status.room_temperature == 24


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (Command.POWER_ON, "473900000000000000000000003946"),
        (Command.POWER_OFF, "473a00000000000000000000003a46"),
        (Command.FLAME_EFFECT_ON, "475600000000000000000000005646"),
        (Command.FLAME_EFFECT_OFF, "475500000000000000000000005546"),
        (Command.FAN_BOOST_ON, "473700000000000000000000003746"),
        (Command.FAN_BOOST_OFF, "473800000000000000000000003846"),
        (Command.SEARCH_FOR_FIRES, "475000000000000000000000005046"),
    ],
)
def test_commands_match_captures(command: Command, expected: str) -> None:
    """Frames captured from the Escea app (snikch/escea CODES.md)."""
    assert encode(command).hex() == expected


def test_set_temperature_matches_capture() -> None:
    """NEW_SET_TEMP 19 °C as sent by the Escea app."""
    assert encode_set_temperature(19).hex() == "475701130000000000000000006b46"


@pytest.mark.parametrize("temperature", [3, 31, -1, 255])
def test_set_temperature_range(temperature: int) -> None:
    """The spec allows 3 < °C < 31."""
    with pytest.raises(EsceaProtocolError):
        encode_set_temperature(temperature)


def test_identity_from_capture() -> None:
    """An I_AM_A_FIRE reply with data size 4, as real firmware sends."""
    identity = decode_identity(bytes.fromhex("4790040001021b1791000000005a46"))
    assert identity.serial == 0x0001021B  # 66075
    assert identity.pin == 0x1791  # 6033


@pytest.mark.parametrize(
    "frame",
    [
        "478006000100001B1800000000BB46",  # bad checksum
        "488006000100001B1800000000BA46",  # bad start byte
        "478006000100001B1800000000BA47",  # bad end byte
        "478006000100001B1800000000BA",  # too short
    ],
)
def test_bad_frames_rejected(frame: str) -> None:
    """Corrupt frames raise EsceaProtocolError."""
    with pytest.raises(EsceaProtocolError):
        message_id(bytes.fromhex(frame))


def test_status_decoder_rejects_other_ids() -> None:
    """An ack is not a status."""
    with pytest.raises(EsceaProtocolError):
        decode_status(bytes.fromhex("478d00000000000000000000008d46"))
