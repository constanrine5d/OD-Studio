"""The simulator must reproduce what CSFlashSim in SPF.EXE answers."""
import math

import pytest

from odstudio.simulator import FlashSimulator, WireSession


@pytest.fixture
def sim():
    return FlashSimulator(faithful=True, seed=1, clock=lambda: 1000.0)


def body(sim, cmd):
    status, text = sim.process(cmd)
    assert status == "ok\t", (cmd, status)
    return text


def test_power_on_state(sim):
    assert body(sim, "WAVELENGTH") == "405\t\r"
    assert body(sim, "STRIP") == "1\t12\r"
    assert body(sim, "MODE") == "Endpoint\r"
    assert body(sim, "BAUD") == "19200\r"
    assert body(sim, "FILTER") == "260\r\n652\r\n\r\n"
    assert body(sim, "STATUS") == "OPEN\tMEAS\t\r\n"
    assert body(sim, "QUEUE") == "0\r"
    assert body(sim, "TIME") == "0\r"
    assert body(sim, "VER") == "Version 0.1 of the simulator\r"
    assert body(sim, "OPTION") == "Simulator\r\n250\t750\t1\r\n15\t45\r\n400\t225\t5\t2\r\n"


def test_command_prefix_case_and_whitespace(sim):
    assert sim.process("!status")[0] == "ok\t"
    assert sim.process("  !Close")[0] == "ok\t"
    assert body(sim, "status") == "CLOSED\tMEAS\t\r\n"
    assert sim.process("")[0] == "ok\t"
    assert sim.process("!")[0] == "ok\t"


def test_errors_are_queued(sim):
    assert sim.process("NOPE") == ("fail\t1", "")
    assert sim.process("STRIP 1") == ("fail\t2", "")
    assert sim.process("TEMP 99") == ("fail\t3", "")
    assert sim.process("AT 1 READ") == ("fail\t4", "")
    assert body(sim, "ERROR NUMBER") == "1\r2\r3\r4\r"
    assert body(sim, "ERROR") == ("\r0) Unknown Command (1)\r1) Wrong number of parameters (2)\r"
                                  "2) Parameter is out of range (3)\r3) Command is not implemented (4)\r")
    assert body(sim, "ERROR 2") == "2) Parameter is out of range (3) (3)\r"
    assert body(sim, "ERROR LIST").startswith("\rList of possible errors:\rUnknown Command (1)\r")
    sim.process("CLEAR")
    assert body(sim, "ERROR") == "\r"


def test_unimplemented_commands_answer_ok_but_log_error_4(sim):
    assert sim.process("SHAKE ON") == ("ok\t", "")
    assert sim.process("CALIBRATE ONCE") == ("ok\t", "")
    assert body(sim, "ERROR NUMBER") == "4\r4\r"


def test_error_queue_overflow(sim):
    for _ in range(70):
        sim.process("NOPE")
    assert len(sim.errors) == 64
    assert body(sim, "ERROR").startswith("Overflow error: 5\r")


def test_mode_parsing(sim):
    assert sim.process("MODE KINETIC 10 5")[0] == "ok\t"
    assert body(sim, "MODE") == "Kinetic\t10/5\r"
    assert sim.process("MODE KINETIC 5 5")[0] == "fail\t3"       # interval < 6 s
    assert sim.process("MODE KINETIC 10")[0] == "fail\t2"
    assert sim.process("MODE KINETIC 10 10001")[0] == "fail\t3"
    assert sim.process("MODE SPECTRUM 300 10 20")[0] == "ok\t"
    assert body(sim, "MODE") == "Sweep\t300\t10\t20\r"
    assert sim.process("MODE SPECTRUM 240 10 2")[0] == "fail\t3"
    assert sim.process("MODE SPECTRUM 300 10 51")[0] == "fail\t3"   # 300 + 510 > 800
    assert sim.process("MODE X")[0] == "fail\t3"
    assert sim.process("MODE ENDPOINT")[0] == "ok\t"
    assert body(sim, "MODE") == "Endpoint\r"


def test_strip_wavelength_temp(sim):
    assert sim.process("STRIP 3 4")[0] == "ok\t"
    assert body(sim, "STRIP") == "3\t4\r"
    assert sim.process("STRIP 10 4")[0] == "fail\t3"
    assert sim.process("WAVELENGTH 405 650 ")[0] == "ok\t"
    assert body(sim, "WAVELENGTH") == "405\t650\t\r"
    assert sim.process("WAVELENGTH 249")[0] == "fail\t3"
    assert sim.process("WAVELENGTH F405")[0] == "fail\t3"      # atol("F405") == 0
    assert sim.process("TEMP 37.0")[0] == "ok\t"
    setpoint, actual = body(sim, "TEMP").strip().split("\t")
    assert setpoint == "37.0" and abs(float(actual) - 37.0) < 1.1
    assert sim.process("TEMP 14.9")[0] == "fail\t3"
    assert sim.process("TEMP 0")[0] == "ok\t"


def test_endpoint_read_and_transfer_layout(sim):
    sim.process("WAVELENGTH 405 650")
    sim.process("STRIP 2 2")
    sim.process("READ")
    assert body(sim, "QUEUE") == "1\r"
    text = body(sim, "TRANSFER")
    lines = text.split("\r")
    assert lines[0].split("\t")[0] == "0"
    assert lines[1] == "L:\t405\t650\t"
    assert lines[2] == "L:\t405"
    assert lines[3].startswith("2:\t") and lines[3].endswith("\t")
    assert len(lines[3].rstrip("\t").split("\t")) == 9          # label + rows A..H
    assert lines[4].startswith("3:\t")
    assert lines[5] == ""
    assert lines[6] == "L:\t650"
    # value model: (exp((col + 2*row + read)/10) + wavelength_index) / 10
    first = float(lines[3].split("\t")[1])
    assert first == pytest.approx(math.exp(1 / 10) / 10, abs=1e-4)
    assert body(sim, "QUEUE") == "0\r"
    assert sim.process("TRANSFER")[0] == "fail\t3"              # nothing left


def test_kinetic_read_queue_is_capped_at_three(sim):
    sim.process("MODE KINETIC 10 5")
    sim.process("READ")
    assert len(sim.queue) == 5
    assert body(sim, "QUEUE") == "3\r"
    sim.process("CANCEL")
    assert body(sim, "QUEUE") == "0\r"


def test_sweep_makes_one_plate_per_wavelength(sim):
    sim.process("MODE SPECTRUM 400 50 3")
    sim.process("READ")
    assert [r.wavelengths for r in sim.queue] == [[400], [450], [500]]
    assert sim.spectrum_value(400, 0, 0) == pytest.approx((800 - 400) / 550 * 2 + 0.1)


def test_test_pattern_wavelengths(sim):
    sim.wavelengths = [700]
    assert sim.well_value(0, 0, 0, 0) == pytest.approx(-0.1)
    sim.wavelengths = [703]
    assert sim.well_value(13, 0, 1, 1) == 2.0
    sim.wavelengths = [704]
    assert sim.well_value(13, 0, 1, 1) == 0.0


def test_reset_restores_defaults(sim):
    sim.process("WAVELENGTH 500")
    sim.process("CLOSE")
    sim.process("NOPE")
    sim.process("RESET")
    assert body(sim, "WAVELENGTH") == "405\t\r"
    assert body(sim, "STATUS").startswith("OPEN")
    assert body(sim, "ERROR") == "\r"


def test_wire_framing():
    w = WireSession(FlashSimulator(faithful=True, seed=1))
    assert w.feed(b"!CLOSE\r") == b"ok\t\r>"
    assert w.feed(b"!STA") == b""
    assert w.feed(b"TUS\r") == b"ok\t\r>\r\nCLOSED\tMEAS\t\r\n>"
    assert w.feed(b"!RESEND\r") == b"ok\t\r>\r\nCLOSED\tMEAS\t\r\n>"
    assert w.feed(b"!NOPE\r") == b"fail\t1\r>"
    assert w.feed(b"!\r") == b"ok\t\r>"


def test_friendly_personality():
    sim = FlashSimulator(seed=1)
    assert sim.process("SHAKE ON") == ("OK\t", "")
    assert sim.errors == []
    assert sim.process("STATUS") == ("OK\t", "OPEN\tIDLE\t\r\n")
    # error number sits where SOFTmax PRO looks for it: reply[6:9]
    status, _ = sim.process("NOPE")
    assert status == "FAIL\t 1" and int((status + "\r>")[6:9].strip("\r>")) == 1


def test_realtime_reads_arrive_over_time():
    now = [0.0]
    sim = FlashSimulator(realtime=True, seed=1, clock=lambda: now[0])
    sim.process("MODE KINETIC 10 3")
    sim.process("READ")
    assert sim.process("QUEUE")[1] == "0\r"
    assert sim.process("STATUS")[1] == "OPEN\tMEAS\t\r\n"
    now[0] = 9.5
    assert sim.process("QUEUE")[1] == "1\r"
    now[0] = 30
    assert sim.process("QUEUE")[1] == "3\r"
    assert sim.process("STATUS")[1] == "OPEN\tIDLE\t\r\n"
