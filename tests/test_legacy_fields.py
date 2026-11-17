"""Decoders for the SOFTmax PRO 1.x reduction / display / mask / curve-fit fields,
checked on synthetic big-endian byte streams (no sample files needed)."""
import struct

import pytest

from odstudio import legacy
from odstudio.document import document_from_legacy


def pstr(s: str) -> bytes:
    return bytes([len(s)]) + s.encode("mac_roman")


def cstr(s: str) -> bytes:
    return s.encode("mac_roman") + b"\0"


def calc_plate_body(display=3, with_number=1, wl_code=4, kin_code=2, vmax_points=12,
                    blank=1, wl_formula="L1", wl_name="L1-L2", kin_formula="Average(!KinPlot)",
                    low=0.5, high=2.5, start=30, end=900, min_od=-0.1, max_od=1.5,
                    onset_od=0.25, relative=1) -> bytes:
    b = pstr("CSCalcPlateBody")
    b += struct.pack(">i", 7) + bytes([0, 1, 1])
    b += struct.pack(">hB", display, with_number)
    b += struct.pack(">hhiB", wl_code, kin_code, vmax_points, blank)
    b += cstr(wl_formula) + cstr(wl_name) + cstr(kin_formula)
    b += struct.pack(">2d", low, high) + struct.pack(">11d", *([0.0] * 11))
    b += struct.pack(">ii3d", start, end, min_od, max_od, onset_od)
    b += bytes([relative])
    return b + pstr("CSCalcPlateTable")


def test_kinetic_body():
    s = legacy._Stream(calc_plate_body(), 0, 10005)
    red, disp = legacy._read_calc_plate_body(s, "kinetic")
    assert red["wavelength_choice"] == "named" and red["wavelength_name"] == "L1-L2"
    assert red["kinetic"] == "onset" and red["vmax_points"] == 12
    assert (red["lag_time"], red["end_time"]) == (30, 900)
    assert (red["min_od"], red["max_od"], red["onset_od"]) == (-0.1, 1.5, 0.25)
    assert red["plate_blank"] is True and red["relative_od"] is True
    # the old keys keep their meaning
    assert (red["wavelength_combination"], red["label"], red["formula"]) == ("L1", "L1-L2", "Average(!KinPlot)")
    assert disp == {"type": "gray", "with_number": True, "low": 0.5, "high": 2.5}
    assert s.d[s.p:] == pstr("CSCalcPlateTable")


def test_spectrum_body():
    s = legacy._Stream(calc_plate_body(wl_code=6, display=1, start=300, end=600), 0, 10005)
    red, disp = legacy._read_calc_plate_body(s, "spectrum")
    assert red["wavelength_choice"] == "minlambda" and (red["start_nm"], red["end_nm"]) == (300, 600)
    assert "kinetic" not in red and disp["type"] == "number"
    s = legacy._Stream(calc_plate_body(display=9), 0, 10005)
    red, disp = legacy._read_calc_plate_body(s, "endpoint")
    assert disp is None and red["wavelength_choice"] == "named"


def test_bad_body_is_rejected():
    assert legacy._read_calc_plate_body(legacy._Stream(pstr("CSOther"), 0), "endpoint") == (None, None)
    raw = calc_plate_body(wl_name="L1\x01")
    assert legacy._read_calc_plate_body(legacy._Stream(raw, 0), "endpoint") == (None, None)


def test_mask_is_row_major():
    flags = [0] * 96
    flags[0] = flags[13] = flags[95] = 1
    assert legacy._mask({"well_flags": flags}) == ["A1", "B2", "H12"]
    assert legacy._mask({"well_flags": [0] * 96}) == []


def graph_section(fit: int) -> bytes:
    b = bytes(18)                                    # pleat body
    b += struct.pack(">i", 1)                        # one plot
    b += cstr("P1") + cstr("") + struct.pack(">3h", 3, 0, 1) + bytes([1, 0, 0, 0, 0, 1])
    b += cstr("X@G") + cstr("Y@G") + cstr("") + cstr("") + struct.pack(">2h", 0, -1)
    b += cstr("Title") + bytes([1, 1]) + struct.pack("<4d", 0, 1, 0, 2) + bytes(6)
    b += cstr("x label") + cstr("y label")
    b += bytes(6) + bytes(72) + cstr("") + cstr("") + b"\xff" * 16
    b += struct.pack(">ihi", 100, fit, 0)
    return b


@pytest.mark.parametrize("code,kind", sorted(legacy.FIT_CODES.items()))
def test_graph_fit(code, kind):
    raw = graph_section(code)
    g = legacy._read_graph_section(legacy._Stream(raw, 0), "G", len(raw), [])
    assert g["fit"] == kind and g["plots"][0]["fit"] == kind
    assert (g["x_label"], g["y_range"]) == ("x label", [0.0, 2.0])


def test_truncated_graph_has_no_fit():
    raw = graph_section(5)[:-8]
    g = legacy._read_graph_section(legacy._Stream(raw, 0), "G", len(raw), [])
    assert "fit" not in g and g["y_label"] == "y label"


def kinetic_info(**red) -> dict:
    settings = dict(legacy.DEFAULT_SETTINGS, mode="kinetic")
    reduction = {"wavelength_combination": "L1", "label": "L1-L2", "formula": "Average(!KinPlot)",
                 "wavelength_choice": "named", "wavelength_name": "L1-L2", "wavelength_formula": "L1",
                 "kinetic_formula": "Average(!KinPlot)", "plate_blank": False, "subtract_preread": True,
                 "kinetic": "time_to_vmax", "vmax_points": 5, "lag_time": 10, "end_time": 200,
                 "min_od": 0.0, "max_od": 0.3, "onset_od": 0.05, "relative_od": True}
    reduction.update(red)
    return {"plates": [{"name": "P", "settings": settings, "data": None, "template": {},
                        "mask": ["C3", "Z9"], "reduction": reduction,
                        "display": {"type": "threshold", "with_number": False, "low": 0.2, "high": 0.8}}],
            "groups": [], "notes": [], "graphs": [{"name": "G", "fit": "4param", "plots": []}]}


def test_document_mapping():
    d = document_from_legacy(kinetic_info())
    p = [s for s in d["sections"] if s["kind"] == "plate"][0]
    r = p["reduction"]
    assert r["wavelength"] == "!L1-!L2" and r["kinetic"] == "time_to_vmax"
    assert (r["vmax_points"], r["lag_time"], r["end_time"]) == (5, 10, 200)
    assert (r["min_od"], r["max_od"], r["onset_od"]) == (0.0, 0.3, 0.05)
    assert r["zero_first"] is True and r["plate_blank"] is False and r["use_preread"] is True
    assert p["display"] == {"view": "reduced", "type": "threshold", "low": 0.2, "high": 0.8}
    assert p["mask"] == ["C3"]
    assert [s["fit"] for s in d["sections"] if s["kind"] == "graph"] == ["4param"]
    assert d["import_notices"] == []


def test_document_mapping_custom_and_raw():
    d = document_from_legacy(kinetic_info(wavelength_choice="custom", wavelength_formula="L1/L2",
                                          kinetic="custom", kinetic_formula="Max(!KinPlot)", vmax_points=1))
    p = [s for s in d["sections"] if s["kind"] == "plate"][0]
    assert p["reduction"]["wavelength"] == "!L1/!L2" and p["reduction"]["kinetic"] == "custom"
    assert p["reduction"]["kinetic_formula"] == "Max(!KinPlot)" and p["reduction"]["vmax_points"] is None
    info = kinetic_info()
    info["plates"][0]["display"] = {"type": "raw", "with_number": True, "low": 1.0, "high": 1.0}
    info["graphs"][0].pop("fit")
    d = document_from_legacy(info)
    p = [s for s in d["sections"] if s["kind"] == "plate"][0]
    assert p["display"]["view"] == "raw" and (p["display"]["low"], p["display"]["high"]) == (0.0, 1.0)
    assert any("Linear" in n for n in d["import_notices"])
