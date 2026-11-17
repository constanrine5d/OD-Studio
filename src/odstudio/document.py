"""Experiment document: sections, templates, data reduction, group tables, graphs.

A document is a plain JSON-serialisable dict (so saving, undo and the GUI are
trivial).  ``compute(doc)`` evaluates everything and returns the view the GUI
shows.  Terminology and behaviour follow SOFTmax PRO (see docs/FEATURES.md).
"""
from __future__ import annotations

import copy
import math
import re
import time
from typing import Any, Optional

from . import fits
from .formula import (E_ERROR, E_LIMITS, E_MASKED, E_NAME, E_RANGE, EMPTY, Err, Evaluator,
                      FormulaError, Plot, depth, flatten, is_num, lift2, linreg, to_text)

ROWS = "ABCDEFGH"
NCOLS = 12
FORMAT = "odstudio-document"
FORMATS = (FORMAT, "spectramax-document")   # files saved before the rename still open
VERSION = 1

GROUP_COLORS = ["#4f8ad9", "#e0823d", "#57a55a", "#c9566b", "#8b6bc4", "#b59a2e", "#3aa7a3", "#d46fb1"]


def well_name(r: int, c: int) -> str:
    return f"{ROWS[r]}{c + 1}"


def well_rc(name: str) -> tuple:
    m = re.fullmatch(r"([A-Ha-h])(\d{1,2})", name.strip())
    if not m or not 1 <= int(m.group(2)) <= NCOLS:
        raise ValueError(f"bad well name {name!r}")
    return ROWS.index(m.group(1).upper()), int(m.group(2)) - 1


ALL_WELLS = [well_name(r, c) for r in range(8) for c in range(NCOLS)]
COLUMN_MAJOR = [well_name(r, c) for c in range(NCOLS) for r in range(8)]


def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


# ------------------------------------------------------------------ defaults
def default_settings() -> dict:
    return {"mode": "endpoint", "wavelengths": [405], "run_time": 300, "interval": 30,
            "first_col": 1, "last_col": 12, "mix_before": False, "mix_before_s": 5,
            "mix_between": False, "mix_between_s": 3, "autocalibrate": True, "pre_read": False,
            "start": 350, "stop": 750, "step": 10, "delay": 0, "wait_temp": False, "columns": None}


def default_reduction() -> dict:
    return {"wavelength": "!L1", "kinetic": "vmax", "kinetic_formula": "Average(!KinPlot)",
            "spectrum": "maxlambda", "spectrum_formula": "Max(!KinPlot)",
            "lag_time": 0, "end_time": None, "min_od": None, "max_od": None,
            "vmax_points": None, "onset_od": 0.1, "start_nm": None, "end_nm": None,
            "plate_blank": True, "use_preread": True,
            "raw_mode": "od", "zero_first": False, "smoothing": 0}


def default_display() -> dict:
    return {"view": "raw", "type": "number", "low": 0.0, "high": 1.0}


WEIGHTINGS = {"none": "None", "1/y": "1/Y", "1/y2": "1/Y\u00b2", "1/x": "1/X", "1/x2": "1/X\u00b2",
              "1/sd2": "1/SD\u00b2 (Y error bars)"}


def default_fit_options() -> dict:
    """Weighting, confidence level, bands, and per-parameter Fixed / Seed values."""
    return {"weighting": "none", "conf": 0.95, "bands": False, "fixed": {}, "seeds": {}}


def default_axis() -> dict:
    return {"auto": True, "min": 0.0, "max": 1.0, "log": False, "grid": False, "ticks": True,
            "label": ""}


GROUP_KINDS = {
    "Standards": {"descriptor": "Concentration", "units": "mg/ml", "columns": [
        ("Sample", "!SampleNames", None), ("Concentration", "!Concentration", 3),
        ("Wells", "!WellIDs", None), ("Values", "!WellValues", 3),
        ("MeanValue", "Average(Values)", 3), ("Std.Dev.", "Stdev(Values)", 3),
        ("CV%", "Cv(Values)", 1)]},
    "Unknowns": {"descriptor": "", "units": "", "columns": [
        ("Sample", "!SampleNames", None), ("Wells", "!WellIDs", None),
        ("Values", "!WellValues", 3), ("MeanValue", "Average(Values)", 3),
        ("Std.Dev.", "Stdev(Values)", 3), ("CV%", "Cv(Values)", 1)]},
    "Controls": {"descriptor": "", "units": "", "columns": [
        ("Sample", "!SampleNames", None), ("Wells", "!WellIDs", None),
        ("Values", "!WellValues", 3), ("MeanValue", "Average(Values)", 3),
        ("Std.Dev.", "Stdev(Values)", 3), ("CV%", "Cv(Values)", 1)]},
    "Custom": {"descriptor": "", "units": "", "columns": [
        ("Sample", "!SampleNames", None), ("Wells", "!WellIDs", None),
        ("Values", "!WellValues", 3)]},
}


def new_document() -> dict:
    doc = {"format": FORMAT, "version": VERSION, "next_id": 1, "sections": [], "groups": {},
           "created": time.time()}
    add_section(doc, "plate")
    return doc


def _unique_name(doc: dict, base: str) -> str:
    names = {s["name"].lower() for s in doc["sections"]}
    i = 1
    while f"{base}#{i}".lower() in names:
        i += 1
    return f"{base}#{i}"


def add_section(doc: dict, kind: str, after: Optional[int] = None, **kw) -> dict:
    sid = doc["next_id"]
    doc["next_id"] += 1
    s: dict = {"id": sid, "kind": kind, "report": True, "open": True}
    if kind == "plate":
        s.update(name=_unique_name(doc, "Plate"), settings=default_settings(), template={},
                 mask=[], data=None, preread=None, reduction=default_reduction(),
                 display=default_display(), summaries=[])
    elif kind == "notes":
        s.update(name=_unique_name(doc, "Notes"), html="", summaries=[], show_formulas=False)
    elif kind == "graph":
        s.update(name=_unique_name(doc, "Graph"), plots=[], fit="linear", type="scatter",
                 connect=False, symbols=True, height=100, xaxis=default_axis(),
                 yaxis=default_axis(), fit_options=default_fit_options())
    elif kind == "group":
        s.update(name=kw["group"], group=kw["group"])
    elif kind == "workflow":
        s.update(name="Workflow", steps=[], log=[], next_step=1)
    else:
        raise ValueError(kind)
    index = kw.pop("index", None)
    s.update({k: v for k, v in kw.items() if k != "group"})
    idx = len(doc["sections"]) if index is None else index
    if after is not None:
        for i, x in enumerate(doc["sections"]):
            if x["id"] == after:
                idx = i + 1
    doc["sections"].insert(idx, s)
    return s


def section_by_id(doc: dict, sid: int) -> dict:
    for s in doc["sections"]:
        if s["id"] == sid:
            return s
    raise KeyError(f"no section {sid}")


def section_by_name(doc: dict, name: str) -> Optional[dict]:
    low = name.strip().lower()
    for s in doc["sections"]:
        if s["name"].lower() == low:
            return s
    return None


def ensure_group(doc: dict, name: str, kind: str = "", descriptor: Optional[str] = None,
                 units: Optional[str] = None) -> dict:
    """Create the group (and its Group section) if it does not exist yet."""
    for g in doc["groups"]:
        if g.lower() == name.lower():
            return doc["groups"][g]
    if not name.strip() or section_by_name(doc, name):
        raise ValueError("This name is already being used.  Please enter a different name.")
    base = GROUP_KINDS.get(kind) or GROUP_KINDS.get(name) or GROUP_KINDS[
        "Standards" if name.lower().startswith(("std", "stand")) else "Unknowns"]
    g = {"name": name, "descriptor": base["descriptor"] if descriptor is None else descriptor,
         "units": base["units"] if units is None else units,
         "columns": [{"name": n, "formula": f, "decimals": d, "hidden": False, "style": {}}
                     for n, f, d in base["columns"]],
         "summaries": [], "hide_replicates": False, "show_formulas": False,
         "color": GROUP_COLORS[len(doc["groups"]) % len(GROUP_COLORS)]}
    if g["descriptor"] and not any(c["formula"].lower() == "!" + g["descriptor"].lower()
                                   for c in g["columns"]):
        g["columns"].insert(1, {"name": g["descriptor"], "formula": "!" + g["descriptor"],
                                "decimals": 3, "hidden": False, "style": {}})
    doc["groups"][name] = g
    add_section(doc, "group", group=name)
    return g


# ------------------------------------------------------------ renaming
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_#.%]*")


def quote_name(name: str) -> str:
    if _IDENT.fullmatch(name) and name.lower() not in ("and", "or", "not", "mod"):
        return name
    return "'" + name.replace("'", "") + "'"


def _tokens(text: str) -> list:
    from .formula import _TOKEN
    out, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            break
        kind = m.lastgroup
        out.append([kind, m.group(kind), m.start(kind), m.end(kind)])
        pos = m.end()
    return out


def _tok_name(t) -> Optional[str]:
    if t[0] == "name":
        return t[1]
    if t[0] == "qname":
        return t[1][1:-1]
    return None


def rename_in_formula(formula: str, old: str, new: str, *, section: Optional[str] = None,
                      unqualified: bool = False, as_section: bool = False) -> str:
    """Rewrite references to ``old`` in one formula.

    as_section: ``X@old`` -> ``X@new`` (a section or group was renamed).
    Otherwise a column / summary / plot ``old`` living in ``section`` was renamed:
    ``old@section`` is rewritten, and bare ``old`` too when ``unqualified``.
    """
    toks = _tokens(formula)
    low = old.lower()
    edits = []
    for i, t in enumerate(toks):
        name = _tok_name(t)
        if name is None or name.lower() != low:
            continue
        prev_at = i > 0 and toks[i - 1][0] == "op" and toks[i - 1][1] == "@"
        next_at = i + 1 < len(toks) and toks[i + 1][0] == "op" and toks[i + 1][1] == "@"
        call = i + 1 < len(toks) and toks[i + 1][0] == "op" and toks[i + 1][1] == "("
        if as_section:
            if prev_at:
                edits.append((t[2], t[3], quote_name(new)))
            continue
        if prev_at or call:
            continue
        if next_at:
            sec = _tok_name(toks[i + 2]) if i + 2 < len(toks) else None
            if section is not None and sec is not None and sec.lower() == section.lower():
                edits.append((t[2], t[3], quote_name(new)))
        elif unqualified:
            edits.append((t[2], t[3], quote_name(new)))
    for a, b, text in reversed(edits):
        formula = formula[:a] + text + formula[b:]
    return formula


def all_formulas(doc: dict):
    """Yield (container, key, home_section_name) for every stored formula."""
    for gname, g in doc["groups"].items():
        for c in g["columns"]:
            yield c, "formula", gname
        for sm in g["summaries"]:
            yield sm, "formula", gname
    for sec in doc["sections"]:
        for sm in sec.get("summaries", []) if sec["kind"] != "group" else []:
            yield sm, "formula", sec["name"]


def rename_references(doc: dict, old: str, new: str, home: Optional[str] = None,
                      kind: str = "item") -> int:
    """Update formulas after a rename.  kind 'section': a section/group called old;
    'item': a column/summary called old in section ``home``; 'plot': a plot in graph ``home``."""
    if not old or old == new:
        return 0
    n = 0
    for obj, key, where in all_formulas(doc):
        f = obj.get(key) or ""
        if kind == "section":
            g = rename_in_formula(f, old, new, as_section=True)
        elif kind == "plot":
            g = rename_in_formula(f, old, new, section=home, unqualified=True)
        else:
            g = rename_in_formula(f, old, new, section=home,
                                  unqualified=(home is not None and where.lower() == home.lower()))
        if g != f:
            obj[key] = g
            n += 1
    return n


def rename_group(doc: dict, old: str, new: str) -> None:
    new = new.strip()
    if new == old:
        return
    other = section_by_name(doc, new)
    if not new or (other and other.get("group") != old):
        raise ValueError("This name is already being used.  Please enter a different name.")
    g = doc["groups"].pop(old)
    g["name"] = new
    doc["groups"][new] = g
    rename_references(doc, old, new, kind="section")
    for s in doc["sections"]:
        if s["kind"] == "group" and s["group"] == old:
            s["group"] = s["name"] = new
        elif s["kind"] == "plate":
            for w in s["template"].values():
                if w.get("group") == old:
                    w["group"] = new
        elif s["kind"] == "graph":
            for p in s["plots"]:
                if p.get("group") == old:
                    p["group"] = new


def delete_section(doc: dict, sid: int) -> None:
    s = section_by_id(doc, sid)
    doc["sections"].remove(s)
    if s["kind"] == "group":
        doc["groups"].pop(s["group"], None)
        for g in doc["sections"]:
            if g["kind"] == "graph":
                for p in g["plots"]:
                    if p.get("group") == s["group"]:
                        p.update(group=None, x=None, y=None, xerr=None, yerr=None)
        for p in doc["sections"]:
            if p["kind"] == "plate":
                for w in [w for w, t in p["template"].items() if t.get("group") == s["group"]]:
                    del p["template"][w]


def strip_data(doc: dict) -> dict:
    """A protocol is a document without data."""
    d = copy.deepcopy(doc)
    d.pop("history", None)
    for s in d["sections"]:
        if s["kind"] == "plate":
            s["data"] = None
            s["preread"] = None
            s["mask"] = []
        elif s["kind"] == "workflow":
            s["log"] = []
    return d


# ------------------------------------------------------------------ template
def assign_wells(doc: dict, plate: dict, wells: list, group: Optional[str], sample: str = "",
                 value: Optional[float] = None, wtype: str = "sample") -> None:
    """Template Editor 'Assign' (or 'Clear' when group is None and type is 'clear')."""
    for w in wells:
        well_rc(w)
        if wtype == "clear":
            plate["template"].pop(w, None)
        elif wtype == "plateblank":
            plate["template"][w] = {"type": "plateblank", "group": None, "sample": "BL", "value": None}
        else:
            if not group:
                raise ValueError("Choose a group first")
            ensure_group(doc, group)
            name = "BL" if wtype == "groupblank" else (sample or next_sample_name(doc, group))
            plate["template"][w] = {"type": wtype, "group": group, "sample": name, "value": value}
    if wtype == "sample" and value is not None and sample:
        _sync_sample_value(doc, group, sample, value)


def _sync_sample_value(doc, group, sample, value):
    for s in doc["sections"]:
        if s["kind"] == "plate":
            for t in s["template"].values():
                if t.get("group") == group and t.get("sample") == sample and t.get("type") == "sample":
                    t["value"] = value


def group_prefix(group: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", group)[:3] or "S"


def next_sample_name(doc: dict, group: str) -> str:
    used = set()
    for s in doc["sections"]:
        if s["kind"] == "plate":
            used.update(t["sample"] for t in s["template"].values() if t.get("group") == group)
    i = 1
    while f"{group_prefix(group)}{i:02d}" in used:
        i += 1
    return f"{group_prefix(group)}{i:02d}"


def _bump(name: str, k: int) -> str:
    m = re.search(r"(\d+)(?!.*\d)", name)
    if not m:
        return f"{name}{k + 1:02d}"
    n = int(m.group(1)) + k
    return name[:m.start()] + str(n).zfill(len(m.group(1))) + name[m.end():]


def series_fill(doc: dict, plate: dict, wells: list, group: str, first_sample: str,
                start_from: str = "top", start_value: Optional[float] = None, op: str = "*",
                step: float = 2.0) -> None:
    """Template Editor 'Series': replicate sets along rows or columns of the selection.

    ``start_from`` top/bottom: every row of the selection is one sample;
    left/right: every column is one sample.
    """
    if not wells:
        raise ValueError("Select wells first")
    ensure_group(doc, group)
    rc = sorted(well_rc(w) for w in wells)
    rows = sorted({r for r, _ in rc})
    cols = sorted({c for _, c in rc})
    if start_from in ("top", "bottom"):
        sets = [[(r, c) for c in cols if (r, c) in rc] for r in rows]
        if start_from == "bottom":
            sets.reverse()
    else:
        sets = [[(r, c) for r in rows if (r, c) in rc] for c in cols]
        if start_from == "right":
            sets.reverse()
    first_sample = first_sample or next_sample_name(doc, group)
    v = start_value
    for k, members in enumerate(s for s in sets if s):
        name = first_sample if k == 0 else _bump(first_sample, k)
        for r, c in members:
            plate["template"][well_name(r, c)] = {"type": "sample", "group": group,
                                                  "sample": name, "value": v}
        if v is not None:
            try:
                v = {"+": v + step, "-": v - step, "*": v * step, "/": v / step}[op]
            except ZeroDivisionError:
                raise ValueError("Step cannot be zero for division") from None


# ------------------------------------------------------------------ columns
def selected_columns(st: dict) -> list:
    """Columns chosen for reading (1-based).  Any set, not only a contiguous block.

    The reader only reads one contiguous block (STRIP first count), so a read
    sweeps first..last and the columns in between that were not chosen are
    dropped from the data.  ``columns`` None means the whole first..last block.
    """
    cols = st.get("columns")
    if cols:
        out = sorted({int(c) for c in cols if 1 <= int(c) <= NCOLS})
        if out:
            return out
    return list(range(int(st["first_col"]), int(st["last_col"]) + 1))


def columns_text(cols: list) -> str:
    """[1, 3, 7, 8, 9] -> '1, 3, 7-9'."""
    parts, i = [], 0
    while i < len(cols):
        j = i
        while j + 1 < len(cols) and cols[j + 1] == cols[j] + 1:
            j += 1
        parts.append(str(cols[i]) if i == j else f"{cols[i]}-{cols[j]}")
        i = j + 1
    return ", ".join(parts)


# ------------------------------------------------------------------ plate data
def empty_grid() -> list:
    return [[None] * NCOLS for _ in range(8)]


def data_from_plates(plates: list, settings: dict, setpoint: float = 0.0) -> dict:
    """Convert driver ``PlateData`` objects into the document's data block."""
    mode = settings["mode"]
    data = {"mode": mode, "wavelengths": [], "times": [], "temps": [], "sweep": [], "values": [],
            "saturated": [], "read_at": time.time(), "imported": False, "setpoint": setpoint,
            "first_col": settings["first_col"], "last_col": settings["last_col"]}
    keep = set(c - 1 for c in selected_columns(settings))
    if len(keep) < NCOLS:
        data["columns"] = sorted(c + 1 for c in keep)
    t0 = plates[0].time_s if plates else 0
    for p in plates:
        if mode == "spectrum":
            data["sweep"].append(p.wavelengths[0])
            wls = [p.wavelengths[0]]
        else:
            wls = list(p.wavelengths)
            data["wavelengths"] = wls
        data["times"].append(p.time_s - t0)
        data["temps"].append(None if math.isnan(p.temperature) else p.temperature)
        read = []
        for wl in wls:
            grid = empty_grid()
            src = p.values.get(wl) or empty_grid()
            for r in range(8):
                for c in range(NCOLS):
                    if c not in keep:
                        continue
                    v = src[r][c]
                    if v is None or (isinstance(v, float) and math.isnan(v)):
                        continue
                    if isinstance(v, float) and math.isinf(v):
                        if well_name(r, c) not in data["saturated"]:
                            data["saturated"].append(well_name(r, c))
                        continue
                    grid[r][c] = v
            read.append(grid)
        data["values"].append(read)
    return data


def append_reading(base: Optional[dict], new: dict, elapsed: float) -> dict:
    """Add one endpoint reading to earlier ones as the next time point (workflow cycles).

    The result is kinetic data, so Vmax and the other kinetic reductions apply.
    """
    if not base or not base.get("values"):
        out = dict(new, mode="kinetic", times=[float(elapsed)], temps=list(new["temps"][:1]),
                   values=[new["values"][0]], cycle=True)
        return out
    out = dict(base)
    out["values"] = base["values"] + [new["values"][0]]
    out["times"] = base["times"] + [float(elapsed)]
    out["temps"] = base["temps"] + new["temps"][:1]
    out["saturated"] = sorted(set(base.get("saturated", [])) | set(new.get("saturated", [])))
    out["mode"] = "kinetic"
    return out


def data_points(data: Optional[dict]) -> int:
    return len(data["values"]) if data else 0


def paste_grid(plate: dict, grid: list) -> None:
    """Edit > Paste of an 8x12 block of numbers: becomes imported endpoint data."""
    if len(grid) != 8 or any(len(r) != NCOLS for r in grid):
        raise ValueError("Data to paste must be 8 rows of 12 numbers")
    for row in grid:
        for v in row:
            if v is not None and not -4.0 <= v <= 4.0:
                raise ValueError("Pasted numbers must be between -4.000 and 4.000")
    wl = plate["settings"]["wavelengths"][0]
    plate["preread"] = None
    plate["data"] = {"mode": "endpoint", "wavelengths": [wl], "times": [0], "temps": [None],
                     "sweep": [], "values": [[[list(row) for row in grid]]],
                     "saturated": [], "read_at": time.time(), "imported": True, "setpoint": 0,
                     "first_col": 1, "last_col": 12}
    plate["settings"]["mode"] = "endpoint"
    plate["settings"]["wavelengths"] = [wl]


# ------------------------------------------------------------------ reduction
def _limits_ok(y, red):
    return (red.get("min_od") is None or y >= red["min_od"]) and \
           (red.get("max_od") is None or y <= red["max_od"])


def kinetic_reduce(ts: list, ys: list, red: dict) -> tuple:
    """Returns (value, overlay_line) for Vmax / Time to Vmax / Onset Time."""
    lag = red.get("lag_time") or 0
    end = red.get("end_time")
    pts = [(t, y) for t, y in zip(ts, ys) if is_num(y) and t >= lag
           and (end is None or t <= end) and _limits_ok(y, red)]
    kind = red.get("kinetic", "vmax")
    if len(pts) < 2:
        return E_LIMITS, None, pts
    if kind in ("vmax", "time_to_vmax"):
        n = red.get("vmax_points") or len(pts)
        n = max(2, min(int(n), len(pts)))
        best = None
        for i in range(len(pts) - n + 1):
            seg = pts[i:i + n]
            try:
                a, b = linreg([p[0] for p in seg], [p[1] for p in seg])
            except ValueError:
                continue
            if best is None or abs(b) > abs(best[0]) * (1 + 1e-9) + 1e-15:
                best = (b, a, seg)
        if best is None:
            return E_LIMITS, None, pts
        b, a, seg = best
        line = [[seg[0][0], a + b * seg[0][0]], [seg[-1][0], a + b * seg[-1][0]]]
        if kind == "vmax":
            return b * 60.0 * 1000.0, line, pts        # milli-OD per minute
        return (seg[0][0] + seg[-1][0]) / 2.0, line, pts
    if kind == "onset":
        target = pts[0][1] + (red.get("onset_od") or 0.1)
        line = [[pts[0][0], target], [pts[-1][0], target]]
        for (t0, y0), (t1, y1) in zip(pts, pts[1:]):
            if y0 < target <= y1:
                return t0 + (target - y0) / (y1 - y0) * (t1 - t0), line, pts
        if pts[0][1] >= target:
            return float(pts[0][0]), line, pts
        return E_RANGE, line, pts
    if kind in ("time_at_max", "time_at_min"):
        p = (max if kind == "time_at_max" else min)(pts, key=lambda q: q[1])
        return float(p[0]), [[p[0], p[1]], [p[0], p[1]]], pts
    if kind == "time_at_half_max":
        lo, hi = min(q[1] for q in pts), max(q[1] for q in pts)
        target = (lo + hi) / 2.0
        line = [[pts[0][0], target], [pts[-1][0], target]]
        for (t0, y0), (t1, y1) in zip(pts, pts[1:]):
            if (y0 - target) * (y1 - target) <= 0 and y0 != y1:
                return t0 + (target - y0) / (y1 - y0) * (t1 - t0), line, pts
        return E_RANGE, line, pts
    if kind == "auc":
        area = sum((t1 - t0) * (y0 + y1) / 2.0 for (t0, y0), (t1, y1) in zip(pts, pts[1:]))
        return area, None, pts
    if kind == "slope":
        try:
            a, b = linreg([q[0] for q in pts], [q[1] for q in pts])
        except ValueError:
            return E_LIMITS, None, pts
        return b * 60.0 * 1000.0, [[pts[0][0], a + b * pts[0][0]], [pts[-1][0], a + b * pts[-1][0]]], pts
    return None, None, pts


KINETIC_KINDS = {"vmax": ("Vmax", "mOD/min"), "time_to_vmax": ("Time to Vmax", "s"), "onset": ("Onset Time", "s"),
                 "time_at_max": ("Time at Maximum", "s"), "time_at_min": ("Time at Minimum", "s"),
                 "time_at_half_max": ("Time at 1/2 Maximum", "s"), "auc": ("Area Under Curve", "OD\u00b7s"),
                 "slope": ("Slope", "mOD/min"), "custom": ("Custom", "")}
SPECTRUM_KINDS = {"maxlambda": ("Lambda at Maximum", "nm"), "minlambda": ("Lambda at Minimum", "nm"),
                  "max_od": ("Maximum OD", "OD"), "min_od": ("Minimum OD", "OD"),
                  "auc": ("Area Under Curve", "OD\u00b7nm"), "custom": ("Custom", "")}
# Accessors available in custom kinetic formulas, computed for the well being reduced.
KIN_ACCESSORS = {"!vmax": "vmax", "!timetovmax": "time_to_vmax", "!onsettime": "onset",
                 "!timeatmax": "time_at_max", "!timeatmin": "time_at_min", "!timeathalfmax": "time_at_half_max",
                 "!auc": "auc", "!slope": "slope"}


def smooth(ys: list, n: int) -> list:
    """Centred moving average over n points (3 or 9); empty readings stay empty."""
    if not n or n < 2:
        return ys
    h = n // 2
    out = []
    for i, y in enumerate(ys):
        if not is_num(y):
            out.append(y)
            continue
        win = [v for v in ys[max(0, i - h):i + h + 1] if is_num(v)]
        out.append(sum(win) / len(win))
    return out


def vmax_correlation(ts: list, ys: list, red: dict):
    """Correlation coefficient r of the Vmax window (SoftMax Pro's Vmaxcorr)."""
    v, line, pts = kinetic_reduce(ts, ys, {**red, "kinetic": "vmax"})
    if not line:
        return E_LIMITS
    seg = [p for p in pts if line[0][0] <= p[0] <= line[1][0]]
    n = len(seg)
    if n < 2:
        return E_LIMITS
    mx = sum(p[0] for p in seg) / n
    my = sum(p[1] for p in seg) / n
    sxy = sum((p[0] - mx) * (p[1] - my) for p in seg)
    sxx = sum((p[0] - mx) ** 2 for p in seg)
    syy = sum((p[1] - my) ** 2 for p in seg)
    if sxx == 0 or syy == 0:
        return 1.0
    return sxy / math.sqrt(sxx * syy)


def kinetic_stats(ts: list, ys: list, red: dict) -> dict:
    """Vmax, its R\u00b2 and points, Time to Vmax and Onset Time for the Well Graph legend."""
    out = {}
    for kind in ("vmax", "time_to_vmax", "onset"):
        v, line, pts = kinetic_reduce(ts, ys, {**red, "kinetic": kind})
        out[kind] = v if is_num(v) else None
        if kind == "vmax" and line and pts:
            n = red.get("vmax_points") or len(pts)
            n = max(2, min(int(n), len(pts)))
            seg = [p for p in pts if line[0][0] <= p[0] <= line[1][0]]
            xs, yv = [p[0] for p in seg], [p[1] for p in seg]
            try:
                a, b = linreg(xs, yv)
                fitted = [a + b * x for x in xs]
                m = sum(yv) / len(yv)
                tot = sum((y - m) ** 2 for y in yv)
                out["r2"] = 1.0 - sum((y - f) ** 2 for y, f in zip(yv, fitted)) / tot if tot else 1.0
            except ValueError:
                out["r2"] = None
            out["points"] = f"{n}/{len(ts)}"
    return out


def spectrum_reduce(wls: list, ys: list, red: dict) -> tuple:
    lo, hi = red.get("start_nm"), red.get("end_nm")
    pts = [(w, y) for w, y in zip(wls, ys) if is_num(y) and (lo is None or w >= lo)
           and (hi is None or w <= hi) and _limits_ok(y, red)]
    if not pts:
        return E_LIMITS, pts
    kind = red.get("spectrum", "maxlambda")
    if kind == "maxlambda":
        return float(max(pts, key=lambda p: p[1])[0]), pts
    if kind == "minlambda":
        return float(min(pts, key=lambda p: p[1])[0]), pts
    if kind == "max_od":
        return float(max(p[1] for p in pts)), pts
    if kind == "min_od":
        return float(min(p[1] for p in pts)), pts
    if kind == "auc":
        return sum((w1 - w0) * (y0 + y1) / 2.0 for (w0, y0), (w1, y1) in zip(pts, pts[1:])), pts
    return None, pts


_LNAME = re.compile(r"^!?l(?:m)?([1-6])$", re.I)


class Calc:
    """One evaluation pass over a document (memoised, with cycle detection)."""

    def __init__(self, doc: dict):
        self.doc = doc
        self.memo: dict = {}
        self.busy: list = []
        self.problems: list = []

    # -- helpers --------------------------------------------------------
    def _memo(self, key, fn):
        if key in self.memo:
            return self.memo[key]
        if key in self.busy:
            self.problems.append(f"Possible circular reference in {key[-1]}.")
            return E_ERROR
        self.busy.append(key)
        try:
            v = fn()
        finally:
            self.busy.pop()
        self.memo[key] = v
        return v

    def plates(self):
        return [s for s in self.doc["sections"] if s["kind"] == "plate"]

    # -- plates ---------------------------------------------------------
    def plate(self, sid: int) -> dict:
        return self._memo(("plate", sid), lambda: self._plate(section_by_id(self.doc, sid)))

    def _plate_accessor(self, plate: dict, name: str):
        st, d = plate["settings"], plate.get("data")
        low = name.lower()
        table = {
            "!platetype": {"endpoint": 0, "kinetic": 1, "spectrum": 2}[st["mode"]],
            "!firstcolumn": st["first_col"], "!lastcolumn": st["last_col"],
            "!numwavelengths": len(st["wavelengths"]),
            "!numpoints": expected_reads(st), "!readtime": st["run_time"],
            "!readinterval": st["interval"],
            "!hastemperaturedata": bool(d and any(t is not None for t in d["temps"])),
            "!temperaturesetpoint": (d or {}).get("setpoint", 0),
            "!premixon": bool(st["mix_before"]), "!premixduration": st["mix_before_s"],
            "!premixlevel": 1, "!intervalmixon": bool(st["mix_between"]),
            "!intervalmixduration": st["mix_between_s"], "!intervalmixlevel": 1,
            "!numpointsread": data_points(d),
            "!numwavelengthsread": len(d["wavelengths"]) if d else 0,
            "!autocalibrateon": bool(st["autocalibrate"]), "!startsweep": st["start"],
            "!endsweep": st["stop"], "!stepsweep": st["step"], "!prereadplate": bool(st["pre_read"]),
            "!readtimes": [float(t) for t in d["times"]] if d else [],
            "!timerun": [float(t) for t in d["times"]] if d else [],
            "!sweepwavelengths": [float(w) for w in d["sweep"]] if d else [],
            "!platename": plate["name"],
        }
        if low in table:
            v = table[low]
            return float(v) if is_num(v) else v
        return None

    def _plate(self, plate: dict) -> dict:
        d = plate.get("data")
        red = plate["reduction"]
        st = plate["settings"]
        out = {"wells": {}, "xs": [], "blank": None, "unit": ""}
        if not d or not d["values"]:
            return out
        mode = d["mode"]
        nw = 1 if mode == "spectrum" else len(d["wavelengths"])
        xs = [float(w) for w in d["sweep"]] if mode == "spectrum" else [float(t) for t in d["times"]]
        out["xs"] = xs
        pre = plate.get("preread") if (st.get("pre_read") and red.get("use_preread", True)) else None
        masked = set(plate.get("mask") or [])
        tmpl = plate["template"]

        transmittance = red.get("raw_mode") == "transmittance"

        def raw(r, c):
            """per wavelength: scalar (endpoint) or list over reads"""
            res = []
            for w in range(nw):
                if mode == "spectrum":
                    series = [d["values"][k][0][r][c] for k in range(len(d["values"]))]
                else:
                    series = [d["values"][k][w][r][c] if w < len(d["values"][k]) else None
                              for k in range(len(d["values"]))]
                if pre and pre.get("values") and mode != "spectrum" and w < len(pre["values"][0]):
                    p = pre["values"][0][w][r][c]
                    if p is not None:
                        series = [None if v is None else v - p for v in series]
                res.append(series)
            return res

        def tr(v):
            """OD -> % transmittance, applied after blanks (which are subtracted in OD)."""
            if not transmittance or not is_num(v):
                return v
            try:
                return 100.0 * 10.0 ** (-v)
            except OverflowError:
                return E_RANGE

        def combine(lists, r, c):
            vals = [[EMPTY if v is None else v for v in s] for s in lists]
            if mode == "endpoint":
                vals = [s[0] if s else EMPTY for s in vals]

            def resolve(name, section):
                m = _LNAME.match(name)
                if m and section is None:
                    i = int(m.group(1)) - 1
                    return vals[i] if i < len(vals) else E_NAME
                acc = self._plate_accessor(plate, name)
                return E_NAME if acc is None else acc
            formula = "!L1" if mode == "spectrum" else (red.get("wavelength") or "!L1")
            try:
                return Evaluator(resolve).eval(formula)
            except FormulaError:
                return E_ERROR if mode == "endpoint" else [E_ERROR] * len(xs)

        combined, raws = {}, {}
        for r in range(8):
            for c in range(NCOLS):
                lists = raw(r, c)
                if all(v is None for s in lists for v in s):
                    continue
                w = well_name(r, c)
                raws[w] = lists
                v = combine(lists, r, c)
                if mode != "endpoint" and not isinstance(v, list):
                    v = [v] * len(xs)              # e.g. a formula that returns one number
                elif mode == "endpoint" and isinstance(v, list):
                    v = E_ERROR                    # an endpoint formula must give one value
                combined[w] = v
        # saturated wells have no number but must stay visible (as Limits?)
        for w in d.get("saturated") or []:
            if w not in combined and w in ALL_WELLS:
                combined[w] = E_LIMITS if mode == "endpoint" else [E_LIMITS] * len(xs)
                raws[w] = [[None] * len(xs) for _ in range(nw)]

        # plate blank
        blank = None
        bl = [w for w, t in tmpl.items() if t.get("type") == "plateblank" and w in combined
              and w not in masked]
        if red.get("plate_blank", True) and bl:
            if mode == "endpoint":
                nums = [combined[w] for w in bl if is_num(combined[w])]
                blank = sum(nums) / len(nums) if nums else None
            else:
                blank = []
                for k in range(len(xs)):
                    nums = [combined[w][k] for w in bl if is_num(combined[w][k])]
                    blank.append(sum(nums) / len(nums) if nums else 0.0)
        out["blank"] = blank if mode == "endpoint" else (sum(blank) / len(blank) if blank else None)

        def custom(formula, pts, w, ys=None):
            def resolve(name, section):
                low = name.lower()
                if ys is not None and mode == "kinetic" and low in KIN_ACCESSORS:
                    v = kinetic_reduce(xs, ys, {**red, "kinetic": KIN_ACCESSORS[low]})[0]
                    return v if v is not None else E_ERROR
                if ys is not None and mode == "kinetic" and low == "!vmaxcorr":
                    return vmax_correlation(xs, ys, red)
                if low in ("!kinplot", "!spectrumplot", "!wellplot"):
                    return [p[1] for p in pts]
                if low in ("!readtimes", "!timerun", "!sweepwavelengths"):
                    return [float(p[0]) for p in pts]
                m = _LNAME.match(name)
                if m:
                    i = int(m.group(1)) - 1
                    s = raws[w][i] if i < len(raws[w]) else []
                    return [EMPTY if v is None else v for v in s]
                acc = self._plate_accessor(plate, name)
                return E_NAME if acc is None else acc
            try:
                v = Evaluator(resolve).eval(formula)
            except FormulaError:
                return E_ERROR
            return v if not isinstance(v, list) else (v[0] if len(v) == 1 else E_ERROR)

        for w, val in combined.items():
            info: dict = {"raw": raws[w]}
            if transmittance:
                info["raw"] = [[tr(v) if v is not None else None for v in ser] for ser in raws[w]]
            if mode == "endpoint":
                v = val
                if is_num(v) and blank is not None:
                    v = v - blank
                info["value"] = tr(v)
            else:
                ys = [y - blank[k] if (is_num(y) and blank is not None and k < len(blank)) else y
                      for k, y in enumerate(val)]
                ys = [tr(y) for y in ys]
                ys = smooth(ys, int(red.get("smoothing") or 0))
                if red.get("zero_first") and mode == "kinetic":
                    first = next((y for y in ys if is_num(y)), None)
                    if first is not None:
                        ys = [y - first if is_num(y) else y for y in ys]
                info["ys"] = [y if is_num(y) else None for y in ys]
                if mode == "kinetic":
                    v, line, pts = kinetic_reduce(xs, ys, red)
                    if v is None:
                        v = custom(red.get("kinetic_formula") or "Average(!KinPlot)", pts, w, ys)
                    info["line"] = line if line else kinetic_reduce(xs, ys, {**red, "kinetic": "vmax"})[1]
                    info["stats"] = kinetic_stats(xs, ys, red)
                else:
                    v, pts = spectrum_reduce(xs, ys, red)
                    if v is None:
                        v = custom(red.get("spectrum_formula") or "Max(!KinPlot)", pts, w)
                    hi = max(pts, key=lambda p: p[1]) if pts else None
                    lo = min(pts, key=lambda p: p[1]) if pts else None
                    info["stats"] = {"lambda_max": hi[0] if hi else None, "od_max": hi[1] if hi else None,
                                     "lambda_min": lo[0] if lo else None, "od_min": lo[1] if lo else None}
                info["value"] = v
            if w in masked:
                info["value"] = E_MASKED
            elif w in (d.get("saturated") or []) and not is_num(info["value"]):
                info["value"] = E_LIMITS
            out["wells"][w] = info
        if mode == "kinetic":
            out["unit"] = KINETIC_KINDS.get(red["kinetic"], ("", ""))[1]
        elif mode == "spectrum":
            out["unit"] = SPECTRUM_KINDS.get(red["spectrum"], ("", ""))[1]
        elif transmittance and red.get("wavelength", "!L1").strip().upper() in ("!L1", "!L2", "!L3", "!L4", "!L5", "!L6"):
            out["unit"] = "%T"
        return out

    def well_value(self, plate: dict, well: str):
        info = self.plate(plate["id"])["wells"].get(well)
        return info["value"] if info else EMPTY

    # -- groups ---------------------------------------------------------
    def samples(self, gname: str) -> list:
        def build():
            by: dict = {}
            blanks = []
            for p in self.plates():
                for w in COLUMN_MAJOR:
                    t = p["template"].get(w)
                    if not t or t.get("group") != gname:
                        continue
                    if t.get("type") == "groupblank":
                        blanks.append((p, w))
                        continue
                    e = by.setdefault(t["sample"], {"name": t["sample"], "value": None, "wells": []})
                    e["wells"].append((p, w))
                    if e["value"] is None and t.get("value") is not None:
                        e["value"] = t["value"]
            nums = [v for v in (self.well_value(p, w) for p, w in blanks) if is_num(v)]
            blank = sum(nums) / len(nums) if nums else None
            return {"samples": sorted(by.values(), key=lambda e: natural_key(e["name"])),
                    "blank": blank, "blank_values": nums}
        return self._memo(("samples", gname), build)

    def _group_accessor(self, gname: str, name: str):
        g = self.doc["groups"][gname]
        info = self.samples(gname)
        ss = info["samples"]
        low = name.lower()
        if low == "!wellvalues":
            b = info["blank"]
            return [[(v - b if (is_num(v) and b is not None) else v)
                     for v in (self.well_value(p, w) for p, w in s["wells"])] for s in ss]
        if low == "!wellids":
            return [[w for _, w in s["wells"]] for s in ss]
        if low == "!wellnum":
            return [[float(ALL_WELLS.index(w) + 1) for _, w in s["wells"]] for s in ss]
        if low in ("!samplenames", "!samplename"):
            return [s["name"] for s in ss]
        if low in ("!concentration", "!factor", "!constant", "!sampledescriptor"):
            return [EMPTY if s["value"] is None else float(s["value"]) for s in ss]
        if low == "!units":
            return [g.get("units", "") for _ in ss]
        if low == "!platename":
            return [[p["name"] for p, _ in s["wells"]] for s in ss]
        if low == "!groupblank":
            return EMPTY if info["blank"] is None else info["blank"]
        if low == "!groupblankvalues":
            return list(info["blank_values"])
        return None

    def _find(self, items: list, name: str):
        low = name.lower()
        for it in items:
            if it["name"].lower() == low:
                return it
        return None

    def column(self, gname: str, cname: str):
        g = self.doc["groups"][gname]
        col = self._find(g["columns"], cname)
        if col is None:
            return E_NAME

        def run():
            try:
                return Evaluator(self.resolver(("group", gname))).eval(col["formula"])
            except FormulaError as e:
                self.problems.append(f"{gname} / {col['name']}: {e}")
                return E_ERROR
        return self._memo(("column", gname, col["name"]), run)

    def summary(self, section: dict, sname: str):
        items = self.doc["groups"][section["group"]]["summaries"] if section["kind"] == "group" \
            else section.get("summaries", [])
        s = self._find(items, sname)
        if s is None:
            return E_NAME
        ctx = ("group", section["group"]) if section["kind"] == "group" else ("section", section["id"])

        def run():
            try:
                return Evaluator(self.resolver(ctx)).eval(s["formula"])
            except FormulaError as e:
                self.problems.append(f"{section['name']} / {s['name']}: {e}")
                return E_ERROR
        return self._memo(("summary", section["id"], s["name"]), run)

    # -- graphs ---------------------------------------------------------
    def plot(self, graph: dict, pname: str):
        p = self._find(graph["plots"], pname)
        if p is None:
            return E_NAME
        return self._memo(("plot", graph["id"], p["name"]), lambda: self._plot(graph, p))

    def _plot(self, graph: dict, p: dict) -> dict:
        res = {"name": p["name"], "group": p.get("group"), "x": p.get("x"), "y": p.get("y"),
               "points": [], "fit": None, "plot": Plot(None, p["name"])}
        g = p.get("group")
        if g not in self.doc["groups"] or not p.get("x") or not p.get("y"):
            return res
        xv, yv = self.column(g, p["x"]), self.column(g, p["y"])
        pairs = flatten(lift2(lambda a, b: (a, b), xv, yv)) if (isinstance(xv, list) or isinstance(yv, list)) else [(xv, yv)]

        def errs(cname, n):
            if not cname:
                return [None] * n
            ev = self.column(g, cname)
            flat = flatten(lift2(lambda a, b: b, yv if depth(yv) >= depth(xv) else xv, ev)) \
                if isinstance(ev, list) else [ev] * n
            return [(e if is_num(e) else None) for e in flat] + [None] * n
        ex, ey = errs(p.get("xerr"), len(pairs)), errs(p.get("yerr"), len(pairs))
        for i, pr in enumerate(pairs):
            if isinstance(pr, tuple) and is_num(pr[0]) and is_num(pr[1]):
                res["points"].append([float(pr[0]), float(pr[1]), ex[i], ey[i]])
        if graph.get("fit", "none") != "none":
            opt = {**default_fit_options(), **(graph.get("fit_options") or {})}
            pts = res["points"]
            weights = None
            wmode = opt["weighting"]
            if wmode != "none":
                def wt(q):
                    x, y, _, sd = q
                    v = {"1/y": y, "1/y2": y * y, "1/x": x, "1/x2": x * x,
                         "1/sd2": (sd * sd if sd else None)}.get(wmode)
                    return 1.0 / v if v and v > 0 else None
                weights = [wt(q) for q in pts]
            f = fits.fit(graph["fit"], [q[0] for q in pts], [q[1] for q in pts], weights=weights,
                         fixed={k: float(v) for k, v in (opt.get("fixed") or {}).items() if is_num(v)},
                         seeds={k: float(v) for k, v in (opt.get("seeds") or {}).items() if is_num(v)},
                         conf=float(opt.get("conf") or 0.95))
            if wmode == "1/sd2" and not any(q[3] for q in pts):
                f.ok = False
                f.error = "1/SD\u00b2 weighting needs Y error bars (choose them under Plots)"
            res["fit"] = f
            res["plot"] = Plot(f, p["name"])
        return res

    # -- name resolution -------------------------------------------------
    def resolver(self, ctx):
        def in_section(sec: dict, name: str):
            if sec["kind"] == "group":
                gname = sec["group"]
                if name.startswith("!"):
                    return self._group_accessor(gname, name)
                g = self.doc["groups"][gname]
                if self._find(g["columns"], name):
                    return self.column(gname, name)
                if self._find(g["summaries"], name):
                    return self.summary(sec, name)
            elif sec["kind"] == "graph":
                if self._find(sec["plots"], name):
                    return self.plot(sec, name)["plot"]
            elif sec["kind"] == "plate":
                if name.startswith("!"):
                    m = re.fullmatch(r"!([A-Ha-h]\d{1,2})(?:values?)?", name)
                    if m:
                        try:
                            well_rc(m.group(1))
                        except ValueError:
                            return None
                        return self.well_value(sec, m.group(1).upper())
                    if name.lower() == "!wellvalues":
                        return [self.well_value(sec, w) for w in ALL_WELLS]
                    return self._plate_accessor(sec, name)
                if self._find(sec.get("summaries", []), name):
                    return self.summary(sec, name)
            elif sec["kind"] == "notes":
                if self._find(sec.get("summaries", []), name):
                    return self.summary(sec, name)
            return None

        def resolve(name: str, section: Optional[str]):
            if section is not None:
                sec = section_by_name(self.doc, section)
                if sec is None:
                    return E_NAME
                v = in_section(sec, name)
                return E_NAME if v is None else v
            home = None
            if ctx[0] == "group":
                home = section_by_name(self.doc, ctx[1])
            elif ctx[0] == "section":
                home = section_by_id(self.doc, ctx[1])
            if home is not None:
                v = in_section(home, name)
                if v is not None:
                    return v
            if not name.startswith("!"):
                for sec in self.doc["sections"]:
                    if sec is home:
                        continue
                    v = in_section(sec, name)
                    if v is not None:
                        return v
            elif home is None or home["kind"] != "plate":
                for sec in self.plates():
                    v = in_section(sec, name)
                    if v is not None:
                        return v
            return E_NAME
        return resolve


def settings_summary(st: dict) -> str:
    """One line describing instrument settings, for logs."""
    if st["mode"] == "spectrum":
        parts = [f"spectrum {st['start']}-{st['stop']} nm step {st['step']}"]
    else:
        parts = [st["mode"], "/".join(str(w) for w in st["wavelengths"]) + " nm"]
        if st["mode"] == "kinetic":
            parts.append(f"run {st['run_time']} s every {st['interval']} s")
    parts.append(f"columns {columns_text(selected_columns(st))}")
    if st.get("mix_before"):
        parts.append(f"mix {st['mix_before_s']} s before")
    if st["mode"] == "kinetic" and st.get("mix_between"):
        parts.append(f"mix {st['mix_between_s']} s between")
    parts.append("calibrate on" if st.get("autocalibrate") else "calibrate off")
    return ", ".join(parts)


def read_seconds(st: dict) -> int:
    """Time one pass over the plate takes, as SOFTmax PRO estimates it (SPF.EXE 68:30ce).

    Uses the host's default timing constants: 400 ms per strip, 225 ms per strip
    move, 5 ms per nm of wavelength change, 500 ms per wavelength pass, 2 s minimum.
    """
    wls = [int(str(w).lstrip("Ff") or 0) for w in st["wavelengths"]] or [405]
    if st["mode"] == "spectrum":
        wls = [st["start"]]
    w = len(wls)
    strips = max(1, st["last_col"] - st["first_col"] + 1)
    move = sum(abs(a - b) for a, b in zip(wls, wls[1:] + wls[:1]))
    ms = move * 5 + 2 * (w - 1) * 225 + w * (strips * 400 + 500 + 1.25 * (strips - 1) * 225)
    if st.get("autocalibrate"):
        ms += move * 5 + w * (400 + 500) + 4 * 225 + (st["first_col"] - 1) * 225
    sec = math.ceil(ms / 1000.0)
    if st["mode"] == "kinetic" and st.get("mix_between"):
        sec += int(st.get("mix_between_s") or 0)
    return max(sec, 2)


def expected_reads(st: dict) -> int:
    if st["mode"] == "kinetic":
        if not st["interval"] or not st["run_time"]:
            return 2
        return int(st["run_time"] // st["interval"]) + 1
    if st["mode"] == "spectrum":
        return (st["stop"] - st["start"]) // max(1, st["step"]) + 1
    return 1


def fmt_value(v, decimals: Optional[int] = None) -> str:
    if isinstance(v, list):
        return ", ".join(fmt_value(x, decimals) for x in v[:50])
    if is_num(v):
        return f"{v:.{3 if decimals is None else decimals}f}"
    return to_text(v)


def fmt_hms(seconds: float) -> str:
    s = int(round(seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


# ---------------------------------------------------------------------- view
def compute(doc: dict) -> dict:
    """Evaluate the whole document for display."""
    calc = Calc(doc)
    out = []
    for s in doc["sections"]:
        v: dict = {k: s[k] for k in ("id", "kind", "name", "report", "open")}
        try:
            if s["kind"] == "plate":
                v.update(_plate_view(calc, s))
            elif s["kind"] == "group":
                v.update(_group_view(calc, s))
            elif s["kind"] == "graph":
                v.update(_graph_view(calc, s))
            elif s["kind"] == "workflow":
                v.update(steps=s.get("steps", []), log=s.get("log", [])[-300:])
            else:
                v.update(html=s.get("html", ""), show_formulas=s.get("show_formulas", False))
            if s["kind"] != "group":
                v["summaries"] = _summaries(calc, s, s.get("summaries", []))
        except Exception as e:  # never let one section take the whole window down
            calc.problems.append(f"{s['name']} could not be calculated ({type(e).__name__}: {e}). "
                                 "Undo the last change or check its formulas.")
            v = _broken_view(s, v)
        out.append(v)
    problems = []
    for p in calc.problems:
        if p not in problems:
            problems.append(p)
    return {"sections": out, "problems": problems,
            "groups": {n: {"name": n, "descriptor": g["descriptor"], "units": g["units"],
                           "color": g["color"], "columns": [c["name"] for c in g["columns"]]}
                       for n, g in doc["groups"].items()}}


def _broken_view(s: dict, v: dict) -> dict:
    """Minimal view of a section whose calculation failed, so the window still draws."""
    base = {"summaries": []}
    if s["kind"] == "plate":
        base.update(settings=s["settings"], columns=selected_columns(s["settings"]),
                    columns_text=columns_text(selected_columns(s["settings"])), template=s["template"],
                    mask=s.get("mask", []), reduction=s["reduction"], display=s["display"],
                    has_data=bool(s.get("data")), has_preread=bool(s.get("preread")), info={}, wells={},
                    xs=[], unit="", blank=None, expected=expected_reads(s["settings"]))
    elif s["kind"] == "group":
        base.update(group=s["group"], descriptor="", units="", color="#999", columns=[], rows=[],
                    hide_replicates=False, show_formulas=False, blank=None)
    elif s["kind"] == "graph":
        base.update(plots=[], fit=s.get("fit", "none"), type=s.get("type", "scatter"), connect=False,
                    symbols=True, height=s.get("height", 100), xaxis=s["xaxis"], yaxis=s["yaxis"],
                    fit_options=default_fit_options(), param_names={})
    elif s["kind"] == "workflow":
        base.update(steps=s.get("steps", []), log=s.get("log", []))
    else:
        base.update(html=s.get("html", ""), show_formulas=False)
    return {**v, **base}


def _summaries(calc: Calc, section: dict, items: list) -> list:
    res = []
    for s in items:
        val = calc.summary(section, s["name"])
        res.append({"name": s["name"], "description": s.get("description", ""),
                    "formula": s["formula"], "decimals": s.get("decimals", 3),
                    "hide_name": s.get("hide_name", False), "style": s.get("style", {}),
                    "text": fmt_value(val, s.get("decimals", 3))})
    return res


def _plate_view(calc: Calc, s: dict) -> dict:
    res = calc.plate(s["id"])
    d = s.get("data")
    wells = {}
    for w, info in res["wells"].items():
        val = info["value"]
        e = {"num": val if is_num(val) else None, "text": fmt_value(val)}
        if d["mode"] == "endpoint":
            e["raw"] = [x[0] if x else None for x in info["raw"]]
        else:
            e["raw"] = info["raw"]
            e["ys"] = info.get("ys")
            if info.get("line"):
                e["line"] = info["line"]
            if info.get("stats"):
                e["stats"] = info["stats"]
        wells[w] = e
    info = {}
    if d:
        temps = [t for t in d["temps"] if t is not None]
        info = {"mode": d["mode"], "points": len(d["values"]), "expected": expected_reads(s["settings"])
                if not d.get("imported") else len(d["values"]),
                "wavelengths": d["wavelengths"], "sweep": d["sweep"], "read_at": d.get("read_at"),
                "imported": d.get("imported", False), "setpoint": d.get("setpoint", 0),
                "mean_temp": sum(temps) / len(temps) if temps else None,
                "min_temp": min(temps) if temps else None, "max_temp": max(temps) if temps else None,
                "saturated": d.get("saturated", [])}
    return {"settings": s["settings"], "columns": selected_columns(s["settings"]),
            "columns_text": columns_text(selected_columns(s["settings"])),
            "template": s["template"], "mask": s.get("mask", []),
            "reduction": s["reduction"], "display": s["display"], "has_data": bool(d),
            "has_preread": bool(s.get("preread")), "info": info, "wells": wells,
            "xs": res["xs"], "unit": res["unit"], "blank": res["blank"],
            "expected": expected_reads(s["settings"])}


def _group_view(calc: Calc, s: dict) -> dict:
    gname = s["group"]
    g = calc.doc["groups"][gname]
    info = calc.samples(gname)
    ss = info["samples"]
    cols = []
    values = []
    for c in g["columns"]:
        v = calc.column(gname, c["name"])
        values.append(v)
        per_well = depth(v) >= 2
        cols.append({"name": c["name"], "formula": c["formula"], "decimals": c.get("decimals"),
                     "hidden": c.get("hidden", False), "style": c.get("style", {}),
                     "per_well": per_well})
    rows = []
    for si, smp in enumerate(ss):
        n = 1 if g.get("hide_replicates") else max(1, len(smp["wells"]))
        for j in range(n):
            cells = []
            for c, v in zip(g["columns"], values):
                if depth(v) >= 2:
                    item = v[si][j] if si < len(v) and isinstance(v[si], list) and j < len(v[si]) else EMPTY
                    cells.append("" if g.get("hide_replicates") else fmt_value(item, c.get("decimals")))
                elif isinstance(v, list):
                    cells.append(fmt_value(v[si] if si < len(v) else EMPTY, c.get("decimals")) if j == 0 else "")
                else:
                    cells.append(fmt_value(v, c.get("decimals")) if j == 0 else "")
            rows.append({"sample": si, "first": j == 0, "cells": cells})
    return {"group": gname, "descriptor": g["descriptor"], "units": g["units"], "color": g["color"],
            "columns": cols, "rows": rows, "hide_replicates": g.get("hide_replicates", False),
            "show_formulas": g.get("show_formulas", False), "blank": info["blank"],
            "summaries": _summaries(calc, s, g["summaries"])}


def _graph_view(calc: Calc, s: dict) -> dict:
    plots = []
    for p in s["plots"]:
        r = calc.plot(s, p["name"])
        f = r["fit"]
        xs = [q[0] for q in r["points"]]
        xmin, xmax = (min(xs), max(xs)) if xs else (0.0, 1.0)
        ax = s["xaxis"]
        if not ax["auto"]:
            xmin, xmax = min(xmin, ax["min"]), max(xmax, ax["max"])
        if ax["log"] or s.get("fit") in ("semilog", "loglog", "4param"):
            pos = [x for x in xs if x > 0]
            xmin = max(xmin, min(pos) / 2 if pos else 1e-6)
        if s.get("fit") == "mm":
            xmin = min(xmin, 0.0) if not ax["log"] else xmin
        opt = {**default_fit_options(), **(s.get("fit_options") or {})}
        plots.append({"name": p["name"], "group": p.get("group"), "x": p.get("x"), "y": p.get("y"),
                      "xerr": p.get("xerr"), "yerr": p.get("yerr"), "points": r["points"],
                      "color": calc.doc["groups"].get(p.get("group"), {}).get("color", "#4f8ad9"),
                      "fit": f.as_dict() if f else None,
                      "curve": fits.curve_points(f, xmin, xmax, ax["log"], bands=bool(opt["bands"])) if f else []})
    return {"plots": plots, "fit": s.get("fit", "none"), "type": s.get("type", "scatter"),
            "fit_options": {**default_fit_options(), **(s.get("fit_options") or {})},
            "param_names": fits.PARAM_NAMES.get(s.get("fit", "none"), {}),
            "connect": s.get("connect", False), "symbols": s.get("symbols", True),
            "height": s.get("height", 100), "xaxis": s["xaxis"], "yaxis": s["yaxis"]}


def validate_formula(doc: dict, context_section: Optional[int], formula: str) -> Optional[str]:
    """Message to show under a formula box, or None when it evaluates."""
    from .formula import parse
    try:
        parse(formula)
    except FormulaError as e:
        return str(e)
    calc = Calc(doc)
    sec = section_by_id(doc, context_section) if context_section else None
    ctx = ("none",)
    if sec is not None:
        ctx = ("group", sec["group"]) if sec["kind"] == "group" else ("section", sec["id"])
    try:
        v = Evaluator(calc.resolver(ctx)).eval(formula)
    except FormulaError as e:
        return str(e)
    if E_NAME in flatten(v):
        return "Unknown identifier"
    return None


# ------------------------------------------------------- old SOFTmax PRO files
def document_from_legacy(info: dict) -> dict:
    """Build a document from ``odstudio.legacy.read_pda`` output (a .pda or .ppr file)."""
    import html as _html

    doc = {"format": FORMAT, "version": VERSION, "next_id": 1, "sections": [], "groups": {},
           "created": time.time()}
    used = set()
    notices: list = []

    def unique(name: str, base: str) -> str:
        name = (name or "").strip() or base
        cand, k = name, 2
        while cand.lower() in used:
            cand = f"{name} ({k})"
            k += 1
        used.add(cand.lower())
        return cand

    group_names: dict = {}

    def group(name: str, spec: Optional[dict] = None) -> str:
        if not name:
            return ""
        if name in group_names:
            return group_names[name]
        real = unique(name, "Group")
        spec = spec or {}
        g = ensure_group(doc, real, "", spec.get("descriptor"), spec.get("units"))
        if spec.get("columns"):
            g["columns"] = [{"name": c.get("name") or f"Col#{i + 1}", "formula": c.get("formula") or "",
                             "decimals": c.get("decimals"), "hidden": bool(c.get("hidden")), "style": {}}
                            for i, c in enumerate(spec["columns"]) if c.get("formula")] or g["columns"]
        g["summaries"].extend(_legacy_summaries(spec.get("summaries"), g["summaries"]))
        group_names[name] = real
        return real

    specs = {g.get("name"): g for g in info.get("groups") or [] if g.get("name")}
    for i, p in enumerate(info.get("plates") or []):
        s = add_section(doc, "plate")
        s["name"] = unique(p.get("name"), f"Plate#{i + 1}")
        base = default_settings()
        s["settings"] = {**base, **{k: v for k, v in (p.get("settings") or {}).items() if v is not None and k in base}}
        st = s["settings"]
        st["first_col"] = max(1, min(12, int(st["first_col"])))
        st["last_col"] = max(st["first_col"], min(12, int(st["last_col"])))
        st["columns"] = None
        if p.get("data"):
            data = dict(p["data"])
            data.setdefault("saturated", [])
            data.setdefault("imported", True)
            data.setdefault("setpoint", 0)
            data.setdefault("read_at", 0)
            data.setdefault("first_col", st["first_col"])
            data.setdefault("last_col", st["last_col"])
            data.setdefault("sweep", [])
            n = len(data.get("values") or [])
            data["times"] = list(data.get("times") or [0] * n)[:n] + [0] * max(0, n - len(data.get("times") or []))
            data["temps"] = list(data.get("temps") or [None] * n)[:n] + [None] * max(0, n - len(data.get("temps") or []))
            if data.get("mode") != "spectrum" and not data.get("wavelengths") and n:
                data["wavelengths"] = list(st["wavelengths"])[:len(data["values"][0])]
            s["data"] = data if n else None
        if p.get("preread") and (p["preread"].get("values") or []):
            pre = dict(p["preread"])
            n = len(pre["values"])
            pre.setdefault("saturated", [])
            pre.update(mode="endpoint", imported=True)
            pre.setdefault("setpoint", 0)
            pre.setdefault("read_at", 0)
            pre.setdefault("first_col", st["first_col"])
            pre.setdefault("last_col", st["last_col"])
            pre.setdefault("sweep", [])
            pre["times"] = [0] * n
            pre["temps"] = (list(pre.get("temps") or []) + [None] * n)[:n]
            s["preread"] = pre
        red = p.get("reduction") or {}
        sr = s["reduction"]

        def lnames(text: str) -> str:
            return re.sub(r"(?<![!\w])L([1-6])\b", r"!L\1", text)

        choice = red.get("wavelength_choice")
        if choice:
            # decoded CSCalcPlateBody: menu choice + custom formulas
            wl_name = (red.get("wavelength_name") or "").strip()
            wl_formula = (red.get("wavelength_formula") or "").strip()
            if st["mode"] == "spectrum":
                if choice in ("maxlambda", "minlambda"):
                    sr["spectrum"] = choice
                elif choice == "custom" and wl_formula:
                    sr["spectrum"] = "custom"
                    sr["spectrum_formula"] = lnames(wl_formula)
            elif choice == "custom" and wl_formula:
                sr["wavelength"] = lnames(wl_formula)
            elif choice == "named" and wl_name and wl_name.lower() != "custom":
                sr["wavelength"] = lnames(wl_name)
            if st["mode"] == "kinetic":
                if red.get("kinetic") in KINETIC_KINDS:
                    sr["kinetic"] = red["kinetic"]
                if (red.get("kinetic_formula") or "").strip():
                    sr["kinetic_formula"] = red["kinetic_formula"].strip()
                for key in ("lag_time", "end_time", "min_od", "max_od", "onset_od"):
                    if isinstance(red.get(key), (int, float)):
                        sr[key] = red[key]
                vp = red.get("vmax_points")
                sr["vmax_points"] = int(vp) if isinstance(vp, int) and vp >= 2 else None
                if red.get("relative_od") is not None:
                    sr["zero_first"] = bool(red["relative_od"])
            if st["mode"] == "spectrum":
                for key in ("start_nm", "end_nm"):
                    if isinstance(red.get(key), (int, float)) and red[key] > 0:
                        sr[key] = red[key]
            if red.get("plate_blank") is not None:
                sr["plate_blank"] = bool(red["plate_blank"])
            if red.get("subtract_preread") is not None:
                sr["use_preread"] = bool(red["subtract_preread"])
        else:
            # only the formula strings are known
            combo = (red.get("wavelength_combination") or "").strip()
            if combo and combo.lower() != "custom":
                sr["wavelength"] = lnames(combo)
            elif combo.lower() == "custom" and red.get("formula"):
                sr["wavelength"] = red["formula"]
            if red.get("formula") and combo.lower() != "custom":
                sr["kinetic_formula"] = red["formula"]
                sr["spectrum_formula"] = red["formula"]
        disp = p.get("display") or {}
        dtype = disp.get("type")
        if dtype in ("raw", "number", "plots", "gray", "ranged", "threshold"):
            if dtype == "plots" and st["mode"] == "endpoint":
                dtype = "number"
            s["display"]["view"] = "raw" if dtype == "raw" else "reduced"
            if dtype != "raw":
                s["display"]["type"] = dtype
            low, high = disp.get("low"), disp.get("high")
            if isinstance(low, (int, float)) and isinstance(high, (int, float)) and high > low:
                s["display"]["low"], s["display"]["high"] = float(low), float(high)
        for well, t in (p.get("template") or {}).items():
            try:
                well_rc(well)
            except ValueError:
                continue
            kind = t.get("type") or "sample"
            if kind == "plateblank":
                s["template"][well] = {"type": "plateblank", "group": None, "sample": "BL", "value": None}
                continue
            gname = group(t.get("group") or "", specs.get(t.get("group")))
            if not gname:
                continue
            s["template"][well] = {"type": kind if kind in ("sample", "groupblank") else "sample", "group": gname,
                                   "sample": t.get("sample") or ("BL" if kind == "groupblank" else well),
                                   "value": t.get("value")}
        s["mask"] = [w for w in p.get("mask") or [] if w in ALL_WELLS]
    for name, spec in specs.items():
        group(name, spec)
    for n in info.get("notes") or []:
        s = add_section(doc, "notes")
        s["name"] = unique(n.get("name"), "Notes")
        text = n.get("text") or ""
        s["html"] = "<br>".join(_html.escape(line) for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
        s["summaries"] = _legacy_summaries(n.get("calculations") or n.get("summaries"), [])
    for gph in info.get("graphs") or []:
        if not isinstance(gph, dict):
            continue
        s = add_section(doc, "graph")
        s["name"] = unique(gph.get("name"), "Graph")
        fit = str(gph.get("fit") or "").lower().replace("-", "").replace(" ", "")
        s["fit"] = {"linear": "linear", "semilog": "semilog", "loglog": "loglog", "quadratic": "quadratic",
                    "4parameter": "4param", "4param": "4param", "fourparameter": "4param", "nofit": "none",
                    "none": "none"}.get(fit, "linear")
        if not fit:
            notices.append(f"the curve fit of {s['name']} is not stored in old files; it was set to Linear")
        for k, pl in enumerate(gph.get("plots") or []):
            if not isinstance(pl, dict):
                continue
            grp = pl.get("group")
            axes = {}
            for key in ("x", "y", "xerr", "yerr"):
                ref = (pl.get(key) or "").strip()
                if "@" in ref:
                    col, _, g2 = ref.partition("@")
                    grp = grp or g2.strip()
                    ref = col.strip()
                axes[key] = ref.strip("'") or None
            grp = group_names.get(grp, grp)
            s["plots"].append({"name": pl.get("name") or f"Plot#{k + 1}", "group": grp, **axes})
        if gph.get("x_label"):
            s["xaxis"]["label"] = gph["x_label"]
        if gph.get("y_label"):
            s["yaxis"]["label"] = gph["y_label"]
    if not any(s["kind"] == "plate" for s in doc["sections"]):
        add_section(doc, "plate")
    doc["import_notices"] = notices
    return doc


def _legacy_summaries(items, existing) -> list:
    out = []
    names = {x["name"].lower() for x in existing}
    for sm in items or []:
        label = sm.get("label") or sm.get("description") or ""
        formula = (sm.get("formula") or "").strip()
        name = (sm.get("name") or "").strip() or f"Summary#{len(existing) + len(out) + 1}"
        while name.lower() in names:
            name += "_"
        names.add(name.lower())
        dec = sm.get("decimals")
        out.append({"name": name, "formula": formula or '""', "description": label,
                    "decimals": 3 if dec is None else int(dec),
                    "hide_name": bool(sm.get("hide_name")) or not formula, "style": {}})
    return out
