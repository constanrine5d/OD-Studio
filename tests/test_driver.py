import math
import sys

import pytest

from odstudio import (CommandFailed, FlashSimulator, ReadSettings, ReplyTimeout,
                        SpectraMax, SpectraMaxError)
from odstudio.cli import main
from odstudio.serve import SimulatorTCPServer
from odstudio.transport import SimulatorTransport, Transport


class Scripted(Transport):
    """Transport that answers from a table and records what was sent."""

    def __init__(self, replies):
        self.replies = dict(replies)
        self.sent = []
        self._rx = b""

    def write(self, data):
        cmd = data.decode()
        self.sent.append(cmd)
        r = self.replies.get(cmd, b"OK\t\r>")
        if isinstance(r, list):
            r = r.pop(0) if r else b""
        self._rx += r

    def read(self, timeout):
        data, self._rx = self._rx, b""
        return data

    def reset_input(self):
        self._rx = b""


def connected(sim=None, **kw):
    tx = SimulatorTransport(sim or FlashSimulator(seed=1))
    sm = SpectraMax(transport=tx, **kw)
    sm.connect()
    return sm, tx.sim


def test_connect_reads_identity():
    sm, sim = connected()
    assert sm.info.version == "Version 0.1 of the simulator"
    assert sm.info.model == "Simulator"
    assert (sm.info.options.wavelength_min, sm.info.options.wavelength_max) == (250, 750)
    assert sim.remote is True
    sm.disconnect()
    assert sim.remote is False


def test_setup_sequence_matches_softmax_pro():
    tx = Scripted({"!TIME\r": b"OK\t\r>\r\n100\r>", "!QUEUE\r": b"OK\t\r>\r\n0\r>"})
    sm = SpectraMax(transport=tx)
    sm.remote = True
    s = ReadSettings("kinetic", (405, 650), first_column=2, num_columns=3, interval=30,
                     reads=11, shake_before=5, shake_between=3)
    sm.start_read(s)
    assert tx.sent == [
        "!CLEAR DATA\r", "!TIME\r", "!WAVELENGTH 405 650\r", "!SHAKE ON\r",
        "!SHAKE  5 30 27 3 0\r", "!STRIP 2 3\r", "!CALIBRATE ON\r",
        "!MODE KINETIC 30 11\r", "!READ\r"]

    tx.sent.clear()
    sm.start_read(ReadSettings("endpoint", (450,), autocalibrate=False))
    assert tx.sent[2:] == ["!WAVELENGTH 450\r", "!SHAKE OFF\r", "!SHAKE  0 0 0 0 0\r",
                           "!STRIP 1 12\r", "!CALIBRATE OFF\r", "!MODE ENDPOINT\r", "!READ\r"]

    tx.sent.clear()
    sm.start_read(ReadSettings("spectrum", start=300, stop=400, step=20))
    assert "!WAVELENGTH 300\r" in tx.sent and "!MODE SPECTRUM 300 20 6\r" in tx.sent
    assert "!CALIBRATE ONCE\r" in tx.sent


def test_endpoint_read():
    sm, _ = connected()
    p = sm.read_endpoint([405, 650], columns=(1, 4), poll_interval=0)
    assert p.wavelengths == [405, 650] and p.columns == [1, 2, 3, 4]
    assert p.well("A1", 405) == pytest.approx(0.1)
    assert p.well("A1", 650) == pytest.approx(0.2)
    assert p.well("H4", 405) == pytest.approx(math.exp((3 + 14) / 10) / 10, abs=1e-4)
    assert p.well("A5") is None
    assert p.timestamp is not None


def test_kinetic_and_spectrum_reads():
    sm, _ = connected()
    got = []
    ks = sm.read_kinetic([405], interval=10, reads=12, poll_interval=0,
                         on_plate=lambda p, i, n: got.append((i, n)))
    assert [k.time_s for k in ks] == [10.0 * i for i in range(12)]
    assert got[0] == (1, 12) and got[-1] == (12, 12)
    assert ks[5].well("A1") > ks[0].well("A1")
    sp = sm.read_spectrum(300, 400, 25, columns=(1, 1), poll_interval=0)
    assert [x.wavelengths[0] for x in sp] == [300, 325, 350, 375, 400]


def test_faithful_simulator_still_works_end_to_end():
    sm, _ = connected(FlashSimulator(faithful=True, seed=1))
    ks = sm.read_kinetic([405], interval=10, reads=7, poll_interval=0)   # QUEUE says max 3
    assert len(ks) == 7


def test_realtime_read_waits_for_data():
    now = [1000.0]
    sim = FlashSimulator(realtime=True, seed=1, clock=lambda: now[0])
    sm, _ = connected(sim)
    seen = []

    def on_status(st):
        seen.append(st.state)
        now[0] += 5

    p = sm.read_endpoint([405], poll_interval=0, on_status=on_status)
    assert seen and set(seen) == {"MEAS"}
    assert p.well("A1") == pytest.approx(0.1)


def test_instrument_error_is_reported_with_text():
    sm, sim = connected()
    with pytest.raises(CommandFailed) as e:
        sm.command("TEMP 99")
    assert e.value.code == 3 and "out of range" in str(e.value)
    assert "Parameter is out of range" in sm.errors()
    sm.clear_errors()
    assert sm.errors() == ""
    with pytest.raises(ValueError):
        sm.set_temperature(60)
    sm.set_temperature(37)
    assert sim.temp_setpoint == 37.0
    sm.set_temperature(None)
    assert sim.temp_setpoint == 0.0


def test_stalled_read_raises_and_cancels():
    sm, sim = connected()
    s = ReadSettings("endpoint", (405,))
    sm.start_read(s)
    sim.queue.clear()                       # data never shows up, instrument idle
    with pytest.raises(SpectraMaxError, match="0 of 1 reads"):
        sm.collect(s, poll_interval=0, stall_timeout=0.05)


def test_timeout_and_no_instrument():
    silent = Scripted({"!\r": b""})
    sm = SpectraMax(transport=silent, timeout=0.05)
    with pytest.raises(SpectraMaxError, match="no SpectraMax answering"):
        sm.connect()
    partial = Scripted({"!STATUS\r": b"OK\t\r>\r\nOPEN\tID"})
    sm = SpectraMax(transport=partial, timeout=0.05)
    with pytest.raises(ReplyTimeout):
        sm.status()
    assert partial.sent == ["!STATUS\r", "!RESEND\r"]


def test_resend_recovers_a_lost_data_block():
    tx = Scripted({"!STATUS\r": b"OK\t\r>", "!RESEND\r": b"OK\t\r>\r\nCLOSED\tIDLE\t\r\n>"})
    sm = SpectraMax(transport=tx, timeout=0.05)
    assert sm.status().drawer == "CLOSED"


def test_tolerates_other_reply_dialects():
    tx = Scripted({
        "!STATUS\r": b"\r\nOK\t\r\n>\r\nCLOSED\tIDLE\r\n>",
        "!TEMP 99.0\r": b"\r\nFAIL\t 101\r\n>",
        "!TEMP\r": b">OK>37.0\t36.9\r\n>",
    })
    sm = SpectraMax(transport=tx, timeout=0.05)
    assert sm.status().drawer == "CLOSED"
    assert sm.temperature() == (37.0, 36.9)
    with pytest.raises(CommandFailed) as e:
        sm.command("TEMP 99.0")
    assert e.value.code == 101 and "invalid argument" in str(e.value)


def test_settings_validation():
    for bad in (ReadSettings("endpoint", ()), ReadSettings("endpoint", (200,)),
                ReadSettings("endpoint", (405,) * 7), ReadSettings("endpoint", (405,), 10, 5),
                ReadSettings("kinetic", (405,), interval=1, reads=3),
                ReadSettings("kinetic", (405,), interval=10, reads=3, shake_between=10),
                ReadSettings("spectrum", start=400, stop=300), ReadSettings("bogus")):
        with pytest.raises(ValueError):
            bad.validate()
    ReadSettings("endpoint", ("F405", 650)).validate()


def test_over_tcp():
    srv = SimulatorTCPServer("127.0.0.1", 0, FlashSimulator(seed=1))
    srv.start_background()
    try:
        with SpectraMax(srv.url) as sm:
            assert sm.info.model == "Simulator"
            p = sm.read_endpoint([405], columns=(1, 2), poll_interval=0)
            assert p.well("B2") == pytest.approx(math.exp(3 / 10) / 10, abs=1e-4)
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="pty is Unix-only")
def test_over_pty():
    import threading

    from odstudio.serve import serve_pty

    names, stop = [], threading.Event()
    t = threading.Thread(target=serve_pty, args=(FlashSimulator(seed=1), None, names.append, stop),
                         daemon=True)
    t.start()
    for _ in range(200):
        if names:
            break
        threading.Event().wait(0.01)
    try:
        with SpectraMax(names[0]) as sm:
            assert sm.status().drawer == "OPEN"
            assert sm.read_endpoint([405], columns=(1, 1), poll_interval=0).well("A1") == pytest.approx(0.1)
    finally:
        stop.set()
        t.join(2)


def test_cli(capsys, tmp_path):
    assert main(["-p", "sim", "info"]) == 0
    assert "Simulator" in capsys.readouterr().out
    out = tmp_path / "r.csv"
    assert main(["-p", "sim", "read", "kinetic", "-i", "10", "-n", "2", "-c", "1-2",
                 "-f", "csv", "-o", str(out)]) == 0
    rows = out.read_text().splitlines()
    assert rows[0].startswith("read,time_s") and len(rows) == 1 + 2 * 16
    assert main(["-p", "sim", "send", "NOPE"]) == 1
    assert "Unknown Command" in capsys.readouterr().err
    assert main(["-p", "sim", "read", "endpoint", "-w", "100"]) == 1
    assert "outside 250..750" in capsys.readouterr().err
