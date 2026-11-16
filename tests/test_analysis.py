import math

import pytest

from odstudio import document as D
from odstudio import fits, textio
from odstudio.formula import EMPTY, Err, Evaluator, FormulaError, check


def ev(text, **names):
    low = {k.lower(): v for k, v in names.items()}
    return Evaluator(lambda n, s: low.get(n.lower(), Err(102))).eval(text)


@pytest.mark.parametrize("text,expected", [
    ("1+2*3", 7), ("(1+2)*3", 9), ("2^3^2", 512), ("-2^2", -4), ("10 mod 4", 2), ("5=5", True),
    ("3<>5", True), ("30>=35", False), ("Not True", False), ("True And False", False),
    ("True Or False", True), ('If(5>3,"pass","fail")', "pass"), ("Abs(-10)", 10),
    ("Ceil(1.9)", 2), ("Ceil(-1.9)", -1), ("Floor(-1.9)", -2), ("Int(-6.9)", -6),
    ("Fact(4)", 24), ("Sign(-25)", -1), ("Round(3.456,2)", 3.46), ("Sqrt(64)", 8),
    ('Concat("SOFTmax ","Pro")', "SOFTmax Pro"), ('"a"+"b"', "ab"), ('Left("SOFTmax Pro",4)', "SOFT"),
    ('Mid("SOFTmax Pro",5,3)', "max"), ('Right("SOFTmax Pro",7)', "max Pro"),
    ('Len("SOFTmax Pro")', 11), ('Upper("ab")', "AB"), ('Val("24")', 24), ('Text(26)', "26"),
    ('Exact("a","A")', False), ('"a"="A"', True),
])
def test_formula_scalars(text, expected):
    got = ev(text)
    if isinstance(expected, float) or (isinstance(expected, int) and not isinstance(expected, bool)):
        assert got == pytest.approx(expected)
    else:
        assert got == expected


def test_formula_help_examples():
    assert ev("Acos(-0.25)") == pytest.approx(1.82348, abs=1e-5)
    assert ev("Atan2(1,1)") == pytest.approx(0.78540, abs=1e-5)
    assert ev("Cosh(4)") == pytest.approx(27.30823, abs=1e-5)
    assert ev("Fract(34.5678)") == pytest.approx(0.5678)
    assert ev("Log10(5)") == pytest.approx(0.69897, abs=1e-5)
    assert ev("Tanh(-2)") == pytest.approx(-0.96403, abs=1e-5)
    assert ev("Pi") == pytest.approx(math.pi)


def test_formula_lists_and_statistics():
    vals = [[1.0, 2.0, 3.0], [4.0, 6.0]]
    assert ev("Average(Values)", Values=vals) == [2.0, 5.0]
    assert ev("Average(Average(Values))", Values=vals) == 3.5
    assert ev("Values*2", Values=vals) == [[2.0, 4.0, 6.0], [8.0, 12.0]]
    assert ev("Values-M", Values=vals, M=[2.0, 5.0]) == [[-1.0, 0.0, 1.0], [-1.0, 1.0]]
    assert ev("Stdev(L)", L=[1.0, 2.0, 3.0]) == pytest.approx(1.0)
    assert ev("Stdevp(L)", L=[1.0, 3.0]) == pytest.approx(1.0)
    assert ev("Cv(L)", L=[9.0, 11.0]) == pytest.approx(math.sqrt(2) / 10 * 100)
    assert ev("Max(L)-Min(L)+Sum(L)", L=[1.0, 2.0, 3.0]) == 8
    assert ev("Count(L)", L=[1.0, EMPTY, Err(103)]) == 1
    assert ev("ItemCount(L)", L=[1.0, EMPTY, Err(103)]) == 3
    assert ev("Index(L)", L=[5.0, 6.0]) == [1.0, 2.0]
    assert ev("Item(L,2)", L=[5.0, 6.0]) == 6.0
    assert ev("If(L>1,1,0)", L=[1.0, 2.0]) == [0, 1]
    assert ev("Slope(X,Y)", X=[1.0, 2.0, 3.0], Y=[3.0, 5.0, 7.0]) == pytest.approx(2)
    assert ev("Intercept(X,Y)", X=[1.0, 2.0, 3.0], Y=[3.0, 5.0, 7.0]) == pytest.approx(1)


def test_formula_errors():
    assert ev("1/0") == Err(109)
    assert ev("Sqrt(-1)") == Err(109)
    assert ev("MakeErr(103)") == Err(103) and str(Err(103)) == "Masked"
    assert ev("IsErr(1/0)") is True and ev("IsEmpty(MakeErr(101))") is True
    assert ev("Nope+1") == Err(102)
    assert ev("Average(L)", L=[Err(103), 2.0, 4.0]) == 3.0
    for bad, msg in [("", "Empty expression"), ("1+", "Unexpected end"), ("(1+2", "Missing right"),
                     ("1+2)", "Extra right"), ('"abc', "Missing quotation"), ("1 2", "Operator expected"),
                     ("#", "Illegal character")]:
        assert msg in check(bad)
    with pytest.raises(FormulaError, match="Too few"):
        ev("Round(1)")
    with pytest.raises(FormulaError, match="Unknown identifier"):
        ev("Bogus(1)")
    assert check("Average(Values)") is None and check("'Std Curve'@Graph#1") is None


def test_fits_recover_known_curves():
    xs = [1.0, 2.0, 4.0, 8.0, 16.0]
    f = fits.fit("linear", xs, [0.1 + 0.05 * x for x in xs])
    assert f.params["A"] == pytest.approx(0.1) and f.params["B"] == pytest.approx(0.05)
    assert f.r2 == pytest.approx(1) and f.inverse(0.35) == pytest.approx(5)
    f = fits.fit("semilog", xs, [0.2 + 0.3 * math.log10(x) for x in xs])
    assert f.params["B"] == pytest.approx(0.3) and f.inverse(f.predict(3.0)) == pytest.approx(3)
    f = fits.fit("loglog", xs, [10 ** (0.1 + 0.9 * math.log10(x)) for x in xs])
    assert f.params["B"] == pytest.approx(0.9) and f.inverse(f.predict(5.0)) == pytest.approx(5)
    f = fits.fit("quadratic", xs, [1 + 2 * x + 0.5 * x * x for x in xs])
    assert f.params["C"] == pytest.approx(0.5) and f.inverse(f.predict(3.0)) == pytest.approx(3)
    true = (0.05, 1.3, 6.0, 2.1)
    xs4 = [0.5, 1, 2, 4, 8, 16, 32, 64]
    ys4 = [(true[0] - true[3]) / (1 + (x / true[2]) ** true[1]) + true[3] for x in xs4]
    f = fits.fit("4param", xs4, ys4)
    assert f.ok and f.r2 == pytest.approx(1, abs=1e-6)
    for k, v in zip("ABCD", true):
        assert f.params[k] == pytest.approx(v, rel=1e-3)
    assert f.inverse(f.predict(10.0)) == pytest.approx(10, rel=1e-4)
    assert f.inverse(5.0) is None                    # above the top asymptote
    assert not fits.fit("linear", [1.0], [2.0]).ok
    assert not fits.fit("4param", [1.0, 2.0, 3.0], [1.0, 2.0, 3.0]).ok


def grid(fn):
    return [[fn(r, c) for c in range(12)] for r in range(8)]


def endpoint_doc():
    doc = D.new_document()
    plate = doc["sections"][0]
    plate["data"] = {"mode": "endpoint", "wavelengths": [405, 650], "times": [0], "temps": [37.0],
                     "sweep": [], "saturated": [], "imported": False, "setpoint": 37, "read_at": 0,
                     "first_col": 1, "last_col": 12,
                     "values": [[grid(lambda r, c: 0.1 + 0.1 * c + 0.01 * r), grid(lambda r, c: 0.05)]]}
    plate["settings"]["wavelengths"] = [405, 650]
    return doc, plate


def test_endpoint_reduction_blanks_and_mask():
    doc, plate = endpoint_doc()
    plate["reduction"]["wavelength"] = "!L1-!L2"
    v = D.compute(doc)["sections"][0]
    assert v["wells"]["A1"]["raw"] == [pytest.approx(0.1), 0.05]
    assert v["wells"]["A1"]["num"] == pytest.approx(0.05)
    assert v["wells"]["B3"]["num"] == pytest.approx(0.26)
    D.assign_wells(doc, plate, ["A1", "B1"], None, wtype="plateblank")
    v = D.compute(doc)["sections"][0]
    assert v["blank"] == pytest.approx(0.055)
    assert v["wells"]["A2"]["num"] == pytest.approx(0.15 - 0.055)
    plate["mask"] = ["A2"]
    assert D.compute(doc)["sections"][0]["wells"]["A2"]["text"] == "Masked"
    plate["reduction"]["wavelength"] = "Log10(L1/L2)"
    assert D.compute(doc)["sections"][0]["wells"]["A3"]["num"] == pytest.approx(
        math.log10(0.3 / 0.05) - (math.log10(0.1 / 0.05) + math.log10(0.11 / 0.05)) / 2)


def test_standard_curve_workflow():
    doc, plate = endpoint_doc()
    plate["reduction"]["wavelength"] = "!L1"
    # standards in columns 1-4 (rows A,B replicate), unknown in column 6
    D.series_fill(doc, plate, [f"{r}{c}" for r in "AB" for c in range(1, 5)], "Standards", "Sta01",
                  start_from="left", start_value=1.0, op="*", step=2.0)
    D.assign_wells(doc, plate, ["A6", "B6"], "Unknowns", "Unk01")
    g = D.add_section(doc, "graph")
    g["plots"].append({"name": "Plot#1", "group": "Standards", "x": "Concentration", "y": "MeanValue",
                       "xerr": None, "yerr": None})
    g["fit"] = "linear"
    doc["groups"]["Unknowns"]["columns"].append(
        {"name": "Result", "formula": "InterpX(Plot#1@Graph#1,Values)", "decimals": 3, "hidden": False, "style": {}})
    doc["groups"]["Unknowns"]["summaries"].append(
        {"name": "Mean", "formula": "Average(Result)", "decimals": 2, "description": "", "hide_name": False})
    view = D.compute(doc)
    assert view["problems"] == []
    std = next(s for s in view["sections"] if s["kind"] == "group" and s["name"] == "Standards")
    assert [c["name"] for c in std["columns"]][:2] == ["Sample", "Concentration"]
    first = [r["cells"] for r in std["rows"] if r["first"]]
    assert [r[0] for r in first] == ["Sta01", "Sta02", "Sta03", "Sta04"]
    assert [r[1] for r in first] == ["1.000", "2.000", "4.000", "8.000"]
    assert std["rows"][0]["cells"][2] == "A1" and std["rows"][1]["cells"][2] == "B1"
    assert first[0][4] == "0.105"                      # mean of 0.10 and 0.11
    graph = next(s for s in view["sections"] if s["kind"] == "graph")
    assert len(graph["plots"][0]["points"]) == 4 and graph["plots"][0]["fit"]["ok"]
    unk = next(s for s in view["sections"] if s["kind"] == "group" and s["name"] == "Unknowns")
    # OD(col6)=0.6 lies between Sta03 (0.305 @4) and Sta04 (0.405 @8) extrapolated linearly
    res = float(unk["rows"][0]["cells"][-1])
    f = graph["plots"][0]["fit"]["params"]
    assert res == pytest.approx((0.6 - f["A"]) / f["B"], abs=2e-3)
    assert unk["summaries"][0]["text"] == f"{(res + float(unk['rows'][1]['cells'][-1])) / 2:.2f}"
    # rename keeps everything connected; deleting the group clears its wells
    D.rename_group(doc, "Unknowns", "Samples")
    assert plate["template"]["A6"]["group"] == "Samples"
    D.delete_section(doc, D.section_by_name(doc, "Samples")["id"])
    assert "A6" not in plate["template"] and "Samples" not in doc["groups"]


def test_group_blank_and_circular_reference():
    doc, plate = endpoint_doc()
    D.assign_wells(doc, plate, ["A1"], "Unknowns", wtype="groupblank")
    D.assign_wells(doc, plate, ["A2"], "Unknowns", "U1")
    cols = doc["groups"]["Unknowns"]["columns"]
    view = D.compute(doc)
    grp = next(s for s in view["sections"] if s["kind"] == "group")
    assert grp["rows"][0]["cells"][2] == "0.100" and grp["blank"] == pytest.approx(0.1)
    cols.append({"name": "X", "formula": "Y+1", "decimals": 3, "hidden": False, "style": {}})
    cols.append({"name": "Y", "formula": "X+1", "decimals": 3, "hidden": False, "style": {}})
    assert any("circular" in p for p in D.compute(doc)["problems"])


def kinetic_plate(slope_per_s=0.001, n=11, dt=30):
    doc = D.new_document()
    plate = doc["sections"][0]
    plate["settings"].update(mode="kinetic", run_time=(n - 1) * dt, interval=dt)
    plate["data"] = {"mode": "kinetic", "wavelengths": [405], "times": [i * dt for i in range(n)],
                     "temps": [25.0] * n, "sweep": [], "saturated": [], "imported": False,
                     "setpoint": 0, "read_at": 0, "first_col": 1, "last_col": 12,
                     "values": [[grid(lambda r, c, i=i: 0.1 + slope_per_s * (c + 1) * i * dt)] for i in range(n)]}
    return doc, plate


def test_kinetic_reductions():
    doc, plate = kinetic_plate()
    v = D.compute(doc)["sections"][0]
    assert v["unit"] == "mOD/min"
    assert v["wells"]["A1"]["num"] == pytest.approx(60.0)        # 0.001 OD/s
    assert v["wells"]["A3"]["num"] == pytest.approx(180.0)
    assert v["wells"]["A1"]["line"][0] == [0, pytest.approx(0.1)]
    plate["reduction"].update(kinetic="onset", onset_od=0.15)
    assert D.compute(doc)["sections"][0]["wells"]["A1"]["num"] == pytest.approx(150.0)
    plate["reduction"].update(kinetic="time_to_vmax", vmax_points=3)
    assert D.compute(doc)["sections"][0]["wells"]["A1"]["num"] == pytest.approx(30.0)
    plate["reduction"].update(kinetic="custom", kinetic_formula="Max(!KinPlot)-Min(!KinPlot)")
    assert D.compute(doc)["sections"][0]["wells"]["A1"]["num"] == pytest.approx(0.3)
    plate["reduction"].update(kinetic="vmax", vmax_points=None, lag_time=1000)
    assert D.compute(doc)["sections"][0]["wells"]["A1"]["text"] == "Limits?"
    plate["reduction"].update(lag_time=0, max_od=0.2)             # only the first 4 points remain
    assert D.compute(doc)["sections"][0]["wells"]["A1"]["num"] == pytest.approx(60.0)


def test_spectrum_reduction():
    doc = D.new_document()
    plate = doc["sections"][0]
    sweep = list(range(400, 501, 10))
    plate["settings"].update(mode="spectrum", start=400, stop=500, step=10)
    plate["data"] = {"mode": "spectrum", "wavelengths": [], "times": [0] * len(sweep), "temps": [25.0] * len(sweep),
                     "sweep": sweep, "saturated": [], "imported": False, "setpoint": 0, "read_at": 0,
                     "first_col": 1, "last_col": 12,
                     "values": [[grid(lambda r, c, w=w: 1.0 - abs(w - 450) / 100)] for w in sweep]}
    assert D.compute(doc)["sections"][0]["wells"]["C5"]["num"] == 450
    plate["reduction"]["spectrum"] = "minlambda"
    assert D.compute(doc)["sections"][0]["wells"]["C5"]["num"] in (400, 500)
    plate["reduction"].update(spectrum="custom", spectrum_formula="Max(!KinPlot)")
    assert D.compute(doc)["sections"][0]["wells"]["C5"]["num"] == pytest.approx(1.0)


def test_template_series_directions_and_paste():
    doc, plate = endpoint_doc()
    D.series_fill(doc, plate, ["A1", "A2", "B1", "B2", "C1", "C2"], "Standards", "Sta01",
                  start_from="top", start_value=100.0, op="/", step=10.0)
    t = plate["template"]
    assert (t["A1"]["sample"], t["A2"]["sample"], t["B1"]["sample"], t["C2"]["sample"]) == ("Sta01", "Sta01", "Sta02", "Sta03")
    assert (t["A1"]["value"], t["B1"]["value"], t["C1"]["value"]) == (100.0, 10.0, 1.0)
    D.series_fill(doc, plate, ["A1", "A2", "B1", "B2"], "Standards", "Sta01", start_from="right",
                  start_value=1.0, op="+", step=1.0)
    assert (t["A2"]["sample"], t["A1"]["sample"], t["A1"]["value"]) == ("Sta01", "Sta02", 2.0)
    with pytest.raises(ValueError):
        D.paste_grid(plate, [[5.0] * 12] * 8)
    D.paste_grid(plate, [[0.5] * 12] * 8)
    assert plate["data"]["imported"] and D.compute(doc)["sections"][0]["wells"]["H12"]["num"] == 0.5
    assert D.strip_data(doc)["sections"][0]["data"] is None and plate["data"] is not None


@pytest.mark.parametrize("layout", ["time", "plate"])
def test_text_export_roundtrip(layout):
    doc, plate = kinetic_plate(n=4, dt=10)
    text = textio.export_text(plate["data"], layout, True)
    first = text.split("\r\n")[0].split("\t")
    assert first[0] == "Time(hh:mm:ss)" and first[1] == "Temperature(°C)"
    assert first[2] == ("A1" if layout == "time" else "1")
    back = textio.import_text(text)
    assert back["mode"] == "kinetic" and back["times"] == [0, 10, 20, 30]
    assert back["values"][3][0][0][2] == pytest.approx(plate["data"]["values"][3][0][0][2], abs=1e-3)
    assert back["temps"][0] == 25.0


def test_kinetic_and_spectrum_statistics_for_well_graph():
    doc, plate = kinetic_plate()
    w = D.compute(doc)["sections"][0]["wells"]["A2"]
    st = w["stats"]
    assert st["vmax"] == pytest.approx(120.0) and st["r2"] == pytest.approx(1.0)
    assert st["points"] == "11/11" and st["onset"] == pytest.approx(50.0)
    plate["reduction"]["vmax_points"] = 4
    assert D.compute(doc)["sections"][0]["wells"]["A2"]["stats"]["points"] == "4/11"
    plate["reduction"]["kinetic"] = "onset"
    assert D.compute(doc)["sections"][0]["wells"]["A2"]["line"] is not None


def test_minimum_read_interval_matches_softmax_pro_estimate():
    st = D.default_settings()
    st.update(mode="kinetic", autocalibrate=False)
    assert D.read_seconds(st) == 9                    # full plate, one wavelength
    st["autocalibrate"] = True
    assert D.read_seconds(st) == 11
    st.update(first_col=1, last_col=2, autocalibrate=False)
    assert D.read_seconds(st) == 2                    # never below 2 s
    st.update(first_col=1, last_col=12, wavelengths=[405, 650], mix_between=True, mix_between_s=3)
    assert D.read_seconds(st) == 23                   # 19.7 s rounded up, plus 3 s shaking


def test_document_from_legacy_builds_sections_and_groups():
    flat = [[0.1] * 12 for _ in range(8)]
    info = {"plates": [{"name": "Standard", "settings": {"mode": "endpoint", "wavelengths": [450]},
                        "data": {"mode": "endpoint", "wavelengths": [450], "times": [0], "temps": [25.0],
                                 "values": [[flat]]},
                        "template": {"A1": {"type": "sample", "group": "Standard", "sample": "S1", "value": 2.0},
                                     "B1": {"type": "groupblank", "group": "Standard"},
                                     "H12": {"type": "plateblank"}, "Z9": {"type": "sample", "group": "x"}},
                        "mask": ["C3", "nope"]}],
            "groups": [{"name": "Standard", "descriptor": "Concentration", "units": "ug/ml",
                        "columns": [{"name": "Values", "formula": "!WellValues", "decimals": 3}],
                        "summaries": [{"name": "N", "formula": "Count(Values)"}]}],
            "notes": [{"name": "Notes", "text": "line 1\rline <2>"}],
            "graphs": [{"name": "Graph#1", "fit": "4-Parameter",
                        "plots": [{"name": "Plot#1", "group": "Standard", "x": "Concentration", "y": "Values"}]}]}
    doc = D.document_from_legacy(info)
    names = [(s["kind"], s["name"]) for s in doc["sections"]]
    # the group could not take the plate's name, so it is renamed; references follow it
    assert names[0] == ("plate", "Standard") and ("group", "Standard (2)") in names
    plate = doc["sections"][0]
    assert plate["template"]["A1"]["group"] == "Standard (2)" and "Z9" not in plate["template"]
    assert plate["mask"] == ["C3"] and doc["groups"]["Standard (2)"]["units"] == "ug/ml"
    graph = next(s for s in doc["sections"] if s["kind"] == "graph")
    assert graph["fit"] == "4param" and graph["plots"][0]["group"] == "Standard (2)"
    notes = next(s for s in doc["sections"] if s["kind"] == "notes")
    assert notes["html"] == "line 1<br>line &lt;2&gt;"
    view = D.compute(doc)
    assert view["problems"] == []
    grp = next(s for s in view["sections"] if s["kind"] == "group")
    assert grp["summaries"][0]["text"] == "1.000"
    assert D.document_from_legacy({"plates": []})["sections"][0]["kind"] == "plate"


def test_new_kinetic_reductions_and_options():
    doc, plate = kinetic_plate()            # A1: 0.1 + 0.001*t, t = 0..300 s step 30
    red = plate["reduction"]

    def a1(**kw):
        red.update(kw)
        return D.compute(doc)["sections"][0]["wells"]["A1"]

    assert a1(kinetic="time_at_max")["num"] == 300 and a1(kinetic="time_at_min")["num"] == 0
    assert a1(kinetic="time_at_half_max")["num"] == pytest.approx(150)
    assert a1(kinetic="auc")["num"] == pytest.approx(0.1 * 300 + 0.001 * 300 ** 2 / 2)
    assert a1(kinetic="slope")["num"] == pytest.approx(60.0)
    assert D.compute(doc)["sections"][0]["unit"] == "mOD/min"
    assert a1(kinetic="custom", kinetic_formula="!Vmax/2")["num"] == pytest.approx(30.0)
    assert a1(kinetic_formula="!VmaxCorr")["num"] == pytest.approx(1.0)
    assert a1(kinetic_formula="!TimeAtHalfMax+!AUC*0")["num"] == pytest.approx(150)
    w = a1(kinetic="time_at_max", zero_first=True)
    assert w["ys"][0] == pytest.approx(0.0) and w["ys"][-1] == pytest.approx(0.3)
    red.update(zero_first=False)
    # smoothing keeps a straight line straight, and evens out a spike
    plate["data"]["values"][5][0][0][0] = 2.0
    assert a1(kinetic="time_at_max", smoothing=0)["num"] == 150
    assert a1(smoothing=9)["ys"][5] < 0.5
    red.update(smoothing=0)
    # % transmittance: 100 * 10^-OD
    assert a1(kinetic="custom", kinetic_formula="Item(!KinPlot,1)", raw_mode="transmittance")["num"] == pytest.approx(100 * 10 ** -0.1)


def test_new_spectrum_reductions():
    doc = D.new_document()
    plate = doc["sections"][0]
    sweep = list(range(400, 501, 10))
    plate["settings"].update(mode="spectrum", start=400, stop=500, step=10)
    plate["data"] = {"mode": "spectrum", "wavelengths": [], "times": [0] * len(sweep), "temps": [25.0] * len(sweep),
                     "sweep": sweep, "saturated": [], "imported": False, "setpoint": 0, "read_at": 0,
                     "first_col": 1, "last_col": 12,
                     "values": [[grid(lambda r, c, w=w: 1.0 - abs(w - 450) / 100)] for w in sweep]}
    red = plate["reduction"]

    def c5(**kw):
        red.update(kw)
        v = D.compute(doc)["sections"][0]
        return v["wells"]["C5"]["num"], v["unit"]

    assert c5(spectrum="max_od") == (pytest.approx(1.0), "OD")
    assert c5(spectrum="min_od")[0] == pytest.approx(0.5)
    assert c5(spectrum="auc") == (pytest.approx(75.0), "OD·nm")


def test_michaelis_menten_from_well_vmax_with_fit_statistics():
    """Substrate series across wells -> Vmax of each well -> Michaelis-Menten fit of Vmax vs [S]."""
    vmax_true, km_true = 400.0, 2.0                       # mOD/min, mM
    conc = [0.25, 0.5, 1, 2, 4, 8, 16, 32]
    n, dt = 11, 30
    doc = D.new_document()
    plate = doc["sections"][0]
    plate["settings"].update(mode="kinetic", run_time=(n - 1) * dt, interval=dt)

    def od(r, c, i):
        s = conc[c] if c < 8 else 0.0
        rate = vmax_true * s / (km_true + s) / 60000.0      # OD per second
        return 0.05 + rate * i * dt + (0.0004 if r % 2 else -0.0004) * (i % 2)
    plate["data"] = {"mode": "kinetic", "wavelengths": [405], "times": [i * dt for i in range(n)],
                     "temps": [30.0] * n, "sweep": [], "saturated": [], "imported": False, "setpoint": 30,
                     "read_at": 0, "first_col": 1, "last_col": 12,
                     "values": [[[[od(r, c, i) for c in range(12)] for r in range(8)]] for i in range(n)]}
    D.ensure_group(doc, "Substrate", "Standards", "Concentration", "mM")
    D.series_fill(doc, plate, [f"{R}{c}" for R in "ABC" for c in range(1, 9)], "Substrate", "S01",
                  start_from="left", start_value=0.25, op="*", step=2.0)
    g = D.add_section(doc, "graph")
    g["plots"].append({"name": "MM", "group": "Substrate", "x": "Concentration", "y": "Values",
                       "xerr": None, "yerr": None})
    g["fit"] = "mm"
    g["fit_options"] = {**D.default_fit_options(), "bands": True}
    doc["groups"]["Substrate"]["summaries"] += [
        {"name": "Km", "formula": "EC50(MM@Graph#1)", "decimals": 3},
        {"name": "Vmax", "formula": "ParmA(MM@Graph#1)", "decimals": 1},
        {"name": "KmErr", "formula": "ParmErr(MM@Graph#1, \"B\")", "decimals": 3}]
    view = D.compute(doc)
    assert view["problems"] == []
    graph = next(s for s in view["sections"] if s["kind"] == "graph")
    p = graph["plots"][0]
    assert len(p["points"]) == 24                          # every well is a point
    f = p["fit"]
    assert f["ok"] and f["label"] == "Michaelis-Menten" and f["names"]["B"] == "Km"
    assert f["params"]["A"] == pytest.approx(vmax_true, rel=0.02) and f["params"]["B"] == pytest.approx(km_true, rel=0.05)
    assert f["ci"]["B"][0] - 1e-9 <= km_true <= f["ci"]["B"][1] + 1e-9 and f["n"] == 24 and f["dof"] == 22
    assert len(p["curve"][0]) == 6 and p["curve"][0][0] == 0.0         # bands drawn from x = 0
    grp = next(s for s in view["sections"] if s["kind"] == "group")
    sums = {m["name"]: m["text"] for m in grp["summaries"]}
    assert float(sums["Km"]) == pytest.approx(km_true, rel=0.05) and float(sums["KmErr"]) >= 0
    # weighting needing error bars says so
    g["fit_options"]["weighting"] = "1/sd2"
    f = next(s for s in D.compute(doc)["sections"] if s["kind"] == "graph")["plots"][0]["fit"]
    assert "error bars" in f["error"]
