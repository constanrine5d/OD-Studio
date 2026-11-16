import math

import pytest

from odstudio import protocol as P
from odstudio.errors import CommandFailed, ProtocolError


def test_frame():
    assert P.frame("STATUS") == b"!STATUS\r"
    assert P.frame("!TEMP 37.0 ") == b"!TEMP 37.0\r"
    assert P.frame("") == b"!\r"


@pytest.mark.parametrize("cmd,expected", [
    ("STATUS", True), ("TEMP", True), ("TEMP 37", False), ("TRANSFER", True),
    ("TRANSFER 2", True), ("READ", False), ("MODE", True), ("MODE ENDPOINT", False),
    ("ERROR", True), ("ERROR LIST", True), ("CLEAR", False), ("", False), ("!queue", True),
])
def test_expects_body(cmd, expected):
    assert P.expects_body(cmd) is expected


@pytest.mark.parametrize("chunk", ["ok\t\r", "OK\t\r\n", "\r\nOK", "OK"])
def test_status_ok(chunk):
    P.check_status("X", chunk)


@pytest.mark.parametrize("chunk,code", [
    ("fail\t3\r", 3), ("FAIL\t 107\r", 107), ("\r\nFAIL\t107", 107), ("\nFAIL\t 3", 3), ("FAIL", 0),
])
def test_status_fail(chunk, code):
    with pytest.raises(CommandFailed) as e:
        P.check_status("X", chunk)
    assert e.value.code == code


def test_status_garbage():
    with pytest.raises(CommandFailed) as e:
        P.check_status("X", "SERIES1\r")
    assert e.value.code is None


def test_parse_status_and_temp():
    st = P.parse_status("CLOSED\tMEAS\t\r\n")
    assert (st.drawer, st.state, st.drawer_open, st.busy) == ("CLOSED", "MEAS", False, True)
    assert P.parse_status("OPEN\tIDLE").busy is False
    assert P.parse_status("OPEN\tCAL\t12\r").state == "CAL"
    assert P.parse_temp("37.0\t36.4\r") == (37.0, 36.4)
    with pytest.raises(ProtocolError):
        P.parse_temp("garbage")


def test_parse_option():
    o = P.parse_option("Simulator\r\n250\t750\t1\r\n15\t45\r\n400\t225\t5\t2\r\n")
    assert (o.name, o.wavelength_min, o.wavelength_max, o.wavelength_step) == ("Simulator", 250, 750, 1)
    assert (o.temp_min, o.temp_max, o.extra) == (15.0, 45.0, ["400", "225", "5", "2"])


SIM_TRANSFER = ("12\t37.1\rL:\t405\t650\t\r"
                "L:\t405\r1:\t0.1000\t0.1221\t0.1492\t0.1822\t0.2226\t0.2718\t0.3320\t0.4055\t\r"
                "2:\t0.1105\t0.1350\t0.1649\t0.2014\t0.2460\t0.3004\t0.3669\t0.4482\t\r\r"
                "L:\t650\r1:\t0.2000\t0.2221\t0.2492\t0.2822\t0.3226\t0.3718\t0.4320\t0.5055\t\r"
                "2:\t0.2105\t0.2350\t0.2649\t0.3014\t0.3460\t0.4004\t0.4669\t0.5482\t\r\r")


def test_parse_transfer_simulator_layout():
    (p,) = P.parse_transfer(SIM_TRANSFER)
    assert (p.time_s, p.temperature, p.wavelengths) == (12.0, 37.1, [405, 650])
    assert p.columns == [1, 2]
    assert p.well("A1") == 0.1 and p.well("H1") == 0.4055
    assert p.well("B2", 650) == 0.2350
    assert p.well("A3") is None


def test_parse_transfer_crlf_saturation_and_two_plates():
    text = ("5\t25.0\r\nL:\t450\r\nL:\t450\r\n1:\t0.5\t#SAT\t1.0\t1.0\t1.0\t1.0\t1.0\t***\r\n\r\n"
            "15\t25.1\r\nL:\t450\r\nL:\t450\r\n1:\t0.6\t3.9\t1.0\t1.0\t1.0\t1.0\t1.0\t1.0\r\n")
    a, b = P.parse_transfer(text)
    assert a.well("A1") == 0.5 and math.isinf(a.well("B1")) and math.isnan(a.well("H1"))
    assert (b.time_s, b.well("A1")) == (15.0, 0.6)


def test_parse_transfer_rejects_garbage():
    with pytest.raises(ProtocolError):
        P.parse_transfer("1:\t0.1\t0.2\r")
    with pytest.raises(ProtocolError):
        P.parse_transfer("hello world\r")


def test_well_names():
    (p,) = P.parse_transfer(SIM_TRANSFER)
    with pytest.raises(ValueError):
        p.well("I1")
    with pytest.raises(ValueError):
        p.well("A13")
