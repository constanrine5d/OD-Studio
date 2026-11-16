"""What each supported plate reader can do, and checks against it.

OD Studio talks to two families of Molecular Devices readers:

* the SpectraMax 250 ("Flash" protocol, :mod:`odstudio.driver`), a monochromator reader;
* the older filter readers ThermoMax, UVmax, Vmax and Emax (:mod:`odstudio.tmax`).

Both drivers expose the same methods to the program and describe themselves with a
``capabilities`` dict built by :func:`capabilities`.  The program greys out what the
connected reader cannot do and :func:`unsupported` refuses such reads before they start.
"""
from __future__ import annotations

from typing import Optional, Sequence

ALL_MODES = ("endpoint", "kinetic", "spectrum")


def capabilities(model: str, *, family: str = "spectramax", modes: Sequence[str] = ALL_MODES,
                 max_wavelengths: int = 6, filters: Optional[Sequence[int]] = None,
                 wl_min: int = 250, wl_max: int = 750, temperature: bool = True,
                 temp_min: float = 15.0, temp_max: float = 45.0, shake: bool = True,
                 calibrate: bool = True, column_range: bool = True,
                 min_interval: Optional[int] = None) -> dict:
    """Describe a reader.

    ``filters``       fixed filter wavelengths in nm, or None for a monochromator
    ``column_range``  whether a block of columns (strips) can be read instead of the whole plate
    ``min_interval``  shortest kinetic interval in seconds, None if it depends on the read
    """
    return {"model": model, "family": family, "modes": list(modes),
            "max_wavelengths": max_wavelengths,
            "filters": sorted(int(f) for f in filters) if filters is not None else None,
            "wl_min": wl_min, "wl_max": wl_max, "temperature": temperature,
            "temp_min": temp_min, "temp_max": temp_max, "shake": shake,
            "calibrate": calibrate, "column_range": column_range, "min_interval": min_interval}


def unsupported(caps: dict, rs) -> Optional[str]:
    """Why ``rs`` (a driver ReadSettings) cannot be read on this reader, or None."""
    name = caps.get("model") or "this reader"
    if rs.mode not in caps["modes"]:
        return f"The {name} cannot do {rs.mode} reads."
    if rs.mode != "spectrum":
        wls = [int(str(w).lstrip("Ff")) for w in rs.wavelengths]
        if len(wls) > caps["max_wavelengths"]:
            n = caps["max_wavelengths"]
            return f"The {name} reads at most {n} wavelength{'s' if n > 1 else ''} at a time."
        if caps["filters"] is not None:
            missing = [w for w in wls if w not in caps["filters"]]
            if missing:
                have = ", ".join(str(f) for f in caps["filters"]) or "none"
                return (f"The {name} has no {missing[0]} nm filter. Its filters: {have} nm.")
    if rs.mode == "kinetic" and caps["min_interval"] and rs.interval < caps["min_interval"]:
        return f"The {name} needs at least {caps['min_interval']} s between kinetic reads."
    return None


READER_KINDS = ("auto", "spectramax", "thermomax", "uvmax", "vmax", "emax")


def open_reader(port: str, *, kind: str = "auto", filters=(), trace=None, display=None):
    """Connect to the reader on ``port`` and return the connected driver.

    ``kind`` "auto" tries the SpectraMax 250 first and then the older filter readers,
    as SOFTmax PRO does (SPF.EXE 64:1198, 64:0cea).  ``sim:<model>`` ports pick the
    matching simulator.
    """
    from .driver import SpectraMax
    from .errors import SpectraMaxError
    from .tmax import MODEL_NAMES, TMaxReader

    if kind not in READER_KINDS:
        raise ValueError(f"unknown reader type {kind!r}")
    opts = port.strip().lower().split(":")
    if opts[0] == "sim":
        old = [o for o in opts[1:] if o in MODEL_NAMES]
        kind = old[0] if old else "spectramax"

    def flash():
        sm = SpectraMax(port, trace=trace)
        try:
            sm.connect(display=display)
        except BaseException:
            sm.disconnect()
            raise
        return sm

    def filter_reader(model):
        r = TMaxReader(port, model=model, filters=filters, trace=trace)
        r.connect()
        return r

    if kind == "spectramax":
        return flash()
    if kind != "auto":
        return filter_reader(kind)
    try:
        return flash()
    except SpectraMaxError as first:
        try:
            return filter_reader(None)
        except SpectraMaxError:
            raise first from None
