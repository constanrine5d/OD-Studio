#!/usr/bin/env python3
"""Generate the built-in protocol library (src/odstudio/protocols/*.odxp).

    PYTHONPATH=src python tools/make_protocols.py

Each protocol is an ordinary protocol file: instrument settings, template,
reduction, groups with result columns and summaries, a standard-curve graph,
notes and an optional workflow.  Concentrations and volumes are typical
starting points; the notes ask the user to follow their reagent's instructions.
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from odstudio import document as D  # noqa: E402

OUT = os.path.join(os.path.dirname(__file__), "..", "src", "odstudio", "protocols")
ROWS = "ABCDEFGH"


def col(name, formula, decimals=3):
    return {"name": name, "formula": formula, "decimals": decimals, "hidden": False, "style": {}}


def summary(name, formula, description="", decimals=3, hide_name=False):
    return {"name": name, "formula": formula, "description": description, "decimals": decimals,
            "hide_name": hide_name, "style": {}}


def notes(doc, title, paragraphs):
    s = D.add_section(doc, "notes")
    s["name"] = "Protocol"
    html = f"<h2>{title}</h2>" + "".join(f"<p>{p}</p>" for p in paragraphs)
    s["html"] = html
    doc["sections"].remove(s)
    doc["sections"].insert(0, s)
    return s


def standards(doc, plate, group, values, units, rows=None, cols=(1, 2)):
    """One standard per row, replicated across ``cols``."""
    D.ensure_group(doc, group, "Standards", "Concentration", units)
    for i, v in enumerate(values):
        r = (rows or ROWS)[i]
        D.assign_wells(doc, plate, [f"{r}{c}" for c in cols], group, f"Std{i + 1:02d}", v)


def unknowns(doc, plate, group="Unknowns", pairs=((3, 4), (5, 6), (7, 8), (9, 10), (11, 12)), rows=ROWS):
    D.ensure_group(doc, group, "Unknowns", "Factor", "")
    n = 1
    for a, b in pairs:
        for r in rows:
            D.assign_wells(doc, plate, [f"{r}{a}", f"{r}{b}"], group, f"Un{n:02d}", 1.0)
            n += 1


def curve(doc, group, fit, name="Standard Curve", x="Concentration", y="MeanValue", **fit_options):
    g = D.add_section(doc, "graph")
    g["name"] = name
    g["plots"] = [{"name": "STD", "group": group, "x": x, "y": y, "xerr": None, "yerr": "Std.Dev." if y == "MeanValue" else None}]
    g["fit"] = fit
    g["fit_options"] = {**D.default_fit_options(), **fit_options}
    g["xaxis"]["label"] = x
    g["yaxis"]["label"] = y
    return g


def interpolate(doc, group, graph="'Standard Curve'", std_group="Standards", units=""):
    g = doc["groups"][group]
    g["columns"] += [
        col("Result", f"InterpX(STD@{graph},Values)"),
        col("MeanResult", "Average(Result)"),
        col("Adj.Result", "MeanResult*!Factor"),
        col("Range", f'If(MeanValue<MinStd@{std_group} or MeanValue>MaxStd@{std_group},"outside curve","")', None),
    ]
    sg = doc["groups"][std_group]
    sg["summaries"] += [summary("MinStd", "Min(MeanValue)", "Lowest standard:"),
                        summary("MaxStd", "Max(MeanValue)", "Highest standard:")]
    g["summaries"].append(summary("Note", '""', f"Adj.Result = MeanResult × dilution factor ({units}). "
                                               "Set each sample's Factor in the template.", 0, True))


def workflow(doc, steps):
    w = D.add_section(doc, "workflow", index=1)
    sid = 1

    def ids(lst):
        nonlocal sid
        for st in lst:
            st["sid"] = sid
            sid += 1
            if st["type"] == "cycle":
                ids(st["steps"])
    ids(steps)
    w["steps"] = steps
    return w


def protein(name, title, wl, std_values, units, fit, incubation, paragraphs, temp=0):
    doc = D.new_document()
    plate = doc["sections"][0]
    plate["name"] = title.split(" ")[0]
    plate["settings"].update(mode="endpoint", wavelengths=[wl])
    standards(doc, plate, "Standards", std_values, units, rows=ROWS[:7])
    D.assign_wells(doc, plate, ["H1", "H2"], None, wtype="plateblank")
    unknowns(doc, plate)
    curve(doc, "Standards", fit)
    interpolate(doc, "Unknowns", units=units)
    steps = []
    if temp:
        steps.append({"type": "temperature", "celsius": temp, "wait": True})
    steps += [{"type": "drawer", "open": True},
              {"type": "message", "text": "Put the plate with samples and reagent in the drawer, then press Continue."},
              {"type": "drawer", "open": False}, {"type": "shake", "seconds": 30},
              {"type": "delay", "seconds": incubation}]
    if temp:
        steps.append({"type": "temperature", "celsius": 0, "wait": False})
    steps.append({"type": "read", "plate": plate["id"]})
    workflow(doc, steps)
    notes(doc, title, paragraphs + [
        "<b>Layout.</b> Standards in rows A–G of columns 1–2 (duplicates); buffer blanks in H1–H2, "
        "subtracted from every well. Unknowns in duplicate pairs of columns 3–12 (Un01 … Un40); "
        "change the template to match your plate.",
        "<b>Results.</b> The Unknowns table interpolates each well on the standard curve "
        f"({fit} fit). Adj.Result multiplies by the dilution factor. Values outside the standards are flagged.",
        "<b>Workflow.</b> Run it from the Workflow section to have the reader shake, incubate and read; "
        "or just press Read when the plate is ready."])
    return name, doc


def build():
    out = []
    out.append(protein(
        "Bradford protein assay", "Bradford protein assay (Coomassie), 595 nm", 595,
        [0.125, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0], "mg/ml", "quadratic", 10 * 60,
        ["Coomassie dye binds protein and shifts its absorbance to 595 nm. Typical microplate format: "
         "5 µl of standard or sample plus 250 µl of dye reagent, 10 minutes at room temperature. "
         "Follow your reagent supplier's instructions for volumes and timing.",
         "The response is curved, so a quadratic fit is used. Read within an hour; the colour drifts."]))
    out.append(protein(
        "BCA protein assay", "BCA protein assay, 562 nm", 562,
        [0.125, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0], "mg/ml", "quadratic", 30 * 60,
        ["Protein reduces Cu²⁺ to Cu⁺, which forms a purple complex with bicinchoninic acid "
         "(562 nm). Typical microplate format: 25 µl of standard or sample plus 200 µl of working "
         "reagent, 30 minutes at 37 °C. Follow your kit's instructions.",
         "The workflow warms the chamber to 37 °C, incubates, then switches the incubator off before reading."],
        temp=37))
    out.append(protein(
        "Lowry protein assay", "Lowry protein assay, 750 nm", 750,
        [0.0625, 0.125, 0.25, 0.5, 0.75, 1.0, 1.5], "mg/ml", "quadratic", 30 * 60,
        ["Alkaline copper treatment followed by the Folin–Ciocalteu reagent gives a blue colour read at "
         "750 nm (the top of the SpectraMax 250 range). Typical incubation is 30 minutes at room "
         "temperature after adding the Folin reagent. Follow your kit's instructions."]))

    # ELISA
    doc = D.new_document()
    plate = doc["sections"][0]
    plate["name"] = "ELISA"
    plate["settings"].update(mode="endpoint", wavelengths=[450, 650])
    plate["reduction"]["wavelength"] = "!L1-!L2"
    D.ensure_group(doc, "Standards", "Standards", "Concentration", "pg/ml")
    D.series_fill(doc, plate, [f"{r}{c}" for r in ROWS[:7] for c in (1, 2)], "Standards", "Std01",
                  start_from="top", start_value=1000.0, op="/", step=2.0)
    D.assign_wells(doc, plate, ["H1", "H2"], None, wtype="plateblank")
    unknowns(doc, plate)
    curve(doc, "Standards", "4param", weighting="1/y2", bands=True)
    interpolate(doc, "Unknowns", units="pg/ml")
    workflow(doc, [{"type": "drawer", "open": True},
                   {"type": "message", "text": "Add stop solution, put the plate in the drawer and press Continue."},
                   {"type": "drawer", "open": False}, {"type": "shake", "seconds": 5},
                   {"type": "read", "plate": plate["id"]}])
    notes(doc, "Sandwich ELISA (TMB), 450 nm with 650 nm reference", [
        "Read after adding acid stop solution to the TMB substrate. The reduction subtracts the 650 nm "
        "reading (plate imperfections, scratches) from the 450 nm signal: L1−L2.",
        "<b>Layout.</b> Standards as a 2-fold dilution series from 1000 pg/ml in rows A–G of columns 1–2; "
        "zero standard (H1–H2) is the plate blank. Unknowns in duplicate pairs of columns 3–12.",
        "<b>Curve.</b> 4-parameter logistic fit weighted 1/Y², with confidence bands. Change the top "
        "standard in the template (Series) to match your kit.",
        "Follow your kit's instructions for incubation times, washes and dilutions."])
    out.append(("ELISA (TMB, 450 nm)", doc))

    # Kinetic enzyme assay
    doc = D.new_document()
    plate = doc["sections"][0]
    plate["name"] = "Kinetics"
    plate["settings"].update(mode="kinetic", wavelengths=[405], run_time=600, interval=20)
    plate["reduction"].update(kinetic="vmax", vmax_points=None)
    D.ensure_group(doc, "Samples", "Unknowns", "Factor", "")
    n = 1
    for r in ROWS[:7]:
        for a in (1, 4, 7, 10):
            D.assign_wells(doc, plate, [f"{r}{a}", f"{r}{a + 1}", f"{r}{a + 2}"], "Samples", f"S{n:02d}", 1.0)
            n += 1
    D.assign_wells(doc, plate, [f"H{c}" for c in range(1, 4)], None, wtype="plateblank")
    g = doc["groups"]["Samples"]
    g["columns"] += [col("Rate (OD/min)", "MeanValue/1000", 5),
                     col("Activity", "MeanValue/1000/(Epsilon@Samples*Path@Samples)*!Factor*1000", 4)]
    g["summaries"] += [summary("Epsilon", "18.5", "Extinction coefficient (mM⁻¹ cm⁻¹) — edit:", 2),
                       summary("Path", "0.6", "Light path (cm) for your volume — edit:", 2)]
    workflow(doc, [{"type": "temperature", "celsius": 37, "wait": True},
                   {"type": "drawer", "open": True},
                   {"type": "message", "text": "Start the reaction (add substrate), put the plate in and press Continue."},
                   {"type": "drawer", "open": False},
                   {"type": "read", "plate": plate["id"]}])
    notes(doc, "Kinetic enzyme assay, 405 nm", [
        "Reads every 20 seconds for 10 minutes and reports Vmax, the steepest slope, in mOD/min. "
        "405 nm suits p-nitrophenol products (for example from pNPP); change the wavelength for other "
        "chromophores (340 nm for NADH, read as a decrease).",
        "<b>Layout.</b> 28 samples in triplicate (S01 in A1–A3, S02 in A4–A6 …); no-enzyme "
        "controls in H1–H3 are the plate blank, so their background curve is subtracted from every well.",
        "<b>Activity.</b> Activity = rate / (ε × path) × dilution, in µmol/min per ml of "
        "sample when ε is in mM⁻¹ cm⁻¹. Edit the Epsilon and Path summaries for "
        "your product and well volume (18.5 is p-nitrophenol at alkaline pH; 0.6 cm is roughly 200 µl "
        "in a 96-well plate).",
        "Check the Well Graph: Vmax, R² and Vmax Points per well. Set Lag Time or Vmax Points under "
        "Reduction if the start of the curve is not linear."])
    out.append(("Kinetic enzyme assay (Vmax)", doc))

    # Michaelis-Menten
    doc = D.new_document()
    plate = doc["sections"][0]
    plate["name"] = "Kinetics"
    plate["settings"].update(mode="kinetic", wavelengths=[405], run_time=600, interval=20)
    D.ensure_group(doc, "Substrate", "Standards", "Concentration", "mM")
    D.series_fill(doc, plate, [f"{r}{c}" for r in "ABC" for c in range(1, 9)], "Substrate", "S01",
                  start_from="left", start_value=0.0625, op="*", step=2.0)
    D.assign_wells(doc, plate, ["A9", "B9", "C9"], None, wtype="plateblank")
    gr = curve(doc, "Substrate", "mm", name="Michaelis-Menten", x="Concentration", y="Values", bands=True)
    gr["plots"][0]["name"] = "MM"
    gr["yaxis"]["label"] = "Vmax (mOD/min)"
    g = doc["groups"]["Substrate"]
    g["summaries"] += [
        summary("Km", "EC50(MM@'Michaelis-Menten')", "Km:", 4),
        summary("KmErr", "ParmErr(MM@'Michaelis-Menten',\"B\")", "±", 4),
        summary("Vmax", "ParmA(MM@'Michaelis-Menten')", "Vmax (mOD/min):", 2),
        summary("VmaxErr", "ParmErr(MM@'Michaelis-Menten',\"A\")", "±", 2),
        summary("R2", "RSquared(MM@'Michaelis-Menten')", "R²:", 4)]
    workflow(doc, [{"type": "temperature", "celsius": 37, "wait": True},
                   {"type": "drawer", "open": True},
                   {"type": "message", "text": "Add enzyme to all wells, put the plate in and press Continue."},
                   {"type": "drawer", "open": False},
                   {"type": "read", "plate": plate["id"]}])
    notes(doc, "Michaelis–Menten (Km and Vmax)", [
        "Each well gets a different substrate concentration. The plate is read kinetically and each well's "
        "Vmax becomes one point of the substrate curve; the graph fits v = Vmax·[S]/(Km + [S]).",
        "<b>Layout.</b> A 2-fold substrate series from 0.0625 to 8 mM across columns 1–8, in triplicate "
        "(rows A–C). No-substrate wells A9–C9 are the plate blank. Change the series in the template "
        "(Series button) so that it spans roughly 0.2 × Km to 5 × Km.",
        "<b>Results.</b> Km and Vmax with standard errors are in the Substrate table summaries; the graph "
        "legend also shows the confidence intervals, and the shaded band is the 95 % confidence band. "
        "Fit Options can weight the points or fix a parameter.",
        "Vmax here is in mOD/min, as measured. To convert to concentration per minute divide by "
        "(ε × path length)."])
    out.append(("Michaelis-Menten (Km, Vmax)", doc))
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        if f.endswith((".odxp", ".smxp")) or f == "index.json":
            os.remove(os.path.join(OUT, f))
    index = []
    for name, doc in build():
        view = D.compute(doc)
        assert not view["problems"], (name, view["problems"])
        fname = "".join(ch if ch.isalnum() else "-" for ch in name.lower()).strip("-")
        while "--" in fname:
            fname = fname.replace("--", "-")
        fname += ".odxp"
        with open(os.path.join(OUT, fname), "w", encoding="utf-8") as fh:
            json.dump(D.strip_data(doc), fh, indent=1)
        first = next(s for s in doc["sections"] if s["kind"] == "notes")
        import re
        desc = re.sub(r"<[^>]+>", "", first["html"].split("</h2>", 1)[-1].split("</p>", 1)[0])
        index.append({"file": fname, "name": name, "description": desc})
        print("wrote", fname)
    with open(os.path.join(OUT, "index.json"), "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=1, ensure_ascii=False)


if __name__ == "__main__":
    main()
