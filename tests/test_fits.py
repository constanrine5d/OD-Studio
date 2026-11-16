"""Curve fits: models, weighting, fixed parameters and fit statistics.

Expected values come from textbook formulas / tables computed independently of the
fitting code (closed-form regression, Student-t tables, noise-free generated data).
"""
import math

import pytest

from odstudio import fits

# Classic small regression example: y-hat = 2.2 + 0.6x, SSE = 2.4, Sxx = 10, SST = 6.
X5 = [1.0, 2.0, 3.0, 4.0, 5.0]
Y5 = [2.0, 4.0, 5.0, 4.0, 5.0]
T975_3 = 3.182446305284263            # t(0.975, 3)

VMAX, KM = 2.0, 5.0
XMM = [0.0, 1.0, 2.5, 5.0, 10.0, 20.0, 40.0, 80.0]
NOISE = [0.01, -0.02, 0.015, -0.01, 0.02, -0.015, 0.01, -0.005]


def mm(x, a=VMAX, b=KM):
    return a * x / (b + x)


# --- Student t -------------------------------------------------------------

@pytest.mark.parametrize("p,dof,expected", [
    (0.975, 1, 12.706), (0.975, 2, 4.303), (0.975, 5, 2.571), (0.975, 10, 2.228),
    (0.975, 30, 2.042), (0.95, 10, 1.812), (0.995, 4, 4.604), (0.9995, 1, 636.619),
])
def test_t_quantile_table(p, dof, expected):
    assert fits.t_quantile(p, dof) == pytest.approx(expected, abs=6e-4)


def test_t_quantile_closed_forms():
    for p in (0.6, 0.9, 0.975, 0.999):
        assert fits.t_quantile(p, 1) == pytest.approx(math.tan(math.pi * (p - 0.5)), rel=1e-10)
        assert fits.t_quantile(p, 2) == pytest.approx(
            (2 * p - 1) / math.sqrt(2 * p * (1 - p)), rel=1e-10)
    assert fits.t_quantile(0.975, 1e7) == pytest.approx(1.959964, abs=1e-5)  # normal limit
    assert fits.t_quantile(0.025, 5) == pytest.approx(-2.570582, abs=1e-6)
    assert fits.t_cdf(fits.t_quantile(0.9, 7.5), 7.5) == pytest.approx(0.9, abs=1e-12)
    with pytest.raises(ValueError):
        fits.t_quantile(1.0, 3)


# --- Linear statistics -----------------------------------------------------

def test_linear_statistics_match_textbook():
    f = fits.fit("linear", X5, Y5)
    assert f.ok and f.n == 5 and f.dof == 3 and not f.weighted
    assert f.params["A"] == pytest.approx(2.2) and f.params["B"] == pytest.approx(0.6)
    assert f.sse == pytest.approx(2.4) and f.r2 == pytest.approx(0.6)
    assert f.sy == pytest.approx(math.sqrt(0.8))
    assert f.se["B"] == pytest.approx(math.sqrt(0.8 / 10))               # s/sqrt(Sxx)
    assert f.se["A"] == pytest.approx(math.sqrt(0.8 * (1 / 5 + 9 / 10)))  # s*sqrt(1/n+xbar^2/Sxx)
    assert f.ci["B"] == pytest.approx([0.6 - T975_3 * math.sqrt(0.08), 0.6 + T975_3 * math.sqrt(0.08)])
    assert f.ci["B"] == pytest.approx([-0.300132, 1.500132], abs=1e-6)
    assert f.ec50 is None and f.fixed == [] and f.names == {"A": "Intercept", "B": "Slope"}
    d = f.as_dict()
    for key in ("kind", "label", "equation", "ok", "params", "r2", "error", "n", "dof", "sse",
                "sy", "se", "ci", "conf", "fixed", "ec50", "names", "weighted"):
        assert key in d
    # 99 % intervals are wider
    g = fits.fit("linear", X5, Y5, conf=0.99)
    assert g.ci["B"][1] - g.ci["B"][0] > f.ci["B"][1] - f.ci["B"][0]


def test_weighted_linear_matches_closed_form():
    xs = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    ys = [1.2, 1.9, 3.2, 3.8, 5.3, 5.9]
    ws = [1 / x ** 2 for x in xs]
    f = fits.fit("linear", xs, ys, weights=ws)
    sw = sum(ws)
    xb = sum(w * x for w, x in zip(ws, xs)) / sw
    yb = sum(w * y for w, y in zip(ws, ys)) / sw
    sxx = sum(w * (x - xb) ** 2 for w, x in zip(ws, xs))
    b = sum(w * (x - xb) * (y - yb) for w, x, y in zip(ws, xs, ys)) / sxx
    a = yb - b * xb
    ssew = sum(w * (y - a - b * x) ** 2 for w, x, y in zip(ws, xs, ys))
    sstw = sum(w * (y - yb) ** 2 for w, y in zip(ws, ys))
    s2 = ssew / 4
    assert f.weighted and f.params["A"] == pytest.approx(a) and f.params["B"] == pytest.approx(b)
    assert f.sse == pytest.approx(ssew) and f.r2 == pytest.approx(1 - ssew / sstw)
    assert f.se["B"] == pytest.approx(math.sqrt(s2 / sxx))
    assert f.se["A"] == pytest.approx(math.sqrt(s2 * (1 / sw + xb ** 2 / sxx)))
    # invalid weights drop their points
    g = fits.fit("linear", xs + [7.0, 8.0], ys + [100.0, -50.0],
                 weights=ws + [0.0, float("nan")])
    assert g.n == 6 and g.params["B"] == pytest.approx(b)


def test_linear_through_origin_and_fixed_slope():
    f = fits.fit("linear", X5, Y5, fixed={"A": 0})
    b = sum(x * y for x, y in zip(X5, Y5)) / sum(x * x for x in X5)
    sse = sum((y - b * x) ** 2 for x, y in zip(X5, Y5))
    assert f.ok and f.fixed == ["A"] and f.dof == 4
    assert f.params == pytest.approx({"A": 0.0, "B": b})
    assert f.sse == pytest.approx(sse)
    assert f.se["A"] is None and f.ci["A"] is None
    assert f.se["B"] == pytest.approx(math.sqrt(sse / 4 / sum(x * x for x in X5)))
    assert f.predict(0.0) == 0.0
    g = fits.fit("linear", X5, Y5, fixed={"B": 0.6})
    assert g.params["A"] == pytest.approx(2.2) and g.dof == 4
    one = fits.fit("linear", [2.0], [3.0], fixed={"A": 1.0})      # one free param, one point
    assert one.ok and one.params["B"] == pytest.approx(1.0) and one.dof == 0
    assert one.sy is None and one.se is None and one.band(1.0) is None


def test_lm_agrees_with_closed_form():
    """The general LM, run on a linear-in-parameters model, gives the closed-form answer."""
    xs = [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [1.1, 2.9, 7.2, 13.1, 20.8, 31.2]
    m = fits._MODELS["quadratic"]
    p, _, ok = fits._levenberg_marquardt(m, [0.0, 0.0, 0.0], [0, 1, 2], xs, ys, [1.0] * 6)
    f = fits.fit("quadratic", xs, ys)
    assert ok and p == pytest.approx([f.params[k] for k in "ABC"], rel=1e-7)


# --- Bands -----------------------------------------------------------------

def test_linear_bands():
    f = fits.fit("linear", X5, Y5)
    s2 = 0.8
    for x in (0.0, 3.0, 5.0, 7.5):
        b = f.band(x)
        half = T975_3 * math.sqrt(s2 * (1 / 5 + (x - 3) ** 2 / 10))
        halfp = T975_3 * math.sqrt(s2 * (1 + 1 / 5 + (x - 3) ** 2 / 10))
        assert b["y"] == pytest.approx(f.predict(x))
        assert b["conf_hi"] - b["y"] == pytest.approx(half)
        assert b["pred_hi"] - b["y"] == pytest.approx(halfp)
        assert b["pred_lo"] < b["conf_lo"] < b["y"] < b["conf_hi"] < b["pred_hi"]
    widths = [(f.band(x)["conf_hi"] - f.band(x)["conf_lo"], x) for x in
              [1 + 0.25 * i for i in range(17)]]
    assert min(widths)[1] == pytest.approx(3.0)                   # narrowest at x mean
    pts = fits.curve_points(f, 1, 5, bands=True, n=5)
    assert len(pts) == 5 and all(len(p) == 6 for p in pts)
    assert pts[2][2:] == pytest.approx([f.band(3)[k] for k in
                                        ("conf_lo", "conf_hi", "pred_lo", "pred_hi")])
    assert all(len(p) == 2 for p in fits.curve_points(f, 1, 5))   # unchanged default


def test_loglog_and_semilog_bands_back_transform():
    xs = [1.0, 2.0, 4.0, 8.0, 16.0]
    ys = [1.1, 2.3, 3.9, 8.4, 15.2]
    f = fits.fit("loglog", xs, ys, weights=[1, 2, 3, 4, 5])
    b = f.band(3.0)
    assert b["y"] == pytest.approx(f.predict(3.0))
    assert b["pred_lo"] < b["conf_lo"] < b["y"] < b["conf_hi"] < b["pred_hi"]
    # symmetric in log space
    assert math.log10(b["conf_hi"]) - math.log10(b["y"]) == pytest.approx(
        math.log10(b["y"]) - math.log10(b["conf_lo"]))
    s = fits.fit("semilog", xs, [0.2 + 0.3 * math.log10(x) + e for x, e in zip(xs, NOISE)])
    b = s.band(5.0)
    assert b["conf_lo"] < s.predict(5.0) < b["conf_hi"]
    assert s.band(0.0) is None                                    # outside the domain
    rows = fits.curve_points(fits.fit("linear", [1, 2], [1, 2]), 0, 1, bands=True, n=3)
    assert rows[0][2:] == [None] * 4                              # dof = 0: no bands


# --- Michaelis-Menten ------------------------------------------------------

def test_mm_registered():
    assert fits.FIT_KINDS[-2:] == ["4param", "mm"]
    assert fits.FIT_LABELS["mm"] == "Michaelis-Menten"
    assert fits.FIT_EQUATIONS["mm"] == "y = A*x/(B + x)"
    assert fits.PARAM_NAMES["mm"] == {"A": "Vmax", "B": "Km"}
    assert fits.PARAM_NAMES["4param"]["C"] == "EC50"
    assert set(fits.PARAM_NAMES) == set(fits.FIT_KINDS)


def test_mm_noise_free_recovery():
    f = fits.fit("mm", XMM, [mm(x) for x in XMM])
    assert f.ok and f.n == 8 and f.dof == 6
    assert f.params["A"] == pytest.approx(VMAX, rel=1e-8)
    assert f.params["B"] == pytest.approx(KM, rel=1e-8)
    assert f.ec50 == f.params["B"] and f.r2 == pytest.approx(1)
    for x in (0.5, 3.0, 12.0, 60.0):
        assert f.inverse(f.predict(x)) == pytest.approx(x, rel=1e-9)
    assert f.predict(0.0) == 0.0
    assert f.inverse(VMAX) is None and f.inverse(3.0) is None


def test_mm_noisy_statistics():
    ys = [mm(x) + e for x, e in zip(XMM, NOISE)]
    f = fits.fit("mm", XMM, ys)
    assert f.ok
    a, b = f.params["A"], f.params["B"]
    # least-squares optimum: the SSE gradient vanishes
    ga = sum((y - mm(x, a, b)) * x / (b + x) for x, y in zip(XMM, ys))
    gb = sum((y - mm(x, a, b)) * a * x / (b + x) ** 2 for x, y in zip(XMM, ys))
    assert abs(ga) < 1e-9 and abs(gb) < 1e-9
    for k, true in (("A", VMAX), ("B", KM)):
        lo, hi = f.ci[k]
        assert lo < true < hi and lo < f.params[k] < hi
        assert f.se[k] > 0
    # SE from an independent finite-difference Jacobian
    h = 1e-6
    rows = [[(mm(x, a + h, b) - mm(x, a - h, b)) / (2 * h),
             (mm(x, a, b + h) - mm(x, a, b - h)) / (2 * h)] for x in XMM]
    jtj = [[sum(r[i] * r[j] for r in rows) for j in range(2)] for i in range(2)]
    det = jtj[0][0] * jtj[1][1] - jtj[0][1] ** 2
    s2 = f.sse / 6
    assert f.se["A"] == pytest.approx(math.sqrt(s2 * jtj[1][1] / det), rel=1e-5)
    assert f.se["B"] == pytest.approx(math.sqrt(s2 * jtj[0][0] / det), rel=1e-5)
    band = f.band(5.0)
    assert band["pred_lo"] < band["conf_lo"] < f.predict(5.0) < band["conf_hi"] < band["pred_hi"]


def test_mm_seeds_fixed_and_weights():
    ys = [mm(x) + e for x, e in zip(XMM, NOISE)]
    ref = fits.fit("mm", XMM, ys)
    seeded = fits.fit("mm", XMM, ys, seeds={"A": 10.0, "B": 0.5})
    assert seeded.params == pytest.approx(ref.params, rel=1e-7)
    assert fits.fit("mm", XMM, ys, seeds={"A": "junk"}).ok
    held = fits.fit("mm", XMM, ys, fixed={"A": VMAX})
    assert held.ok and held.fixed == ["A"] and held.dof == 7
    assert held.params["A"] == VMAX and held.se["A"] is None and held.ci["A"] is None
    assert held.ci["B"][0] < KM < held.ci["B"][1]
    # with A fixed the model is linear in 1/(B+x); check stationarity in B
    b = held.params["B"]
    assert abs(sum((y - mm(x, VMAX, b)) * x / (b + x) ** 2 for x, y in zip(XMM, ys))) < 1e-10
    # 1/y^2 weights: the x = 0 point (y = 0) has an infinite weight and is ignored
    ys0 = [mm(x) for x in XMM]
    w = [1 / y ** 2 if y else float("inf") for y in ys0]
    wf = fits.fit("mm", XMM, ys0, weights=w)
    assert wf.ok and wf.weighted and wf.n == 7
    assert wf.params["B"] == pytest.approx(KM, rel=1e-7)


def test_mm_failures():
    assert fits.fit("mm", [1.0], [1.0]).error.startswith("needs at least 2 points")
    bad = fits.fit("mm", XMM, [mm(x) for x in XMM], fixed={"B": -1.0})
    assert not bad.ok and bad.error
    lin = fits.fit("mm", [1.0, 2.0, 3.0, 4.0], [1.0, 2.0, 3.0, 4.0])   # no saturation
    assert not lin.ok and lin.error == "did not converge"


# --- 4-parameter -----------------------------------------------------------

def test_4param_recovery_and_statistics():
    true = (0.05, 1.3, 6.0, 2.1)
    xs = [0.5, 1, 2, 4, 8, 16, 32, 64]
    clean = [(true[0] - true[3]) / (1 + (x / true[2]) ** true[1]) + true[3] for x in xs]
    f = fits.fit("4param", xs, clean)
    for k, v in zip("ABCD", true):
        assert f.params[k] == pytest.approx(v, rel=1e-6)
    assert f.ec50 == f.params["C"] and f.dof == 4
    noisy = [y + e for y, e in zip(clean, NOISE)]
    f = fits.fit("4param", xs, noisy)
    assert f.ok and f.ec50 == f.params["C"]
    for k, v in zip("ABCD", true):
        assert f.ci[k][0] < v < f.ci[k][1]
    assert f.as_dict()["ci"]["C"] == f.ci["C"]
    seeded = fits.fit("4param", xs, noisy, seeds={"B": 3.0, "C": 20.0})
    assert seeded.params == pytest.approx(f.params, rel=1e-6)
    held = fits.fit("4param", xs, noisy, fixed={"A": 0.05, "D": 2.1})
    assert held.ok and held.dof == 6 and held.params["A"] == 0.05 and held.fixed == ["A", "D"]
    assert held.ci["C"][0] < 6.0 < held.ci["C"][1]
    assert f.predict(0.0) == f.params["A"]                        # zero-dose asymptote
    b0 = f.band(0.0)
    assert b0["conf_hi"] - b0["y"] == pytest.approx(fits.t_quantile(0.975, 4) * f.se["A"])


# --- Graceful failure ------------------------------------------------------

def test_failures_never_raise():
    assert fits.fit("linear", [1.0], [2.0]).error == "needs at least 2 points"
    assert fits.fit("quadratic", [1.0, 2.0], [1.0, 2.0]).error == "needs at least 3 points"
    assert not fits.fit("4param", [1.0, 2.0, 3.0], [1.0, 2.0, 3.0]).ok
    assert not fits.fit("linear", ["a", None, "x"], [1, 2, 3]).ok
    assert not fits.fit("linear", [1, 2, 3], [1, 2, 3], weights=[0, -1, None]).ok
    assert not fits.fit("linear", [1, 2, 3], [1, 2, 3], weights=[1, 2]).ok
    assert not fits.fit("linear", [1, 2, 3], [1, 2, 3], fixed={"A": "zero"}).ok
    assert not fits.fit("linear", [1, 2, 3], [1, 2, 3], conf=1.5).ok
    assert not fits.fit("quadratic", [1, 1, 2], [1, 2, 3]).ok        # singular
    assert fits.fit("none", [1, 2], [1, 2]).error == "No Fit"
    assert fits.fit("bogus", [1, 2], [1, 2]).error == "No Fit"
    failed = fits.fit("linear", [1.0], [2.0])
    assert failed.predict(1.0) is None and failed.band(1.0) is None
    assert fits.curve_points(failed, 0, 1) == []


def test_singular_covariance_keeps_fit():
    # all parameters fixed: nothing to estimate, but the fit is still usable
    f = fits.fit("linear", X5, Y5, fixed={"A": 2.2, "B": 0.6})
    assert f.ok and f.dof == 5 and f.sse == pytest.approx(2.4)
    assert f.se == {"A": None, "B": None}
    b = f.band(3.0)                                               # prediction band = +/- t*s
    assert b["conf_lo"] == b["y"] == b["conf_hi"]
    assert b["pred_hi"] - b["y"] == pytest.approx(fits.t_quantile(0.975, 5) * math.sqrt(2.4 / 5))
