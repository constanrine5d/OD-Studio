"""Export of plate sections: tab-delimited text, CSV and Excel (.xlsx), raw and/or reduced.

Used by File > Export and by Auto Export after every reading.  The .xlsx
writer is a minimal, dependency-free implementation of the Office Open XML
spreadsheet format (inline strings, numbers, one sheet per table).
"""
from __future__ import annotations

import csv
import io
import os
import re
import time
import zipfile
from typing import Optional
from xml.sax.saxutils import escape

from . import textio
from .document import ALL_WELLS, ROWS, fmt_hms, settings_summary

FORMATS = {"txt": ".txt", "csv": ".csv", "xlsx": ".xlsx"}
CONTENTS = ("raw", "reduced", "both")
DEFAULT_NAME = "{experiment}_{plate}_{date}_{time}"
TOKENS = ("experiment", "plate", "date", "time", "mode", "n")


# ----------------------------------------------------------------- file names
def expand_name(pattern: str, *, experiment: str, plate: str, mode: str, n: int = 1,
                when: Optional[float] = None) -> str:
    """Fill {experiment} {plate} {date} {time} {mode} {n} and make the result a safe file name."""
    t = time.localtime(when if when is not None else time.time())
    values = {"experiment": os.path.splitext(experiment)[0] or "Untitled", "plate": plate, "mode": mode,
              "date": time.strftime("%Y-%m-%d", t), "time": time.strftime("%H%M%S", t), "n": f"{n:03d}"}

    def sub(m):
        key = m.group(1).lower()
        return values.get(key, m.group(0))
    name = re.sub(r"\{(\w+)\}", sub, pattern or DEFAULT_NAME)
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .")
    return name or "export"


def unique_path(folder: str, name: str, ext: str, overwrite: bool = False) -> str:
    path = os.path.join(folder, name + ext)
    if overwrite:
        return path
    k = 2
    while os.path.exists(path):
        path = os.path.join(folder, f"{name}_{k}{ext}")
        k += 1
    return path


# ------------------------------------------------------------------- tables
def _num(v):
    return None if v is None else round(float(v), 6)


def reduced_rows(view: dict) -> list:
    """8 x 12 grid of reduced values (numbers, or the error text), with headings."""
    unit = view.get("unit") or ""
    rows = [[f"Reduced{(' (' + unit + ')') if unit else ''}"] + [str(c) for c in range(1, 13)]]
    for r in range(8):
        row = [ROWS[r]]
        for c in range(12):
            w = view["wells"].get(f"{ROWS[r]}{c + 1}")
            row.append(None if not w else (w["num"] if w["num"] is not None else w["text"]))
        rows.append(row)
    return rows


def raw_rows(data: dict) -> list:
    """Time-layout raw table: one row per reading, wells across (per wavelength)."""
    nw = len(data["values"][0]) if data["values"] else 1
    if data["mode"] == "spectrum":
        first = "Wavelength (nm)"
        labels = [str(w) for w in data["sweep"]]
    elif data["mode"] == "kinetic":
        first = "Time (h:mm:ss)"
        labels = [fmt_hms(t) for t in data["times"]]
    else:
        first = ""
        labels = [""] * len(data["values"])
    head = [first, "Temperature (°C)"]
    wls = data.get("wavelengths") or []
    for k in range(nw):
        suffix = f" {wls[k]}" if nw > 1 and k < len(wls) else ""
        head += [w + suffix for w in ALL_WELLS]
    rows = [head]
    for i, read in enumerate(data["values"]):
        t = data["temps"][i] if i < len(data["temps"]) else None
        row = [labels[i] if i < len(labels) else "", _num(t)]
        for grid in read:
            row += [_num(grid[r][c]) for r in range(8) for c in range(12)]
        rows.append(row)
    return rows


def info_rows(plate: dict, view: dict, experiment: str) -> list:
    d = plate.get("data") or {}
    rows = [["Experiment", experiment], ["Plate", plate["name"]],
            ["Settings", settings_summary(plate["settings"])],
            ["Read", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(d["read_at"])) if d.get("read_at") else ""],
            ["Readings", len(d.get("values") or [])]]
    red = plate["reduction"]
    rows.append(["Wavelength formula", red.get("wavelength", "")])
    if plate["settings"]["mode"] == "kinetic":
        rows.append(["Kinetic reduction", red.get("kinetic", "")])
    if plate["settings"]["mode"] == "spectrum":
        rows.append(["Spectrum reduction", red.get("spectrum", "")])
    rows.append(["Unit", view.get("unit") or ""])
    if view.get("blank") is not None:
        rows.append(["Plate blank", view["blank"]])
    return rows


# -------------------------------------------------------------------- text
def _tsv(rows: list) -> str:
    out = []
    for row in rows:
        out.append("\t".join("" if v is None else (f"{v:.6g}" if isinstance(v, float) else str(v)) for v in row))
    return "\r\n".join(out) + "\r\n"


def to_text(items: list, content: str, layout: str = "time", labels: bool = True) -> str:
    parts = []
    for plate, view, _ in items:
        if len(items) > 1:
            parts.append(f"{plate['name']}\r\n")
        if content in ("raw", "both") and plate.get("data"):
            parts.append(textio.export_text(plate["data"], layout, labels))
        if content in ("reduced", "both"):
            if content == "both":
                parts.append("\r\n")
            parts.append(_tsv(reduced_rows(view)))
        parts.append("\r\n")
    return "".join(parts)


def to_csv(items: list, content: str) -> str:
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    if content == "reduced":
        w.writerow(["plate", "well", "reduced", "unit"])
        for plate, view, _ in items:
            for well in ALL_WELLS:
                v = view["wells"].get(well)
                if v:
                    w.writerow([plate["name"], well, v["num"] if v["num"] is not None else v["text"], view.get("unit") or ""])
        return out.getvalue()
    head = ["plate", "well", "reading", "time_s", "temperature_c", "wavelength_nm", "od"]
    if content == "both":
        head += ["reduced", "unit"]
    w.writerow(head)
    for plate, view, _ in items:
        d = plate.get("data")
        if not d:
            continue
        wls = d.get("wavelengths") or []
        for i, read in enumerate(d["values"]):
            for k, grid in enumerate(read):
                wl = d["sweep"][i] if d["mode"] == "spectrum" else (wls[k] if k < len(wls) else "")
                for r in range(8):
                    for c in range(12):
                        v = grid[r][c]
                        if v is None:
                            continue
                        well = f"{ROWS[r]}{c + 1}"
                        row = [plate["name"], well, i + 1, d["times"][i] if i < len(d["times"]) else "",
                               d["temps"][i] if i < len(d["temps"]) else "", wl, round(v, 6)]
                        if content == "both":
                            red = view["wells"].get(well)
                            row += [("" if not red else (red["num"] if red["num"] is not None else red["text"])),
                                    view.get("unit") or ""]
                        w.writerow(row)
    return out.getvalue()


# -------------------------------------------------------------------- xlsx
def _col(n: int) -> str:
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _sheet_xml(rows: list) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>']
    for i, row in enumerate(rows):
        cells = []
        for j, v in enumerate(row):
            if v is None or v == "":
                continue
            ref = f"{_col(j)}{i + 1}"
            if isinstance(v, bool):
                v = str(v)
            if isinstance(v, (int, float)):
                cells.append(f'<c r="{ref}"><v>{repr(float(v)) if isinstance(v, float) else v}</v></c>')
            else:
                text = escape(str(v))
                cells.append(f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>')
        out.append(f'<row r="{i + 1}">{"".join(cells)}</row>')
    out.append("</sheetData></worksheet>")
    return "".join(out)


def write_xlsx(path: str, sheets: list) -> None:
    """sheets: [(name, rows)]; rows are lists of numbers / strings / None."""
    names, used = [], set()
    for name, _ in sheets:
        n = re.sub(r"[\[\]:*?/\\]", "_", name)[:31] or "Sheet"
        base, k = n, 2
        while n.lower() in used:
            n = f"{base[:28]}_{k}"
            k += 1
        used.add(n.lower())
        names.append(n)
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">',
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>',
          '<Default Extension="xml" ContentType="application/xml"/>',
          '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>']
    ct += [f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
           for i in range(len(sheets))]
    ct.append("</Types>")
    wb = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
          '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
          'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>']
    wb += [f'<sheet name="{escape(n, {chr(34): "&quot;"})}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(names)]
    wb.append("</sheets></workbook>")
    rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">']
    rels += [f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
             for i in range(len(sheets))]
    rels.append("</Relationships>")
    root = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
            '</Relationships>')
    tmp = path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(ct))
        z.writestr("_rels/.rels", root)
        z.writestr("xl/workbook.xml", "".join(wb))
        z.writestr("xl/_rels/workbook.xml.rels", "".join(rels))
        for i, (_, rows) in enumerate(sheets):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(rows))
    os.replace(tmp, path)


# ------------------------------------------------------------------- entry
def export(path: str, items: list, fmt: str = "txt", content: str = "raw", layout: str = "time",
           labels: bool = True) -> str:
    """items: [(plate_section, plate_view, experiment_title)].  Returns the path written."""
    if fmt not in FORMATS:
        raise ValueError(f"Unknown export format {fmt!r}")
    if content not in CONTENTS:
        raise ValueError(f"Unknown export content {content!r}")
    if content == "raw":
        items = [it for it in items if it[0].get("data")]
    if not items:
        raise ValueError("There is no data to export")
    if not os.path.splitext(path)[1]:
        path += FORMATS[fmt]
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    if fmt == "txt":
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(to_text(items, content, layout, labels))
    elif fmt == "csv":
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(to_csv(items, content))
    else:
        sheets = []
        for plate, view, experiment in items:
            prefix = plate["name"] if len(items) > 1 else ""
            if content in ("raw", "both") and plate.get("data"):
                sheets.append((f"{prefix} Raw".strip(), raw_rows(plate["data"])))
            if content in ("reduced", "both"):
                sheets.append((f"{prefix} Reduced".strip(), reduced_rows(view)))
            sheets.append((f"{prefix} Info".strip(), info_rows(plate, view, experiment)))
        write_xlsx(path, sheets)
    return path
