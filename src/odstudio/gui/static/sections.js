"use strict";
/* Rendering of the four section types. */

const KIND_LABEL = {plate: "Plate", group: "Group", graph: "Graph", notes: "Notes", workflow: "Workflow"};

function tb(label, action, opts) {
  opts = opts || {};
  return h("button", {type: "button", class: "tb" + (opts.on ? " on" : "") + (opts.primary ? " primary" : ""), title: opts.title || null, disabled: opts.enabled === false, onclick: e => { e.stopPropagation(); action(); }}, label);
}

/* Window id of a rendered card. */
function CUR_AT(node) { return +node.dataset.doc; }

function sectionCard(s) {
  const active = UI.active === s.id;
  const reading = S.reading && S.reading.doc === CUR && S.reading.id === s.id;
  const card = h("section", {class: `card kind-${s.kind}` + (active ? " active" : "") + (s.report ? "" : " noreport") + (s.open ? "" : " closed"), "data-id": s.id, "data-doc": CUR,
    onmousedown: () => { if (UI.active !== s.id) { UI.active = s.id; markActive(); } }});
  const head = h("div", {class: "card-head", draggable: true,
    ondragstart: e => { e.dataTransfer.setData("application/x-odstudio-section", JSON.stringify({doc: CUR_AT(card), id: s.id})); e.dataTransfer.effectAllowed = "move"; },
    ondragover: e => { e.preventDefault(); card.classList.add("drop"); },
    ondragleave: () => card.classList.remove("drop"),
    ondrop: e => {
      e.preventDefault(); card.classList.remove("drop");
      let src = null;
      try { src = JSON.parse(e.dataTransfer.getData("application/x-odstudio-section") || "null"); } catch (ex) { src = null; }
      const doc = CUR_AT(card);
      if (!src || src.doc !== doc || src.id === s.id) return;           // only reorder within one window
      const d = S.docs.find(x => x.id === doc);
      op("move_section", {doc, id: src.id, index: d.doc.sections.findIndex(x => x.id === s.id)});
    },
    ondblclick: e => { if (!e.target.closest("button,select,input")) op("section_settings", {id: s.id, open: !s.open}); }},
    h("button", {type: "button", class: "tri", "aria-expanded": String(!!s.open), title: s.open ? "Minimize" : "Expand", "aria-label": (s.open ? "Minimize " : "Expand ") + s.name,
      onclick: e => { e.stopPropagation(); if (e.altKey) op("all_open", {open: !s.open}); else op("section_settings", {id: s.id, open: !s.open}); }}, s.open ? "▾" : "▸"),
    h("span", {class: "badge"}, KIND_LABEL[s.kind]),
    h("h2", {class: "card-title"}, s.name),
    s.report ? null : h("span", {class: "muted noprint", title: "Not included in the printed report"}, "not in report"),
    reading ? h("span", {class: "reading"}, `${S.reading.phase} ${S.reading.got} of ${S.reading.total}`) : null,
    h("span", {class: "spacer"}),
    h("div", {class: "toolbar noprint"}, s.open ? toolbarFor(s) : null));
  card.append(head);
  if (s.open) card.append(h("div", {class: "card-body"}, bodyFor(s)));
  return card;
}

function markActive() {
  document.querySelectorAll("section.card").forEach(c => c.classList.toggle("active", +c.dataset.doc === S.active && +c.dataset.id === UI.active));
  renderMenus();
}

function toolbarFor(s) {
  const id = s.id;
  if (s.kind === "plate") {
    const sel = UI.sel[id] || [], st0 = s.settings;
    return [tb("Setup", () => dlgInstrument(id), {title: "Instrument Settings"}), tb("Template", () => dlgTemplate(id)),
      tb("Reduction", () => dlgReduction(id)), tb("Display", () => dlgDisplay(id)),
      tb("Well Graph", () => dlgWellGraph(id, sel.length ? sel : (s.columns || colsOf(st0)).flatMap(c => [...ROWS].map(r => r + c)).slice(0, 12)), {enabled: dataMode(s) !== "endpoint" && (s.has_data || (S.reading && S.reading.doc === CUR && S.reading.id === s.id)), title: "Enlarge the plots of the selected wells"}),
      tb("Mask", () => sel.length ? op("mask", {id, wells: sel}) : alertDlg("Select the wells to mask first."), {enabled: s.has_data, title: "Leave the selected wells out of all calculations"}),
      h("span", {class: "seg"}, tb("Raw", () => op("display", {id, display: {view: "raw"}}), {on: s.display.view === "raw"}), tb("Reduced", () => op("display", {id, display: {view: "reduced"}}), {on: s.display.view === "reduced"}))];
  }
  if (s.kind === "group") {
    const g = s.group, cols = UI.cols[id] || [], sums = UI.sums[id] || [];
    return [tb("+ Column", () => dlgCalc("column", {id, group: g, after: cols[cols.length - 1]}), {title: "Create Column"}),
      tb("+ Summary", () => dlgCalc("summary", {id}), {title: "Create Summary"}),
      tb("Edit", () => editSelected(s), {enabled: cols.length === 1 || sums.length === 1}),
      tb("Hide", () => op("column_hide", {group: g, names: cols}), {enabled: cols.length > 0}),
      tb("Replicates", () => op("group_view", {group: g, hide_replicates: !s.hide_replicates}), {on: !s.hide_replicates, title: "Show or hide the per-well rows"}),
      tb("Show All", () => op("group_view", {group: g, show_all: true})),
      tb("Formulas", () => op("group_view", {group: g, show_formulas: !s.show_formulas}), {on: s.show_formulas})];
  }
  if (s.kind === "graph") {
    return [tb("Plots", () => dlgPlots(id)),
      h("label", {class: "mini"}, "Fit: ", select(S.fits, s.fit, {onchange: e => op("graph", {id, fit: e.target.value}), onclick: e => e.stopPropagation()})),
      tb("Fit Options", () => dlgFitOptions(id), {enabled: s.fit !== "none", title: "Weighting, confidence level, bands, fixed or seeded parameters"}),
      tb("Type", () => dlgGraphType(id), {title: "Graph Type"}), tb("X Axis", () => dlgAxis(id, "x")), tb("Y Axis", () => dlgAxis(id, "y"))];
  }
  if (s.kind === "workflow") {
    const w = S.workflow, mine = w && w.doc === CUR && w.section === id;
    const live = mine && ["running", "paused", "pausing", "message"].includes(w.status);
    if (!live) return [tb("\u25b6 Run Workflow", () => op("workflow_run", {id}, {confirmLabel: "Run"}), {primary: true, enabled: S.instrument.connected && !S.reading && s.steps.length > 0,
      title: S.instrument.connected ? "Run the steps in order" : "Connect the instrument first"})];
    return [w.status === "message" ? tb("Continue\u2026", () => workflowPrompt(true), {primary: true, title: "The workflow is waiting for you"}) : null,
      w.status === "paused" || w.status === "pausing" ? tb("Resume", () => op("workflow_resume")) : tb("Pause", () => op("workflow_pause"), {title: "Pause after the current step"}),
      tb("Stop", () => op("stop"))];
  }
  return [tb("+ Summary", () => dlgCalc("summary", {id}), {title: "Create Summary"}),
    tb("Formulas", () => op("notes", {id, show_formulas: !s.show_formulas}), {on: s.show_formulas})];
}

function editSelected(s) {
  const id = s.id, cols = UI.cols[id] || [], sums = UI.sums[id] || [];
  if (s.kind === "group" && cols.length === 1) {
    const c = s.columns.find(x => x.name === cols[0]);
    if (c) return dlgCalc("column", {id, group: s.group, old: c});
  }
  if (sums.length === 1) {
    const m = s.summaries.find(x => x.name === sums[0]);
    if (m) return dlgCalc("summary", {id, old: m});
  }
}

function bodyFor(s) {
  if (s.kind === "plate") return plateBody(s);
  if (s.kind === "group") return groupBody(s);
  if (s.kind === "graph") return graphBody(s);
  if (s.kind === "workflow") return workflowBody(s);
  return notesBody(s);
}

function styleOf(st) {
  st = st || {};
  return {fontWeight: st.bold ? "700" : null, fontStyle: st.italic ? "italic" : null, textDecoration: st.underline ? "underline" : null,
    fontSize: st.size ? st.size + "pt" : null, webkitTextStroke: st.outline ? "0.6px currentColor" : null, color: st.outline ? "transparent" : null,
    textShadow: st.shadow ? "1px 1px 1px rgba(0,0,0,.35)" : null, letterSpacing: st.condensed ? "-0.04em" : st.extended ? "0.12em" : null};
}

function summariesBlock(s, showFormulas) {
  if (!s.summaries || !s.summaries.length) return null;
  const id = s.id, sel = UI.sums[id] || [];
  return h("div", {class: "summaries"}, s.summaries.map(m => h("div", {class: "summary" + (sel.includes(m.name) ? " sel" : ""), style: styleOf(m.style), tabindex: "0",
    title: "Click to select, double-click to edit",
    onclick: e => { UI.sums[id] = e.ctrlKey || e.metaKey ? [...new Set(sel.concat([m.name]))] : [m.name]; UI.cols[id] = []; render(true); },
    ondblclick: () => dlgCalc("summary", {id, old: m})},
    m.hide_name ? null : h("span", {class: "s-name"}, m.name + " "), m.description ? h("span", {class: "s-desc"}, m.description + " ") : null,
    h("b", {class: "s-val"}, m.text), showFormulas ? h("code", {class: "s-formula"}, "  = " + m.formula) : null)));
}

/* ------------------------------------------------------------------ plate */
function plateRange(s) {
  let lo = Infinity, hi = -Infinity;
  for (const w in s.wells) for (const y of s.wells[w].ys || []) if (y !== null) { lo = Math.min(lo, y); hi = Math.max(hi, y); }
  if (!(hi > lo)) { lo = 0; hi = 1; }
  const r = s.reduction;
  if (r.min_od !== null && r.min_od !== undefined) lo = r.min_od;
  if (r.max_od !== null && r.max_od !== undefined) hi = r.max_od;
  return [lo, hi];
}

/* In very narrow windows a well has room for about four digits. */
function compact(text) {
  if (!UI.narrow) return text;
  const v = Number(text);
  if (text === "" || !Number.isFinite(v)) return text;
  if (Math.abs(v) >= 9999.5) return v.toExponential(1).replace("e+", "e");
  return String(+v.toPrecision(4));
}

function wellContent(s, w, range) {
  const d = s.wells[w], disp = s.display, mode = dataMode(s), masked = s.mask.includes(w);
  if (!d) return null;
  if (masked) return h("span", {class: "masked"}, "Masked");
  if ((s.info.saturated || []).includes(w)) return h("span", {class: "masked"}, ">>>>");
  if (disp.view === "raw") {
    if (mode === "endpoint") return h("div", {class: "nums"}, d.raw.map(v => h("span", {title: fmtNum(v)}, compact(fmtNum(v)))));
    return h("div", {class: "plotcell"}, sparkline(s.xs, d.ys, range, null), h("span", {class: "pv", title: "Reduced value " + d.text}, compact(d.text)));
  }
  const v = d.num;
  if (disp.type === "plots" && mode !== "endpoint") return h("div", {class: "plotcell"}, sparkline(s.xs, d.ys, range, d.line), h("span", {class: "pv", title: d.text}, compact(d.text)));
  if (v === null) return h("span", {class: "errv"}, d.text);
  const t = (v - disp.low) / (disp.high - disp.low);
  if (disp.type === "gray") { const k = Math.max(0, Math.min(6, Math.floor(t * 7))); return h("div", {class: "gray g" + k, title: d.text}, d.text); }
  if (disp.type === "ranged") return h("span", {class: "big"}, t < 0 ? "−" : t >= 1 ? "+" : String(Math.min(9, Math.floor(t * 10))));
  if (disp.type === "threshold") return h("span", {class: "big"}, v < disp.low ? "−" : v > disp.high ? "+" : "*");
  return h("span", {class: "num", title: d.text}, compact(d.text));
}

function plateBody(s) {
  const id = s.id, st = s.settings, r = s.reduction;
  const sel = new Set(UI.sel[id] || []);
  const range = plateRange(s);
  const grid = wellGrid({sel, onselect: () => { UI.sel[id] = [...sel]; }, onend: updateToolbars,
    ondbl: w => { if (dataMode(s) !== "endpoint" && (s.has_data || (S.reading && S.reading.id === id))) dlgWellGraph(id, sel.size ? [...sel] : [w]); else dlgTemplate(id); },
    style: w => wellStyle(s.template[w]),
    cls: w => ((s.columns || colsOf(st)).includes(+w.slice(1)) ? "" : "unread ") + (s.mask.includes(w) ? "ismasked" : ""),
    cell: w => {
      const t = s.template[w];
      return [t ? h("span", {class: "wtag", title: (t.group ? t.group + " • " : "") + t.sample + (t.value !== null && t.value !== undefined ? " • " + t.value : "")}, t.type === "plateblank" ? "BL" : t.sample) : null, wellContent(s, w, range)];
    }});
  const wl = st.mode === "spectrum" ? `${st.start}–${st.stop} nm, step ${st.step}` : st.wavelengths.map(w => w + " nm").join(", ");
  const line = (k, v) => v === null || v === undefined || v === "" ? null : h("div", {class: "kv"}, h("span", {}, k), h("b", {}, v));
  const dm = dataMode(s);
  const kindName = (list, k) => ((list || []).find(x => x[0] === k) || [k, k])[1];
  const redText = dm === "kinetic" ? kindName(S.kinetic_kinds, r.kinetic) : dm === "spectrum" ? kindName(S.spectrum_kinds, r.spectrum) : null;
  const i = s.info;
  const side = h("aside", {class: "plate-info"},
    h("h3", {}, "Instrument Settings"),
    line("Mode", {endpoint: "Endpoint", kinetic: "Kinetic", spectrum: "Spectrum"}[st.mode]),
    line(st.mode === "spectrum" ? "Sweep" : "Wavelengths", wl),
    st.mode === "kinetic" ? [line("Time", fmtTime(st.run_time)), line("Interval", fmtTime(st.interval)), line("Reads", s.expected)] : null,
    line("Strips", (s.columns || colsOf(st)).length === 12 ? "Read entire plate" : "Columns " + colsText(s.columns || colsOf(st))),
    line("Auto Mix", (st.mix_before ? `before ${st.mix_before_s} s` : "") + (st.mix_before && st.mix_between && st.mode === "kinetic" ? ", " : "") + (st.mix_between && st.mode === "kinetic" ? `between ${st.mix_between_s} s` : "") || "Off"),
    line("Calibrate", st.autocalibrate ? "On" : "Off"),
    st.pre_read && st.mode !== "spectrum" ? line("Pre-Read", s.has_preread ? "taken" : "not taken yet") : null,
    h("h3", {}, "Reduction"),
    st.mode !== "spectrum" ? line("Wavelength Formula", r.wavelength.replace(/!/g, "")) : null,
    redText ? line(dm === "kinetic" ? "Kinetic" : "Spectrum", redText + (s.unit ? ` (${s.unit})` : "")) : null,
    r.raw_mode === "transmittance" ? line("Raw data", "% Transmittance") : null,
    r.smoothing ? line("Smoothing", `${r.smoothing}-point moving average`) : null,
    r.zero_first && dm === "kinetic" ? line("First reading", "set to zero") : null,
    st.mode === "kinetic" ? [r.lag_time ? line("Lag Time", r.lag_time + " s") : null, r.end_time !== null ? line("End Time", r.end_time + " s") : null,
      r.vmax_points ? line("Vmax Pts", r.vmax_points + "/" + (i.points || s.expected)) : null] : null,
    r.min_od !== null ? line("OD Min", r.min_od) : null, r.max_od !== null ? line("OD Max", r.max_od) : null,
    line("Plate Blank", s.blank !== null && s.blank !== undefined && r.plate_blank ? "Subtracted (" + fmtNum(s.blank) + ")" : "No blanks"),
    h("h3", {}, "Data"),
    s.has_data ? [line(i.imported ? "Imported Data" : "Plate Read", i.read_at ? new Date(i.read_at * 1000).toLocaleString() : ""),
      dm !== "endpoint" ? line("Points Read", i.points + (i.points < i.expected && st.mode !== "endpoint" ? " of " + i.expected : "")) : null,
      dm !== st.mode ? line("Collected by", "workflow cycle") : null,
      i.mean_temp !== null && i.mean_temp !== undefined ? line("Temperature", i.min_temp === i.max_temp ? fmtNum(i.mean_temp, 1) + " °C" : `${fmtNum(i.mean_temp, 1)} °C  (${fmtNum(i.min_temp, 1)}–${fmtNum(i.max_temp, 1)})`) : null,
      i.setpoint ? line("Set Point", fmtNum(i.setpoint, 1) + " °C") : null,
      line("View", s.display.view === "raw" ? "Raw Data" : "Reduced" + (s.unit ? ` (${s.unit})` : ""))]
      : h("p", {class: "muted"}, "No Data. Press Read to read this plate."));
  return [h("div", {class: "plate-wrap"}, grid, side), summariesBlock(s, false)];
}

/* ------------------------------------------------------------------ group */
function groupBody(s) {
  const id = s.id, selCols = UI.cols[id] || [];
  const visible = s.columns.map((c, i) => [c, i]).filter(([c]) => !c.hidden && !(s.hide_replicates && c.per_well));
  if (!s.rows.length) {
    return [h("p", {class: "muted"}, `No wells are assigned to “${s.group}” yet. Open a plate's Template to assign some.`), summariesBlock(s, s.show_formulas)];
  }
  const table = h("table", {class: "grouptable"});
  table.append(h("thead", {}, h("tr", {}, visible.map(([c]) => h("th", {class: selCols.includes(c.name) ? "sel" : "", style: styleOf(c.style), tabindex: "0", scope: "col",
    title: "Click to select, double-click to edit",
    onclick: e => { UI.cols[id] = e.ctrlKey || e.metaKey || e.shiftKey ? [...new Set(selCols.concat([c.name]))] : [c.name]; UI.sums[id] = []; render(true); },
    ondblclick: () => dlgCalc("column", {id, group: s.group, old: c})}, c.name))),
    s.show_formulas ? h("tr", {class: "formulas"}, visible.map(([c]) => h("td", {}, h("code", {}, c.formula)))) : null));
  const body = h("tbody");
  for (const row of s.rows) {
    body.append(h("tr", {class: row.first ? "first" : ""}, visible.map(([c, i]) => h("td", {class: (selCols.includes(c.name) ? "sel " : "") + (isNaN(parseFloat(row.cells[i])) ? "txt" : "numc"), style: styleOf(c.style)}, row.cells[i]))));
  }
  table.append(body);
  const hidden = s.columns.filter(c => c.hidden).length;
  return [h("div", {class: "tablewrap"}, table),
    hidden ? h("p", {class: "muted noprint"}, `${hidden} hidden column${hidden > 1 ? "s" : ""}. `, h("a", {href: "#", onclick: e => { e.preventDefault(); op("group_view", {group: s.group, show_all: true}); }}, "Show All")) : null,
    s.blank !== null && s.blank !== undefined ? h("p", {class: "muted"}, "Group blank subtracted: " + fmtNum(s.blank)) : null,
    summariesBlock(s, s.show_formulas)];
}

/* ------------------------------------------------------------------ graph */
function graphBody(s) {
  const id = s.id;
  if (!s.plots.length) return h("p", {class: "muted"}, "This graph has no plots. Click Plots to add one.");
  const usable = s.plots.filter(p => p.points.length);
  const first = s.plots[0];
  const xl = s.xaxis.label || (first.x || ""), yl = s.yaxis.label || (first.y || "");
  const H = Math.round(360 * s.height / 100);
  const series = s.plots.map((p, i) => ({name: p.name, color: s.plots.length > 1 ? PALETTE[i % PALETTE.length] : p.color, line: s.connect, symbols: s.symbols, pts: p.points}));
  const logFit = s.fit === "semilog" || s.fit === "loglog";
  const xo = Object.assign({}, s.xaxis, {label: xl}), yo = Object.assign({}, s.yaxis, {label: yl});
  let svg;
  if (!usable.length) svg = h("p", {class: "muted"}, "*** NO DATA AVAILABLE ***");
  else if (s.type === "scatter") {
    const zkey = CUR + ":" + s.id;
    UI.zoom = UI.zoom || {};
    const zs = UI.zoom[zkey] || (UI.zoom[zkey] = {view: null});
    svg = zoomable(view => chart({w: 720, h: H, view, series, curves: s.plots.map((p, i) => ({pts: p.curve || [], color: series[i].color, bands: s.fit_options && s.fit_options.bands})), x: xo, y: yo}), zs);
  }
  else {
    const same = usable.every(p => p.points.length === usable[0].points.length && p.points.every((q, k) => q[0] === usable[0].points[k][0]));
    if (!same) svg = h("p", {class: "muted"}, "This graph cannot be displayed as a bar chart. Plots must have the same number of points and matching x-coordinates.");
    else svg = barChart({w: 720, h: H, series: series.filter(x => x.pts.length), stack: s.type === "stack", x: xo, y: yo});
  }
  const legend = h("div", {class: "fitlegend"}, s.plots.map((p, i) => {
    const f = p.fit;
    return h("div", {class: "fl", ondblclick: () => dlgPlots(id), title: "Double-click to edit plots"},
      legendMark(i, series[i].color), " ", h("b", {}, `${p.name}`), ` (${p.group || "no group"}: ${p.y || "?"} vs ${p.x || "?"})`,
      f ? (f.ok ? fitLegend(f, s) : h("div", {class: "fitline err"}, `${f.label}: ${p.points.length ? "Fit Error" + (f.error ? " \u2014 " + f.error : "") : "No Fit"}`)) : null);
  }));
  return [h("div", {class: "graphwrap"}, svg), legend, logFit && !s.xaxis.log ? h("p", {class: "muted noprint"}, "Tip: set the X axis to Log to see this fit as a straight line.") : null, summariesBlock(s, false)];
}
function fitLegend(f, s) {
  const o = s.fit_options || {};
  const conf = Math.round((f.conf || 0.95) * 1000) / 10;
  const rows = Object.keys(f.params).map(k => {
    const name = (f.names || {})[k];
    const se = f.se && f.se[k] !== null && f.se[k] !== undefined ? f.se[k] : null;
    const ci = f.ci && f.ci[k] ? f.ci[k] : null;
    const fixed = (f.fixed || []).includes(k);
    return h("tr", {}, h("td", {}, h("b", {}, k), name ? h("span", {class: "muted"}, " " + name) : null),
      h("td", {class: "numc"}, fmtParm(f.params[k])),
      h("td", {class: "numc"}, fixed ? "fixed" : se === null ? "\u2014" : "\u00b1 " + fmtParm(se)),
      h("td", {class: "numc"}, ci ? `${fmtParm(ci[0])} \u2026 ${fmtParm(ci[1])}` : "\u2014"));
  });
  const extra = [f.r2 !== null && f.r2 !== undefined ? `R\u00b2 = ${f.r2.toFixed(4)}` : null,
    f.n ? `n = ${f.n}` : null, f.sy !== null && f.sy !== undefined ? `Sy.x = ${fmtParm(f.sy)}` : null,
    f.ec50 !== null && f.ec50 !== undefined ? `${s.fit === "mm" ? "Km" : "EC50"} = ${fmtParm(f.ec50)}` : null,
    o.weighting && o.weighting !== "none" ? "weighted " + ((S.weightings.find(w => w[0] === o.weighting) || [0, o.weighting])[1]) : null].filter(Boolean);
  return h("div", {class: "fitline"}, `${f.label}:  ${f.equation}`,
    h("table", {class: "fittable"}, h("thead", {}, h("tr", {}, h("th", {}, "Parameter"), h("th", {}, "Value"), h("th", {}, "Std. error"), h("th", {}, `${conf}% confidence interval`))), h("tbody", {}, rows)),
    h("div", {}, extra.map(t => h("span", {class: "parm"}, t))),
    o.bands ? h("div", {class: "muted"}, `Shaded: ${conf}% confidence band of the curve. Dashed: ${conf}% prediction band for new measurements.`) : null,
    s.fit === "mm" && !(f.params.A > 0 && f.params.B > 0) ? h("div", {class: "fitline err"}, "Vmax or Km is not positive: these points do not follow Michaelis\u2013Menten kinetics (check the plate blank and the substrate range).") : null,
    f.r2 !== null && f.r2 !== undefined && f.r2 < 0.5 ? h("div", {class: "fitline err"}, "R\u00b2 is low: the curve describes these points poorly.") : null);
}

function fmtParm(v) { const a = Math.abs(v); return a !== 0 && (a >= 1e5 || a < 1e-3) ? v.toExponential(3) : String(+v.toPrecision(4)); }

/* ------------------------------------------------------------------ notes */
function notesBody(s) {
  const id = s.id;
  const ed = h("div", {class: "notes-edit", contenteditable: "true", role: "textbox", "aria-multiline": "true", "aria-label": s.name + " text", html: s.html,
    "data-placeholder": "Type notes about the experiment here…",
    onblur: () => { if (ed.innerHTML !== s.html) op("notes", {id, html: ed.innerHTML}, {quiet: false}); }});
  const cmd = (c, v) => e => { e.preventDefault(); ed.focus(); document.execCommand(c, false, v); };
  const bar = h("div", {class: "fmtbar noprint", onmousedown: e => { if (e.target.tagName === "BUTTON") e.preventDefault(); }},
    h("button", {type: "button", title: "Bold", onclick: cmd("bold")}, h("b", {}, "B")),
    h("button", {type: "button", title: "Italic", onclick: cmd("italic")}, h("i", {}, "I")),
    h("button", {type: "button", title: "Underline", onclick: cmd("underline")}, h("u", {}, "U")),
    select([["", "Size"], ["2", "Small"], ["3", "Normal"], ["5", "Large"], ["6", "Title"]], "", {"aria-label": "Text size", onchange: e => { if (e.target.value) { ed.focus(); document.execCommand("fontSize", false, e.target.value); } e.target.value = ""; }}),
    h("button", {type: "button", title: "Bulleted list", onclick: cmd("insertUnorderedList")}, "• List"));
  return [bar, ed, summariesBlock(s, s.show_formulas)];
}

/* ---------------------------------------------------------------- workflow */
const WF_TYPES = [["temperature", "Set Temperature"], ["shake", "Shake"], ["delay", "Delay"], ["read", "Read"], ["drawer", "Drawer"], ["message", "Message"], ["cycle", "Cycle"]];

function wfNew(type, plates) {
  return {temperature: {type, celsius: 37, wait: true}, shake: {type, seconds: 10}, delay: {type, seconds: 60},
    read: {type, plate: plates.length ? plates[0].id : null}, drawer: {type, open: true},
    message: {type, text: "Add the reagent, put the plate back and press Continue."},
    cycle: {type, interval: 600, run_time: 3600, steps: []}}[type];
}

/* Rough duration in seconds (temperature waits not included). */
function wfSeconds(steps, plates) {
  let t = 0;
  for (const st of steps) {
    if (st.type === "shake" || st.type === "delay") t += st.seconds;
    else if (st.type === "read") {
      const p = plates.find(x => x.id === st.plate);
      if (p) t += p.settings.mode === "kinetic" ? p.settings.run_time + readSeconds(p.settings) : p.settings.mode === "spectrum"
        ? Math.ceil((p.settings.stop - p.settings.start) / Math.max(1, p.settings.step) + 1) * 2 : readSeconds(p.settings);
    } else if (st.type === "cycle") t += st.run_time + wfSeconds(st.steps, plates);
  }
  return t;
}

function workflowBody(s) {
  const id = s.id, plates = S.doc.sections.filter(x => x.kind === "plate");
  const w = S.workflow, mine = w && w.doc === CUR && w.section === id;
  const live = mine && ["running", "paused", "pausing", "message"].includes(w.status);
  const steps = JSON.parse(JSON.stringify(s.steps));
  const save = () => op("workflow_save", {id, steps});
  const move = (list, i, d) => { const j = i + d; if (j < 0 || j >= list.length) return; [list[i], list[j]] = [list[j], list[i]]; save(); };
  const time = (v, set, label) => textInput(fmtTime(v), {class: "narrow", "aria-label": label, disabled: live, onchange: e => { const x = parseTime(e.target.value); if (Number.isFinite(x) && x >= 0) { set(x); save(); } else e.target.value = fmtTime(v); }});

  function editor(st) {
    const dis = live;
    switch (st.type) {
      case "temperature": return [numInput(st.celsius, {class: "narrow", min: 0, max: 45, step: 0.1, disabled: dis, "aria-label": "Temperature", onchange: e => { st.celsius = +e.target.value; save(); }}), " °C (0 = off) ",
        check("wait until reached", st.wait, {disabled: dis, onchange: e => { st.wait = e.target.checked; save(); }})];
      case "shake": return [time(st.seconds, v => { st.seconds = v; }, "Shake time"), " m:ss"];
      case "delay": return [time(st.seconds, v => { st.seconds = v; }, "Delay"), " h:mm:ss"];
      case "read": return [select(plates.map(p => [p.id, p.name]), st.plate, {disabled: dis, "aria-label": "Plate", onchange: e => { st.plate = +e.target.value; save(); }}),
        (() => { const p = plates.find(x => x.id === st.plate); return p ? h("span", {class: "muted"}, " " + p.settings.mode + ", " + (p.settings.mode === "spectrum" ? `${p.settings.start}–${p.settings.stop} nm` : p.settings.wavelengths.join("/") + " nm")) : null; })()];
      case "drawer": return [select([["true", "Open"], ["false", "Close"]], String(st.open), {disabled: dis, "aria-label": "Drawer", onchange: e => { st.open = e.target.value === "true"; save(); }})];
      case "message": return [textInput(st.text, {class: "wide-in", disabled: dis, "aria-label": "Message", onchange: e => { st.text = e.target.value; save(); }})];
      case "cycle": return ["every ", time(st.interval, v => { st.interval = v; }, "Cycle interval"), " for ", time(st.run_time, v => { st.run_time = v; }, "Cycle run time"),
        h("span", {class: "muted"}, ` = ${Math.floor(st.run_time / Math.max(1, st.interval)) + 1} cycles`)];
    }
    return [];
  }

  function list(arr, nested) {
    const box = h("ol", {class: "wf-list" + (nested ? " nested" : "")});
    arr.forEach((st, i) => {
      const current = live && w.sid === st.sid;
      box.append(h("li", {class: "wf-step" + (current ? " current" : "") + (st.type === "cycle" ? " cycle" : "")},
        h("div", {class: "wf-row"},
          h("span", {class: "wf-type"}, (WF_TYPES.find(t => t[0] === st.type) || [0, st.type])[1]),
          h("span", {class: "wf-edit"}, editor(st)),
          h("span", {class: "spacer"}),
          live ? null : h("span", {class: "wf-tools noprint"},
            h("button", {type: "button", class: "tb", title: "Move up", "aria-label": "Move up", disabled: i === 0, onclick: () => move(arr, i, -1)}, "↑"),
            h("button", {type: "button", class: "tb", title: "Move down", "aria-label": "Move down", disabled: i === arr.length - 1, onclick: () => move(arr, i, 1)}, "↓"),
            h("button", {type: "button", class: "tb", title: "Remove", "aria-label": "Remove step", onclick: () => { arr.splice(i, 1); save(); }}, "✕"))),
        st.type === "cycle" ? [list(st.steps, true), live ? null : addBar(st.steps, true)] : null));
    });
    if (!arr.length) box.append(h("li", {class: "muted wf-empty"}, nested ? "Add the steps to repeat in each cycle." : "No steps yet. Add them with the buttons below."));
    return box;
  }

  function addBar(arr, nested) {
    return h("div", {class: "wf-add noprint"}, h("span", {class: "muted"}, nested ? "Add to cycle:" : "Add step:"),
      WF_TYPES.filter(t => !(nested && t[0] === "cycle")).map(([t, label]) => tb("+ " + label, () => {
        if (t === "read" && !plates.length) return alertDlg("Add a plate first.");
        arr.push(wfNew(t, plates)); save();
      })));
  }

  const total = wfSeconds(steps, plates);
  const status = live ? h("div", {class: "wf-status", role: "status"},
      h("b", {}, {running: "Running", paused: "Paused", pausing: "Pausing after this step", message: "Waiting for you"}[w.status]),
      w.step ? " — " + w.step : "", w.cycle ? ` (cycle ${w.cycle})` : "",
      S.reading && S.reading.wait ? h("span", {class: "muted"}, "  " + S.reading.wait) : null,
      h("span", {class: "muted"}, "  • elapsed " + fmtTime((Date.now() / 1000) - w.started)))
    : (mine && w.status === "error" ? h("div", {class: "wf-status err"}, "Last run failed: " + (w.error || "")) : null);
  return [
    status,
    h("p", {class: "muted"}, steps.length ? `About ${fmtTime(total)} (h:mm:ss)` + (JSON.stringify(steps).includes('"wait":true') ? " plus the time to reach the temperature" : "") + ". Plates read inside a cycle collect one time point per cycle, so they reduce like a kinetic read (Vmax and so on)." : ""),
    list(steps, false),
    live ? null : addBar(steps, false),
    s.log && s.log.length ? h("details", {class: "wf-log", open: live}, h("summary", {}, `Log of the last run (${s.log.length} lines)`), h("pre", {class: "termlog"}, s.log.slice(-300).join("\n"))) : null,
  ];
}
