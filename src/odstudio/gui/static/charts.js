"use strict";
/* Small SVG charting helpers: no dependencies, crisp when printed. */
const SVGNS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs, ...kids) {
  const e = document.createElementNS(SVGNS, tag);
  for (const k in attrs || {}) if (attrs[k] !== null && attrs[k] !== undefined) e.setAttribute(k, attrs[k]);
  for (const k of kids) e.append(k);
  return e;
}

function niceTicks(lo, hi, n = 5) {
  if (!(hi > lo)) { hi = lo + 1; }
  const span = hi - lo, raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= n + 1) || 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + step * 1e-9; v += step) out.push(+v.toPrecision(12));
  return out;
}

function logTicks(lo, hi) {
  const out = [];
  for (let e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++) out.push(Math.pow(10, e));
  return out.filter(v => v >= lo * 0.999 && v <= hi * 1.001);
}

function fmtTick(v) {
  const a = Math.abs(v);
  if (a !== 0 && (a >= 1e5 || a < 1e-3)) return v.toExponential(0).replace("e+", "e");
  return String(+v.toPrecision(6));
}

function extent(values, pad = 0.05) {
  const vs = values.filter(v => Number.isFinite(v));
  if (!vs.length) return [0, 1];
  let lo = Math.min(...vs), hi = Math.max(...vs);
  if (lo === hi) { lo -= Math.abs(lo) * 0.1 || 0.5; hi += Math.abs(hi) * 0.1 || 0.5; }
  const d = (hi - lo) * pad;
  return [lo - d, hi + d];
}

/* Eight point shapes; with ten colours that is 80 distinct curves before anything repeats. */
const SHAPES = 8;
function symbol(k, x, y, color) {
  const sh = ((k % SHAPES) + SHAPES) % SHAPES, a = {fill: color, class: "sym"};
  const poly = pts => svgEl("path", Object.assign({d: "M" + pts.map(p => (x + p[0]).toFixed(1) + " " + (y + p[1]).toFixed(1)).join("L") + "Z"}, a));
  switch (sh) {
    case 0: return svgEl("circle", Object.assign({cx: x, cy: y, r: 3.6}, a));
    case 1: return svgEl("rect", Object.assign({x: x - 3.3, y: y - 3.3, width: 6.6, height: 6.6}, a));
    case 2: return poly([[0, -4.4], [4, 3], [-4, 3]]);
    case 3: return poly([[0, -4.6], [4.6, 0], [0, 4.6], [-4.6, 0]]);
    case 4: return poly([[0, 4.4], [4, -3], [-4, -3]]);
    case 5: return svgEl("circle", {cx: x, cy: y, r: 3.4, fill: "none", stroke: color, "stroke-width": 1.6, class: "symo"});
    case 6: return svgEl("rect", {x: x - 3.1, y: y - 3.1, width: 6.2, height: 6.2, fill: "none", stroke: color, "stroke-width": 1.6, class: "symo"});
    default: return svgEl("path", {d: `M${x - 3.6} ${y - 3.6}L${x + 3.6} ${y + 3.6}M${x + 3.6} ${y - 3.6}L${x - 3.6} ${y + 3.6}`, stroke: color, "stroke-width": 1.8, fill: "none", class: "symo"});
  }
}
/* Small inline legend mark matching a series' colour and symbol. */
function legendMark(k, color) {
  return svgEl("svg", {viewBox: "-6 -6 12 12", width: 12, height: 12, class: "lgmark"}, symbol(k, 0, 0, color));
}

/* opts: {w,h, series:[{pts:[[x,y,ex,ey]], color, line, symbols, name}], curves:[{pts,color}],
          x:{log,min,max,label,grid,ticks,auto}, y:{...}, legend:bool} */
function chart(opts) {
  const W = opts.w || 640, H = opts.h || 360;
  const m = {l: 58, r: 16, t: 14, b: 44};
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  const svg = svgEl("svg", {viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img"});
  const allX = [], allY = [];
  for (const s of opts.series || []) for (const p of s.pts) { allX.push(p[0]); allY.push(p[1]); }
  for (const c of opts.curves || []) for (const p of c.pts) { allY.push(p[1]); if (c.bands) for (const k of [4, 5]) if (Number.isFinite(p[k])) allY.push(p[k]); }
  const ax = (o, vals) => {
    o = Object.assign({log: false, auto: true, ticks: true, grid: false, label: ""}, o || {});
    let [lo, hi] = o.auto === false ? [o.min, o.max] : extent(o.log ? vals.filter(v => v > 0) : vals);
    if (o.log) { if (!(lo > 0)) lo = Math.min(...vals.filter(v => v > 0), 1) / 2 || 0.1; if (!(hi > lo)) hi = lo * 10; }
    const f = o.log ? v => Math.log10(v) : v => v;
    return {o, lo, hi, t: v => (f(v) - f(lo)) / (f(hi) - f(lo))};
  };
  const view = opts.view || {};
  const X = ax(view.x ? Object.assign({}, opts.x, {auto: false, min: view.x[0], max: view.x[1]}) : opts.x, allX);
  const Y = ax(view.y ? Object.assign({}, opts.y, {auto: false, min: view.y[0], max: view.y[1]}) : opts.y, allY);
  const px = v => m.l + X.t(v) * pw, py = v => m.t + ph - Y.t(v) * ph;
  const ok = (x, y) => Number.isFinite(px(x)) && Number.isFinite(py(y));
  svg.append(svgEl("rect", {x: m.l, y: m.t, width: pw, height: ph, class: "plotarea"}));
  const xt = X.o.log ? logTicks(X.lo, X.hi) : niceTicks(X.lo, X.hi, 6);
  const yt = Y.o.log ? logTicks(Y.lo, Y.hi) : niceTicks(Y.lo, Y.hi, 5);
  for (const v of xt) {
    const x = px(v);
    if (x < m.l - 0.5 || x > m.l + pw + 0.5) continue;
    if (X.o.grid) svg.append(svgEl("line", {x1: x, x2: x, y1: m.t, y2: m.t + ph, class: "grid"}));
    if (X.o.ticks) svg.append(svgEl("line", {x1: x, x2: x, y1: m.t + ph, y2: m.t + ph + 5, class: "tick"}));
    const t = svgEl("text", {x, y: m.t + ph + 18, class: "ticklabel", "text-anchor": "middle"}); t.textContent = fmtTick(v); svg.append(t);
  }
  for (const v of yt) {
    const y = py(v);
    if (y < m.t - 0.5 || y > m.t + ph + 0.5) continue;
    if (Y.o.grid) svg.append(svgEl("line", {x1: m.l, x2: m.l + pw, y1: y, y2: y, class: "grid"}));
    if (Y.o.ticks) svg.append(svgEl("line", {x1: m.l - 5, x2: m.l, y1: y, y2: y, class: "tick"}));
    const t = svgEl("text", {x: m.l - 8, y: y + 4, class: "ticklabel", "text-anchor": "end"}); t.textContent = fmtTick(v); svg.append(t);
  }
  if (X.o.label) { const t = svgEl("text", {x: m.l + pw / 2, y: H - 6, class: "axislabel", "text-anchor": "middle"}); t.textContent = X.o.label; svg.append(t); }
  if (Y.o.label) { const t = svgEl("text", {x: 14, y: m.t + ph / 2, class: "axislabel", "text-anchor": "middle", transform: `rotate(-90 14 ${m.t + ph / 2})`}); t.textContent = Y.o.label; svg.append(t); }
  const clip = "clip" + Math.random().toString(36).slice(2);
  svg.append(svgEl("clipPath", {id: clip}, svgEl("rect", {x: m.l, y: m.t, width: pw, height: ph})));
  const g = svgEl("g", {"clip-path": `url(#${clip})`}); svg.append(g);
  const path = pts => pts.filter(p => ok(p[0], p[1])).map((p, i) => (i ? "L" : "M") + px(p[0]).toFixed(1) + " " + py(p[1]).toFixed(1)).join("");
  for (const c of opts.curves || []) {
    if (!c.pts.length) continue;
    if (c.bands) {
      const conf = c.pts.filter(p => Number.isFinite(p[2]) && Number.isFinite(p[3]));
      if (conf.length > 1) {
        const d = conf.map((p, i) => (i ? "L" : "M") + px(p[0]).toFixed(1) + " " + py(p[3]).toFixed(1)).join("") +
          conf.slice().reverse().map(p => "L" + px(p[0]).toFixed(1) + " " + py(p[2]).toFixed(1)).join("") + "Z";
        g.append(svgEl("path", {d, class: "band", fill: c.color}));
      }
      for (const k of [4, 5]) {
        const pr = c.pts.filter(p => Number.isFinite(p[k])).map(p => [p[0], p[k]]);
        if (pr.length > 1) g.append(svgEl("path", {d: path(pr), class: "predband", stroke: c.color}));
      }
    }
    g.append(svgEl("path", {d: path(c.pts), class: "curve", stroke: c.color, "stroke-dasharray": c.dash || null}));
  }
  svg.__axes = {m, pw, ph, X, Y, W, H};
  (opts.series || []).forEach((s, si) => {
    if (s.line && s.pts.length > 1) g.append(svgEl("path", {d: path(s.pts), class: "series", stroke: s.color}));
    for (const p of s.pts) {
      if (!ok(p[0], p[1])) continue;
      const x = px(p[0]), y = py(p[1]);
      if (p[3]) g.append(svgEl("line", {x1: x, x2: x, y1: py(p[1] - p[3]), y2: py(p[1] + p[3]), class: "errbar", stroke: s.color}));
      if (p[2]) g.append(svgEl("line", {x1: px(p[0] - p[2]), x2: px(p[0] + p[2]), y1: y, y2: y, class: "errbar", stroke: s.color}));
      if (s.symbols !== false) {
        g.append(symbol(s.shape === undefined ? si : s.shape, x, y, s.color));
      }
    }
  });
  return svg;
}

/* Cluster / stack bars: categories are the x values of the first plot. */
function barChart(opts) {
  const W = opts.w || 640, H = opts.h || 360, m = {l: 58, r: 16, t: 14, b: 44};
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  const svg = svgEl("svg", {viewBox: `0 0 ${W} ${H}`, class: "chart"});
  const cats = (opts.series[0] || {pts: []}).pts.map(p => p[0]);
  const stack = opts.stack;
  let hi = 0, lo = 0;
  cats.forEach((_, i) => {
    let sum = 0;
    for (const s of opts.series) { const v = (s.pts[i] || [0, 0])[1]; sum += v; if (!stack) { hi = Math.max(hi, v); lo = Math.min(lo, v); } }
    if (stack) hi = Math.max(hi, sum);
  });
  if (hi === lo) hi = lo + 1;
  const py = v => m.t + ph - (v - lo) / (hi - lo) * ph;
  svg.append(svgEl("rect", {x: m.l, y: m.t, width: pw, height: ph, class: "plotarea"}));
  for (const v of niceTicks(lo, hi, 5)) {
    const y = py(v);
    if (opts.y && opts.y.grid) svg.append(svgEl("line", {x1: m.l, x2: m.l + pw, y1: y, y2: y, class: "grid"}));
    const t = svgEl("text", {x: m.l - 8, y: y + 4, class: "ticklabel", "text-anchor": "end"}); t.textContent = fmtTick(v); svg.append(t);
  }
  const bw = pw / Math.max(1, cats.length);
  cats.forEach((c, i) => {
    let base = 0;
    const inner = bw * 0.8, n = opts.series.length;
    opts.series.forEach((s, k) => {
      const v = (s.pts[i] || [0, 0])[1];
      const x = stack ? m.l + i * bw + bw * 0.1 : m.l + i * bw + bw * 0.1 + k * inner / n;
      const w = stack ? inner : inner / n - 1;
      const y0 = py(stack ? base : Math.max(0, lo)), y1 = py(stack ? base + v : v);
      svg.append(svgEl("rect", {x, y: Math.min(y0, y1), width: Math.max(1, w), height: Math.abs(y0 - y1), fill: s.color, class: "bar"}));
      base += v;
    });
    const t = svgEl("text", {x: m.l + i * bw + bw / 2, y: m.t + ph + 18, class: "ticklabel", "text-anchor": "middle"}); t.textContent = fmtTick(c); svg.append(t);
  });
  if (opts.x && opts.x.label) { const t = svgEl("text", {x: m.l + pw / 2, y: H - 6, class: "axislabel", "text-anchor": "middle"}); t.textContent = opts.x.label; svg.append(t); }
  if (opts.y && opts.y.label) { const t = svgEl("text", {x: 14, y: m.t + ph / 2, class: "axislabel", "text-anchor": "middle", transform: `rotate(-90 14 ${m.t + ph / 2})`}); t.textContent = opts.y.label; svg.append(t); }
  return svg;
}

/* Tiny plot drawn inside a well: polyline + optional overlay segment. */
function sparkline(xs, ys, range, line, color) {
  const W = 60, H = 34;
  const svg = svgEl("svg", {viewBox: `0 0 ${W} ${H}`, class: "spark", preserveAspectRatio: "none"});
  const pts = xs.map((x, i) => [x, ys[i]]).filter(p => Number.isFinite(p[1]));
  if (!pts.length) return svg;
  const x0 = xs[0], x1 = xs[xs.length - 1] === x0 ? x0 + 1 : xs[xs.length - 1];
  const [lo, hi] = range;
  const px = x => 2 + (x - x0) / (x1 - x0) * (W - 4), py = y => H - 2 - (Math.min(hi, Math.max(lo, y)) - lo) / ((hi - lo) || 1) * (H - 4);
  svg.append(svgEl("path", {d: pts.map((p, i) => (i ? "L" : "M") + px(p[0]).toFixed(1) + " " + py(p[1]).toFixed(1)).join(""), class: "sparkline", stroke: color || "currentColor"}));
  if (line) svg.append(svgEl("line", {x1: px(line[0][0]), y1: py(line[0][1]), x2: px(line[1][0]), y2: py(line[1][1]), class: "sparkfit"}));
  return svg;
}

/* ------------------------------------------------------------------ zoom */
/* Wrap a chart so it can be zoomed and panned.
   build(view) draws the chart for a view {x:[lo,hi], y:[lo,hi]} (or null = automatic).
   state: object kept by the caller, so the zoom survives redraws ({view}).
   Drag: zoom to a box.  Shift-drag: pan.  Ctrl/Cmd + wheel or pinch: zoom at the pointer.
   Double-click or Reset: back to the automatic range. */
function zoomable(build, state) {
  state = state || {};
  const holder = h("div", {class: "zoomwrap"});
  const hint = h("span", {class: "zoomhint muted"});
  const bar = h("div", {class: "zoombar noprint"},
    h("button", {type: "button", class: "tb", title: "Zoom in", "aria-label": "Zoom in", onclick: () => zoomBy(0.6)}, "+"),
    h("button", {type: "button", class: "tb", title: "Zoom out", "aria-label": "Zoom out", onclick: () => zoomBy(1 / 0.6)}, "−"),
    h("button", {type: "button", class: "tb", title: "Show everything", onclick: () => { state.view = null; draw(); }}, "Reset"),
    hint);
  const box = h("div", {class: "zoombox"});
  const plot = h("div", {class: "zoomplot"}, box);
  holder.append(plot, bar);
  let svg = null;

  const fwd = (a, v) => a.o.log ? Math.log10(v) : v;
  const back = (a, v) => a.o.log ? Math.pow(10, v) : v;
  const range = a => [fwd(a, a.lo), fwd(a, a.hi)];
  function toData(clientX, clientY) {
    const r = svg.getBoundingClientRect(), A = svg.__axes;
    const sx = (clientX - r.left) / r.width * A.W, sy = (clientY - r.top) / r.height * A.H;
    const [x0, x1] = range(A.X), [y0, y1] = range(A.Y);
    const tx = (sx - A.m.l) / A.pw, ty = 1 - (sy - A.m.t) / A.ph;
    return {tx, ty, x: x0 + tx * (x1 - x0), y: y0 + ty * (y1 - y0)};       // x, y in axis space
  }
  function setView(xa, ya) {                                               // axis-space ranges
    const A = svg.__axes;
    if (!(xa[1] > xa[0]) || !(ya[1] > ya[0])) return;
    state.view = {x: [back(A.X, xa[0]), back(A.X, xa[1])], y: [back(A.Y, ya[0]), back(A.Y, ya[1])]};
    draw();
  }
  function zoomBy(f, cx, cy) {
    if (!svg) return;
    const A = svg.__axes, [x0, x1] = range(A.X), [y0, y1] = range(A.Y);
    const px = cx === undefined ? (x0 + x1) / 2 : cx, py = cy === undefined ? (y0 + y1) / 2 : cy;
    setView([px - (px - x0) * f, px + (x1 - px) * f], [py - (py - y0) * f, py + (y1 - py) * f]);
  }
  function draw() {
    holder.pending = false;
    const next = build(state.view);
    if (svg) svg.replaceWith(next); else plot.prepend(next);
    svg = next;
    hint.textContent = state.view ? "Zoomed — double-click or Reset to see everything" : "Drag to zoom • Shift-drag to pan • " + (MOD === "⌘" ? "⌘" : "Ctrl") + "-scroll to zoom";
    attach();
  }
  function attach() {
    if (!svg.__axes) return;                      // not a zoomable chart (e.g. a message)
    svg.addEventListener("dblclick", e => { e.preventDefault(); state.view = null; draw(); });
    svg.addEventListener("wheel", e => {
      if (!(e.ctrlKey || e.metaKey)) return;      // plain scrolling still scrolls the page
      e.preventDefault();
      const p = toData(e.clientX, e.clientY);
      zoomBy(Math.exp(e.deltaY * 0.01), p.x, p.y);
    }, {passive: false});
    svg.addEventListener("mousedown", e => {
      if (e.button) return;
      e.preventDefault();
      holder.busy = true;                       // live updates wait until the drag ends
      DRAG_END = () => {};                      // and section cards are not rebuilt under the pointer
      const start = toData(e.clientX, e.clientY);
      const pr = plot.getBoundingClientRect();
      const A = svg.__axes, [x0, x1] = range(A.X), [y0, y1] = range(A.Y);
      const pan = e.shiftKey;
      const move = ev => {
        if (pan) {
          const p = toData(ev.clientX, ev.clientY);
          const dx = p.x - start.x, dy = p.y - start.y;
          state.view = {x: [back(A.X, x0 - dx), back(A.X, x1 - dx)], y: [back(A.Y, y0 - dy), back(A.Y, y1 - dy)]};
          const nx = build(state.view); svg.replaceWith(nx); svg = nx;
          return;
        }
        Object.assign(box.style, {display: "block", left: Math.min(e.clientX, ev.clientX) - pr.left + "px", top: Math.min(e.clientY, ev.clientY) - pr.top + "px",
          width: Math.abs(ev.clientX - e.clientX) + "px", height: Math.abs(ev.clientY - e.clientY) + "px"});
      };
      const upFn = ev => {
        window.removeEventListener("mousemove", move);
        window.removeEventListener("mouseup", upFn);
        holder.busy = false;
        box.style.display = "none";
        if (pan) { draw(); return; }
        if (Math.abs(ev.clientX - e.clientX) < 6 || Math.abs(ev.clientY - e.clientY) < 6) {
          if (holder.pending) draw();
          return;
        }
        const end = toData(ev.clientX, ev.clientY);
        setView([Math.min(start.x, end.x), Math.max(start.x, end.x)], [Math.min(start.y, end.y), Math.max(start.y, end.y)]);
      };
      window.addEventListener("mousemove", move);
      window.addEventListener("mouseup", upFn);
    });
  }
  holder.redraw = () => { if (holder.busy) holder.pending = true; else draw(); };
  draw();
  return holder;
}
