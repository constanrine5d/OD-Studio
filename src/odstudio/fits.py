"""Curve fits offered by SOFTmax PRO's Graph section (SPF.RSR STR# 4500..4505),
plus a Michaelis-Menten (rectangular hyperbola) model.

    Linear            y = A + Bx
    Semi-Log          y = A + B * Log(x)
    Log-Log           Log(y) = A + B * Log(x)
    Quadratic         y = A + Bx + Cx^2
    4-Parameter       y = ((A - D) / (1 + (x/C)^B)) + D
    Michaelis-Menten  y = A*x/(B + x)            (A = Vmax, B = Km)

Every model is described in its *fitting space* u = tx(x), v = ty(y) (log10 of x
for Semi-Log, log10 of x and y for Log-Log, identity otherwise) and fitted there by
weighted least squares, minimising  sum w * (v - f(p, u))^2.  Parameters may be held
fixed.  Models that are linear in their parameters are solved in closed form (normal
equations over the free parameters); the others use a general weighted
Levenberg-Marquardt over the free parameters with analytic derivatives.

Fit-quality statistics follow the usual asymptotic (linearised) theory:

    s^2 = SSE_w / dof                        dof = n - number of free parameters
    Cov = s^2 * (J^T W J)^-1                 J = d f / d p (free parameters)
    CI  = p +/- t(1 - alpha/2, dof) * se

and confidence/prediction bands use the delta method (g^T Cov g, plus s^2 for a
new observation of unit weight).  The Student-t quantile is implemented here via
the regularized incomplete beta function.

Pure Python; data sets here are a handful of standards.
"""
from __future__ import annotations

import math
from typing import Callable, Optional

FIT_KINDS = ["none", "linear", "semilog", "loglog", "quadratic", "4param", "mm"]
FIT_LABELS = {"none": "No Fit", "linear": "Linear", "semilog": "Semi-Log", "loglog": "Log-Log",
              "quadratic": "Quadratic", "4param": "4-Parameter", "mm": "Michaelis-Menten"}
FIT_EQUATIONS = {"linear": "y = A + Bx", "semilog": "y = A + B * Log(x)",
                 "loglog": "Log(y) = A + B * Log(x)", "quadratic": "y = A + Bx + Cx^2",
                 "4param": "y = ((A - D)/(1 + (x/C)^B)) + D", "mm": "y = A*x/(B + x)"}
PARAM_NAMES = {
    "none": {},
    "linear": {"A": "Intercept", "B": "Slope"},
    "semilog": {"A": "Intercept (y at x = 1)", "B": "Slope per decade of x"},
    "loglog": {"A": "Intercept (Log y at x = 1)", "B": "Slope (Log-Log)"},
    "quadratic": {"A": "Constant", "B": "Linear coefficient", "C": "Quadratic coefficient"},
    "4param": {"A": "Bottom/zero-dose asymptote", "B": "Slope", "C": "EC50",
               "D": "Top asymptote"},
    "mm": {"A": "Vmax", "B": "Km"},
}

# Errors that evaluating a model on awkward parameters/data can produce.
_MATH_ERRORS = (ValueError, ZeroDivisionError, OverflowError, TypeError)


class FitError(ValueError):
    """A fit could not be made; the message is shown to the user."""


# ---------------------------------------------------------------------------
# Student-t distribution
# ---------------------------------------------------------------------------

def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (modified Lentz)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 1000):
        m2 = 2 * m
        for aa in (m * (b - m) * x / ((qam + m2) * (a + m2)),
                   -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))):
            d = 1.0 + aa * d
            d = 1.0 / (d if abs(d) > tiny else tiny)
            c = 1.0 + aa / c
            c = c if abs(c) > tiny else tiny
            delta = c * d
            h *= delta
        if abs(delta - 1.0) < 1e-15:
            break
    return h


def _betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def t_cdf(t: float, dof: float) -> float:
    """P(T <= t) for Student's t with ``dof`` (> 0, may be fractional) degrees of freedom."""
    if not dof > 0:
        raise ValueError("degrees of freedom must be positive")
    tail = 0.5 * _betainc(dof / 2.0, 0.5, dof / (dof + t * t))
    return 1.0 - tail if t > 0 else tail


def t_quantile(p: float, dof: float) -> float:
    """Inverse of :func:`t_cdf`: the t with P(T <= t) = p, for 0 < p < 1.

    E.g. t_quantile(0.975, 5) = 2.5706 (two-sided 95 % critical value).
    Solved by bracketing and bisection on the upper tail, to ~1e-13 relative.
    """
    if not 0.0 < p < 1.0:
        raise ValueError("probability must be between 0 and 1")
    if not dof > 0:
        raise ValueError("degrees of freedom must be positive")
    if p == 0.5:
        return 0.0
    if p < 0.5:
        return -t_quantile(1.0 - p, dof)
    tail = 1.0 - p                       # want 0.5 * I_{dof/(dof+t^2)}(dof/2, 1/2) = tail

    def upper(t):
        return 0.5 * _betainc(dof / 2.0, 0.5, dof / (dof + t * t))

    lo, hi = 0.0, 1.0
    while upper(hi) > tail:
        lo, hi = hi, hi * 2.0
        if hi > 1e300:
            return math.inf
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if upper(mid) > tail:
            lo = mid
        else:
            hi = mid
        if hi - lo <= 1e-14 * hi:
            break
    return 0.5 * (lo + hi)


# ---------------------------------------------------------------------------
# Small dense linear algebra
# ---------------------------------------------------------------------------

def _solve(a: list, b: list, tol: float = 1e-300) -> list:
    """Gaussian elimination with partial pivoting; ValueError("singular") if a pivot < tol."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        if abs(m[p][c]) < tol:
            raise ValueError("singular")
        m[c], m[p] = m[p], m[c]
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            for k in range(c, n + 1):
                m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x


def _solve_spd(a: list, b: list, tol: float = 1e-12) -> list:
    """Solve a symmetric positive (semi)definite system such as normal equations.

    The matrix is first scaled to unit diagonal (a correlation-like matrix) so that the
    singularity test ``tol`` is relative and independent of the parameters' units.
    """
    n = len(b)
    d = [math.sqrt(a[i][i]) if a[i][i] > 0 else 0.0 for i in range(n)]
    if any(v == 0.0 or not math.isfinite(v) for v in d):
        raise ValueError("singular")
    s = [[a[i][j] / (d[i] * d[j]) for j in range(n)] for i in range(n)]
    y = _solve(s, [b[i] / d[i] for i in range(n)], tol)
    return [y[i] / d[i] for i in range(n)]


def _inv_spd(a: list) -> Optional[list]:
    """Inverse of a symmetric positive definite matrix, or None if (nearly) singular."""
    n = len(a)
    try:
        cols = [_solve_spd(a, [1.0 if i == k else 0.0 for i in range(n)]) for k in range(n)]
    except ValueError:
        return None
    return [[cols[j][i] for j in range(n)] for i in range(n)]


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

def _ident(v: float) -> float:
    return v


def _pow10(v: float) -> float:
    return 10.0 ** v


class _Model:
    """One fit kind, described in its fitting space (u = tx(x), v = ty(y)).

    Linear-in-parameter models give ``basis(u)`` (f = sum p_k * basis_k); the others give
    ``f(p, u)``, its gradient ``grad(p, u)`` with respect to p, and ``starts(U, V)``
    returning candidate starting vectors.  ``point_ok(x, y)`` filters usable points
    (``domain`` describes that filter in error messages) and ``feasible(p)`` rejects
    parameter vectors outside the model's domain during iteration.
    """

    def __init__(self, names, *, basis=None, f=None, grad=None, starts=None,
                 tx=_ident, ty=_ident, back=_ident, point_ok=None, domain="", feasible=None):
        self.names = names
        self.basis = basis
        if basis is not None:
            self.f = lambda p, u: sum(pk * bk for pk, bk in zip(p, basis(u)))
            self.grad = lambda p, u: basis(u)
        else:
            self.f, self.grad = f, grad
        self.starts = starts
        self.tx, self.ty, self.back = tx, ty, back
        self.point_ok = point_ok or (lambda x, y: True)
        self.domain = domain
        self.feasible = feasible or (lambda p: True)

    @property
    def linear(self) -> bool:
        return self.basis is not None

    def predict(self, p, x):
        return self.back(self.f(p, self.tx(x)))


def _f4(p, x):
    a, b, c, d = p
    if x <= 0:                      # zero-dose limit
        return a if b > 0 else d
    return (a - d) / (1.0 + (x / c) ** b) + d


def _g4(p, x):
    a, b, c, d = p
    if x <= 0:
        return [1.0, 0.0, 0.0, 0.0] if b > 0 else [0.0, 0.0, 0.0, 1.0]
    t = (x / c) ** b
    den = 1.0 + t
    q = 1.0 / den
    k = (a - d) * (t / den) / den           # = -(d f / d t) * t
    return [q, -k * math.log(x / c), k * b / c, 1.0 - q]


def _starts_4param(U, V):
    pts = sorted(zip(U, V))
    xs = [p[0] for p in pts]
    lo, hi = pts[0][1], pts[-1][1]
    mid = math.sqrt(xs[0] * xs[-1])
    return [[lo, b0, c0, hi] for b0 in (1.0, 0.5, 2.0, 4.0)
            for c0 in (mid, xs[len(xs) // 2], xs[-1] / 2)]


def _fmm(p, x):
    a, b = p
    return a * x / (b + x)


def _gmm(p, x):
    a, b = p
    q = x / (b + x)
    return [q, -a * q / (b + x)]


def _starts_mm(U, V):
    """A ~ 1.1 * max y; B ~ the x where y first reaches half of max y (interpolated)."""
    pts = sorted(zip(U, V))
    top = max((p[1] for p in pts), key=abs)
    if top == 0:
        top = 1.0
    half = top / 2.0
    km = None
    prev = (0.0, 0.0)                       # the curve passes through the origin
    for x, y in pts:
        if abs(y) >= abs(half):
            x0, y0 = prev
            km = x0 + (x - x0) * (half - y0) / (y - y0) if y != y0 else x
            break
        prev = (x, y)
    if not km or km <= 0 or not math.isfinite(km):
        pos = sorted(x for x, _ in pts if x > 0)
        km = pos[len(pos) // 2] if pos else 1.0
    a0 = 1.1 * top
    return [[a0, km], [a0, 0.2 * km], [a0, 5.0 * km], [2.0 * a0, 3.0 * km]]


_MODELS = {
    "linear": _Model("AB", basis=lambda u: [1.0, u]),
    "semilog": _Model("AB", basis=lambda u: [1.0, u], tx=math.log10,
                      point_ok=lambda x, y: x > 0, domain=" with x > 0"),
    "loglog": _Model("AB", basis=lambda u: [1.0, u], tx=math.log10, ty=math.log10,
                     back=_pow10, point_ok=lambda x, y: x > 0 and y > 0,
                     domain=" with x > 0 and y > 0"),
    "quadratic": _Model("ABC", basis=lambda u: [1.0, u, u * u]),
    "4param": _Model("ABCD", f=_f4, grad=_g4, starts=_starts_4param,
                     point_ok=lambda x, y: x > 0, domain=" with x > 0",
                     feasible=lambda p: p[2] > 0),
    "mm": _Model("AB", f=_fmm, grad=_gmm, starts=_starts_mm,
                 point_ok=lambda x, y: x >= 0, domain=" with x >= 0",
                 feasible=lambda p: p[1] > 0),
}


# ---------------------------------------------------------------------------
# Least squares
# ---------------------------------------------------------------------------

def _wsse(model, p, U, V, W) -> float:
    """Weighted sum of squared residuals in fitting space (inf if not computable)."""
    if not model.feasible(p):
        return math.inf
    try:
        s = sum(w * (v - model.f(p, u)) ** 2 for u, v, w in zip(U, V, W))
    except _MATH_ERRORS:
        return math.inf
    return s if math.isfinite(s) else math.inf


def _normal_eq(model, p, free, U, V, W):
    """J^T W J and J^T W r over the free parameter indices."""
    k = len(free)
    jtj = [[0.0] * k for _ in range(k)]
    jtr = [0.0] * k
    for u, v, w in zip(U, V, W):
        g = model.grad(p, u)
        g = [g[i] for i in free]
        r = v - model.f(p, u)
        for i in range(k):
            jtr[i] += w * g[i] * r
            for j in range(i, k):
                jtj[i][j] += w * g[i] * g[j]
    for i in range(k):
        for j in range(i):
            jtj[i][j] = jtj[j][i]
    if not all(math.isfinite(v) for row in jtj for v in row):
        raise ValueError("non-finite Jacobian")
    return jtj, jtr


def _levenberg_marquardt(model, p0, free, U, V, W, max_iter: int = 500):
    """Weighted Levenberg-Marquardt over the parameter indices ``free``.

    Uses Marquardt's diagonal scaling (invariant to parameter units).  Returns
    ``(p, sse, converged)``; converged means the parameters stopped moving, or no
    downhill step exists even with heavy damping (a minimum to machine precision).
    """
    p = [float(v) for v in p0]
    s = _wsse(model, p, U, V, W)
    if not math.isfinite(s):
        return p, s, False
    if not free:
        return p, s, True
    lam = 1e-3
    for _ in range(max_iter):
        if s == 0.0:
            return p, s, True
        try:
            jtj, jtr = _normal_eq(model, p, free, U, V, W)
        except _MATH_ERRORS:
            return p, s, False
        while True:
            m = [[jtj[i][j] + (lam * (jtj[i][i] if jtj[i][i] > 0 else 1.0) if i == j else 0.0)
                  for j in range(len(free))] for i in range(len(free))]
            try:
                step = _solve_spd(m, jtr, tol=1e-15)
            except ValueError:
                step = None
            if step is not None:
                q = p[:]
                for k, i in enumerate(free):
                    q[i] += step[k]
                s2 = _wsse(model, q, U, V, W)
                if s2 < s:
                    rel = max(abs(step[k]) / (abs(p[i]) + 1e-12) for k, i in enumerate(free))
                    gain = (s - s2) / s
                    p, s = q, s2
                    lam = max(lam / 10.0, 1e-12)
                    if rel <= 1e-9 or (gain <= 1e-12 and rel <= 1e-5):
                        return p, s, True
                    break
            lam *= 10.0
            if lam > 1e16:
                return p, s, True
    return p, s, False


def _linear_solve(model, fixed, free, U, V, W) -> list:
    """Closed-form weighted least squares for a linear-in-parameters model."""
    p = [fixed.get(k, 0.0) for k in model.names]
    if not free:
        return p
    rows = [model.basis(u) for u in U]
    fixed_idx = [i for i in range(len(p)) if i not in free]
    target = [v - sum(p[i] * row[i] for i in fixed_idx) for row, v in zip(rows, V)]
    a = [[sum(w * row[i] * row[j] for row, w in zip(rows, W)) for j in free] for i in free]
    b = [sum(w * row[i] * t for row, t, w in zip(rows, target, W)) for i in free]
    try:
        sol = _solve_spd(a, b)
    except ValueError:
        raise FitError("data do not determine the fit (singular)") from None
    for k, i in enumerate(free):
        p[i] = sol[k]
    return p


def _nonlinear_solve(model, fixed, free, seeds, U, V, W) -> list:
    """Multi-start Levenberg-Marquardt; seeds (if any) give the first start."""
    names = model.names
    auto = model.starts(U, V)

    def prepare(start):
        q = list(start)
        for k, v in fixed.items():
            q[names.index(k)] = v
        return q

    tries = [auto]
    if seeds:
        seeded = list(auto[0])
        for k, v in seeds.items():
            seeded[names.index(k)] = v
        tries = [[seeded], auto]            # fall back to the automatic starts
    for starts in tries:
        best, best_s = None, math.inf
        for start in starts:
            p, s, ok = _levenberg_marquardt(model, prepare(start), free, U, V, W)
            if ok and s < best_s:
                best, best_s = p, s
        if best is not None:
            return best
    raise FitError("did not converge")


# ---------------------------------------------------------------------------
# Fit result
# ---------------------------------------------------------------------------

class Fit:
    """Result of :func:`fit`.

    Attributes: kind, ok, error, params {"A": .., ..}, r2, xmin, xmax, n, dof, sse, sy,
    se {param: se or None} or None, ci {param: [lo, hi] or None} or None, conf, fixed
    (list of fixed parameter names), ec50, names (PARAM_NAMES[kind]), weighted.
    """

    def __init__(self, kind: str):
        self.kind = kind
        self.ok = False
        self.params: dict = {}
        self.r2: Optional[float] = None
        self.error = ""
        self.xmin = self.xmax = 0.0
        self.n = 0
        self.dof: Optional[int] = None
        self.sse: Optional[float] = None
        self.sy: Optional[float] = None
        self.se: Optional[dict] = None
        self.ci: Optional[dict] = None
        self.conf = 0.95
        self.fixed: list = []
        self.ec50: Optional[float] = None
        self.names: dict = PARAM_NAMES.get(kind, {})
        self.weighted = False
        self._f: Optional[Callable] = None
        self._inv: Optional[Callable] = None
        # statistics for band(): model, parameter vector, free indices, Cov over them,
        # residual variance s^2 and the t critical value
        self._model: Optional[_Model] = None
        self._p: list = []
        self._free: list = []
        self._cov: Optional[list] = None
        self._s2: Optional[float] = None
        self._t: Optional[float] = None

    def predict(self, x: float) -> Optional[float]:
        try:
            y = self._f(x)
        except _MATH_ERRORS:
            return None
        if isinstance(y, complex) or math.isnan(y) or math.isinf(y):
            return None
        return y

    def inverse(self, y: float) -> Optional[float]:
        try:
            x = self._inv(y)
        except _MATH_ERRORS:
            return None
        if x is None or isinstance(x, complex) or math.isnan(x) or math.isinf(x):
            return None
        return x

    def band(self, x: float) -> Optional[dict]:
        """Confidence and prediction band at ``x`` (level ``self.conf``).

        Returns {"y", "conf_lo", "conf_hi", "pred_lo", "pred_hi"} computed by the delta
        method in fitting space and transformed back (Log-Log bands are asymmetric), or
        None when statistics are unavailable (dof <= 0, singular covariance) or x is
        outside the model's domain.  The prediction band is for one new observation of
        unit weight.
        """
        if not self.ok or self._cov is None or self._s2 is None or self._t is None:
            return None
        m = self._model
        try:
            u = m.tx(x)
            v = m.f(self._p, u)
            g = m.grad(self._p, u)
            g = [g[i] for i in self._free]
            var_mean = sum(g[i] * self._cov[i][j] * g[j]
                           for i in range(len(g)) for j in range(len(g)))
            var_mean = max(var_mean, 0.0)
            h_conf = self._t * math.sqrt(var_mean)
            h_pred = self._t * math.sqrt(var_mean + self._s2)
            out = {"y": m.back(v), "conf_lo": m.back(v - h_conf), "conf_hi": m.back(v + h_conf),
                   "pred_lo": m.back(v - h_pred), "pred_hi": m.back(v + h_pred)}
        except _MATH_ERRORS:
            return None
        if not all(isinstance(val, float) and math.isfinite(val) for val in out.values()):
            return None
        return out

    def as_dict(self) -> dict:
        return {"kind": self.kind, "label": FIT_LABELS.get(self.kind, self.kind),
                "equation": FIT_EQUATIONS.get(self.kind, ""), "ok": self.ok,
                "params": self.params, "r2": self.r2, "error": self.error,
                "n": self.n, "dof": self.dof, "sse": self.sse, "sy": self.sy,
                "se": self.se, "ci": self.ci, "conf": self.conf, "fixed": self.fixed,
                "ec50": self.ec50, "names": self.names, "weighted": self.weighted}


def _num(v) -> Optional[float]:
    """float(v) if it is a finite number, else None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _make_inverse(kind: str, p: list, xmin: float, xmax: float) -> Callable:
    """x for a given y (None where the curve never reaches y)."""
    if kind == "linear":
        a, b = p
        return lambda y: (y - a) / b
    if kind == "semilog":
        a, b = p
        return lambda y: 10 ** ((y - a) / b)
    if kind == "loglog":
        a, b = p
        return lambda y: 10 ** ((math.log10(y) - a) / b)
    if kind == "quadratic":
        a, b, c = p

        def inv(y):
            if abs(c) < 1e-300:
                return (y - a) / b
            disc = b * b - 4 * c * (a - y)
            if disc < 0:
                return None
            roots = [(-b + s * math.sqrt(disc)) / (2 * c) for s in (1, -1)]
            inside = [x for x in roots if xmin <= x <= xmax]
            if inside:
                return inside[0]
            return min(roots, key=lambda x: min(abs(x - xmin), abs(x - xmax)))
        return inv
    if kind == "4param":
        a, b, c, d = p

        def inv4(y):
            ratio = (a - d) / (y - d) - 1.0
            if ratio <= 0:
                return None
            return c * ratio ** (1.0 / b)
        return inv4
    a, b = p                                # mm: x = B*y/(A - y), y must lie below Vmax

    def inv_mm(y):
        if a == 0 or (a > 0 and y >= a) or (a < 0 and y <= a):
            return None
        return b * y / (a - y)
    return inv_mm


def _weighted_r2(V, fitted, W, sse) -> float:
    sw = sum(W)
    mean = sum(w * v for v, w in zip(V, W)) / sw
    ss_tot = sum(w * (v - mean) ** 2 for v, w in zip(V, W))
    if ss_tot == 0:
        return 1.0 if sse == 0 else 0.0
    return 1.0 - sse / ss_tot


def fit(kind: str, xs: list, ys: list, weights: Optional[list] = None,
        fixed: Optional[dict] = None, seeds: Optional[dict] = None, conf: float = 0.95) -> Fit:
    """Fit ``kind`` to the paired data by (weighted) least squares.  Never raises.

    weights: optional per-point weights (minimise sum w*(y - f)^2, in fitting space for
             Semi-Log/Log-Log); points whose weight is not a finite positive number are
             ignored, as are pairs that are not finite numbers or outside the model's
             domain (e.g. x <= 0 for Semi-Log).
    fixed:   {param: value} held constant (any model; e.g. linear {"A": 0} = through origin).
    seeds:   {param: initial value} for the iterative models (4param, mm); ignored otherwise.
    conf:    confidence level for ci and band().
    On failure ``ok`` is False and ``error`` holds a short message.
    """
    r = Fit(kind)
    r.conf = conf
    if kind == "none" or kind not in _MODELS:
        r.error = "No Fit"
        return r
    try:
        _fit_into(r, _MODELS[kind], xs, ys, weights, fixed, seeds, conf)
    except FitError as e:
        r.error = str(e)
    except _MATH_ERRORS as e:
        r.error = str(e) or "Fit Error"
    except Exception as e:  # noqa: BLE001 - a fit must never take the caller down
        r.error = f"Fit Error: {e}"
    if not r.ok:
        r._f = r._inv = None
    return r


def _fit_into(r: Fit, model: _Model, xs, ys, weights, fixed, seeds, conf) -> None:
    names = model.names
    if not (isinstance(conf, (int, float)) and 0.0 < conf < 1.0):
        raise FitError("confidence level must be between 0 and 1")

    fixed_v = {}
    for k, v in (fixed or {}).items():
        if k in names:
            fv = _num(v)
            if fv is None:
                raise FitError(f"fixed value for {k} is not a number")
            fixed_v[k] = fv
    seed_v = {}
    if not model.linear:
        for k, v in (seeds or {}).items():
            if k in names and k not in fixed_v and _num(v) is not None:
                seed_v[k] = _num(v)

    xs, ys = list(xs), list(ys)
    if weights is not None:
        weights = list(weights)
        if len(weights) != len(xs):
            raise FitError("weights must have one value per point")
    U, V, W, X = [], [], [], []
    for i, (x, y) in enumerate(zip(xs, ys)):
        x, y = _num(x), _num(y)
        w = 1.0 if weights is None else _num(weights[i])
        if x is None or y is None or w is None or w <= 0 or not model.point_ok(x, y):
            continue
        X.append(x)
        U.append(model.tx(x))
        V.append(model.ty(y))
        W.append(w)

    free = [i for i, k in enumerate(names) if k not in fixed_v]
    need = max(len(free), 1)
    if len(U) < need or len(set(X)) < min(len(free), 2):
        raise FitError(f"needs at least {need} points{model.domain}")
    r.xmin, r.xmax = min(X), max(X)

    if model.linear:
        p = _linear_solve(model, fixed_v, free, U, V, W)
    else:
        start_check = [fixed_v.get(k, 1.0) for k in names]
        if fixed_v and not model.feasible(start_check):
            raise FitError("fixed value outside the model's range")
        p = _nonlinear_solve(model, fixed_v, free, seed_v, U, V, W)

    fitted = [model.f(p, u) for u in U]
    sse = sum(w * (v - f) ** 2 for v, f, w in zip(V, fitted, W))
    if not math.isfinite(sse):
        raise FitError("Fit Error")

    r.params = {k: p[i] for i, k in enumerate(names)}
    r.n = len(U)
    r.dof = len(U) - len(free)
    r.sse = sse
    r.r2 = _weighted_r2(V, fitted, W, sse)
    r.fixed = [k for k in names if k in fixed_v]
    r.weighted = weights is not None
    r.ec50 = r.params.get("C") if r.kind == "4param" else (
        r.params.get("B") if r.kind == "mm" else None)
    r._model, r._p, r._free = model, p, free
    r._f = lambda x: model.predict(p, x)
    r._inv = _make_inverse(r.kind, p, r.xmin, r.xmax)
    r.ok = True
    _statistics(r, model, p, free, U, W, sse)


def _statistics(r: Fit, model, p, free, U, W, sse) -> None:
    """Fill sy, se, ci and the band() internals (left None when unavailable)."""
    if r.dof <= 0:
        return
    s2 = sse / r.dof
    r.sy = math.sqrt(s2)
    try:
        t = t_quantile(0.5 + r.conf / 2.0, r.dof)
        k = len(free)
        jtj = [[0.0] * k for _ in range(k)]
        for u, w in zip(U, W):
            g = model.grad(p, u)
            for i in range(k):
                for j in range(k):
                    jtj[i][j] += w * g[free[i]] * g[free[j]]
    except _MATH_ERRORS:
        return
    inv = _inv_spd(jtj)
    if inv is None:
        return
    cov = [[s2 * v for v in row] for row in inv]
    se = {k: None for k in model.names}
    ci = {k: None for k in model.names}
    for idx, i in enumerate(free):
        name = model.names[i]
        e = math.sqrt(max(cov[idx][idx], 0.0))
        se[name] = e
        ci[name] = [p[i] - t * e, p[i] + t * e]
    r.se, r.ci = se, ci
    r._cov, r._s2, r._t = cov, s2, t


def curve_points(f: Fit, xmin: float, xmax: float, log_x: bool = False, n: int = 80,
                 bands: bool = False) -> list:
    """Sample the fitted curve for drawing.

    Each point is [x, y]; with ``bands`` it is [x, y, conf_lo, conf_hi, pred_lo, pred_hi]
    where the band entries are None if the fit has no statistics.
    """
    if not f.ok or not xmax > xmin:
        return []
    n = max(n, 2)
    out = []
    if log_x and xmin > 0:
        a, b = math.log10(xmin), math.log10(xmax)
        xs = [10 ** (a + (b - a) * i / (n - 1)) for i in range(n)]
    else:
        xs = [xmin + (xmax - xmin) * i / (n - 1) for i in range(n)]
    for x in xs:
        y = f.predict(x)
        if y is None:
            continue
        if bands:
            bd = f.band(x)
            out.append([x, y] + ([bd["conf_lo"], bd["conf_hi"], bd["pred_lo"], bd["pred_hi"]]
                                 if bd else [None] * 4))
        else:
            out.append([x, y])
    return out
