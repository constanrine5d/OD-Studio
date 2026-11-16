"""Exceptions and instrument error-code tables."""
from __future__ import annotations

# Error numbers used by the simulator that ships inside SOFTmax PRO 1.x
# (SPF.EXE seg69:0027..00a0, class CSFlashSim).
SIM_ERRORS = {
    0: "No error",
    1: "Unknown Command",
    2: "Wrong number of parameters",
    3: "Parameter is out of range",
    4: "Command is not implemented",
    5: "Error overflow",
}

# Error numbers reported by real Molecular Devices firmware of this protocol
# family ("FAIL\t<code>").  SOFTmax PRO reads exactly three digits
# (SPF.EXE seg68:4051), so real codes are 100..999.  The meanings below are the
# ones documented for later SpectraMax models that kept the same command set;
# treat them as best-effort labels on a SpectraMax 250.
FIRMWARE_ERRORS = {
    100: "command not found",
    101: "invalid argument",
    102: "too many arguments",
    103: "not enough arguments",
    104: "input line too long",
    105: "command invalid, system busy",
    106: "command invalid, measurement in progress",
    107: "no data to transfer",
    108: "data buffer full",
    109: "error buffer overflow",
    110: "stray light, door open?",
    111: "invalid read settings",
    200: "assert failed",
    201: "bad error number",
    202: "receive queue overflow",
    203: "serial port parity error",
    204: "serial port overrun error",
    205: "serial port framing error",
    206: "command generated too much output",
    207: "fatal trap",
    208: "RTOS error",
    209: "stack overflow",
    210: "unknown interrupt",
    300: "thermistor faulty",
    301: "safe temperature limit exceeded",
    302: "low light",
    303: "unable to calibrate dark current",
    304: "signal level saturation",
    305: "reference level saturation",
    306: "plate air calibration failed, low light",
    308: "stray light",
    312: "gain calibration failed",
    313: "reference gain check failed",
    314: "low lamp level warning",
    315: "cannot find zero order",
    316: "grating motor driver faulty",
    317: "monitor ADC faulty",
    400: "carriage motion error",
    401: "filter wheel error",
    402: "grating error",
    403: "stage error",
    500: "NVRAM CRC corrupt",
    501: "NVRAM grating calibration data bad",
    503: "NVRAM plate air calibration data error",
    504: "NVRAM carriage offset error",
    505: "NVRAM stage offset error",
}


def describe_error(code: int) -> str:
    if code in FIRMWARE_ERRORS:
        return FIRMWARE_ERRORS[code]
    if code in SIM_ERRORS:
        return SIM_ERRORS[code]
    return "unknown error"


class SpectraMaxError(Exception):
    """Base class for everything this package raises."""


class TransportError(SpectraMaxError):
    """The serial link itself failed (port missing, closed, write error)."""


class ReplyTimeout(SpectraMaxError):
    """No '>' prompt arrived in time.

    ``partial`` holds whatever bytes did arrive (SOFTmax PRO distinguishes
    "nothing at all" from "garbled / incomplete reply" the same way).
    """

    def __init__(self, command: str, partial: bytes = b""):
        self.command = command
        self.partial = partial
        what = "no reply" if not partial else f"incomplete reply {partial!r}"
        super().__init__(f"{command!r}: {what} from instrument")


class CommandFailed(SpectraMaxError):
    """The instrument answered FAIL (or something that is neither OK nor FAIL)."""

    def __init__(self, command: str, code: int | None, raw: str = ""):
        self.command = command
        self.code = code
        self.raw = raw
        if code is None:
            msg = f"{command!r}: unrecognised reply {raw!r}"
        else:
            msg = f"{command!r} failed: error {code} ({describe_error(code)})"
        super().__init__(msg)


class ProtocolError(SpectraMaxError):
    """A reply arrived but could not be parsed."""
