"""Write read results as plate-layout text, long-format CSV or JSON."""
from __future__ import annotations

import csv
import io
import json
import math
from typing import Iterable

from .protocol import COLUMNS, ROW_LETTERS, ROWS, PlateData


def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and math.isinf(v):
        return "#SAT"
    if isinstance(v, float) and math.isnan(v):
        return "#ERR"
    return f"{v:.4f}"


def to_plate_text(plates: Iterable[PlateData]) -> str:
    """Human-readable 8x12 grids, one per read and wavelength (tab separated)."""
    out = io.StringIO()
    for i, p in enumerate(plates, 1):
        for wl in p.wavelengths:
            out.write(f"Read {i}\tTime(s) {p.time_s:g}\tTemp(C) {p.temperature:.1f}\t"
                      f"Wavelength(nm) {wl}\n")
            out.write("\t" + "\t".join(str(c) for c in range(1, COLUMNS + 1)) + "\n")
            grid = p.values.get(wl)
            for r in range(ROWS):
                row = grid[r] if grid else [None] * COLUMNS
                out.write(ROW_LETTERS[r] + "\t" + "\t".join(_fmt(v) for v in row) + "\n")
            out.write("\n")
    return out.getvalue()


def to_csv(plates: Iterable[PlateData]) -> str:
    """Long format: one line per well, read and wavelength."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(["read", "time_s", "temperature_c", "wavelength_nm", "well", "row", "column", "od"])
    for i, p in enumerate(plates, 1):
        for wl in p.wavelengths:
            grid = p.values.get(wl)
            if not grid:
                continue
            for r in range(ROWS):
                for c in range(COLUMNS):
                    v = grid[r][c]
                    if v is None:
                        continue
                    w.writerow([i, f"{p.time_s:g}", f"{p.temperature:.1f}", wl,
                                f"{ROW_LETTERS[r]}{c + 1}", ROW_LETTERS[r], c + 1, _fmt(v)])
    return out.getvalue()


def to_json(plates: Iterable[PlateData]) -> str:
    def clean(v):
        if v is None:
            return None
        if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
            return None
        return round(v, 4)

    data = []
    for i, p in enumerate(plates, 1):
        data.append({
            "read": i,
            "time_s": p.time_s,
            "temperature_c": p.temperature,
            "wavelengths_nm": list(p.wavelengths),
            "rows": list(ROW_LETTERS),
            "od": {str(wl): [[clean(v) for v in row] for row in grid]
                   for wl, grid in p.values.items()},
        })
    return json.dumps(data, indent=2)


FORMATS = {"plate": to_plate_text, "csv": to_csv, "json": to_json}


def render(plates: Iterable[PlateData], fmt: str = "plate") -> str:
    try:
        return FORMATS[fmt](list(plates))
    except KeyError:
        raise ValueError(f"unknown format {fmt!r}; choose from {', '.join(FORMATS)}") from None
