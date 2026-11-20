"""Exceptions raised by aioescea."""

from __future__ import annotations


class EsceaError(Exception):
    """Base class for every aioescea error."""


class EsceaProtocolError(EsceaError, ValueError):
    """A frame did not match the wire format, or a value is out of range."""


class EsceaConnectionError(EsceaError):
    """The fireplace could not be reached or did not answer in time."""


class EsceaPortInUseError(EsceaConnectionError):
    """UDP port 3300 is already taken by another program on this host.

    Fireplaces always reply to port 3300, so only one program per host can
    talk to them reliably.
    """


class EsceaBusyError(EsceaError):
    """The fire is lighting or shutting down and ignores commands for now."""

    def __init__(self, remaining: float) -> None:
        super().__init__(
            f"the fire is lighting or shutting down; try again in {remaining:.0f} s"
        )
        self.remaining = remaining


class EsceaStateError(EsceaError):
    """The command does not make sense in the fire's current state."""


class EsceaAuthError(EsceaError):
    """The fireplace's web page rejected the login."""
