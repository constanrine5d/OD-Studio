"""ThermoMax / UVmax / Vmax / Emax driver against the simulator."""
import time

import pytest

from odstudio import tmax
from odstudio.driver import ReadSettings
from odstudio.errors import SpectraMaxError
from odstudio.readers import open_reader
from odstudio.tmax import ReaderError, TMaxReader, min_interval
from odstudio.tmaxsim import TMaxSimTransport

FILTERS = [405, 450, 490, 540, 570, 650]


def reader(model, *, detect=True, byteorder="big", **kw):
    log = []
    tx = TMaxSimTransport(model, byteorder=byteorder)
    r = TMaxReader(f"sim:{model}", model=None if detect else model, filters=FILTERS,
                   transport=tx, trace=lambda d, b: log.append((d, b)), **kw)
    r.connect()
    return r, tx, log


def sent(log):
    return b"".join(b for d, b in log if d == ">").decode()


@pytest.mark.parametrize("model,name", [("thermomax", "ThermoMax"), ("uvmax", "UVmax"),
                                        ("vmax", "Vmax"), ("emax", "Emax")])
def test_detects_each_model(model, name):
    r, _, log = reader(model)
    assert r.model == model and r.info.model == name
    assert sent(log).startswith("\rLCLVER")
    # the Vmax is the one recognised by accepting SHK (64:0cea)
    assert sent(log).endswith("SHK") == (model == "vmax")


@pytest.mark.parametrize("order", ["big", "little"])
def test_endpoint_read_and_byte_order(order):
    r, tx, log = reader("vmax", byteorder=order)
    log.clear()
    p = r.read_endpoint([450])
    assert sent(log) == "RMTDSHSWV2RDP"
    assert r.byteorder == order                       # worked out from the data
    expect = tx.sim.well_od(0, 2, 5)
    assert p.well("C6", 450) == pytest.approx(round(expect * 1000) / 1000)
    assert p.wavelengths == [450] and len(p.columns) == 12


def test_dual_wavelength_and_no_calibration():
    r, tx, log = reader("uvmax")
    log.clear()
    p = r.read(ReadSettings("endpoint", (405, 650), autocalibrate=False, shake_before=5))[0]
    assert sent(log) == "RMTSHKDWV1,6RNE"
    assert p.wavelengths == [405]                     # the reader subtracts the reference itself


def test_thermomax_incubator_and_status():
    r, tx, log = reader("thermomax")
    log.clear()
    r.set_temperature(37)
    assert sent(log) == "RMTTMP37.0CONLCL"
    assert r.temperature() == (37.0, 37.0)
    r.close_drawer()
    assert r.status().drawer == "CLOSED"
    r.open_drawer()
    assert r.status().drawer == "OPEN"
    with pytest.raises(ValueError):
        r.set_temperature(50)
    r.set_temperature(0)
    assert r.temperature()[0] == 0.0


def test_features_missing_from_models():
    r, _, _ = reader("vmax")
    with pytest.raises(ReaderError, match="no incubator"):
        r.set_temperature(37)
    e, _, _ = reader("emax")
    with pytest.raises(ReaderError, match="cannot shake"):
        e.shake()
    with pytest.raises(SpectraMaxError, match="cannot do kinetic"):
        e.read(ReadSettings("kinetic", (405,), interval=10, reads=2))
    with pytest.raises(SpectraMaxError, match="cannot do spectrum"):
        e.read(ReadSettings("spectrum", (405,), start=400, stop=500))
    with pytest.raises(SpectraMaxError, match="No 600 nm filter"):
        e.read_endpoint([600])
    assert e.capabilities["modes"] == ["endpoint"] and not e.capabilities["shake"]


def test_reader_refusal_is_reported():
    r, tx, _ = reader("vmax")
    with pytest.raises(ReaderError) as err:
        r.command("XYZ")
    assert err.value.code == 302                      # 300 + the number after BEL


def test_resync_after_a_lost_echo():
    r, tx, log = reader("vmax")
    tx.sim._buf = "R"                                 # the reader is out of step
    log.clear()
    r.open_drawer()                                   # first try fails, BREAK + CR, second try works
    # the garbled command is refused, then BREAK + CR resynchronise and it is sent again
    assert sent(log) == "RM\rRMTOPNLCL" and r.status().drawer == "OPEN"


def test_kinetic_is_timed_by_the_host(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(tmax.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(tmax.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + max(s, 0.01)))
    r, tx, log = reader("thermomax")
    log.clear()
    got = []
    plates = r.read(ReadSettings("kinetic", (405,), interval=10, reads=3, shake_between=2),
                    on_plate=lambda p, i, n: got.append((i, n)))
    s = sent(log)
    assert s.startswith("RMTDSHSWV1STKRDTSHK") and s.count("NXK") == 2
    assert [p.time_s for p in plates] == [0.0, 10, 20] and got[-1] == (3, 3)
    assert plates[2].well("A1") > plates[0].well("A1")
    assert plates[0].temperature == pytest.approx(23.0)
    with pytest.raises(SpectraMaxError, match="Minimum 9 seconds"):
        r.read(ReadSettings("kinetic", (405,), interval=8, reads=2, shake_between=1))
    assert [min_interval(d, m) for d in (False, True) for m in (False, True)] == [6, 9, 14, 19]


def test_late_kinetic_read_is_recorded_as_missing(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(tmax.time, "monotonic", lambda: clock[0])
    jumps = iter([0.01, 15.0])                        # the second wait overshoots by 5 s

    def sleep(s):
        clock[0] += next(jumps, max(s, 0.01))
    monkeypatch.setattr(tmax.time, "sleep", sleep)
    r, _, _ = reader("vmax")
    plates = r.read(ReadSettings("kinetic", (405,), interval=10, reads=3))
    assert len(plates) == 3
    missing = [p for p in plates if p.well("A1") is None]
    assert len(missing) == 1


def test_open_reader_picks_the_driver():
    sm = open_reader("sim")
    assert type(sm).__name__ == "SpectraMax" and sm.capabilities["family"] == "spectramax"
    sm.disconnect()
    r = open_reader("sim:emax", filters=FILTERS)
    assert isinstance(r, TMaxReader) and r.model == "emax"
    r.disconnect()


def test_program_with_an_old_reader(tmp_path, monkeypatch):
    from odstudio.gui.app import App, UserError
    monkeypatch.setenv("ODSTUDIO_HOME", str(tmp_path / "cfg"))
    app = App(port="", load_prefs=False)
    try:
        app.op("prefs", {"port": "sim:vmax", "filters": [405, 0, 490, 0, 0, 650]})
        info = app.state()["instrument"]
        assert info["connected"] and info["model"] == "Vmax"
        assert info["options"]["filters"] == [405, 490, 650] and info["options"]["family"] == "tmax"
        pid = next(s for s in app.state()["doc"]["sections"] if s["kind"] == "plate")["id"]
        with pytest.raises(UserError, match="no 450 nm filter"):
            st = dict(next(s for s in app.doc["sections"] if s["id"] == pid)["settings"], wavelengths=[450])
            app.op("instrument_settings", {"id": pid, "settings": st})
            app.op("read", {"id": pid})
        st = dict(next(s for s in app.doc["sections"] if s["id"] == pid)["settings"], wavelengths=[490])
        app.op("instrument_settings", {"id": pid, "settings": st})
        app.op("read", {"id": pid})
        for _ in range(200):
            if not app.inst.reading:
                break
            time.sleep(0.02)
        plate = next(s for s in app.state()["doc"]["sections"] if s["kind"] == "plate")
        assert plate["has_data"] and plate["wells"]["A1"]["raw"][0] > 0
        st = dict(st, mode="spectrum")
        app.op("instrument_settings", {"id": pid, "settings": st, "confirm": True})
        with pytest.raises(UserError, match="cannot do spectrum"):
            app.op("read", {"id": pid, "confirm": True})
        with pytest.raises(UserError, match="no incubator"):
            app.op("incubator", {"on": True, "temperature": 37})
        # the stored filter positions keep their gaps
        assert app.prefs["filters"] == [405, 0, 490, 0, 0, 650]
    finally:
        app.inst._alive = False
        app.inst.disconnect()
