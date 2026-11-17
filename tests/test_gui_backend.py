import json
import time
import urllib.request

import pytest

from odstudio.gui.app import App, UserError, sanitize_html
from odstudio.gui.server import make_server


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("ODSTUDIO_HOME", str(tmp_path / "cfg"))
    a = App(port="sim", load_prefs=False)
    a.prefs.update(autosave_folder=str(tmp_path / "auto"), assays_folder=str(tmp_path / "assays"),
                   last_dir=str(tmp_path), open_drawer_after_read=True)
    for _ in range(100):
        if a.inst.info["connected"]:
            break
        time.sleep(0.02)
    yield a
    a.inst._alive = False
    a.inst.disconnect()


def wait_read(app):
    for _ in range(400):
        if not app.inst.reading:
            return
        time.sleep(0.02)
    raise AssertionError("read did not finish")


def plate_view(app, i=0):
    return [s for s in app.state()["doc"]["sections"] if s["kind"] == "plate"][i]


def test_connects_and_reports_instrument(app):
    info = app.state()["instrument"]
    assert info["connected"] and info["model"] == "Simulator" and info["drawer"] == "OPEN"
    app.op("drawer", {"open": False})
    assert app.inst.info["drawer"] == "CLOSED"
    app.op("incubator", {"on": True, "temperature": 37})
    assert app.inst.info["setpoint"] == 37.0
    with pytest.raises(UserError):
        app.op("incubator", {"on": True, "temperature": 80})
    app.op("shake", {})
    assert app.inst.info["shaking"] is True
    assert app.op("raw_command", {"command": "QUEUE"})["reply"].strip() == "0"
    with pytest.raises(UserError, match="Unknown Command"):
        app.op("raw_command", {"command": "NOPE"})
    assert "ports" in app.op("ports", {})


def test_endpoint_read_fills_plate_and_can_be_undone(app):
    pid = plate_view(app)["id"]
    app.op("instrument_settings", {"id": pid, "settings": {"wavelengths": [405, 650], "first_col": 1, "last_col": 6}})
    assert app.op("read", {"id": pid}) == {"started": True}
    wait_read(app)
    v = plate_view(app)
    assert v["has_data"] and v["info"]["wavelengths"] == [405, 650]
    assert v["wells"]["A1"]["raw"] == [pytest.approx(0.1), pytest.approx(0.2)]
    assert "A7" not in v["wells"]
    assert app.inst.info["drawer"] == "OPEN"                 # opened when finished
    for _ in range(50):
        if any(e["text"] == "Reading finished." for e in app.events):
            break
        time.sleep(0.02)
    assert any(e["text"] == "Reading finished." for e in app.events)
    assert app.op("read", {"id": pid})["confirm"].startswith("Replace data")
    r = app.op("instrument_settings", {"id": pid, "settings": {"wavelengths": [450]}})
    assert "requires a plate with no data" in r["confirm"]
    app.op("undo", {})
    assert not plate_view(app)["has_data"]
    app.op("redo", {})
    assert plate_view(app)["has_data"]


def test_kinetic_read_stop_and_preread(app):
    pid = plate_view(app)["id"]
    app.op("instrument_settings", {"id": pid, "settings": {"mode": "kinetic", "run_time": 150, "interval": 15, "pre_read": True}})
    assert plate_view(app)["expected"] == 11
    app.op("read", {"id": pid, "preread": True})
    wait_read(app)
    assert plate_view(app)["has_preread"] and not plate_view(app)["has_data"]
    app.op("read", {"id": pid})
    wait_read(app)
    v = plate_view(app)
    assert v["info"]["points"] == 11 and v["xs"][:3] == [0, 15, 30]
    # pre-read (exp(0)/10 = 0.1 in A1) is subtracted from every point
    assert v["wells"]["A1"]["ys"][0] == pytest.approx(0.0, abs=1e-6)
    assert v["wells"]["A1"]["num"] is not None and v["unit"] == "mOD/min"
    with pytest.raises(UserError, match="less than interval"):
        app.op("instrument_settings", {"id": pid, "settings": {"run_time": 5, "interval": 15}, "confirm": True})
    with pytest.raises(UserError, match="too short. Minimum 11 seconds"):
        app.op("instrument_settings", {"id": pid, "settings": {"run_time": 100, "interval": 10}, "confirm": True})
    app.op("instrument_settings", {"id": pid, "settings": {"interval": 6, "first_col": 1, "last_col": 2, "run_time": 60}, "confirm": True})
    with pytest.raises(UserError):
        app.op("instrument_settings", {"id": pid, "settings": {"wavelengths": [100]}, "confirm": True})


def test_template_groups_graph_and_summaries(app):
    pid = plate_view(app)["id"]
    app.op("read", {"id": pid})
    wait_read(app)
    app.op("template_series", {"id": pid, "wells": ["A1", "B1", "A2", "B2", "A3", "B3"], "group": "Standards",
                               "first_sample": "Sta01", "start_from": "left", "start_value": 1, "op": "*", "step": 2})
    app.op("template_assign", {"id": pid, "wells": ["A5", "B5"], "group": "Unknowns", "sample": "Unk01"})
    app.op("template_assign", {"id": pid, "wells": ["H12"], "type": "plateblank"})
    gid = app.op("add_section", {"kind": "graph"})["id"]
    st = app.state()
    graph = next(s for s in st["doc"]["sections"] if s["id"] == gid)
    assert graph["plots"][0]["group"] == "Standards" and graph["plots"][0]["fit"]["ok"]
    app.op("add_interpolation", {"group": "Unknowns", "graph": graph["name"], "plot": "Plot#1"})
    unk = next(s for s in app.state()["doc"]["sections"] if s["name"] == "Unknowns")
    assert "Result" in [c["name"] for c in unk["columns"]]
    assert unk["rows"][0]["cells"][-2] not in ("", "Name?", "Error")
    sid = unk["id"]
    app.op("summary_save", {"id": sid, "name": "Mean", "formula": "Average(MeanResult)", "decimals": 2})
    assert next(s for s in app.state()["doc"]["sections"] if s["id"] == sid)["summaries"][0]["text"] != "Name?"
    with pytest.raises(UserError, match="Missing right"):
        app.op("column_save", {"group": "Unknowns", "name": "Bad", "formula": "Average(Values"})
    assert app.op("check_formula", {"id": sid, "formula": "Nope*2"})["error"] == "Unknown identifier"
    assert app.op("check_formula", {"id": sid, "formula": "Values*2"})["error"] is None
    app.op("column_save", {"group": "Unknowns", "name": "Twice", "formula": "Values*2", "decimals": 1, "after": "Values"})
    app.op("column_hide", {"group": "Unknowns", "names": ["Twice"]})
    app.op("group_view", {"group": "Unknowns", "show_all": True})
    app.op("graph", {"id": gid, "fit": "quadratic", "type": "scatter", "xaxis": {"log": True}})
    app.op("group_settings", {"group": "Unknowns", "name": "Samples", "descriptor": "Factor"})
    assert "Samples" in app.state()["doc"]["groups"]
    app.op("mask", {"id": pid, "wells": ["A5"]})
    assert plate_view(app)["wells"]["A5"]["text"] == "Masked"
    with pytest.raises(UserError, match="already being used"):
        app.op("group_new", {"name": "standards"})


def test_save_open_protocol_export_import(app, tmp_path):
    pid = plate_view(app)["id"]
    app.op("read", {"id": pid})
    wait_read(app)
    assert app.op("save", {}) == {"need_path": True}
    path = app.op("save", {"path": str(tmp_path / "exp1")})["path"]
    assert path.endswith(".odx") and not app.state()["dirty"] and app.state()["title"] == "exp1.odx"
    proto = app.op("save", {"path": str(tmp_path / "proto"), "protocol": True})["path"]
    assert proto.endswith(".odxp") and json.loads(open(proto).read())["sections"][0]["data"] is None
    out = app.op("export", {"path": str(tmp_path / "out"), "layout": "plate"})["path"]
    assert open(out, encoding="utf-8").read().split("\t")[1].startswith("Temperature")
    app.op("section_settings", {"id": pid, "name": "Changed"})
    app.op("revert", {})
    assert plate_view(app)["name"] != "Changed" and not app.state()["dirty"]
    app.op("new", {})
    assert not plate_view(app)["has_data"] and app.state()["title"] == "Untitled"
    app.op("open", {"path": path})
    assert plate_view(app)["wells"]["A1"]["raw"][0] == pytest.approx(0.1)
    app.op("open", {"path": proto})
    assert not plate_view(app)["has_data"] and app.state()["path"] is None
    listing = app.op("list_dir", {"path": str(tmp_path)})
    assert {"exp1.odx", "proto.odxp", "out.txt"} <= {f["name"] for f in listing["files"]}


def test_paste_copy_autosave_default_protocol(app, tmp_path):
    pid = plate_view(app)["id"]
    block = "\n".join("\t".join(f"{0.1 * (r + 1):.3f}" for _ in range(12)) for r in range(8))
    app.op("paste_data", {"id": pid, "text": block})
    assert plate_view(app)["wells"]["H1"]["raw"][0] == pytest.approx(0.8)
    assert app.op("paste_data", {"id": pid, "text": block})["confirm"]
    assert "0.100" in app.op("copy_data", {"id": pid})["text"]
    p2 = app.op("add_section", {"kind": "plate"})["id"]
    app.op("paste_data", {"id": p2})
    assert plate_view(app, 1)["has_data"]
    app.op("template_assign", {"id": pid, "wells": ["A1"], "group": "Unknowns"})
    app.op("copy_template", {"id": pid})
    app.op("paste_template", {"id": p2})
    assert plate_view(app, 1)["template"]["A1"]["group"] == "Unknowns"
    app.op("prefs", {"autosave": True, "autosave_prefix": "Run"})
    with pytest.raises(UserError, match="Invalid character"):
        app.op("prefs", {"autosave_prefix": "a/b"})
    app.op("read", {"id": pid, "confirm": True})
    wait_read(app)
    assert (tmp_path / "auto" / "Run001.odx").exists()
    proto = app.op("save", {"path": str(tmp_path / "default"), "protocol": True})["path"]
    app.op("prefs", {"default_protocol": True, "default_protocol_path": proto})
    app.op("new", {})
    assert len([s for s in app.state()["doc"]["sections"] if s["kind"] == "plate"]) == 2
    assert not plate_view(app)["has_data"] and plate_view(app, 1)["template"]["A1"]["group"] == "Unknowns"


def test_sections_and_notes(app):
    nid = app.op("add_section", {"kind": "notes"})["id"]
    app.op("notes", {"id": nid, "html": '<b onclick="x()">hi</b><script>alert(1)</script>'})
    notes = next(s for s in app.state()["doc"]["sections"] if s["id"] == nid)
    assert notes["html"] == "<b>hi</b>"
    app.op("summary_save", {"id": nid, "name": "Twelve", "formula": "3*4", "decimals": 0})
    assert next(s for s in app.state()["doc"]["sections"] if s["id"] == nid)["summaries"][0]["text"] == "12"
    app.op("move_section", {"id": nid, "index": 0})
    assert app.state()["doc"]["sections"][0]["id"] == nid
    app.op("section_settings", {"id": nid, "name": "Cover", "report": False})
    app.op("all_open", {"open": False})
    assert all(not s["open"] for s in app.state()["doc"]["sections"])
    app.op("delete_section", {"id": nid})
    with pytest.raises(UserError, match="at least one Plate"):
        app.op("delete_section", {"id": plate_view(app)["id"]})
    app.op("suspend", {"on": True})
    pid = plate_view(app)["id"]
    app.op("section_settings", {"id": pid, "name": "Renamed"})
    assert plate_view(app)["name"] != "Renamed"
    app.op("recalculate", {})
    assert plate_view(app)["name"] == "Renamed"
    assert sanitize_html('<p style="color:red">x</p><img src=x>') == '<p style="color:red">x</p>'


def test_http_api(app):
    srv = make_server(app, port=0)
    import threading
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        st = json.loads(urllib.request.urlopen(base + "/api/state").read())
        assert st["instrument"]["connected"] and st["doc"]["sections"][0]["kind"] == "plate"
        assert b"<html" in urllib.request.urlopen(base + "/").read().lower()
        assert b"function" in urllib.request.urlopen(base + "/main.js").read()
        req = urllib.request.Request(base + "/api/op", data=json.dumps({"op": "add_section", "args": {"kind": "notes"}}).encode(),
                                     headers={"Content-Type": "application/json", "X-ODStudio": "1"})
        r = json.loads(urllib.request.urlopen(req).read())
        assert r["ok"] and len(r["state"]["doc"]["sections"]) == 2
        bad = urllib.request.Request(base + "/api/op", data=b"{}", headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(bad)
        req = urllib.request.Request(base + "/api/op", data=json.dumps({"op": "nope"}).encode(),
                                     headers={"X-ODStudio": "1"})
        assert json.loads(urllib.request.urlopen(req).read())["ok"] is False
    finally:
        srv.shutdown()
        srv.server_close()


def test_multiple_windows(app, tmp_path):
    first = app.state()["active"]
    pid = plate_view(app)["id"]
    app.op("read", {"id": pid})
    wait_read(app)
    second = app.op("new", {})["doc"]
    st = app.state()
    assert [d["id"] for d in st["docs"]] == [first, second] and st["active"] == second
    assert not plate_view(app)["has_data"]
    # both windows have a "Plate#1" with section id 1; they stay independent
    p2 = plate_view(app)["id"]
    app.op("instrument_settings", {"doc": second, "id": p2, "settings": {"mode": "kinetic", "run_time": 60, "interval": 15, "delay": 1}})
    app.op("instrument_settings", {"doc": first, "id": pid, "settings": {"wavelengths": [450]}, "confirm": True})
    docs = {d["id"]: d for d in app.state()["docs"]}
    assert docs[second]["doc"]["sections"][0]["settings"]["mode"] == "kinetic"
    assert docs[first]["doc"]["sections"][0]["settings"]["wavelengths"] == [450]
    assert docs[first]["doc"]["sections"][0]["settings"]["mode"] == "endpoint"
    # a read in one window, edits in the other
    app.op("read", {"doc": second, "id": p2})
    app.op("add_section", {"doc": first, "kind": "notes"})
    with pytest.raises(UserError, match="reading a plate"):
        app.op("close", {"doc": second})
    wait_read(app)
    docs = {d["id"]: d for d in app.state()["docs"]}
    assert docs[second]["doc"]["sections"][0]["info"]["points"] == 5 and docs[second]["dirty"]
    assert not docs[first]["doc"]["sections"][0]["has_data"]          # changing settings cleared it
    # opening a file uses a new window unless the current one is empty
    path = app.op("save", {"doc": second, "path": str(tmp_path / "k")})["path"]
    app.op("new", {})
    third = app.state()["active"]
    app.op("open", {"path": path})
    assert app.state()["active"] == third and len(app.state()["docs"]) == 3
    app.op("open", {"path": path})
    assert len(app.state()["docs"]) == 4
    app.op("close", {})
    app.op("activate", {"doc": first})
    assert app.state()["active"] == first and len(app.state()["docs"]) == 3
    for d in list(app.state()["docs"]):
        app.op("close", {"doc": d["id"]})
    assert len(app.state()["docs"]) == 1 and app.state()["title"] == "Untitled"


def test_read_waits_for_delay(app):
    pid = plate_view(app)["id"]
    app.op("instrument_settings", {"id": pid, "settings": {"delay": 1}})
    app.op("read", {"id": pid})
    time.sleep(0.3)
    assert app.inst.reading["phase"] == "Starting in"
    app.op("stop", {})
    wait_read(app)
    assert not plate_view(app)["has_data"]


def test_activity_log_and_document_history(app, tmp_path):
    pid = plate_view(app)["id"]
    app.op("instrument_settings", {"id": pid, "settings": {"wavelengths": [450, 650]}})
    app.op("read", {"id": pid})
    wait_read(app)
    path = app.op("save", {"path": str(tmp_path / "logged")})["path"]
    hist = [h["text"] for h in app.state()["docs"][0]["history"]]
    assert any("Instrument settings of Plate#1 changed: endpoint, 450/650 nm" in h for h in hist)
    assert any(h.startswith("Started endpoint read of Plate#1") for h in hist)
    assert any(h == "Finished endpoint read of Plate#1: 1 reading" for h in hist)
    log = app.op("activity_log", {})["lines"]
    assert any("started" in l for l in log) and any("[Untitled] Finished endpoint read" in l for l in log)
    assert any(f"Saved {path}" in l for l in log)
    app.op("close", {})
    app.op("open", {"path": path})
    hist = [h["text"] for h in app.state()["docs"][-1]["history"]]
    assert any("Finished endpoint read" in h for h in hist) and hist[-1] == f"Opened {path}"


def test_export_formats_and_auto_export(app, tmp_path):
    import csv as _csv
    import zipfile
    pid = plate_view(app)["id"]
    app.op("instrument_settings", {"id": pid, "settings": {"mode": "kinetic", "run_time": 60, "interval": 15}})
    app.op("read", {"id": pid})
    wait_read(app)
    p = app.op("export", {"path": str(tmp_path / "x.csv"), "content": "both"})["path"]
    rows = list(_csv.reader(open(p)))
    assert rows[0] == ["plate", "well", "reading", "time_s", "temperature_c", "wavelength_nm", "od", "reduced", "unit"]
    assert len(rows) == 1 + 96 * 5 and rows[1][-1] == "mOD/min"
    p = app.op("export", {"path": str(tmp_path / "x.xlsx"), "content": "both"})["path"]
    assert zipfile.ZipFile(p).testzip() is None
    try:
        import openpyxl
    except ImportError:
        openpyxl = None
    if openpyxl:
        wb = openpyxl.load_workbook(p)
        assert wb.sheetnames == ["Raw", "Reduced", "Info"]
        assert wb["Raw"]["C2"].value == pytest.approx(0.1, abs=1e-6)           # A1, first reading
        assert wb["Reduced"]["A1"].value == "Reduced (mOD/min)" and isinstance(wb["Reduced"]["B2"].value, float)
    p = app.op("export", {"path": str(tmp_path / "r.txt"), "content": "reduced"})["path"]
    assert open(p).read().startswith("Reduced (mOD/min)\t1\t2")
    # auto export with a custom name after the next read
    app.op("prefs", {"auto_export": True, "auto_export_folder": str(tmp_path / "auto"),
                     "auto_export_name": "{experiment}-{plate}-{mode}-{n}", "auto_export_format": "xlsx",
                     "auto_export_content": "both"})
    app.op("read", {"id": pid, "confirm": True})
    wait_read(app)
    for _ in range(50):
        files = list((tmp_path / "auto").glob("*.xlsx")) if (tmp_path / "auto").exists() else []
        if files:
            break
        time.sleep(0.02)
    assert [f.name for f in files] == ["Untitled-Plate#1-kinetic-001.xlsx"]
    assert any("Auto-exported Plate#1" in h["text"] for h in app.state()["docs"][0]["history"])


def test_export_name_tokens():
    from odstudio import exports
    when = time.mktime((2026, 11, 16, 9, 5, 7, 0, 0, -1))
    assert exports.expand_name("{experiment}_{plate}_{date}_{time}_{n}", experiment="run.smx", plate="Plate#1",
                               mode="endpoint", n=7, when=when) == "run_Plate#1_2026-11-16_090507_007"
    assert exports.expand_name('a/b:c*{plate}', experiment="", plate='x"y', mode="") == "a_b_c_x_y"


def wait_workflow(app, statuses=("done", "stopped", "error"), timeout=20):
    for _ in range(int(timeout / 0.02)):
        st = app.workflow.state
        if st and st["status"] in statuses:
            return st
        time.sleep(0.02)
    raise AssertionError(f"workflow did not reach {statuses}: {app.workflow.state}")


def test_workflow_cycle_collects_time_points(app):
    pid = plate_view(app)["id"]
    app.op("instrument_settings", {"id": pid, "settings": {"wavelengths": [405], "first_col": 1, "last_col": 2}})
    wid = app.op("workflow_open", {})["id"]
    assert app.op("workflow_open", {})["id"] == wid                     # only one per experiment
    steps = [{"type": "temperature", "celsius": 37, "wait": False},
             {"type": "drawer", "open": False},
             {"type": "cycle", "interval": 1, "run_time": 2, "steps": [
                 {"type": "shake", "seconds": 1}, {"type": "read", "plate": pid}]},
             {"type": "message", "text": "Add stop solution"},
             {"type": "drawer", "open": True}]
    app.op("workflow_save", {"id": wid, "steps": steps})
    saved = next(s for s in app.state()["doc"]["sections"] if s["id"] == wid)["steps"]
    assert [s["type"] for s in saved] == ["temperature", "drawer", "cycle", "message", "drawer"]
    assert len({s["sid"] for s in saved} | {s["sid"] for s in saved[2]["steps"]}) == 7
    assert app.op("workflow_run", {"id": wid}) == {"started": True}
    st = wait_workflow(app, ("message",))
    assert st["message"] == "Add stop solution"
    with pytest.raises(UserError, match="busy"):
        app.op("drawer", {})                                             # manual control is locked
    app.op("workflow_continue", {})
    st = wait_workflow(app)
    assert st["status"] == "done", st
    v = plate_view(app)
    assert v["info"]["mode"] == "kinetic" and v["info"]["points"] == 3   # 0, 1, 2 s -> 3 cycles
    # times are measured from the start of the cycle: each reading follows a 1 s shake
    assert v["xs"][0] == pytest.approx(1, abs=0.5) and v["xs"][2] == pytest.approx(3, abs=0.7)
    assert v["unit"] == "mOD/min" and v["wells"]["A1"]["num"] is not None
    assert app.inst.info["setpoint"] == 37.0 and app.inst.info["drawer"] == "OPEN"
    log = next(s for s in app.state()["doc"]["sections"] if s["id"] == wid)["log"]
    assert any("Cycle 3 of 3" in l for l in log) and log[-1].endswith("Finished")
    assert any("Workflow: Read Plate#1" in h["text"] for h in app.state()["docs"][0]["history"])
    assert app.inst.reading is None


def test_workflow_validation_pause_and_stop(app):
    pid = plate_view(app)["id"]
    wid = app.op("workflow_open", {})["id"]
    app.op("instrument_settings", {"id": pid, "settings": {"mode": "kinetic", "run_time": 60, "interval": 15}})
    with pytest.raises(UserError, match="must be set to Endpoint"):
        app.op("workflow_save", {"id": wid, "steps": [{"type": "cycle", "interval": 5, "run_time": 10,
                                                        "steps": [{"type": "read", "plate": pid}]}]})
    with pytest.raises(UserError, match="cannot contain another cycle"):
        app.op("workflow_save", {"id": wid, "steps": [{"type": "cycle", "steps": [{"type": "cycle"}]}]})
    with pytest.raises(UserError, match="0 \\(off\\) or 15 to 45"):
        app.op("workflow_save", {"id": wid, "steps": [{"type": "temperature", "celsius": 10}]})
    with pytest.raises(UserError, match="no steps"):
        app.op("workflow_run", {"id": wid})
    app.op("workflow_save", {"id": wid, "steps": [{"type": "delay", "seconds": 30}, {"type": "read", "plate": pid}]})
    app.op("workflow_run", {"id": wid})
    time.sleep(0.2)
    app.op("workflow_pause", {})
    assert wait_workflow(app, ("paused",))["status"] == "paused"
    app.op("workflow_resume", {})
    assert app.workflow.state["status"] == "running"
    app.op("stop", {})
    assert wait_workflow(app)["status"] == "stopped"
    assert not plate_view(app)["has_data"] and app.inst.reading is None


def test_protocol_library_opens_and_runs(app):
    lib = app.state()["library"]
    assert {e["name"] for e in lib} >= {"Bradford protein assay", "BCA protein assay", "Lowry protein assay",
                                        "ELISA (TMB, 450 nm)", "Kinetic enzyme assay (Vmax)", "Michaelis-Menten (Km, Vmax)"}
    for e in lib:
        app.op("open_library", {"name": e["name"]})
        st = app.state()
        assert st["title"] == e["name"] and st["doc"]["problems"] == []
        kinds = [s["kind"] for s in st["doc"]["sections"]]
        assert kinds[0] == "notes" and "workflow" in kinds and "plate" in kinds
        plate = next(s for s in st["doc"]["sections"] if s["kind"] == "plate")
        app.op("read", {"id": plate["id"]})
        wait_read(app)
        view = app.state()["doc"]
        assert view["problems"] == [], e["name"]
        for s in view["sections"]:
            if s["kind"] == "group":
                cells = [c for r in s["rows"] for c in r["cells"]] + [m["text"] for m in s["summaries"]]
                assert "Name?" not in cells and "Error" not in cells, (e["name"], s["name"])
        wf = next(s for s in view["sections"] if s["kind"] == "workflow")
        assert wf["steps"] and all(isinstance(x["sid"], int) for x in wf["steps"])
        app.op("close", {"doc": app.state()["active"]})


def test_discontinuous_columns(app):
    pid = plate_view(app)["id"]
    for mode, extra in (("endpoint", {}), ("kinetic", {"run_time": 60, "interval": 15}),
                        ("spectrum", {"start": 400, "stop": 440, "step": 20})):
        app.op("instrument_settings", {"id": pid, "confirm": True,
                                       "settings": {"mode": mode, "columns": [9, 1, 3, 9, 4], **extra}})
        v = plate_view(app)
        assert v["columns"] == [1, 3, 4, 9] and v["columns_text"] == "1, 3-4, 9"
        assert (v["settings"]["first_col"], v["settings"]["last_col"]) == (1, 9)
        app.op("read", {"id": pid, "confirm": True})
        wait_read(app)
        v = plate_view(app)
        cols = sorted({int(w[1:]) for w in v["wells"]})
        assert cols == [1, 3, 4, 9], (mode, cols)
    # a contiguous choice is stored as a plain range
    app.op("instrument_settings", {"id": pid, "confirm": True, "settings": {"mode": "endpoint", "columns": [2, 3, 4]}})
    assert plate_view(app)["settings"]["columns"] is None and plate_view(app)["columns"] == [2, 3, 4]
    app.op("instrument_settings", {"id": pid, "confirm": True, "settings": {"first_col": 5, "last_col": 6}})
    assert plate_view(app)["columns"] == [5, 6]
    with pytest.raises(UserError, match="at least one column"):
        app.op("instrument_settings", {"id": pid, "confirm": True, "settings": {"columns": []}})
    hist = [h["text"] for h in app.state()["docs"][0]["history"]]
    assert any("columns 1, 3-4, 9" in h for h in hist)


def test_notes_from_files_are_sanitized_and_deleted_groups_leave_plots_clean(app, tmp_path):
    from odstudio import document as D
    doc = D.new_document()
    n = D.add_section(doc, "notes")
    n["html"] = '<img src=x onerror="alert(1)"><b onclick="x()">ok</b><script>alert(2)</script>'
    D.ensure_group(doc, "Std")
    g = D.add_section(doc, "graph")
    g["plots"].append({"name": "P", "group": "Std", "x": "Concentration", "y": "MeanValue"})
    path = tmp_path / "evil.smx"
    path.write_text(json.dumps(doc))
    app.op("open", {"path": str(path)})
    view = app.state()["doc"]
    assert next(s for s in view["sections"] if s["kind"] == "notes")["html"] == "<b>ok</b>"
    gid = next(s for s in view["sections"] if s["kind"] == "group")["id"]
    app.op("delete_section", {"id": gid})
    plot = next(s for s in app.state()["doc"]["sections"] if s["kind"] == "graph")["plots"][0]
    assert plot["group"] is None and plot["x"] is None


def test_review_fixes_backend(app, tmp_path):
    """Regression tests for the bugs found in the code review."""
    pid = plate_view(app)["id"]
    # 1. a formula giving one number on a kinetic plate, a plot compared with a number, Fact(200)
    app.op("instrument_settings", {"id": pid, "settings": {"mode": "kinetic", "run_time": 60, "interval": 15}})
    app.op("read", {"id": pid})
    wait_read(app)
    app.op("reduction", {"id": pid, "reduction": {"wavelength": "Average(!L1)"}})
    assert app.state()["doc"]["problems"] == [] and plate_view(app)["has_data"]
    app.op("reduction", {"id": pid, "reduction": {"wavelength": "!L2"}})
    assert plate_view(app)["wells"]["A1"]["text"] in ("Name?", "Limits?")
    app.op("reduction", {"id": pid, "reduction": {"wavelength": "!L1"}})
    nid = app.op("add_section", {"kind": "notes"})["id"]
    gid = app.op("add_section", {"kind": "graph"})["id"]
    app.op("summary_save", {"id": nid, "name": "F", "formula": "Fact(200)"})
    app.op("summary_save", {"id": nid, "name": "P", "formula": "Plot#1@Graph#1 > 1"})
    texts = {m["name"]: m["text"] for m in next(s for s in app.state()["doc"]["sections"] if s["id"] == nid)["summaries"]}
    assert texts == {"F": "Range?", "P": "Error"}
    # 2/3. renames rewrite formulas
    app.op("template_assign", {"id": pid, "wells": ["A1", "B1"], "group": "Unknowns", "sample": "U1"})
    app.op("template_assign", {"id": pid, "wells": ["A2"], "group": "Standards", "sample": "S1", "value": 1})
    app.op("summary_save", {"id": nid, "name": "M", "formula": "Average(MeanValue@Unknowns)"})
    before = next(s for s in app.state()["doc"]["sections"] if s["name"] == "Unknowns")["rows"][0]["cells"]
    app.op("column_save", {"group": "Unknowns", "old": "Values", "name": "OD", "formula": "!WellValues"})
    unk = next(s for s in app.state()["doc"]["sections"] if s["name"] == "Unknowns")
    assert unk["rows"][0]["cells"] == before                         # same numbers, from its own wells
    assert "Average(OD)" in [c["formula"] for c in unk["columns"]]
    app.op("section_settings", {"id": unk["id"], "name": "Samples"})
    notes = next(s for s in app.state()["doc"]["sections"] if s["id"] == nid)
    assert next(m for m in notes["summaries"] if m["name"] == "M")["formula"] == "Average(MeanValue@Samples)"
    app.op("section_settings", {"id": gid, "name": "Std Curve"})
    assert next(m for m in next(s for s in app.state()["doc"]["sections"] if s["id"] == nid)["summaries"]
                if m["name"] == "P")["formula"] == "Plot#1@'Std Curve' > 1"
    # 11. a failed edit changes nothing
    snap = json.dumps(app.doc)
    with pytest.raises(UserError):
        app.op("template_assign", {"id": pid, "wells": ["C1", "C2", "Z9"], "group": "Samples"})
    assert json.dumps(app.doc) == snap
    # 10. the plate being read cannot be changed; 9. manual commands are refused at once
    app.op("instrument_settings", {"id": pid, "confirm": True, "settings": {"mode": "endpoint", "delay": 2}})
    app.op("read", {"id": pid, "confirm": True})
    t = time.time()
    with pytest.raises(UserError, match="busy"):
        app.op("drawer", {})
    assert time.time() - t < 0.5
    with pytest.raises(UserError, match="being read"):
        app.op("instrument_settings", {"id": pid, "settings": {"mode": "kinetic"}})
    with pytest.raises(UserError, match="being read"):
        app.op("clear_data", {"id": pid})
    app.op("stop", {})
    wait_read(app)


def test_review_fixes_data(app):
    from odstudio import document as D
    pid = plate_view(app)["id"]
    plate = app.sec(pid)
    # 4. saturated endpoint wells stay visible
    plate["data"] = D.data_from_plates([], plate["settings"])
    plate["data"].update(values=[[[[0.5] * 12 for _ in range(8)]]], times=[0], temps=[25.0], wavelengths=[405],
                         saturated=["A1"])
    plate["data"]["values"][0][0][0][0] = None
    app._touch()
    assert plate_view(app)["wells"]["A1"]["text"] == "Limits?"
    # 5. %T: the blank is subtracted in OD, then converted
    plate["data"]["values"][0][0][0][1] = 0.301
    plate["data"]["values"][0][0][7][11] = 0.0
    plate["template"]["H12"] = {"type": "plateblank", "group": None, "sample": "BL", "value": None}
    plate["reduction"]["raw_mode"] = "transmittance"
    app._touch()
    assert plate_view(app)["wells"]["A2"]["num"] == pytest.approx(50.0, abs=0.1)
    # 6./7. pasting clears an old pre-read and keeps empty wells empty
    plate["preread"] = {"values": [[[[0.1] * 12 for _ in range(8)]]]}
    block = "\n".join("\t".join(["0.5"] * 4 + [""] * 8) for _ in range(8))
    app.op("paste_data", {"id": pid, "text": block, "confirm": True})
    v = plate_view(app)
    assert plate["preread"] is None and "A5" not in v["wells"]
    # 13. Count of a sample whose wells are all masked is 0
    app.op("template_assign", {"id": pid, "wells": ["A3", "B3"], "group": "Unknowns", "sample": "U1"})
    app.op("column_save", {"group": "Unknowns", "name": "N", "formula": "Count(Values)", "decimals": 0})
    app.op("mask", {"id": pid, "wells": ["A3", "B3"]})
    unk = next(s for s in app.state()["doc"]["sections"] if s["name"] == "Unknowns")
    assert unk["rows"][0]["cells"][-1] == "0"


def test_workflow_autosaves_once(app, tmp_path):
    pid = plate_view(app)["id"]
    p2 = app.op("add_section", {"kind": "plate"})["id"]
    app.op("prefs", {"autosave": True, "autosave_prefix": "Wf"})
    wid = app.op("workflow_open", {})["id"]
    app.op("workflow_save", {"id": wid, "steps": [{"type": "read", "plate": pid}, {"type": "read", "plate": p2}]})
    app.op("workflow_run", {"id": wid})
    wait_workflow(app)
    for _ in range(50):
        files = sorted(p.name for p in (tmp_path / "auto").glob("Wf*.odx"))
        if files:
            break
        time.sleep(0.02)
    assert files == ["Wf001.odx"]


def test_files_and_settings_from_before_rename(app, tmp_path, monkeypatch):
    """Documents saved as SpectraMax (.smx/.smxp, old format tag) still open; old settings move over."""
    from odstudio import document as D
    from odstudio.gui import app as appmod
    doc = D.new_document()
    doc["format"] = "spectramax-document"
    old = tmp_path / "old.smx"
    old.write_text(json.dumps(doc))
    app.op("open", {"path": str(old)})
    assert app.state()["title"] == "old.smx"
    saved = app.op("save", {"path": str(tmp_path / "old.smx")})["path"]
    assert saved.endswith("old.odx") and json.loads(open(saved).read())["format"] == "odstudio-document"
    proto = tmp_path / "assays" / "p.smxp"
    proto.parent.mkdir(parents=True, exist_ok=True)
    proto.write_text(json.dumps(doc))
    assert "p.smxp" in app.state()["assays"]
    with pytest.raises(UserError):
        bad = tmp_path / "bad.odx"
        bad.write_text(json.dumps(dict(doc, format="something-else")))
        app.op("open", {"path": str(bad)})

    home = tmp_path / "home"
    (home / ".spectramax").mkdir(parents=True)
    (home / ".spectramax" / "prefs.json").write_text('{"port": "COM3"}')
    monkeypatch.delenv("ODSTUDIO_HOME", raising=False)
    monkeypatch.delenv("SPECTRAMAX_HOME", raising=False)
    monkeypatch.setattr(appmod.Path, "home", classmethod(lambda cls: home))
    assert appmod.config_dir() == home / ".odstudio"
    assert json.loads((home / ".odstudio" / "prefs.json").read_text()) == {"port": "COM3"}


def test_factory_service_modes(app):
    with pytest.raises(UserError, match="Confirm the warning"):
        app.op("service", {"action": "align"})
    r = app.op("service", {"action": "align", "acknowledged": True})
    assert r["value"] == 0.0 and "ALIGN" in r["text"]
    r = app.op("service", {"action": "diagnostic"})
    grid = r["grid"]
    assert len(grid) == 8 and all(len(row) == 12 for row in grid)
    assert all(grid[r][c] > 20000 for r in range(8) for c in range(8))     # 8 values per channel
    assert grid[0][8] is None and grid[7][9] is None and grid[5][11] == 305  # layout of seg70:135c
    assert "Factory service" in json.dumps(app.op("activity_log", {}))
    assert app.state()["instrument"]["options"]["service"] is True


def test_adc_diagnostic_layout():
    """Fixed-width layout the original parses (SPF.EXE seg70:135c, widths at DS:0e7c / 0e8e)."""
    from odstudio.driver import SpectraMax
    lines = ["header"]
    for ch in range(8):
        odd = "".join(f"{v:{w}d}" for v, w in zip((11, 13, 15, 17), (8, 7, 9, 9)))
        even = "".join(f"{v:{w}d}" for v, w in zip((12, 14, 16, 18), (8, 7, 9, 9)))
        lines += [f"{ch}:" + odd, f"{ch}:" + even]
    lines += [lab + f"{1:8d}{2:8d}{3:7d}" for lab in ("aa", "bb", "cc", "dd", "eee", "fff")]
    g = SpectraMax.parse_adc_diag("\r\n".join(lines))
    assert g[3][:8] == [11, 12, 13, 14, 15, 16, 17, 18]
    assert [g[5][9], g[5][10], g[5][11]] == [1, 2, 3] and g[6][9] is None and g[0][8] is None


def test_port_and_reader_survive_a_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("ODSTUDIO_HOME", str(tmp_path / "home"))
    a = App(port=None)
    a.op("prefs", {"port": "sim:vmax", "reader": "vmax", "filters": [405, 0, 0, 0, 0, 650]})
    a.inst._alive = False
    a.inst.disconnect()
    b = App(port=None)                                  # a new start of the program
    for _ in range(100):
        if b.inst.info["connected"]:
            break
        time.sleep(0.02)
    try:
        assert b.prefs["port"] == "sim:vmax" and b.prefs["reader"] == "vmax"
        assert b.inst.info["connected"] and b.inst.info["model"] == "Vmax"
    finally:
        b.inst._alive = False
        b.inst.disconnect()
    c = App(port="sim")                                 # a one-off command-line port ...
    c.op("prefs", {"autosave": True})                   # ... and some other change saved
    c.inst._alive = False
    c.inst.disconnect()
    assert json.loads((tmp_path / "home" / "prefs.json").read_text())["port"] == "sim:vmax"


def test_reconnects_when_the_adapter_moves(tmp_path, monkeypatch):
    from odstudio.gui import app as appmod
    monkeypatch.setenv("ODSTUDIO_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    (tmp_path / "home" / "prefs.json").write_text(json.dumps(
        {"port": "/dev/ttyUSB9", "port_id": {"vid": 1, "pid": 2, "serial_number": "X"}}))
    monkeypatch.setattr(appmod, "find_port", lambda ident: "sim:emax" if ident.get("serial_number") == "X" else None)
    a = App(port=None)
    for _ in range(150):
        if a.inst.info["connected"]:
            break
        time.sleep(0.02)
    try:
        assert a.inst.info["connected"] and a.inst.info["model"] == "Emax"
        assert a.prefs["port"] == "sim:emax"
        for _ in range(50):                             # the note is written right after connecting
            if any("now sim:emax (was /dev/ttyUSB9)" in ln for ln in a.op("activity_log", {})["lines"]):
                break
            time.sleep(0.02)
        assert any("now sim:emax (was /dev/ttyUSB9)" in ln for ln in a.op("activity_log", {})["lines"])
    finally:
        a.inst._alive = False
        a.inst.disconnect()


def test_find_readers(app):
    r = app.op("find_readers", {"ports": ["sim", "sim:thermomax"]})["results"]
    assert r[0]["reader"] in ("Simulator",) and r[0]["note"] == "connected now"
    assert r[1]["reader"] == "ThermoMax"
