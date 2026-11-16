"""Reader for SOFTmax PRO 1.x (SPF.EXE, 1994-95) data (.pda) and protocol (.ppr) files.

Pure Python, no dependencies.  The layout was reverse engineered from SPF.EXE
(CSSoftmaxProFileIO seg 111, CSStream seg 39, CSDataFile seg 37, CSPlateData
seg 35, CSPlateDescriptor/CSSite seg 38, CSFixedArray seg 37, template classes
seg 50-53) and checked against text exports made by the original program.

File layout (all integers and floats big-endian, Mac style)::

    "SPF.EXE\\0"                 C string (magic)
    pstring  app version          "1.1" (release) or "1.1b10" (beta)
    cstring  stream version       "1.0.5" -> 10005 (a*10000 + b*100 + c)
    long     document offset      0x7d0; bytes before it are disk slack
    @offset: 120-byte Mac print record (THPrint)
             template tree        CSTemplate "Template" -> CSTmplGroup -> CSTmplSample -> CSWell
             sections             pstring class, cstring name, long offset-of-next-section, body
                                  (CSPlateSection, CSAnalysisSection, CSGroupSection, CSGraphSection)

Serialized objects are ``pstring class name`` + class-specific fields; tree nodes
add a cstring name and are followed by ``long child count`` + children.
Doubles are 8-byte IEEE (10-byte 68k extended only for streams older than 1.0.5,
which do not occur in practice; both are supported).

A plate section holds CSPlateData (instrument settings), then up to two
CSPlateDescriptor objects (the read and, when pre-read is on, the pre-read),
each with a per-read temperature table and one CSSite per read well holding one
CSFixedArray of raw ODs per wavelength (index = read number).  Read times are
not stored: kinetic reads are ``k * interval`` exactly as SOFTmax PRO exports them.
``read_at`` is the Mac clock (local wall time) converted as if it were UTC.

Not decoded: curve-fit type and graph styling, column/plate display formats,
reduction parameters other than the formula strings, well masks.
"""
from __future__ import annotations

import math
import re
import struct
from typing import Optional

__all__ = ["read_pda", "read_ppr", "parse_softmax"]

MAGIC = b"SPF.EXE\0"
MAC_EPOCH_OFFSET = 2082844800      # seconds from 1904-01-01 to 1970-01-01
PRINT_RECORD_SIZE = 120
MODES = {0: "endpoint", 1: "kinetic", 2: "spectrum"}
SECTION_CLASSES = ("CSPlateSection", "CSAnalysisSection", "CSGroupSection", "CSGraphSection")

DEFAULT_SETTINGS = {
    "mode": "endpoint", "wavelengths": [405], "run_time": 300, "interval": 30,
    "first_col": 1, "last_col": 12, "start": 350, "stop": 750, "step": 10,
    "mix_before": False, "mix_before_s": 5, "mix_between": False, "mix_between_s": 3,
    "autocalibrate": True, "pre_read": False,
}


class _Stream:
    """Big-endian reader mirroring CSStream's primitives."""

    def __init__(self, data: bytes, pos: int = 0, version: int = 10005):
        self.d = data
        self.p = pos
        self.version = version

    def _take(self, n: int) -> bytes:
        if n < 0 or self.p + n > len(self.d):
            raise ValueError(f"unexpected end of file at 0x{self.p:x}")
        b = self.d[self.p:self.p + n]
        self.p += n
        return b

    def byte(self) -> int:
        return self._take(1)[0]

    def short(self) -> int:
        return struct.unpack(">h", self._take(2))[0]

    def long(self) -> int:
        return struct.unpack(">i", self._take(4))[0]

    def ulong(self) -> int:
        return struct.unpack(">I", self._take(4))[0]

    def float(self) -> float:
        return struct.unpack(">f", self._take(4))[0]

    def double(self) -> float:
        if self.version < 10005:   # 68k 80-bit extended (never seen in sample files)
            return _extended(self._take(10))
        return struct.unpack(">d", self._take(8))[0]

    def pstr(self) -> str:
        n = self.byte()
        return self._take(n).decode("mac_roman")

    def cstr(self) -> str:
        e = self.d.find(b"\0", self.p)
        if e < 0:
            raise ValueError(f"unterminated string at 0x{self.p:x}")
        s = self.d[self.p:e].decode("mac_roman")
        self.p = e + 1
        return s


def _extended(b: bytes) -> float:
    """68k/SANE 80-bit extended -> float."""
    exp = ((b[0] & 0x7F) << 8) | b[1]
    mant = int.from_bytes(b[2:10], "big")
    if exp == 0 and mant == 0:
        return 0.0
    if exp == 0x7FFF:
        return math.nan
    try:
        v = math.ldexp(mant / (1 << 63), exp - 16383)
    except OverflowError:
        v = math.inf
    return -v if b[0] & 0x80 else v


def _clean(v: float) -> Optional[float]:
    if v is None or math.isnan(v) or math.isinf(v):
        return None
    return v


# ---------------------------------------------------------------- header


def _header(data: bytes) -> dict:
    if not data.startswith(MAGIC):
        raise ValueError("Not a SOFTmax PRO file")
    s = _Stream(data, len(MAGIC))
    try:
        app = s.pstr()
        ver = s.cstr()
        offset = s.ulong()
    except (ValueError, UnicodeDecodeError) as e:
        raise ValueError("Not a SOFTmax PRO file") from e
    nums = [int(x) if x.isdigit() else 0 for x in re.split(r"\.", ver)[:3]] + [0, 0, 0]
    version = nums[0] * 10000 + nums[1] * 100 + nums[2]
    if version == 10006:          # SPF.EXE treats 1.0.6 as 1.0.5
        version = 10005
    if not 0 < offset < len(data):
        raise ValueError("Not a SOFTmax PRO file")
    # SPF.EXE sets a stream flag when the writer was *not* a 1.1a/1.1b beta; release
    # 1.0.5 streams then always store the per-read (time, temperature) table.
    beta = "1.1b" in app or "1.1a" in app
    return {"app": app, "version_string": ver, "version": version,
            "offset": offset, "old_descriptor": not beta}


# ---------------------------------------------------------------- template tree


def _read_node(s: _Stream, warnings: list, depth: int = 0) -> dict:
    cls = s.pstr()
    name = s.cstr()
    node = {"class": cls, "name": name}
    if cls == "CSTemplate":
        pass
    elif cls == "CSTmplGroup":
        node["scale"] = s.double()
        node["units"] = s.cstr()
        node["b28"] = s.byte()
        node["b4a"] = s.byte()
        node["user_group"] = s.byte()        # 0 for the built-in plate "Blank" group
        node["is_concentration"] = s.byte()  # 1: descriptor is a concentration
        node["descriptor"] = s.cstr()
        node["descriptor2"] = s.cstr()
        node["color"] = s.long()
        node["order"] = s.short()
        s.short(); s.long(); s.long(); s.long()
    elif cls == "CSTmplSample":
        node["value"] = s.double()
        s.long(); s.long(); s.long(); s.long()
    elif cls == "CSWell":
        node["row"] = s.short()
        node["col"] = s.short()
        node["plate"] = s.cstr()
    else:
        raise ValueError(f"unknown template class {cls!r} at 0x{s.p:x}")
    n = s.long()
    if not 0 <= n <= 100000 or depth > 4:
        raise ValueError(f"bad child count {n} at 0x{s.p:x}")
    node["children"] = [_read_node(s, warnings, depth + 1) for _ in range(n)]
    return node


def _read_template(s: _Stream, warnings: list) -> Optional[dict]:
    name = s.cstr()
    n = s.long()
    if not 0 <= n <= 1000:
        raise ValueError("bad template group count")
    groups = [_read_node(s, warnings, 1) for _ in range(n)]
    return {"name": name, "groups": groups}


# ---------------------------------------------------------------- plate data


def _read_plate_data(s: _Stream) -> dict:
    """CSPlateData::Read (seg 35:187e)."""
    o: dict = {}
    o["mode"] = s.short()
    o["start_col"] = s.short()
    o["num_cols"] = s.short()
    o["num_reads"] = s.long()
    o["num_wavelengths"] = s.long()
    o["wavelengths"] = [(s.long(), s.byte()) for _ in range(6)]
    o["run_time"] = s.long()
    o["interval"] = s.long()
    o["incubator"] = s.byte()
    o["setpoint"] = s.double()
    o["b5a"] = s.byte()
    o["mix_before"] = s.byte()
    o["l5e"] = s.long()
    o["mix_before_s"] = s.long()
    o["mix_between"] = s.byte()
    o["l68"] = s.long()
    o["mix_between_s"] = s.long()
    o["autocalibrate"] = s.byte()
    o["start"] = s.long()
    o["stop"] = s.long()
    o["step"] = s.long()
    o["pre_read"] = s.byte()
    o["b7f"] = s.byte()
    o["well_flags"] = list(s._take(96))
    if s.version >= 4:
        o["b158"] = s.byte()
    if s.version > 10000:
        o["b22"] = s.byte()
    if s.version > 10001:
        o["s144"] = s.short()
        o["b146"] = s.byte()
    if s.version > 10003:
        o["l148"] = s.long()
    return o


def _read_fixed_array(s: _Stream) -> list:
    """CSFixedArray::Read (seg 37:17c2): long count, then a handle (long size + bytes)."""
    count = s.long()
    size = s.long()
    raw = s._take(size)
    if count < 0 or size < 0:
        raise ValueError("bad array")
    if s.version < 10005:          # stored as 4-byte floats
        n = min(count, size // 4)
        return list(struct.unpack(f">{n}f", raw[:4 * n]))
    n = min(count, size // 8)
    return list(struct.unpack(f">{n}d", raw[:8 * n]))


def _read_descriptor_body(s: _Stream, with_table: bool) -> dict:
    o: dict = {"flag": s.byte()}
    pairs = []
    if with_table or o["flag"] == 1:
        n = s.long()
        if not 0 <= n <= 1000000:
            raise ValueError("bad read table size")
        for _ in range(n):
            s.long()          # never initialised by SPF.EXE (garbage)
            pairs.append(s.float())
    o["temps"] = pairs
    o["num_wavelengths"] = s.long()
    o["num_reads"] = s.long()
    o["read_at"] = s.ulong()
    o["first_col"] = s.short()
    o["last_col"] = s.short()
    o["reads_done"] = s.long()
    o["arrays_per_site"] = s.short()
    o["b2a"] = s.byte()
    if o["first_col"] <= o["last_col"] and not (1 <= o["first_col"] and o["last_col"] <= 12):
        raise ValueError("bad column range")
    if not 0 <= o["arrays_per_site"] <= 64:
        raise ValueError("bad descriptor")
    sites = {}
    for row in range(8):
        for col in range(1, 13):
            if o["first_col"] <= col <= o["last_col"]:
                cls = s.pstr()
                if cls not in ("CSSite", ""):
                    raise ValueError(f"expected CSSite, got {cls!r}")
                narr = s.long()
                index = s.long()
                if not 0 <= narr <= 64:
                    raise ValueError("bad site")
                arrays = []
                for _ in range(narr):
                    c2 = s.pstr()
                    if c2 != "CSFixedArray":
                        raise ValueError(f"expected CSFixedArray, got {c2!r}")
                    arrays.append(_read_fixed_array(s))
                sites[(row, col - 1)] = {"index": index, "arrays": arrays}
    o["sites"] = sites
    return o


def _read_descriptor(s: _Stream, old_style: bool) -> dict:
    """CSPlateDescriptor::Read (seg 38:0c20).

    For 1.0.5 streams written by the release program the (time, temperature)
    table is always present; files written by the 1.1 betas store it only when
    the leading flag is 1.  Try the expected variant first, then the other one.
    """
    start = s.p
    order = [old_style, not old_style] if s.version == 10005 else [s.version < 10005]
    err: Exception = ValueError("bad descriptor")
    for variant in order:
        s.p = start
        try:
            return _read_descriptor_body(s, variant)
        except (ValueError, struct.error, UnicodeDecodeError) as e:
            err = e
    raise err


def _descriptor_to_data(desc: dict, pd: dict) -> dict:
    mode = MODES.get(pd["mode"], "endpoint")
    nwl = max(1, desc["arrays_per_site"] or desc["num_wavelengths"] or 1)
    lengths = [len(a) for site in desc["sites"].values() for a in site["arrays"]]
    nreads = desc["reads_done"]
    if lengths:
        nreads = min(max(nreads, 0), max(lengths))
    else:
        nreads = max(0, nreads)
    values = []
    for k in range(nreads):
        read = []
        for w in range(nwl):
            grid = [[None] * 12 for _ in range(8)]
            for (r, c), site in desc["sites"].items():
                if w < len(site["arrays"]) and k < len(site["arrays"][w]):
                    grid[r][c] = _clean(site["arrays"][w][k])
            read.append(grid)
        values.append(read)
    temps = [round(t, 2) if not math.isnan(t) else None for t in desc["temps"][:nreads]]
    temps += [None] * (nreads - len(temps))
    if mode == "kinetic":
        times = [float(k * pd["interval"]) for k in range(nreads)]
    else:
        times = [0.0] * nreads
    sweep = [pd["start"] + k * pd["step"] for k in range(nreads)] if mode == "spectrum" else []
    wls = [] if mode == "spectrum" else [w for w, _ in pd["wavelengths"][:nwl]]
    read_at = desc["read_at"] - MAC_EPOCH_OFFSET if desc["read_at"] > MAC_EPOCH_OFFSET else 0
    return {"mode": mode, "wavelengths": wls, "times": times, "temps": temps, "sweep": sweep,
            "values": values, "saturated": [], "read_at": read_at, "imported": True,
            "setpoint": pd["setpoint"], "first_col": desc["first_col"],
            "last_col": desc["last_col"]}


def _settings(pd: dict) -> dict:
    st = dict(DEFAULT_SETTINGS)
    mode = MODES.get(pd["mode"])
    if mode:
        st["mode"] = mode
    nwl = pd["num_wavelengths"]
    if 1 <= nwl <= 6:
        st["wavelengths"] = [w for w, _ in pd["wavelengths"][:nwl]]
    if pd["run_time"] > 0:
        st["run_time"] = pd["run_time"]
    if pd["interval"] > 0:
        st["interval"] = pd["interval"]
    if 1 <= pd["start_col"] <= 12 and pd["num_cols"] >= 1:
        st["first_col"] = pd["start_col"]
        st["last_col"] = min(12, pd["start_col"] + pd["num_cols"] - 1)
    if pd["start"] > 0 and pd["stop"] > 0:
        st["start"], st["stop"] = pd["start"], pd["stop"]
    if pd["step"] > 0:
        st["step"] = pd["step"]
    st["mix_before"] = bool(pd["mix_before"])
    if pd["mix_before_s"] > 0:
        st["mix_before_s"] = pd["mix_before_s"]
    st["mix_between"] = bool(pd["mix_between"])
    if pd["mix_between_s"] > 0:
        st["mix_between_s"] = pd["mix_between_s"]
    st["autocalibrate"] = bool(pd["autocalibrate"])
    st["pre_read"] = bool(pd["pre_read"])
    st["incubator"] = bool(pd["incubator"])
    st["setpoint"] = pd["setpoint"]
    return st


def _read_plate_section(s: _Stream, plate: dict, header: dict, warnings: list) -> None:
    """CSPlateSection body: CSPlateData (settings + up to two descriptors), CSCalcPlateBody.

    Fills *plate* in place so that a failure late in the section keeps what was read.
    """
    name = plate["name"]
    cls = s.pstr()
    if cls != "CSPlateData":
        warnings.append(f"plate {name!r}: expected CSPlateData, found {cls!r}")
        return
    pd = _read_plate_data(s)
    plate["settings"] = _settings(pd)
    for k in range(2):          # [0] = the read, [1] = plate pre-read (when pre-read is on)
        cls = s.pstr()
        if not cls:
            continue
        if cls != "CSPlateDescriptor":
            warnings.append(f"plate {name!r}: unexpected {cls!r} instead of plate data")
            return
        data = _descriptor_to_data(_read_descriptor(s, header["old_descriptor"]), pd)
        plate["data" if k == 0 else "preread"] = data
    try:
        red = _read_calc_plate_body(s)
        if red:
            plate["reduction"] = red
    except (ValueError, struct.error, UnicodeDecodeError):
        pass


# ---------------------------------------------------------------- whole file


def parse_softmax(data: bytes) -> dict:
    """Decode the bytes of a SOFTmax PRO 1.x .pda/.ppr file."""
    hdr = _header(data)
    warnings: list = []
    result = {"plates": [], "groups": [], "notes": [], "graphs": [],
              "version": hdr["app"], "stream_version": hdr["version_string"],
              "warnings": warnings}
    s = _Stream(data, hdr["offset"] + PRINT_RECORD_SIZE, hdr["version"])
    template = None
    first = None
    try:
        template = _read_template(s, warnings)
        first = s.p
    except Exception as e:  # noqa: BLE001
        warnings.append(f"template not decoded: {e}")
    if first is None or not _looks_like_section(data, first):
        first = _find_first_section(data, hdr["offset"] + PRINT_RECORD_SIZE)
    sections = _walk_sections(data, first, warnings)
    plates_by_name: dict = {}
    for cls, name, body, end in sections:
        s = _Stream(data, body, hdr["version"])
        try:
            if cls == "CSPlateSection":
                plate = {"name": name, "settings": dict(DEFAULT_SETTINGS), "data": None,
                         "template": {}, "mask": []}
                result["plates"].append(plate)
                plates_by_name.setdefault(name, plate)
                _read_plate_section(s, plate, hdr, warnings)
            elif cls == "CSAnalysisSection":
                note = _read_analysis_section(s, name, end, warnings)
                if note is not None:
                    result["notes"].append(note)
            elif cls == "CSGroupSection":
                result["groups"].append(_read_group_section(s, name, end, warnings))
            elif cls == "CSGraphSection":
                result["graphs"].append(_read_graph_section(s, name, end, warnings))
        except Exception as e:  # noqa: BLE001 - keep whatever else the file holds
            warnings.append(f"{cls} {name!r} not fully decoded: {e}")
    if template is not None:
        try:
            _apply_template(template, result, plates_by_name)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"template not applied: {e}")
    for i, plate in enumerate(result["plates"]):
        if not plate["name"]:
            plate["name"] = f"Plate#{i + 1}"
    return result


def _looks_like_section(data: bytes, pos: int) -> bool:
    n = data[pos] if pos < len(data) else 0
    return data[pos + 1:pos + 1 + n].decode("latin1") in SECTION_CLASSES


def _find_first_section(data: bytes, start: int) -> Optional[int]:
    best = None
    for cls in SECTION_CLASSES:
        p = data.find(bytes([len(cls)]) + cls.encode(), start)
        if p >= 0 and (best is None or p < best):
            best = p
    return best


def _walk_sections(data: bytes, pos: Optional[int], warnings: list) -> list:
    out = []
    seen = set()
    while pos is not None and pos < len(data) and pos not in seen:
        seen.add(pos)
        s = _Stream(data, pos)
        try:
            cls = s.pstr()
            name = s.cstr()
            nxt = s.ulong()
        except (ValueError, UnicodeDecodeError) as e:
            warnings.append(f"section list broken at 0x{pos:x}: {e}")
            break
        if cls not in SECTION_CLASSES:
            warnings.append(f"unknown section class {cls!r} at 0x{pos:x}")
            break
        if not pos < nxt <= len(data):
            warnings.append(f"section {name!r} has a bad length; stopping")
            out.append((cls, name, s.p, len(data)))
            break
        out.append((cls, name, s.p, nxt))
        pos = nxt if nxt < len(data) else None
    return out


# ---------------------------------------------------------------- template -> plates


def _apply_template(template: dict, result: dict, plates_by_name: dict) -> None:
    groups_meta = {}
    for g in template["groups"]:
        if g["class"] != "CSTmplGroup":
            continue
        if g["name"] == "Clear":   # unassigned wells
            continue
        blank = not g.get("user_group", 1)
        desc = ""
        if g.get("descriptor"):
            desc = "Concentration" if g.get("is_concentration") else "Factor"
        groups_meta[g["name"]] = {"descriptor": desc, "units": g.get("units", ""),
                                  "descriptor_name": g.get("descriptor", "")}
        for smp in g["children"]:
            for w in smp["children"]:
                if w["class"] != "CSWell":
                    continue
                plate = plates_by_name.get(w["plate"])
                if plate is None:
                    continue
                entry = {"type": "plateblank" if blank else "sample",
                         "group": None if blank else g["name"],
                         "sample": smp["name"],
                         "value": None if blank or not g.get("descriptor") else smp["value"]}
                plate["template"][w["name"]] = entry
    # attach template-level info to the groups decoded from group sections
    known = {g["name"]: g for g in result["groups"]}
    for name, meta in groups_meta.items():
        if meta["descriptor"] == "" and name == "Blank":
            continue
        g = known.get(name)
        if g is None:
            g = {"name": name, "columns": [], "summaries": []}
            result["groups"].append(g)
        g["descriptor"] = meta["descriptor"]
        g["units"] = meta["units"]
        g["descriptor_name"] = meta["descriptor_name"]


# ---------------------------------------------------------------- other sections


def _pleat_body(s: _Stream) -> dict:
    """CSPleatBody::Read (seg 14:15d5): section display state, 18 bytes."""
    collapsed = s.byte()
    shown = s.byte()
    s.byte(); s.byte(); s.short(); s.long(); s.long(); s.long()
    return {"collapsed": collapsed, "shown": shown}


def _text(raw: bytes) -> str:
    return raw.decode("mac_roman").replace("\r\n", "\n").replace("\r", "\n").rstrip("\0")


def _calc_pane(s: _Stream) -> dict:
    """CSCalcPane::Read (seg 71:1e8c): a named formula with a label (summary line)."""
    name = s.cstr()
    formula = s.cstr()
    label = s.cstr()
    for _ in range(5):
        s.short()
    s.byte(); s.byte()
    decimals = s.short()
    for _ in range(4):
        s.long()
    return {"name": name, "formula": formula, "label": label, "decimals": decimals}


def _group_column(s: _Stream) -> dict:
    """CSGroupCol::Read (seg 19:13e8)."""
    name = s.cstr()
    formula = s.cstr()
    s.short(); s.short(); s.short(); s.short()     # font id, size, style, ...
    width = s.short()
    decimals = s.short()
    s.byte(); s.byte(); s.short()
    s.long(); s.long(); s.long()
    return {"name": name, "formula": formula, "decimals": decimals, "width": width}


def _read_analysis_section(s: _Stream, name: str, end: int, warnings: list) -> Optional[dict]:
    """CSAnalysisSection = pleat body + panes (2: styled text, 1: calculation) + 4 longs."""
    _pleat_body(s)
    n = s.short()
    texts, calcs = [], []
    for _ in range(max(0, n)):
        tag = s.short()
        if tag == 2:              # CSEditPiece (seg 45:0c04): text handle, style handle, 4 longs
            size = s.long()
            texts.append(_text(s._take(size)))
            s._take(s.long())
            for _ in range(4):
                s.long()
        elif tag == 1:
            calcs.append(_calc_pane(s))
        else:
            warnings.append(f"notes {name!r}: unknown pane type {tag}")
            break
    note = {"name": name, "text": "\n".join(texts)}
    if calcs:
        note["calculations"] = calcs
    return note


def _read_group_section(s: _Stream, name: str, end: int, warnings: list) -> dict:
    """CSGroupSection = pleat body + CSGroupTable (columns) + CSCalcPaneBunch (summaries)."""
    _pleat_body(s)
    ncols = s.short()
    if not 0 <= ncols <= 500:
        raise ValueError("bad column count")
    columns = [_group_column(s) for _ in range(ncols)]
    s.byte()
    for _ in range(4):
        s.long()
    nsum = s.short()
    if not 0 <= nsum <= 500:
        raise ValueError("bad summary count")
    summaries = [_calc_pane(s) for _ in range(nsum)]
    return {"name": name, "descriptor": "", "units": "",
            "columns": [{k: c[k] for k in ("name", "formula", "decimals")} for c in columns],
            "summaries": summaries}


def _read_graph_section(s: _Stream, name: str, end: int, warnings: list) -> dict:
    """CSGraphSection: plots (x/y formulas) and axes.  Curve-fit settings are not decoded."""
    graph: dict = {"name": name, "plots": []}
    _pleat_body(s)
    n = s.long()
    if not 0 <= n <= 100:
        raise ValueError("bad plot count")
    for _ in range(n):
        plot = {"name": s.cstr()}              # CSGraphPlot::Read (seg 57:17fe)
        s.cstr()
        plot["color"] = s.short()
        s.short(); s.short()
        s._take(6)
        plot["x"] = s.cstr()
        plot["y"] = s.cstr()
        s.cstr(); s.cstr()
        s.short(); s.short()
        graph["plots"].append(plot)
    graph["title"] = s.cstr()
    s._take(2)
    lim = struct.unpack("<4d", s._take(32))     # stored in Intel byte order
    graph["x_range"] = [lim[0], lim[1]]
    graph["y_range"] = [lim[2], lim[3]]
    s._take(6)
    graph["x_label"] = s.cstr()
    graph["y_label"] = s.cstr()
    return graph


def _read_calc_plate_body(s: _Stream) -> Optional[dict]:
    """Plate reduction settings (CSCalcPlateBody); only the formula strings are decoded."""
    if s.pstr() != "CSCalcPlateBody":
        return None
    s._take(19)
    a, b, formula = s.cstr(), s.cstr(), s.cstr()
    if not all(x.isprintable() for x in (a, b, formula)):
        return None
    return {"wavelength_combination": a, "label": b, "formula": formula}


# ---------------------------------------------------------------- public API


def read_pda(path) -> dict:
    """Read a SOFTmax PRO 1.x data file (.pda); raises ValueError("Not a SOFTmax PRO file").

    Returns ``{"plates": [...], "groups": [...], "notes": [...], "graphs": [...],
    "version": "1.1", "stream_version": "1.0.5", "warnings": [...]}``.  Each plate is
    ``{"name", "settings", "data", "template", "mask"}`` plus optional ``"preread"``
    (same shape as ``data``) and ``"reduction"``; ``data`` is None in protocols and
    unread plates, else ``values[read][wavelength][row][col]`` with None for wells
    that were not read.  Anything that could not be decoded is listed in ``warnings``.
    """
    with open(path, "rb") as f:
        data = f.read()
    return parse_softmax(data)


def read_ppr(path) -> dict:
    """Read a SOFTmax PRO 1.x protocol file (.ppr) - same format, normally without data."""
    return read_pda(path)
