"""SOFTmax PRO formula language: parser and evaluator.

The function list, operators, error values and messages follow the original's
Formula Help (SPF.HLP) and string tables (SPF.RSR STR# 4700, 8000, 8010).

Values are numbers, text, True/False, :class:`Err`, or lists of those.  A list
of lists is "per sample, per replicate" data as found in group tables.
Operators and plain functions work element by element; statistical functions
collapse the innermost list.
"""
from __future__ import annotations

import datetime as _dt
import math
import random
import re
from typing import Any, Callable, Optional


class Err:
    """A SOFTmax PRO error value (MakeErr codes 101..110)."""

    NAMES = {101: "", 102: "Name?", 103: "Masked", 104: "No Fit", 105: "Fit Error",
             106: "Range?", 107: "Limits?", 108: "Error", 109: "Domain", 110: "PrLoss"}

    __slots__ = ("code",)

    def __init__(self, code: int = 108):
        self.code = code if code in self.NAMES else 108

    def __repr__(self):
        return f"Err({self.code})"

    def __str__(self):
        return self.NAMES[self.code]

    def __eq__(self, other):
        return isinstance(other, Err) and other.code == self.code

    def __hash__(self):
        return hash(("Err", self.code))


EMPTY = Err(101)
E_NAME, E_MASKED, E_NOFIT, E_FIT, E_RANGE, E_LIMITS, E_ERROR, E_DOMAIN = (
    Err(102), Err(103), Err(104), Err(105), Err(106), Err(107), Err(108), Err(109))


class FormulaError(Exception):
    """Syntax or evaluation problem, with the original program's wording."""


# --------------------------------------------------------------------- lexer
_TOKEN = re.compile(r"""
    \s*(?:
      (?P<num>(?:\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?))
    | (?P<str>"[^"]*")
    | (?P<qname>'[^']*')
    | (?P<acc>![A-Za-z_][A-Za-z0-9_]*)
    | (?P<name>[A-Za-z_][A-Za-z0-9_#.%]*)
    | (?P<op><>|<=|>=|[-+*/^=<>(),@])
    )""", re.X)


def tokenize(text: str) -> list:
    out, pos = [], 0
    text = text.rstrip()
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            rest = text[pos:].lstrip()
            if rest.startswith('"'):
                raise FormulaError("Missing quotation mark")
            if rest.startswith("'"):
                raise FormulaError("Missing single quote")
            raise FormulaError(f"Illegal character '{rest[:1]}'")
        pos = m.end()
        kind = m.lastgroup
        val = m.group(kind)
        if kind == "num":
            out.append(("num", float(val)))
        elif kind == "str":
            out.append(("str", val[1:-1]))
        elif kind == "qname":
            out.append(("name", val[1:-1]))
        elif kind == "acc":
            out.append(("name", val))
        elif kind == "name":
            low = val.lower()
            if low in ("and", "or", "not", "mod"):
                out.append(("op", low))
            else:
                out.append(("name", val))
        else:
            out.append(("op", val))
    return out


# -------------------------------------------------------------------- parser
class _Parser:
    def __init__(self, toks):
        self.t = toks
        self.i = 0

    def peek(self):
        return self.t[self.i] if self.i < len(self.t) else (None, None)

    def take(self, kind=None, val=None):
        k, v = self.peek()
        if k is None:
            raise FormulaError("Unexpected end of expression")
        if (kind and k != kind) or (val is not None and v != val):
            raise FormulaError("Syntax error")
        self.i += 1
        return v

    def is_op(self, *vals):
        k, v = self.peek()
        return k == "op" and v in vals

    def parse(self):
        if not self.t:
            raise FormulaError("Empty expression")
        node = self.expr()
        if self.i < len(self.t):
            k, v = self.peek()
            if k == "op" and v == ")":
                raise FormulaError("Extra right parenthesis")
            raise FormulaError("Operator expected")
        return node

    def expr(self):
        node = self.and_()
        while self.is_op("or"):
            self.i += 1
            node = ("bin", "or", node, self.and_())
        return node

    def and_(self):
        node = self.not_()
        while self.is_op("and"):
            self.i += 1
            node = ("bin", "and", node, self.not_())
        return node

    def not_(self):
        if self.is_op("not"):
            self.i += 1
            return ("un", "not", self.not_())
        return self.cmp()

    def cmp(self):
        node = self.add()
        while self.is_op("=", "<>", "<", "<=", ">", ">="):
            op = self.take()
            node = ("bin", op, node, self.add())
        return node

    def add(self):
        node = self.mul()
        while self.is_op("+", "-"):
            op = self.take()
            node = ("bin", op, node, self.mul())
        return node

    def mul(self):
        node = self.unary()
        while self.is_op("*", "/", "mod"):
            op = self.take()
            node = ("bin", op, node, self.unary())
        return node

    def unary(self):
        if self.is_op("-"):
            self.i += 1
            return ("un", "-", self.unary())
        if self.is_op("+"):
            self.i += 1
            return self.unary()
        return self.pow()

    def pow(self):
        node = self.atom()
        if self.is_op("^"):
            self.i += 1
            node = ("bin", "^", node, self.unary())
        return node

    def atom(self):
        k, v = self.peek()
        if k is None:
            raise FormulaError("Unexpected end of expression")
        if k == "num":
            self.i += 1
            return ("num", v)
        if k == "str":
            self.i += 1
            return ("str", v)
        if k == "op" and v == "(":
            self.i += 1
            node = self.expr()
            if not self.is_op(")"):
                raise FormulaError("Missing right parenthesis")
            self.i += 1
            return node
        if k == "name":
            self.i += 1
            section = None
            if self.is_op("@"):
                self.i += 1
                if self.peek()[0] != "name":
                    raise FormulaError("Operand expected")
                section = self.take("name")
            if self.is_op("("):
                self.i += 1
                args = []
                if not self.is_op(")"):
                    args.append(self.expr())
                    while self.is_op(","):
                        self.i += 1
                        args.append(self.expr())
                if not self.is_op(")"):
                    raise FormulaError("Missing right parenthesis")
                self.i += 1
                return ("call", v, args)
            return ("ref", v, section)
        raise FormulaError("Operand expected")


_cache: dict = {}


def parse(text: str):
    if text not in _cache:
        if len(_cache) > 2000:
            _cache.clear()
        _cache[text] = _Parser(tokenize(text)).parse()
    return _cache[text]


def check(text: str) -> Optional[str]:
    """None if ``text`` parses, else the error message."""
    try:
        parse(text)
        return None
    except FormulaError as e:
        return str(e)


# ---------------------------------------------------------------- evaluation
def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _num(v):
    """Coerce a scalar to a number or return an Err."""
    if isinstance(v, Err):
        return v
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if v is None:
        return EMPTY
    return E_ERROR


def lift1(f: Callable, a):
    if isinstance(a, list):
        return [lift1(f, x) for x in a]
    return f(a)


def lift2(f: Callable, a, b):
    if isinstance(a, list) and isinstance(b, list):
        n = max(len(a), len(b))
        if len(a) == 1 and n > 1:
            a = a * n
        if len(b) == 1 and n > 1:
            b = b * n
        return [lift2(f, a[i] if i < len(a) else EMPTY, b[i] if i < len(b) else EMPTY)
                for i in range(n)]
    if isinstance(a, list):
        return [lift2(f, x, b) for x in a]
    if isinstance(b, list):
        return [lift2(f, a, y) for y in b]
    return f(a, b)


def _safe(fn: Callable) -> Callable:
    def g(*a):
        nums = []
        for x in a:
            x = _num(x)
            if isinstance(x, Err):
                return x
            nums.append(x)
        try:
            r = fn(*nums)
        except (ValueError, ZeroDivisionError):
            return E_DOMAIN
        except OverflowError:
            return E_RANGE
        if isinstance(r, float) and (math.isnan(r) or math.isinf(r)):
            return E_DOMAIN
        return r
    return g


def _arith(op):
    fn = {"-": lambda a, b: a - b, "*": lambda a, b: a * b, "/": lambda a, b: a / b,
          "^": lambda a, b: a ** b, "mod": lambda a, b: math.fmod(a, b)}[op]
    g = _safe(lambda a, b: _real(fn(a, b)))
    return lambda a, b: lift2(g, a, b)


def _real(v):
    if isinstance(v, complex):
        raise ValueError
    return v


def _plus(a, b):
    if isinstance(a, str) or isinstance(b, str):
        if isinstance(a, Err):
            return a
        if isinstance(b, Err):
            return b
        return to_text(a) + to_text(b)
    return _safe(lambda x, y: x + y)(a, b)


def _compare(op):
    def f(a, b):
        if isinstance(a, Err):
            return a
        if isinstance(b, Err):
            return b
        if isinstance(a, str) and isinstance(b, str):
            a, b = a.lower(), b.lower()          # "=" on text ignores case (help: Exact)
        elif isinstance(a, str) or isinstance(b, str):
            raise FormulaError("Type mismatch")
        elif not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            return E_ERROR                       # e.g. a plot name compared with a number
        else:
            a, b = float(a), float(b)
            if op in ("=", "<>") and math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12):
                return op == "="
        return {"=": a == b, "<>": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]
    return lambda a, b: lift2(f, a, b)


def _logic(v):
    if isinstance(v, Err):
        return v
    if isinstance(v, bool):
        return v
    if is_num(v):
        return v != 0
    raise FormulaError("Formula must evaluate to TRUE or FALSE")


def to_text(v, decimals: Optional[int] = None) -> str:
    if isinstance(v, Err):
        return str(v)
    if isinstance(v, bool):
        return "True" if v else "False"
    if is_num(v):
        if decimals is not None:
            return f"{v:.{decimals}f}"
        return f"{v:.10g}"
    if isinstance(v, list):
        return ", ".join(to_text(x, decimals) for x in v)
    return "" if v is None else str(v)


def flatten(v) -> list:
    if isinstance(v, list):
        out = []
        for x in v:
            out.extend(flatten(x))
        return out
    return [v]


def depth(v) -> int:
    if not isinstance(v, list):
        return 0
    return 1 + max((depth(x) for x in v), default=0)


def aggregate(f: Callable, v):
    """Collapse the innermost list with ``f(list_of_numbers)``."""
    if isinstance(v, list) and any(isinstance(x, list) for x in v):
        return [aggregate(f, x if isinstance(x, list) else [x]) for x in v]
    items = v if isinstance(v, list) else [v]
    nums = [float(x) for x in items if is_num(x)]
    if not nums:
        errs = [x for x in items if isinstance(x, Err) and x.code not in (101, 103)]
        return errs[0] if errs else EMPTY
    try:
        r = f(nums)
    except (ValueError, ZeroDivisionError):
        return E_DOMAIN
    return r


def _stdev(xs, pop=False):
    n = len(xs)
    if n < (1 if pop else 2):
        raise ValueError
    m = sum(xs) / n
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n if pop else n - 1))


def _cv(xs, pop=False):
    return _stdev(xs, pop) / (sum(xs) / len(xs)) * 100.0


def linreg(xs, ys):
    """Least-squares line through paired numbers; returns (intercept, slope)."""
    pts = [(float(x), float(y)) for x, y in zip(xs, ys) if is_num(x) and is_num(y)]
    n = len(pts)
    if n < 2:
        raise ValueError
    sx = sum(p[0] for p in pts)
    sy = sum(p[1] for p in pts)
    sxx = sum(p[0] ** 2 for p in pts)
    sxy = sum(p[0] * p[1] for p in pts)
    d = n * sxx - sx * sx
    if d == 0:
        raise ValueError
    b = (n * sxy - sx * sy) / d
    return (sy - b * sx) / n, b


class Plot:
    """What a plot name evaluates to: a fitted curve usable by InterpX and friends."""

    def __init__(self, fit=None, name=""):
        self.fit = fit
        self.name = name


CONSTANTS = {"true": True, "false": False, "pi": math.pi}

_MATH1 = {
    "abs": abs, "acos": math.acos, "asin": math.asin, "atan": math.atan, "ceil": math.ceil,
    "cos": math.cos, "cosh": math.cosh, "exp": math.exp, "floor": math.floor,
    "fract": lambda x: math.copysign(abs(x) - math.floor(abs(x)), x), "int": math.trunc,
    "ln": math.log, "log10": math.log10, "log": math.log10,
    "sign": lambda x: (x > 0) - (x < 0), "sin": math.sin, "sinh": math.sinh, "sqrt": math.sqrt,
    "tan": math.tan, "tanh": math.tanh,
    "fact": lambda x: float(math.factorial(int(x))) if 0 <= x <= 170 and x == int(x) else (_ for _ in ()).throw(
        OverflowError() if x > 170 else ValueError()),
}
_AGG = {
    "average": lambda xs: sum(xs) / len(xs), "avg": lambda xs: sum(xs) / len(xs),
    "sum": sum, "min": min, "max": max,
    "stdev": _stdev, "stdevp": lambda xs: _stdev(xs, True),
    "cv": _cv, "cvp": lambda xs: _cv(xs, True),
    "count": len,
}
FUNCTIONS = sorted(list(_MATH1) + list(_AGG) + [
    "atan2", "round", "rand", "pi", "if", "true", "false", "isempty", "iserr", "makeerr",
    "intercept", "slope", "interpx", "interpy", "parma", "parmb", "parmc", "parmd", "rsquared",
    "ec50", "parmerr",
    "index", "item", "itemcount", "itemindex", "concat", "exact", "left", "len", "lower", "mid",
    "now", "right", "text", "today", "upper", "val"])


def _text_fn(fn):
    def g(*a):
        for x in a:
            if isinstance(x, Err):
                return x
        return fn(*a)
    return g


class Evaluator:
    """Evaluates parsed formulas; ``resolve(name, section)`` supplies named values."""

    def __init__(self, resolve: Callable[[str, Optional[str]], Any]):
        self.resolve = resolve

    def eval(self, text: str):
        return self.node(parse(text))

    def node(self, n):
        kind = n[0]
        if kind == "num" or kind == "str":
            return n[1]
        if kind == "ref":
            low = n[1].lower()
            if n[2] is None and low in CONSTANTS:
                return CONSTANTS[low]
            if n[2] is None and low in ("rand", "now", "today"):
                return self.call(low, [])
            return self.resolve(n[1], n[2])
        if kind == "un":
            v = self.node(n[2])
            if n[1] == "-":
                return lift1(_safe(lambda x: -x), v)
            return lift1(lambda x: x if isinstance(x, Err) else (not _logic(x)), v)
        if kind == "bin":
            op = n[1]
            a = self.node(n[2])
            b = self.node(n[3])
            if op == "+":
                return lift2(_plus, a, b)
            if op in ("-", "*", "/", "^", "mod"):
                return _arith(op)(a, b)
            if op in ("and", "or"):
                def f(x, y):
                    x, y = _logic(x), _logic(y)
                    if isinstance(x, Err):
                        return x
                    if isinstance(y, Err):
                        return y
                    return (x and y) if op == "and" else (x or y)
                return lift2(f, a, b)
            return _compare(op)(a, b)
        if kind == "call":
            return self.call(n[1].lower(), n[2], n[1])
        raise FormulaError("Parser Error")

    def _args(self, name, nodes, lo, hi=None):
        hi = lo if hi is None else hi
        if len(nodes) < lo:
            raise FormulaError("Too few parameters")
        if len(nodes) > hi:
            raise FormulaError("Too many parameters")
        return [self.node(x) for x in nodes]

    def call(self, name, nodes, shown=None):
        if name in _MATH1:
            (a,) = self._args(name, nodes, 1)
            return lift1(_safe(_MATH1[name]), a)
        if name in _AGG:
            (a,) = self._args(name, nodes, 1)
            if name == "count":
                def cnt(v):
                    if isinstance(v, list) and any(isinstance(x, list) for x in v):
                        return [cnt(x if isinstance(x, list) else [x]) for x in v]
                    items = v if isinstance(v, list) else [v]
                    return float(sum(1 for x in items if is_num(x)))
                return cnt(a)
            return aggregate(_AGG[name], a)
        if name == "atan2":
            a, b = self._args(name, nodes, 2)
            return lift2(_safe(lambda x, y: math.atan2(y, x)), a, b)
        if name == "round":
            a, b = self._args(name, nodes, 2)
            def rnd(x, d):
                q = 10 ** int(d)
                return math.floor(abs(x) * q + 0.5) / q * (1 if x >= 0 else -1)
            return lift2(_safe(rnd), a, b)
        if name == "rand":
            self._args(name, nodes, 0)
            return random.random()
        if name == "pi":
            self._args(name, nodes, 0)
            return math.pi
        if name in ("true", "false"):
            self._args(name, nodes, 0)
            return name == "true"
        if name == "if":
            c, a, b = self._args(name, nodes, 3)
            def pick(cv, av, bv):
                cv = _logic(cv)
                if isinstance(cv, Err):
                    return cv
                return av if cv else bv
            if isinstance(c, list):
                return self._if_list(c, a, b, pick)
            return pick(c, a, b)
        if name == "isempty":
            (a,) = self._args(name, nodes, 1)
            return lift1(lambda x: x == EMPTY or x is None or x == "", a)
        if name == "iserr":
            (a,) = self._args(name, nodes, 1)
            return lift1(lambda x: isinstance(x, Err) and x.code != 101, a)
        if name == "makeerr":
            (a,) = self._args(name, nodes, 1)
            return lift1(lambda x: Err(int(x)) if is_num(x) else E_ERROR, a)
        if name in ("intercept", "slope"):
            xs, ys = self._args(name, nodes, 2)
            try:
                a, b = linreg(flatten(xs), flatten(ys))
            except ValueError:
                return E_DOMAIN
            return a if name == "intercept" else b
        if name in ("ec50", "parmerr"):
            args = self._args(name, nodes, 1 if name == "ec50" else 2)
            plot = args[0]
            if isinstance(plot, Err):
                return plot
            if not isinstance(plot, Plot):
                raise FormulaError("Type mismatch")
            fit = plot.fit
            if fit is None or not fit.ok:
                return E_NOFIT
            if name == "ec50":
                v = getattr(fit, "ec50", None)
                return E_NOFIT if v is None else v
            k = args[1]
            key = (str(k).strip().upper() if isinstance(k, str) else
                   "ABCDEFGHIJ"[int(k) - 1] if is_num(k) and 1 <= int(k) <= 10 else None)
            se = getattr(fit, "se", None) or {}
            v = se.get(key) if key else None
            return E_NOFIT if v is None else v
        if name in ("interpx", "interpy", "parma", "parmb", "parmc", "parmd", "rsquared"):
            args = self._args(name, nodes, 2 if name.startswith("interp") else 1)
            plot = args[0]
            if isinstance(plot, Err):
                return plot if len(args) == 1 else lift1(lambda _: plot, args[1])
            if not isinstance(plot, Plot):
                raise FormulaError("Type mismatch")
            fit = plot.fit
            if name.startswith("interp"):
                if fit is None or not fit.ok:
                    return lift1(lambda x: x if isinstance(x, Err) else E_NOFIT, args[1])
                fn = fit.inverse if name == "interpx" else fit.predict
                def g(v):
                    v = _num(v)
                    if isinstance(v, Err):
                        return v
                    r = fn(v)
                    return E_RANGE if r is None else r
                return lift1(g, args[1])
            if fit is None or not fit.ok:
                return E_NOFIT
            if name == "rsquared":
                return fit.r2 if fit.r2 is not None else E_NOFIT
            key = name[-1].upper()
            return fit.params.get(key, E_NOFIT)
        if name == "index":
            (a,) = self._args(name, nodes, 1)
            return [float(i + 1) for i in range(len(a))] if isinstance(a, list) else 1.0
        if name == "itemindex":
            (a,) = self._args(name, nodes, 1)
            def idx(v):
                if isinstance(v, list) and any(isinstance(x, list) for x in v):
                    return [idx(x) for x in v]
                return [float(i + 1) for i in range(len(v))] if isinstance(v, list) else 1.0
            return idx(a)
        if name == "item":
            a, i = self._args(name, nodes, 2)
            if not is_num(i):
                raise FormulaError("Type mismatch")
            items = a if isinstance(a, list) else [a]
            k = int(i)
            return items[k - 1] if 1 <= k <= len(items) else EMPTY
        if name == "itemcount":
            (a,) = self._args(name, nodes, 1)
            return float(len(flatten(a)))
        # ---- text ----
        if name == "concat":
            a, b = self._args(name, nodes, 2)
            return lift2(_text_fn(lambda x, y: to_text(x) + to_text(y)), a, b)
        if name == "exact":
            a, b = self._args(name, nodes, 2)
            return lift2(_text_fn(lambda x, y: to_text(x) == to_text(y)), a, b)
        if name in ("left", "right"):
            a, n = self._args(name, nodes, 2)
            if name == "left":
                return lift2(_text_fn(lambda s, k: to_text(s)[:int(k)]), a, n)
            return lift2(_text_fn(lambda s, k: to_text(s)[-int(k):] if int(k) > 0 else ""), a, n)
        if name == "mid":
            a, s, n = self._args(name, nodes, 3)
            if not (is_num(s) and is_num(n)):
                raise FormulaError("Type mismatch")
            return lift1(_text_fn(lambda t: to_text(t)[int(s) - 1:int(s) - 1 + int(n)]), a)
        if name == "len":
            (a,) = self._args(name, nodes, 1)
            return lift1(_text_fn(lambda s: float(len(to_text(s)))), a)
        if name in ("lower", "upper"):
            (a,) = self._args(name, nodes, 1)
            return lift1(_text_fn(lambda s: getattr(to_text(s), name)()), a)
        if name == "text":
            (a,) = self._args(name, nodes, 1)
            return lift1(_text_fn(to_text), a)
        if name == "val":
            (a,) = self._args(name, nodes, 1)
            def val(s):
                try:
                    return float(to_text(s).strip())
                except ValueError:
                    return E_ERROR
            return lift1(_text_fn(val), a)
        if name == "now":
            self._args(name, nodes, 0)
            return _dt.datetime.now().strftime("%I:%M:%S %p").lstrip("0")
        if name == "today":
            self._args(name, nodes, 0)
            d = _dt.date.today()
            return f"{d.strftime('%A, %B')} {d.day}, {d.year}"
        raise FormulaError(f"Unknown identifier '{shown or name}'")

    def _if_list(self, c, a, b, pick):
        def at(v, i):
            return v[i] if isinstance(v, list) and i < len(v) else (v if not isinstance(v, list) else EMPTY)
        out = []
        for i, cv in enumerate(c):
            av, bv = at(a, i), at(b, i)
            if isinstance(cv, list):
                out.append(self._if_list(cv, av, bv, pick))
            else:
                out.append(pick(cv, av, bv))
        return out
