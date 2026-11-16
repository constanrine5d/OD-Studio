from odstudio.driver import ReadSettings, SpectraMax
from odstudio.readers import capabilities, unsupported


def test_spectramax_can_do_everything():
    with SpectraMax("sim") as sm:
        caps = sm.capabilities
    assert caps["modes"] == ["endpoint", "kinetic", "spectrum"] and caps["filters"] is None
    assert unsupported(caps, ReadSettings("spectrum", (300,), start=300, stop=400)) is None
    assert unsupported(caps, ReadSettings("endpoint", (405, 450, 490, 540, 600, 650))) is None


def test_filter_reader_limits():
    caps = capabilities("Emax", family="tmax", modes=["endpoint"], max_wavelengths=2,
                        filters=[650, 405, 450], temperature=False, shake=False,
                        calibrate=False, column_range=False)
    assert caps["filters"] == [405, 450, 650]
    assert "kinetic" in unsupported(caps, ReadSettings("kinetic", (405,), interval=30, reads=3))
    assert "at most 2" in unsupported(caps, ReadSettings("endpoint", (405, 450, 650)))
    assert "no 490 nm filter" in unsupported(caps, ReadSettings("endpoint", (490,)))
    assert "F405" not in (unsupported(caps, ReadSettings("endpoint", ("F405",))) or "")
    # the whole plate is read anyway; the program keeps the chosen columns
    assert unsupported(caps, ReadSettings("endpoint", (405,), first_column=3, num_columns=2)) is None
    assert unsupported(caps, ReadSettings("endpoint", (405, 650))) is None


def test_minimum_kinetic_interval():
    caps = capabilities("Vmax", modes=["endpoint", "kinetic"], min_interval=8)
    assert "at least 8 s" in unsupported(caps, ReadSettings("kinetic", (405,), interval=5, reads=3))
    assert unsupported(caps, ReadSettings("kinetic", (405,), interval=8, reads=3)) is None
