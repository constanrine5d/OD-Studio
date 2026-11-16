"""Tab-delimited text export / import in SOFTmax PRO's two layouts.

*Time format*: one line per read, wells A1..H12 across (header ``A1 A2 ...``).
*Plate format*: an 8 x 12 block per read (header ``1 2 ... 12``).

The first column is the read time (``m:ss``) for kinetic data, the wavelength
for spectrum data and empty for endpoint data; the second is the temperature.
Layouts were taken from files exported by the original program.
"""
from __future__ import annotations

import re
import time
from typing import Optional

from .document import ALL_WELLS, NCOLS, empty_grid, fmt_hms

LABELS = {"kinetic": "Time(hh:mm:ss)", "spectrum": "Wavelength(nm)", "endpoint": ""}
TEMP = "Temperature(°C)"


def _num(v) -> str:
    if v is None:
        return ""
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _first(data: dict, k: int) -> str:
    if data["mode"] == "kinetic":
        return fmt_hms(data["times"][k])
    if data["mode"] == "spectrum":
        return str(data["sweep"][k])
    return ""


def export_text(data: dict, layout: str = "time", labels: bool = True, eol: str = "\r\n") -> str:
    if not data or not data["values"]:
        raise ValueError("There is no data to export")
    nw = len(data["values"][0])
    lines = []
    if layout == "time":
        if labels:
            lines.append("\t".join([LABELS[data["mode"]], TEMP] + ALL_WELLS * nw) + "\t")
        for k, read in enumerate(data["values"]):
            t = data["temps"][k]
            row = [_first(data, k), "" if t is None else f"{t:.2f}"]
            for grid in read:
                row += [_num(grid[r][c]) for r in range(8) for c in range(NCOLS)]
            lines.append("\t".join(row) + "\t")
        lines.append("")
    else:
        if labels:
            hdr = [LABELS[data["mode"]], TEMP]
            for _ in range(nw):
                hdr += [str(c + 1) for c in range(NCOLS)] + [""]
            lines.append("\t".join(hdr) + "\t")
        for k, read in enumerate(data["values"]):
            t = data["temps"][k]
            for r in range(8):
                row = [_first(data, k), "" if t is None else f"{t:.2f}"] if r == 0 else ["", ""]
                for grid in read:
                    row += [_num(grid[r][c]) for c in range(NCOLS)] + [""]
                lines.append("\t".join(row) + "\t")
            lines.append("\t")
    return eol.join(lines) + eol


def _hms(text: str) -> Optional[float]:
    parts = text.strip().split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        return None
    sec = 0.0
    for n in nums:
        sec = sec * 60 + n
    return sec


def _f(text: str) -> Optional[float]:
    text = text.strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def import_text(text: str) -> dict:
    """Parse an exported file (either layout, with or without labels) into a data block."""
    rows = [ln.split("\t") for ln in re.split(r"\r\n|\r|\n", text)]
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("The file contains no data")
    head = rows[0]
    labelled = len(head) > 1 and "temperature" in head[1].lower()
    mode_label = head[0].strip().lower() if labelled else ""
    layout = None
    if labelled:
        cells = [c.strip() for c in head[2:]]
        layout = "time" if "A1" in cells else "plate"
        rows = rows[1:]
    else:
        layout = "time" if max(len(r) for r in rows) >= 96 else "plate"
    firsts, temps, values = [], [], []
    if layout == "time":
        for r in rows:
            cells = r[2:] if (labelled or len(r) >= 98) else r
            n = max(1, len([c for c in cells]) // 96)
            read = []
            for w in range(n):
                grid = empty_grid()
                for i in range(96):
                    j = w * 96 + i
                    grid[i // 12][i % 12] = _f(cells[j]) if j < len(cells) else None
                read.append(grid)
            if all(v is None for g in read for row in g for v in row):
                continue
            firsts.append(r[0].strip() if (labelled or len(r) >= 98) else "")
            temps.append(_f(r[1]) if (labelled or len(r) >= 98) and len(r) > 1 else None)
            values.append(read)
    else:
        block: list = []
        meta = ("", None)
        offset = 2 if (labelled or max(len(r) for r in rows) >= 14) else 0
        for r in rows:
            if not block:
                meta = (r[0].strip(), _f(r[1]) if len(r) > 1 else None) if offset else ("", None)
            block.append(r[offset:])
            if len(block) == 8:
                width = max(len(b) for b in block)
                n = max(1, (width + 1) // 13)
                read = []
                for w in range(n):
                    grid = empty_grid()
                    for i, b in enumerate(block):
                        for c in range(NCOLS):
                            j = w * 13 + c
                            grid[i][c] = _f(b[j]) if j < len(b) else None
                    read.append(grid)
                read = [g for g in read if any(v is not None for row in g for v in row)] or read[:1]
                firsts.append(meta[0])
                temps.append(meta[1])
                values.append(read)
                block = []
        if block:
            raise ValueError("Plate-format data must come in blocks of 8 rows")
    if not values:
        raise ValueError("The file contains no data")
    mode = "endpoint"
    times, sweep = [0.0] * len(values), []
    if mode_label.startswith("time") or (not labelled and any(":" in f for f in firsts)):
        mode = "kinetic"
        times = [_hms(f) or 0.0 for f in firsts]
    elif mode_label.startswith("wavelength") or (not labelled and len(values) > 1 and all(_f(f) for f in firsts)):
        mode = "spectrum"
        sweep = [int(_f(f) or 0) for f in firsts]
        values = [[read[0]] for read in values]
    elif len(values) > 1:
        mode = "kinetic"
        times = [float(i) for i in range(len(values))]
    cols = [c for c in range(NCOLS)
            if any(read[0][r][c] is not None for read in values for r in range(8))]
    return {"mode": mode, "wavelengths": [0] * len(values[0]) if mode != "spectrum" else [],
            "times": times, "temps": temps, "sweep": sweep, "values": values, "saturated": [],
            "read_at": time.time(), "imported": True, "setpoint": 0,
            "first_col": (cols[0] + 1) if cols else 1, "last_col": (cols[-1] + 1) if cols else 12}
