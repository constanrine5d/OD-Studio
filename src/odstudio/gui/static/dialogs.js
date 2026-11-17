"use strict";
/* All dialogs.  Names follow the original program's dialogs (see docs/FEATURES.md). */

/* One global mouse-up ends whichever grid drag is in progress. */
let DRAG_END = null;
window.addEventListener("mouseup", () => { const f = DRAG_END; DRAG_END = null; if (f) f(); });

/* 8 x 12 well grid with rectangle selection.  opts: {sel:Set, cell(well)->node|string,
   cls(well)->string, style(well)->object, onselect(), ondbl(well), dim(well)->bool} */
function wellGrid(opts) {
  const sel = opts.sel;
  const g = h("div", {class: "wellgrid " + (opts.cls0 || ""), role: "grid", "aria-label": "Microplate wells"});
  const cells = {};
  let drag = null;
  const paint = () => { for (const w of WELLS) cells[w].classList.toggle("sel", sel.has(w)); };
  const rect = (a, b) => {
    const r0 = Math.min(ROWS.indexOf(a[0]), ROWS.indexOf(b[0])), r1 = Math.max(ROWS.indexOf(a[0]), ROWS.indexOf(b[0]));
    const c0 = Math.min(+a.slice(1), +b.slice(1)), c1 = Math.max(+a.slice(1), +b.slice(1));
    const out = [];
    for (let r = r0; r <= r1; r++) for (let c = c0; c <= c1; c++) out.push(ROWS[r] + c);
    return out;
  };
  const setSel = (wells, add) => { if (!add) sel.clear(); wells.forEach(w => sel.add(w)); paint(); opts.onselect && opts.onselect(); };
  const pick = (wells, add) => { setSel(wells, add); opts.onend && opts.onend(); };
  g.append(h("div", {class: "wg-corner", title: "Select all", onclick: () => pick(WELLS, false)}));
  for (let c = 1; c <= 12; c++) g.append(h("div", {class: "wg-head", onclick: e => pick(WELLS.filter(w => +w.slice(1) === c), e.shiftKey || e.ctrlKey || e.metaKey)}, c));
  for (let r = 0; r < 8; r++) {
    g.append(h("div", {class: "wg-head", onclick: e => pick(WELLS.filter(w => w[0] === ROWS[r]), e.shiftKey || e.ctrlKey || e.metaKey)}, ROWS[r]));
    for (let c = 1; c <= 12; c++) {
      const w = ROWS[r] + c;
      const el = h("div", {class: "well " + (opts.cls ? opts.cls(w) : ""), role: "gridcell", "aria-label": w, style: opts.style ? opts.style(w) : null,
        onmousedown: e => {
          if (e.button) return;
          e.preventDefault();
          clearTimeout(collapse);
          const add = e.shiftKey || e.ctrlKey || e.metaKey;
          if (!add && sel.size > 1 && sel.has(w)) {
            // Pressing inside a multi-well selection keeps it (so a double-click can open
            // all of them); a plain click collapses it on release, a drag starts a new one.
            drag = {from: w, base: new Set(), keep: true};
            startDrag();
            return;
          }
          drag = {from: w, base: add ? new Set(sel) : new Set()};
          startDrag();
          setSel([...drag.base, w], false);
        },
        onmouseenter: () => {
          if (!drag) return;
          drag.keep = false;
          setSel([...drag.base, ...rect(drag.from, w)], false);
        },
        ondblclick: () => { clearTimeout(collapse); if (opts.ondbl) opts.ondbl(w); }});
      for (const k of [opts.cell ? opts.cell(w) : w].flat(Infinity)) if (k !== null && k !== undefined && k !== false) el.append(k);
      cells[w] = el;
      g.append(el);
    }
  }
  let collapse = null;
  const up = () => {
    if (!drag) return;
    const d = drag;
    drag = null;
    if (d.keep) {                       // click without moving inside a selection
      collapse = setTimeout(() => { setSel([d.from], false); opts.onend && opts.onend(); }, 320);
      return;
    }
    opts.onend && opts.onend();
  };
  const startDrag = () => { DRAG_END = up; };
  paint();
  return g;
}

function plateById(id) { return S.doc.sections.find(s => s.id === id); }
function groupColor(name) { const g = S.doc.groups[name]; return g ? g.color : "#999"; }
function wellStyle(t) {
  if (!t) return null;
  if (t.type === "plateblank") return {background: "var(--blank)"};
  return {background: groupColor(t.group) + "33", boxShadow: "inset 0 0 0 2px " + groupColor(t.group) + "aa"};
}

/* ------------------------------------------------------ Instrument Settings */
/* Same estimate SOFTmax PRO uses (SPF.EXE 68:30ce); mirrors document.read_seconds. */
function readSeconds(st) {
  let wls = st.mode === "spectrum" ? [st.start] : st.wavelengths.map(w => parseInt(String(w).replace(/^F/i, ""), 10) || 0);
  if (!wls.length) wls = [405];
  const w = wls.length, strips = Math.max(1, st.last_col - st.first_col + 1);
  let move = 0;
  wls.forEach((x, i) => { move += Math.abs(x - wls[(i + 1) % w]); });
  let ms = move * 5 + 2 * (w - 1) * 225 + w * (strips * 400 + 500 + 1.25 * (strips - 1) * 225);
  if (st.autocalibrate) ms += move * 5 + w * 900 + 4 * 225 + (st.first_col - 1) * 225;
  let sec = Math.ceil(ms / 1000 - 1e-9);
  if (st.mode === "kinetic" && st.mix_between) sec += +st.mix_between_s || 0;
  return Math.max(sec, 2);
}

/* Whole-plate picture for choosing which columns to read.
   Drag: a range.  Shift-click: extend from the last click.  Cmd/Ctrl-click or drag: add or remove columns. */
function colsOf(st) {
  if (st.columns && st.columns.length) return [...new Set(st.columns.map(Number))].sort((a, b) => a - b);
  const out = [];
  for (let c = st.first_col; c <= st.last_col; c++) out.push(c);
  return out;
}
function colsText(cols) {
  const parts = [];
  for (let i = 0; i < cols.length;) {
    let j = i;
    while (j + 1 < cols.length && cols[j + 1] === cols[j] + 1) j++;
    parts.push(i === j ? String(cols[i]) : `${cols[i]}–${cols[j]}`);
    i = j + 1;
  }
  return parts.join(", ");
}

function stripPicker(st, onchange) {
  const box = h("div", {class: "strippick", role: "group", "aria-label": "Columns to read"});
  let sel = new Set(colsOf(st)), base = null, anchor = null, adding = true, last = colsOf(st)[0];
  const span = (a, b) => { const out = []; for (let c = Math.min(a, b); c <= Math.max(a, b); c++) out.push(c); return out; };
  const commit = () => {
    if (!sel.size) sel = new Set([last || 1]);
    const cols = [...sel].sort((a, b) => a - b);
    st.columns = cols;
    st.first_col = cols[0];
    st.last_col = cols[cols.length - 1];
    paint();
    onchange();
  };
  const down = (c, e) => {
    e.preventDefault();
    if (e.shiftKey && last) {                       // extend from the previous click
      base = new Set(sel); anchor = last; adding = true;
    } else if (e.metaKey || e.ctrlKey) {            // toggle, keep the rest
      base = new Set(sel); anchor = c; adding = !sel.has(c);
    } else {                                         // start again
      base = new Set(); anchor = c; adding = true;
    }
    apply(c);
    DRAG_END = () => { anchor = null; last = c; };
  };
  const apply = c => {
    sel = new Set(base);
    for (const x of span(anchor, c)) adding ? sel.add(x) : sel.delete(x);
    last = c;
    commit();
  };
  const cells = [];
  box.append(h("div", {class: "sp-corner", title: "Whole plate", onclick: () => { sel = new Set(span(1, 12)); commit(); }}, "all"));
  for (let c = 1; c <= 12; c++) {
    const head = h("div", {class: "sp-head", onmousedown: e => down(c, e), onmouseenter: () => { if (anchor) apply(c); }}, c);
    cells.push([c, head]);
    box.append(head);
  }
  for (let r = 0; r < 8; r++) {
    box.append(h("div", {class: "sp-row"}, ROWS[r]));
    for (let c = 1; c <= 12; c++) {
      const w = h("div", {class: "sp-well", onmousedown: e => down(c, e), onmouseenter: () => { if (anchor) apply(c); }});
      cells.push([c, w]);
      box.append(w);
    }
  }
  function paint() {
    for (const [c, el] of cells) {
      el.classList.toggle("on", sel.has(c));
      el.classList.toggle("swept", !sel.has(c) && c > st.first_col && c < st.last_col);
    }
  }
  paint();
  return box;
}

async function dlgInstrument(id) {
  const p = plateById(id), st = Object.assign({delay: 0, wait_temp: false}, p.settings);
  const presets = S.prefs.wavelength_presets, filters = (S.prefs.filters || []).filter(Boolean);
  const body = h("div", {class: "form"});
  const opt = S.instrument.options || {wl_min: 250, wl_max: 750};
  const caps = readerCaps();                       // null: no reader connected, allow everything
  const maxWl = caps ? caps.max_wavelengths : 6;
  let wls = st.wavelengths.slice(0, Math.max(1, maxWl));
  if (caps && caps.filters && caps.filters.length) wls = wls.map(w => caps.filters.includes(+String(w).replace(/^F/i, "")) ? +String(w).replace(/^F/i, "") : caps.filters[0]);
  const timing = h("p", {class: "hint timing", "aria-live": "polite"});
  const updTiming = () => {
    st.wavelengths = wls;
    const tm = caps && caps.family === "tmax";
    const one = tm ? (wls.length === 2 ? 10 : 5) : readSeconds(st), cols = colsOf(st), n = cols.length, spanN = st.last_col - st.first_col + 1;
    const minInt = tm ? (wls.length === 2 ? 14 : 6) + (st.mix_between && caps.shake ? (wls.length === 2 ? 5 : 3) : 0) : one;
    let strips = n === 12 ? "Read entire plate" : `Read column${n > 1 ? "s" : ""} ${colsText(cols)} (${n * 8} wells)`;
    if (tm && n < 12) strips += `. The ${caps.model} reads the whole plate; the other columns are left out of the data`;
    else if (n < spanN) strips += `. The reader sweeps columns ${st.first_col}\u2013${st.last_col} in one pass; the ${spanN - n} in between are left out of the data`;
    let t = `${strips}.  One read takes about ${one} s.`;
    if (st.mode === "kinetic") t += `  Minimum interval: ${minInt} s` + (st.interval < minInt ? "  — the interval is too short." : ".");
    timing.textContent = t;
    timing.classList.toggle("bad", st.mode === "kinetic" && st.interval < minInt);
  };
  const draw = () => {
    const mode = st.mode;
    const dl = h("datalist", {id: "wlpresets"}, presets.map(w => h("option", {value: w})), filters.map(f => h("option", {value: "F" + f}, "filter " + f + " nm")));
    const wlBox = h("div", {class: "row wrap"});
    const fixed = caps && caps.filters && caps.filters.length ? caps.filters : null;
    wls.forEach((w, i) => wlBox.append(h("label", {class: "mini"}, "λ" + (i + 1) + " ",
      fixed
        ? select(fixed, +String(w).replace(/^F/i, ""), {"aria-label": "Filter " + (i + 1), onchange: e => { wls[i] = +e.target.value; updTiming(); }})
        : textInput(w, {list: "wlpresets", class: "narrow", inputmode: "numeric", "aria-label": "Wavelength " + (i + 1),
            onchange: e => { const v = e.target.value.trim().toUpperCase(); wls[i] = /^F/.test(v) ? v : +v; updTiming(); }}), " nm")));
    const noMode = m => caps && !caps.modes.includes(m) ? `The ${caps.model} cannot do ${m} reads` : null;
    const reads = st.interval > 0 ? Math.floor(st.run_time / st.interval) + 1 : 2;
    fill(body, dl,
      caps ? h("p", {class: "hint"}, `Connected reader: ${caps.model}. Settings it cannot do are greyed out.`) : null,
      caps && caps.filters && !caps.filters.length ? h("p", {class: "hint bad"}, `The ${caps.model} reads through its filters only. Enter them in Edit › Preferences › Filters first.`) : null,
      h("fieldset", {}, h("legend", {}, "Mode"), radios("mode", [["endpoint", "Endpoint", noMode("endpoint")], ["kinetic", "Kinetic", noMode("kinetic")], ["spectrum", "Spectrum", noMode("spectrum")]], mode, v => { st.mode = v; draw(); }),
        noMode(mode) ? h("p", {class: "hint bad"}, noMode(mode) + ". Choose another mode.") : null),
      mode === "spectrum"
        ? h("fieldset", {}, h("legend", {}, "Wavelengths"), h("div", {class: "row wrap"},
            field("Start:", numInput(st.start, {class: "narrow", step: 1, onchange: e => { st.start = +e.target.value; updTiming(); }}), "nm"),
            field("Stop:", numInput(st.stop, {class: "narrow", step: 1, onchange: e => { st.stop = +e.target.value; }}), "nm"),
            field("Step:", numInput(st.step, {class: "narrow", step: 1, min: 1, onchange: e => { st.step = +e.target.value; }}), "nm")))
        : h("fieldset", {}, h("legend", {}, "Wavelengths"),
            field("Number:", select([1, 2, 3, 4, 5, 6].slice(0, maxWl), wls.length, {onchange: e => { const n = +e.target.value; while (wls.length < n) wls.push(fixed ? fixed[Math.min(wls.length, fixed.length - 1)] : presets[wls.length] || 405); wls = wls.slice(0, n); draw(); }})),
            wlBox, h("p", {class: "hint"}, fixed ? `Filters in the ${caps.model}: ${fixed.join(", ")} nm` + (maxWl === 2 ? ". With two, the second is the reference wavelength." : ".")
              : `Range ${opt.wl_min}–${opt.wl_max} nm` + (filters.length ? `. Installed filters: ${filters.map(f => "F" + f).join(", ")}` : ""))),
      mode === "kinetic" ? h("fieldset", {}, h("legend", {}, "Timing"), h("div", {class: "row wrap"},
          field("Run Time:", textInput(fmtTime(st.run_time), {class: "narrow", onchange: e => { const v = parseTime(e.target.value); if (Number.isFinite(v)) st.run_time = v; draw(); }}), "h:mm:ss"),
          field("Interval:", textInput(fmtTime(st.interval), {class: "narrow", onchange: e => { const v = parseTime(e.target.value); if (Number.isFinite(v)) st.interval = v; draw(); }}), "m:ss")),
          h("p", {class: "hint"}, reads + " Readings")) : null,
      h("fieldset", {}, h("legend", {}, "Strips"), stripPicker(st, updTiming),
        h("p", {class: "hint"}, "Drag across the plate (or the column numbers) to choose columns. Shift-click extends the choice; " + (MOD === "\u2318" ? "\u2318" : "Ctrl") + "-click or drag adds or removes columns, so they need not be next to each other." + (caps && caps.family === "tmax" ? "" : " Fewer columns allow a shorter kinetic interval.")), timing),
      caps && !caps.shake ? null : h("fieldset", {}, h("legend", {}, "Auto Mix"), h("div", {class: "row wrap"},
          check("Before First Read", st.mix_before, {onchange: e => { st.mix_before = e.target.checked; }}),
          numInput(st.mix_before_s, {class: "narrow", min: 1, step: 1, "aria-label": "Seconds before first read", onchange: e => { st.mix_before_s = +e.target.value; }}), "secs"),
        mode === "kinetic" ? h("div", {class: "row wrap"},
          check("Between Reads", st.mix_between, {onchange: e => { st.mix_between = e.target.checked; updTiming(); }}),
          numInput(st.mix_between_s, {class: "narrow", min: 1, step: 1, "aria-label": "Seconds between reads", onchange: e => { st.mix_between_s = +e.target.value; updTiming(); }}), "secs") : null),
      h("fieldset", {}, h("legend", {}, "Options"),
        caps && !caps.calibrate ? null : check("Auto Calibrate", st.autocalibrate, {onchange: e => { st.autocalibrate = e.target.checked; updTiming(); }}),
        mode !== "spectrum" ? check("Pre-Read Plate (blank each well with its own first reading)", st.pre_read, {onchange: e => { st.pre_read = e.target.checked; }}) : null),
      h("fieldset", {}, h("legend", {}, "Start"),
        caps && !caps.temperature ? null : check("Wait until the incubator reaches its set point", st.wait_temp, {onchange: e => { st.wait_temp = e.target.checked; }}),
        h("div", {class: "row wrap"}, "Start reading", numInput(st.delay, {class: "narrow", min: 0, step: 1, "aria-label": "Delay before reading", onchange: e => { st.delay = +e.target.value || 0; }}), "seconds after pressing Read")));
    updTiming();
  };
  draw();
  return modal("Instrument Settings — " + p.name, body, [{label: "Cancel", value: null},
    {label: "OK", primary: true, action: async () => {
      st.wavelengths = wls;
      return dlgOp("instrument_settings", {id, settings: st}, "Delete");
    }}], {cls: "wide"});
}

/* Capabilities of the connected reader (see odstudio/readers.py), or null when none is connected. */
const canDo = what => { const c = readerCaps(); return !c || !!c[what]; };
function readerCaps() {
  const i = S.instrument;
  return i && i.connected && i.options && i.options.modes ? i.options : null;
}

/* Find Reader: ask the ports the user ticks, gently, whether a plate reader answers.
   Resolves to {port, reader} for the chosen one, or null. */
const READER_KIND = {ThermoMax: "thermomax", UVmax: "uvmax", Vmax: "vmax", Emax: "emax"};
async function dlgFindReader() {
  const ports = ((await op("ports", {}, {quiet: true})) || {ports: []}).ports;
  const boxes = ports.map(p => {
    const c = check(p.device + (p.description ? " — " + p.description : "") + (p.usb ? "  (USB adapter)" : ""), p.suggest);
    c.port = p.device;
    return c;
  });
  const sims = check("Simulators too (for trying OD Studio without a reader)", false);
  const results = h("div", {"aria-live": "polite"});
  let chosen = null;
  const body = h("div", {class: "form"},
    h("p", {}, "OD Studio asks each ticked port whether a SpectraMax, ThermoMax, UVmax, Vmax or Emax is there. It sends only a carriage return (and, if nothing answered, the SpectraMax's own \u201c!\u201d check) and stops at the first sign that a device is not a reader."),
    h("p", {class: "hint"}, "Other instruments on this computer: untick their ports, or close their software first. A port another program is using is skipped. USB adapters and built-in serial ports are ticked to begin with; Bluetooth devices are not."),
    ports.length ? h("fieldset", {}, h("legend", {}, "Serial ports"), boxes) : h("p", {class: "hint bad"}, "No serial ports found. Plug in the reader's USB-to-serial adapter and try again."),
    sims, results);
  return modal("Find Reader", body, [
    {label: "Cancel", value: null},
    {label: "Search", action: async () => {
      const list = boxes.filter(b => b.box.checked).map(b => b.port)
        .concat(sims.box.checked ? ["sim", "sim:thermomax", "sim:vmax", "sim:uvmax", "sim:emax"] : []);
      if (!list.length) { toast("Tick at least one port."); return false; }
      fill(results, h("p", {class: "hint"}, "Searching\u2026"));
      const r = await op("find_readers", {ports: list}, {quiet: true});
      const rows = (r && r.results) || [];
      const found = rows.filter(x => x.reader);
      chosen = found.length === 1 ? found[0] : null;
      fill(results, h("fieldset", {}, h("legend", {}, "Results"),
        rows.map(x => x.reader
          ? h("label", {class: "check"}, h("input", {type: "radio", name: "found", checked: found.length === 1, onchange: () => { chosen = x; }}),
              ` ${x.port}: ${x.reader}` + (x.note ? ` (${x.note})` : ""))
          : h("div", {class: "muted"}, `${x.port}: ${x.note}`)),
        found.length ? null : h("p", {class: "hint bad"}, "No reader answered. Check that it is switched on and its cable is plugged in.")));
      return false;
    }},
    {label: "Use Selected", primary: true, action: () => {
      if (!chosen) { toast(results.textContent ? "Choose one of the readers found." : "Search first."); return false; }
      return {port: chosen.port, reader: READER_KIND[chosen.reader] || "auto"};
    }}], {cls: "wide", noFocus: true});
}

async function dlgIncubator() {
  const on = S.instrument.setpoint > 0;
  const temp = numInput(on ? S.instrument.setpoint : 37, {class: "narrow", step: 0.1});
  const r = radios("inc", [["off", "Off"], ["on", "On"]], on ? "on" : "off");
  const o = S.instrument.options || {temp_min: 15, temp_max: 45};
  return modal("Incubator", h("div", {class: "form"}, r, field("Temperature:", temp, `°C  (${o.temp_min}–${o.temp_max})`)),
    [{label: "Cancel", value: null}, {label: "OK", primary: true, action: () => dlgOp("incubator", {on: r.value() === "on", temperature: +temp.value})}]);
}

async function dlgPrefs() {
  const P = Object.assign({}, S.prefs);
  const ports = (await op("ports", {}, {quiet: true}) || {ports: []}).ports;
  const portOpts = [["", "(not connected)"], ["sim", "Simulator (no instrument needed)"], ["sim:realtime", "Simulator, realistic timing"],
    ["sim:thermomax", "Simulator: ThermoMax"], ["sim:vmax", "Simulator: Vmax"], ["sim:uvmax", "Simulator: UVmax"], ["sim:emax", "Simulator: Emax"]]
    .concat(ports.map(p => [p.device, p.device + (p.description ? " — " + p.description : "")]));
  if (P.port && !portOpts.some(o => o[0] === P.port)) portOpts.push([P.port, P.port]);
  const port = select(portOpts, P.port);
  const custom = textInput("", {placeholder: "or type a port, e.g. COM3 or socket://host:port"});
  const folder = textInput(P.autosave_folder), prefix = textInput(P.autosave_prefix, {class: "narrow"});
  const xfolder = textInput(P.auto_export_folder), xname = textInput(P.auto_export_name);
  const xfmt = select([["txt", "Text"], ["csv", "CSV"], ["xlsx", "Excel"]], P.auto_export_format);
  const xcontent = select([["raw", "Raw readings"], ["reduced", "Reduced values"], ["both", "Both"]], P.auto_export_content);
  const xpreview = h("p", {class: "hint"});
  const upd = () => {
    const now = new Date(), pad = n => String(n).padStart(2, "0");
    const v = {experiment: (S.title || "Untitled").replace(/\.[^.]*$/, ""), plate: "Plate#1", mode: "endpoint", n: "001",
               date: `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`, time: `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`};
    xpreview.textContent = "Example: " + (xname.value || "{experiment}_{plate}_{date}_{time}").replace(/\{(\w+)\}/g, (m, k) => v[k.toLowerCase()] ?? m).replace(/[\\/:*?"<>|]/g, "_") + "." + xfmt.value;
  };
  xname.addEventListener("input", upd); xfmt.addEventListener("change", upd); upd();
  const proto = textInput(P.default_protocol_path);
  const assays = textInput(P.assays_folder);
  const chk = k => { const c = check("", P[k]); c.box.addEventListener("change", () => { P[k] = c.box.checked; }); return c.box; };
  const lay = radios("lay", [["time", "Time (one line per reading)"], ["plate", "Plate (8 × 12 blocks)"]], P.export_layout);
  const presets = P.wavelength_presets.map(w => numInput(w, {class: "narrow", step: 1}));
  const filt = [0, 1, 2, 3, 4, 5].map(i => numInput((P.filters || [])[i] || "", {class: "narrow", step: 1, placeholder: "none", "aria-label": "Filter position " + (i + 1)}));
  const reader = select([["auto", "Automatic"], ["spectramax", "SpectraMax 250"], ["thermomax", "ThermoMax"], ["uvmax", "UVmax"], ["vmax", "Vmax"], ["emax", "Emax"]], P.reader || "auto", {"aria-label": "Reader type"});
  const body = h("div", {class: "form"},
    h("fieldset", {}, h("legend", {}, "Instrument"), field("Reader:", reader, "Automatic finds out which reader is connected"),
      h("div", {class: "row"}, field("Serial Port:", port), h("button", {type: "button", onclick: async () => {
        const f = await dlgFindReader();
        if (!f) return;
        if (![...port.options].some(o => o.value === f.port)) port.append(h("option", {value: f.port}, f.port));
        port.value = f.port; custom.value = ""; reader.value = f.reader;
        toast(`Found on ${f.port}. Press OK to use it.`);
      }}, "Find Reader\u2026")), custom,
      h("label", {class: "check"}, chk("autoconnect"), " Connect when the program starts"),
      h("label", {class: "check"}, chk("open_drawer_after_read"), " Open the drawer when a reading finishes")),
    h("fieldset", {}, h("legend", {}, "After a reading"),
      h("label", {class: "check"}, chk("autosave"), " Autosave"),
      h("div", {class: "row"}, field("Folder:", folder), h("button", {type: "button", onclick: async () => { const d = await fileDialog({mode: "folder", title: "Autosave folder", dir: folder.value}); if (d) folder.value = d; }}, "Set…")),
      field("File Prefix:", prefix),
      h("label", {class: "check"}, chk("autoprint"), " Autoprint")),
    h("fieldset", {}, h("legend", {}, "Auto Export after every reading"),
      h("label", {class: "check"}, chk("auto_export"), " Export each plate when its reading (or workflow) finishes"),
      h("div", {class: "row"}, field("Folder:", xfolder), h("button", {type: "button", onclick: async () => { const d = await fileDialog({mode: "folder", title: "Export folder", dir: xfolder.value}); if (d) xfolder.value = d; }}, "Set\u2026")),
      field("File name:", xname), xpreview,
      h("p", {class: "hint"}, "Use {experiment}, {plate}, {date}, {time}, {mode} and {n} (a running number). An existing file is not overwritten: _2, _3 \u2026 is added, unless you allow overwriting."),
      h("div", {class: "row wrap"}, field("Type:", xfmt), field("Contents:", xcontent)),
      h("label", {class: "check"}, chk("auto_export_overwrite"), " Overwrite a file with the same name")),
    h("fieldset", {}, h("legend", {}, "New experiments"),
      h("label", {class: "check"}, chk("default_protocol"), " Start from a Default Protocol"),
      h("div", {class: "row"}, field("File:", proto), h("button", {type: "button", onclick: async () => { const f = await fileDialog({title: "Default protocol", exts: [".odxp", ".odx", ".smxp", ".smx"]}); if (f) proto.value = f; }}, "Set…")),
      h("div", {class: "row"}, field("Assays folder:", assays), h("button", {type: "button", onclick: async () => { const d = await fileDialog({mode: "folder", title: "Assays folder", dir: assays.value}); if (d) assays.value = d; }}, "Set…"))),
    h("fieldset", {}, h("legend", {}, "Printing"),
      h("label", {class: "check"}, chk("print_template"), " Print Template"),
      h("label", {class: "check"}, chk("print_time"), " Print Time"),
      h("label", {class: "check"}, chk("print_filename"), " Print Filename"),
      h("label", {class: "check"}, chk("print_page_number"), " Print Page Number")),
    h("fieldset", {}, h("legend", {}, "Export format"), lay, h("label", {class: "check"}, chk("export_labels"), " Include Labels")),
    h("fieldset", {}, h("legend", {}, "Wavelength shortcuts"), h("div", {class: "row wrap"}, presets), h("p", {class: "hint"}, "Offered in the Instrument Settings wavelength boxes.")),
    h("fieldset", {}, h("legend", {}, "Filters"), h("div", {class: "row wrap"}, filt.map((f, i) => h("label", {class: "mini"}, (i + 1) + ": ", f))),
      h("p", {class: "hint"}, "Filters in the reader's filter wheel, in nm, by position 1–6. ThermoMax, UVmax, Vmax and Emax readers need these: they read only through their filters and cannot report which are fitted. For a SpectraMax they are optional and appear as F405 and so on in the wavelength boxes. Leave empty positions blank.")));
  return modal("Preferences", body, [{label: "Cancel", value: null}, {label: "OK", primary: true, action: () => {
    Object.assign(P, {auto_export_folder: xfolder.value, auto_export_name: xname.value.trim() || "{experiment}_{plate}_{date}_{time}",
      auto_export_format: xfmt.value, auto_export_content: xcontent.value});
    Object.assign(P, {port: custom.value.trim() || port.value, autosave_folder: folder.value, autosave_prefix: prefix.value,
      default_protocol_path: proto.value, assays_folder: assays.value, export_layout: lay.value(),
      wavelength_presets: presets.map(i => +i.value).filter(Boolean), filters: filt.map(i => +i.value || 0), reader: reader.value});
    return dlgOp("prefs", P);
  }}], {cls: "wide"});
}

/* ---------------------------------------------------------------- Template */
async function dlgGroupSettings(name) {
  const g = name ? S.doc.groups[name] : null;
  const sec = name ? S.doc.sections.find(s => s.kind === "group" && s.group === name) : null;
  const nm = textInput(g ? g.name : "", {autofocus: true});
  const kind = select(S.group_kinds, "Unknowns");
  const desc = select([["", "None"], ["Concentration", "Concentration"], ["Factor", "Factor (dilution)"]], g ? g.descriptor : "");
  const units = textInput(g ? g.units : "", {list: "unitlist", class: "narrow"});
  const rep = check("Include in Report", sec ? sec.report : true);
  if (!g) kind.addEventListener("change", () => { desc.value = kind.value === "Standards" ? "Concentration" : ""; units.value = kind.value === "Standards" ? "mg/ml" : ""; });
  const body = h("div", {class: "form"}, h("datalist", {id: "unitlist"}, ["mg/ml", "ug/ml", "ng/ml", "mg", "ng", "ml"].map(u => h("option", {value: u}))),
    field("Name:", nm), g ? null : field("Set to:", kind, "columns the group table starts with"),
    h("fieldset", {}, h("legend", {}, "Sample Descriptor"), field("Type:", desc), field("Units:", units)), rep);
  return modal(g ? "Group Settings" : "New Group", body, [{label: "Cancel", value: null}, {label: "OK", primary: true, action: async () => {
    const n = nm.value.trim();
    if (!n) throw new Error("This is a required field and cannot be left empty.");
    const r = g ? await dlgOp("group_settings", {group: name, name: n, descriptor: desc.value, units: units.value, report: rep.box.checked})
                : await dlgOp("group_new", {name: n, kind: kind.value, descriptor: desc.value, units: units.value, report: rep.box.checked});
    return r ? n : false;
  }}]);
}

async function dlgSeries(id, wells, group) {
  const g = S.doc.groups[group];
  const first = textInput("", {placeholder: "automatic"});
  const start = numInput(g.descriptor ? 1 : "", {class: "narrow"});
  const opSel = select([["*", "× multiply by"], ["/", "÷ divide by"], ["+", "+ add"], ["-", "− subtract"]], "*");
  const step = numInput(2, {class: "narrow"});
  const from = radios("from", [["top", "Top"], ["bottom", "Bottom"], ["left", "Left"], ["right", "Right"]], "left");
  const body = h("div", {class: "form"},
    field("First Sample:", first),
    h("fieldset", {}, h("legend", {}, "Start From"), from, h("p", {class: "hint"}, "Top/Bottom: each row of the selection is one sample. Left/Right: each column is one sample.")),
    g.descriptor ? h("fieldset", {}, h("legend", {}, g.descriptor + (g.units ? ` (${g.units})` : "")),
      field("Starting value:", start), h("div", {class: "row"}, field("Step by:", opSel), step)) : null);
  return modal("Series — " + group, body, [{label: "Cancel", value: null}, {label: "OK", primary: true, action: () =>
    dlgOp("template_series", {id, wells, group, first_sample: first.value, start_from: from.value(), start_value: g.descriptor ? start.value : null, op: opSel.value, step: +step.value})}]);
}

async function dlgTemplate(id) {
  const sel = new Set(UI.sel[id] || []);
  const body = h("div", {class: "template"});
  let group = Object.keys(S.doc.groups)[0] || "";
  const draw = () => {
    const p = plateById(id);
    const g = S.doc.groups[group];
    const names = Object.keys(S.doc.groups);
    if (!names.includes(group)) group = names[0] || "";
    const groups = select(names.length ? names : [["", "(no groups yet)"]], group, {"aria-label": "Group", disabled: !names.length,
      onchange: e => { group = e.target.value; draw(); }});
    const one = sel.size ? p.template[[...sel][0]] : null;
    const sample = textInput(one && one.group === group && one.type === "sample" ? one.sample : "", {placeholder: "automatic", class: "narrow", "aria-label": "Sample name"});
    const value = numInput(one && one.group === group ? one.value : "", {class: "narrow", "aria-label": "Descriptor value"});
    const need = fn => async () => {
      if (!sel.size) { body.dlg.setError("Select one or more wells first."); return; }
      body.dlg.setError("");
      try { await fn([...sel]); } catch (e) { body.dlg.setError(e.message); }
      draw();
    };
    const needGroup = fn => need(async wells => {
      if (!group) { const n = await dlgGroupSettings(null); if (!n) return; group = n; }
      await fn(wells);
    });
    const grid = wellGrid({sel, cls0: "big", onselect: () => { UI.sel[id] = [...sel]; }, onend: draw,
      style: w => wellStyle(p.template[w]),
      cls: w => (p.columns || colsOf(p.settings)).includes(+w.slice(1)) ? "" : "unread",
      cell: w => { const t = p.template[w]; return t ? h("div", {class: "tw"}, h("b", {}, t.sample), t.value !== null && t.value !== undefined ? h("span", {}, String(+(+t.value).toPrecision(4))) : null) : null; }});
    fill(body, 
      h("div", {class: "row wrap tools"},
        h("label", {class: "mini"}, "Group: ", groups),
        h("button", {type: "button", onclick: async () => { const n = await dlgGroupSettings(null); if (n) group = n; draw(); }}, "New Group\u2026"),
        h("button", {type: "button", disabled: !group, onclick: async () => { const n = await dlgGroupSettings(group); if (n) group = n; draw(); }}, "Edit…"),
        h("label", {class: "mini"}, "Sample: ", sample),
        g && g.descriptor ? h("label", {class: "mini"}, g.descriptor + ": ", value, " " + (g.units || "")) : null),
      h("div", {class: "row wrap tools"},
        h("button", {type: "button", class: "primary", onclick: needGroup(wells => dlgOp("template_assign", {id, wells, group, sample: sample.value, value: g && g.descriptor ? value.value : null}))}, "Assign"),
        h("button", {type: "button", onclick: needGroup(wells => dlgSeries(id, wells, group))}, "Series…"),
        h("button", {type: "button", title: "Average of these wells is subtracted from the whole plate", onclick: need(wells => dlgOp("template_assign", {id, wells, type: "plateblank"}))}, "Plate Blank"),
        h("button", {type: "button", title: "Average of these wells is subtracted within the group", onclick: needGroup(wells => dlgOp("template_assign", {id, wells, type: "groupblank", group}))}, "Group Blank"),
        h("button", {type: "button", onclick: need(wells => dlgOp("template_assign", {id, wells, type: "clear"}))}, "Clear"),
        h("span", {class: "spacer"}),
        h("button", {type: "button", onclick: () => printOnly(body.querySelector(".wellgrid"), p.name + ": Template")}, "Print")),
      grid,
      h("p", {class: "hint"}, sel.size ? `${sel.size} well${sel.size > 1 ? "s" : ""} selected` : "Drag across wells to select them, then choose a group and Assign."));
  };
  draw();
  await modal("Template — " + plateById(id).name, body, [{label: "Done", primary: true, value: true}], {cls: "wide", noFocus: true});
}

/* --------------------------------------------------------------- Reduction */
const WL_COMBOS = {1: ["!L1"], 2: ["!L1", "!L2", "!L1-!L2", "!L1+!L2", "!L1/!L2", "!L1*!L2", "Log10(!L1/!L2)"],
  3: ["!L1", "!L2", "!L3", "!L1-!L2", "!L1+!L2", "!L1/!L2", "!L1*!L2", "!L1+!L2+!L3", "(!L1-!L3)/(!L2-!L3)"],
  4: ["!L1", "!L2", "!L3", "!L4"], 5: ["!L1", "!L2", "!L3", "!L4", "!L5"], 6: ["!L1", "!L2", "!L3", "!L4", "!L5", "!L6"]};

async function dlgReduction(id) {
  const p = plateById(id), red = Object.assign({raw_mode: "od", zero_first: false, smoothing: 0}, p.reduction), mode = dataMode(p);
  const body = h("div", {class: "form"});
  const formulaBox = (key, hint) => {
    const box = textInput(red[key], {class: "formula", "aria-label": "Formula", oninput: async e => {
      red[key] = e.target.value;
      const r = await op("check_formula", {formula: red[key], names: false}, {quiet: true});
      note.textContent = r && r.error ? r.error : "";
    }});
    const note = h("span", {class: "dlg-error"});
    return h("div", {}, box, note, h("p", {class: "hint"}, hint));
  };
  const lim = (label, key, unit) => field(label, numInput(red[key], {class: "narrow", placeholder: "none", onchange: e => { red[key] = numOrNull(e.target); }}), unit);
  const draw = () => {
    const combos = WL_COMBOS[p.settings.wavelengths.length] || WL_COMBOS[1];
    const custom = !combos.includes(red.wavelength);
    fill(body, 
      mode !== "spectrum" ? h("fieldset", {}, h("legend", {}, "Wavelength Combination"),
        select(combos.map(c => [c, c.replace(/!/g, "")]).concat([["\u0000", "Custom"]]), custom ? "\u0000" : red.wavelength, {"aria-label": "Wavelength combination",
          onchange: e => { red.wavelength = e.target.value === "\u0000" ? red.wavelength + " " : e.target.value; draw(); }}),
        custom ? formulaBox("wavelength", "Use !L1 … !L6 for the optical density at each wavelength, e.g. !L1-!L2") : null) : null,
      mode === "kinetic" ? h("fieldset", {}, h("legend", {}, "Kinetic Reduction"),
        select(S.kinetic_kinds.map(([k, label, unit]) => [k, label + (unit ? ` (${unit})` : "")]), red.kinetic, {"aria-label": "Kinetic reduction", onchange: e => { red.kinetic = e.target.value; draw(); }}),
        red.kinetic === "custom" ? formulaBox("kinetic_formula", "!KinPlot is the list of readings and !ReadTimes their times. Also available: !Vmax, !VmaxCorr, !TimeToVmax, !OnsetTime, !TimeAtMax, !TimeAtMin, !TimeAtHalfMax, !AUC, !Slope. Example: !Vmax*!VmaxCorr") : null,
        h("p", {class: "hint"}, {time_at_half_max: "The time at which the readings first cross halfway between their lowest and highest value.",
          auc: "Area under the readings, by the trapezoid rule, in OD \u00d7 seconds.", slope: "Regression slope over all readings inside the limits.",
          time_at_max: "Time of the highest reading.", time_at_min: "Time of the lowest reading."}[red.kinetic] || ""),
        h("div", {class: "row wrap"},
          red.kinetic === "vmax" || red.kinetic === "time_to_vmax" ? field("Vmax Points:", numInput(red.vmax_points, {class: "narrow", min: 2, step: 1, placeholder: "all", onchange: e => { red.vmax_points = numOrNull(e.target); }})) : null,
          red.kinetic === "onset" ? lim("Onset OD:", "onset_od", "OD") : null)) : null,
      mode === "spectrum" ? h("fieldset", {}, h("legend", {}, "Spectrum Reduction"),
        select(S.spectrum_kinds.map(([k, label, unit]) => [k, label + (unit ? ` (${unit})` : "")]), red.spectrum, {"aria-label": "Spectrum reduction", onchange: e => { red.spectrum = e.target.value; draw(); }}),
        red.spectrum === "custom" ? formulaBox("spectrum_formula", "!KinPlot is the list of readings and !SweepWavelengths their wavelengths, e.g. Max(!KinPlot)") : null) : null,
      mode !== "endpoint" ? h("fieldset", {}, h("legend", {}, "Limits"), h("div", {class: "row wrap"},
        mode === "kinetic" ? [lim("Lag Time:", "lag_time", "s"), lim("End Time:", "end_time", "s")] : [lim("Start:", "start_nm", "nm"), lim("End:", "end_nm", "nm")],
        lim("Min OD:", "min_od"), lim("Max OD:", "max_od")), h("p", {class: "hint"}, "Readings outside the limits are ignored. Leave a box empty for no limit.")) : null,
      h("fieldset", {}, h("legend", {}, "Raw data"),
        radios("rawmode", [["od", "Optical density"], ["transmittance", "% Transmittance"]], red.raw_mode, v => { red.raw_mode = v; }),
        mode !== "endpoint" ? h("div", {class: "row wrap"},
          field("Smoothing:", select([[0, "None"], [3, "3-point moving average"], [9, "9-point moving average"]], red.smoothing || 0, {onchange: e => { red.smoothing = +e.target.value; }})),
          mode === "kinetic" ? check("Set the first reading to zero", red.zero_first, {onchange: e => { red.zero_first = e.target.checked; }}) : null) : null),
      h("fieldset", {}, h("legend", {}, "Blanks"),
        check("Subtract the plate blank (wells marked Plate Blank in the template)", red.plate_blank, {onchange: e => { red.plate_blank = e.target.checked; }}),
        p.settings.pre_read ? check("Subtract the pre-read plate", red.use_preread, {onchange: e => { red.use_preread = e.target.checked; }}) : null));
  };
  draw();
  return modal("Reduction — " + p.name, body, [{label: "Cancel", value: null}, {label: "OK", primary: true, action: () => {
    red.wavelength = (red.wavelength || "").trim();
    return dlgOp("reduction", {id, reduction: red});
  }}]);
}

async function dlgDisplay(id) {
  const p = plateById(id), d = Object.assign({}, p.display), plots = dataMode(p) !== "endpoint";
  const view = radios("view", [["raw", plots ? "Raw Data (plots)" : "Raw Data (OD at each wavelength)"], ["reduced", "Reduced" + (p.unit ? ` (${p.unit})` : "")]], d.view);
  const type = radios("dtype", [["number", "Number"]].concat(plots ? [["plots", "Plots"]] : []).concat([["gray", "Gray Scale"], ["ranged", "Ranged (0–9)"], ["threshold", "Threshold (+ / * / −)"]]), d.type === "plots" && !plots ? "number" : d.type);
  const low = numInput(d.low, {class: "narrow"}), high = numInput(d.high, {class: "narrow"});
  return modal("Display — " + p.name, h("div", {class: "form"},
    h("fieldset", {}, h("legend", {}, "Show"), view),
    h("fieldset", {}, h("legend", {}, "Reduced data as"), type, h("div", {class: "row wrap"}, field("Low:", low), field("High:", high)),
      h("p", {class: "hint"}, "Low and High set the scale for Gray Scale, Ranged and Threshold."))),
    [{label: "Cancel", value: null}, {label: "OK", primary: true, action: () => {
      if (!(+high.value > +low.value)) throw new Error("High must be greater than Low.");
      return dlgOp("display", {id, display: {view: view.value(), type: type.value(), low: +low.value, high: +high.value}});
    }}]);
}

const PALETTE = ["#2f6fd0", "#d9642b", "#3c9a4b", "#c2456a", "#7a5cc0", "#a8881c", "#2a9a96", "#c75fa5", "#666", "#000"];
/* Well Graph: one colour per plate column (12) and one symbol shape per row (8),
   so every well of the plate has its own colour + symbol pair. */
const COLUMN_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b",
                       "#e377c2", "#7f7f7f", "#bcbd22", "#17becf", "#000000", "#b8860b"];
const wellColor = w => COLUMN_COLORS[(+w.slice(1) - 1) % 12];
const wellShape = w => ROWS.indexOf(w[0]);

/* Callbacks run after every screen update (live dialogs such as the Well Graph). */
const LIVE = new Set();

async function dlgWellGraph(id, wells) {
  const docId = S.active;
  const plateNow = () => { const d = S.docs.find(x => x.id === docId); return d ? d.doc.sections.find(x => x.id === id) : null; };
  let p = plateNow();
  const readingThis = () => S.reading && S.reading.doc === docId && S.reading.id === id;
  if (dataMode(p) === "endpoint") return alertDlg("The Well Graph shows Kinetic and Spectrum data.");
  wells = wells.filter(w => (p.columns || colsOf(p.settings)).includes(+w.slice(1)));
  if (!wells.length) return alertDlg("Select one or more wells that are read.");
  const o = {connect: true, symbols: true};
  const zoom = {view: null};
  const holder = h("div", {class: "wellgraph"});
  const status = h("p", {class: "hint", "aria-live": "polite"});
  let lastSig = null;
  const kinOf = () => dataMode(p) === "kinetic";
  const ys = w => (p.wells[w] && p.wells[w].ys) || [];
  // The chart is built once; live updates redraw it with the newest data (keeping the zoom).
  const zw = zoomable(view => {
    const series = wells.map(w => ({name: w, color: wellColor(w), shape: wellShape(w), line: o.connect, symbols: o.symbols,
      pts: (p.xs || []).map((x, k) => [x, ys(w)[k]]).filter(q => q[1] !== null && q[1] !== undefined)}));
    const curves = wells.map(w => p.wells[w] && p.wells[w].line ? {pts: p.wells[w].line, color: wellColor(w), dash: "5 3"} : null).filter(Boolean);
    return chart({w: 720, h: 380, view, series, curves, x: {label: kinOf() ? "Time (secs)" : "Wavelength (nm)"}, y: {label: "OD"}});
  }, zoom);
  const table = h("div", {class: "tablewrap"});
  const foot = h("p", {class: "hint"});
  fill(holder, zw, status, table, foot);
  const draw = () => {
    const kin = kinOf();
    const n1 = v => v === null || v === undefined ? "—" : (+v).toFixed(1), n3 = v => v === null || v === undefined ? "—" : (+v).toFixed(3);
    const rows = wells.map(w => {
      const st = (p.wells[w] && p.wells[w].stats) || {};
      const cells = kin ? [n3(st.vmax), st.r2 === null || st.r2 === undefined ? "—" : st.r2.toFixed(4), st.points || "—", n1(st.time_to_vmax), n1(st.onset)]
                        : [n1(st.lambda_max), n3(st.od_max), n1(st.lambda_min), n3(st.od_min)];
      return h("tr", {}, h("td", {}, legendMark(wellShape(w), wellColor(w)), " ", w),
        h("td", {class: "numc"}, p.wells[w] ? p.wells[w].text + (p.unit ? " " + p.unit : "") : "—"), cells.map(c => h("td", {class: "numc"}, c)));
    });
    const head = kin ? ["Vmax (mOD/min)", "R²", "Vmax Points", "Time to Vmax (s)", "Onset Time (s)"] : ["Wavelength Max (nm)", "OD at Max", "Wavelength Min (nm)", "OD at Min"];
    zw.redraw();
    fill(table, h("table", {class: "grouptable statstable"}, h("thead", {}, h("tr", {}, h("th", {}, "Well"), h("th", {}, "Reduced"), head.map(t => h("th", {}, t)))), h("tbody", {}, rows)));
    foot.textContent = kin ? "Dashed lines: the Vmax fit (or the onset level when the reduction is Onset Time). Change Vmax Points, limits and the onset OD under Reduction." : "";
    const r = readingThis() ? S.reading : null;
    status.textContent = r ? `Live: reading ${r.got} of ${r.total}` + (r.wait ? ` (${r.wait})` : "") + " — the graph updates as data arrive." : "";
  };
  const refresh = () => {
    const np = plateNow();
    if (!np) return;
    const r = readingThis() ? [S.reading.got, S.reading.total, S.reading.phase] : null;
    const sig = JSON.stringify([np.xs, wells.map(w => np.wells[w] && [np.wells[w].ys, np.wells[w].text]), r, np.unit]);
    if (sig === lastSig) return;
    lastSig = sig;
    p = np;
    draw();
  };
  refresh();
  LIVE.add(refresh);
  const body = h("div", {}, holder, h("div", {class: "row wrap"},
    check("Connect Points", o.connect, {onchange: e => { o.connect = e.target.checked; lastSig = null; refresh(); }}),
    check("Plot Symbols", o.symbols, {onchange: e => { o.symbols = e.target.checked; lastSig = null; refresh(); }}),
    h("span", {class: "spacer"}), h("button", {type: "button", onclick: () => printOnly(holder, p.name + ": Well Graph")}, "Print")));
  try {
    return await modal("Well Graph — " + p.name, body, [{label: "Close", primary: true, value: true}], {cls: "wide", noFocus: true});
  } finally {
    LIVE.delete(refresh);
  }
}

/* -------------------------------------------------------- Calculation dialog */
/* kind: "column" | "summary".  ctx: {id, group, old:{...}|null, after} */
async function dlgCalc(kind, ctx) {
  const old = ctx.old || {};
  const name = textInput(old.name || "", {autofocus: true});
  const formula = h("textarea", {class: "formula", rows: 3, spellcheck: false, "aria-label": "Formula"}, old.formula || "");
  const dec = numInput(old.decimals === undefined ? 3 : old.decimals, {class: "narrow", min: 0, max: 10, step: 1, placeholder: "text"});
  const desc = textInput(old.description || "");
  const hide = check("Hide Name", old.hide_name);
  const note = h("div", {class: "dlg-error"});
  let timer = null;
  formula.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const r = await op("check_formula", {id: ctx.id, formula: formula.value}, {quiet: true});
      note.textContent = r && r.error ? r.error : "";
    }, 250);
  });
  const save = () => kind === "column"
    ? dlgOp("column_save", {group: ctx.group, name: name.value, formula: formula.value, decimals: dec.value, old: old.name || null, after: ctx.after || null})
    : dlgOp("summary_save", {id: ctx.id, name: name.value, formula: formula.value, description: desc.value, decimals: dec.value === "" ? 3 : +dec.value, hide_name: hide.box.checked, old: old.name || null});
  const body = h("div", {class: "form"}, field("Name:", name), kind === "summary" ? field("Description:", desc) : null,
    h("label", {class: "field col"}, h("span", {class: "lbl"}, "Formula:"), formula), note,
    h("div", {class: "row wrap"}, field("Decimal Places:", dec), kind === "summary" ? hide : null),
    h("p", {class: "hint"}, "Refer to columns and summaries by name; use Name@Section for other sections. ",
      h("a", {href: "#", onclick: e => { e.preventDefault(); dlgFormulaHelp(); }}, "Formula help")));
  return modal("Calculation", body, [{label: "Cancel", value: null}, {label: "OK", primary: true, action: save}]);
}

/* ------------------------------------------------------------------- Graph */
async function dlgPlots(id) {
  const g = plateById(id);
  let plots = g.plots.map(p => ({old: p.name, name: p.name, group: p.group, x: p.x, y: p.y, xerr: p.xerr, yerr: p.yerr}));
  let cur = 0;
  const body = h("div", {class: "plots"});
  const groups = Object.keys(S.doc.groups);
  const draw = () => {
    const p = plots[cur];
    const cols = p && p.group && S.doc.groups[p.group] ? S.doc.groups[p.group].columns : [];
    const colSel = (key, none) => select((none ? [["", none]] : []).concat(cols.map(c => [c, c])), p[key] || "", {onchange: e => { p[key] = e.target.value || null; }});
    fill(body, 
      h("div", {class: "plotlist", role: "listbox"}, plots.map((q, i) => h("div", {class: "fl-item" + (i === cur ? " on" : ""), onclick: () => { cur = i; draw(); }}, q.name)),
        h("div", {class: "row"}, h("button", {type: "button", onclick: () => { let n = plots.length + 1; while (plots.some(q => q.name === "Plot#" + n)) n++; plots.push({name: "Plot#" + n, group: groups[0] || null, x: null, y: null, xerr: null, yerr: null}); cur = plots.length - 1; draw(); }}, "New"),
          h("button", {type: "button", disabled: !p, onclick: () => { plots.splice(cur, 1); cur = Math.max(0, cur - 1); draw(); }}, "Delete"))),
      p ? h("div", {class: "form"},
        field("Plot name:", textInput(p.name, {onchange: e => { p.name = e.target.value; draw(); }})),
        field("Group:", select(groups.length ? groups : [["", "(no groups yet)"]], p.group || "", {onchange: e => { p.group = e.target.value; p.x = p.y = p.xerr = p.yerr = null; draw(); }})),
        field("X:", colSel("x", "None")), field("Error Bars:", colSel("xerr", "No Error")),
        field("Y:", colSel("y", "None")), field("Error Bars:", colSel("yerr", "No Error"))) : h("p", {class: "hint"}, "No plots. Click New."));
  };
  draw();
  return modal("Plots — " + g.name, body, [{label: "Cancel", value: null}, {label: "OK", primary: true, action: () => dlgOp("graph", {id, plots})}], {cls: "wide", noFocus: true});
}

async function dlgFitOptions(id) {
  const g = plateById(id);
  const o = Object.assign({weighting: "none", conf: 0.95, bands: false, fixed: {}, seeds: {}}, g.fit_options || {});
  const names = g.param_names || {};
  const wsel = select(S.weightings, o.weighting);
  const conf = numInput(Math.round(o.conf * 1000) / 10, {class: "narrow", min: 50, max: 99.9, step: 0.5});
  const bands = check("Show confidence and prediction bands", o.bands);
  const rows = Object.keys(names).map(k => {
    const mode = o.fixed[k] !== undefined ? "fixed" : o.seeds[k] !== undefined ? "seed" : "auto";
    const val = numInput(o.fixed[k] ?? o.seeds[k] ?? "", {class: "narrow", "aria-label": "Value of " + k});
    const how = select([["auto", "Fitted"], ["seed", "Fitted, starting from"], ["fixed", "Fixed at"]], mode, {"aria-label": "How " + k + " is set"});
    const upd = () => { val.disabled = how.value === "auto"; };
    how.addEventListener("change", upd); upd();
    return {k, how, val, row: h("tr", {}, h("td", {}, h("b", {}, k), " " + names[k]), h("td", {}, how), h("td", {}, val))};
  });
  const body = h("div", {class: "form"},
    h("fieldset", {}, h("legend", {}, "Weighting"), wsel, h("p", {class: "hint"}, "Weights points by 1/Y\u00b2 and so on, so low values count as much as high ones. 1/SD\u00b2 uses the plot's Y error bars.")),
    h("fieldset", {}, h("legend", {}, "Statistics"), field("Confidence level:", conf, "%"), bands),
    rows.length ? h("fieldset", {}, h("legend", {}, "Parameters"), h("table", {class: "fittable"}, h("tbody", {}, rows.map(r => r.row))),
      h("p", {class: "hint"}, "Fix a parameter to a known value (for example a blank of 0), or give a starting value when a fit does not converge.")) : null);
  return modal("Fit Options \u2014 " + g.name, body, [{label: "Cancel", value: null}, {label: "OK", primary: true, action: () => {
    const fixed = {}, seeds = {};
    for (const r of rows) {
      if (r.how.value === "auto") continue;
      if (r.val.value.trim() === "") throw new Error(`Enter a value for ${r.k} (${names[r.k]}).`);
      (r.how.value === "fixed" ? fixed : seeds)[r.k] = +r.val.value;
    }
    return dlgOp("graph", {id, fit_options: {weighting: wsel.value, conf: +conf.value / 100, bands: bands.box.checked, fixed, seeds}});
  }}]);
}

async function dlgGraphType(id) {
  const g = plateById(id);
  const type = radios("gtype", [["scatter", "Scatter"], ["cluster", "Cluster Bar"], ["stack", "Stack Bar"]], g.type);
  const con = check("Connect Points", g.connect), sym = check("Plot Symbols", g.symbols);
  const height = numInput(g.height, {class: "narrow", min: 20, max: 400, step: 5});
  return modal("Graph Type — " + g.name, h("div", {class: "form"}, type, con, sym, field("Height:", height, "%")),
    [{label: "Cancel", value: null}, {label: "OK", primary: true, action: () => dlgOp("graph", {id, type: type.value(), connect: con.box.checked, symbols: sym.box.checked, height: +height.value})}]);
}

async function dlgAxis(id, which) {
  const g = plateById(id), ax = Object.assign({}, g[which + "axis"]);
  const auto = check("Auto-range", ax.auto);
  const min = numInput(ax.min, {class: "narrow"}), max = numInput(ax.max, {class: "narrow"});
  const scale = radios("scale", [["lin", "Linear"], ["log", "Log"]], ax.log ? "log" : "lin");
  const grid = check("Gridlines", ax.grid), ticks = check("Tick Marks", ax.ticks);
  const label = textInput(ax.label, {placeholder: "automatic"});
  return modal((which === "x" ? "X" : "Y") + " Axis Settings — " + g.name, h("div", {class: "form"},
    h("fieldset", {}, h("legend", {}, "Axis"), auto, h("div", {class: "row wrap"}, field("Min:", min), field("Max:", max))),
    h("fieldset", {}, h("legend", {}, "Scale"), scale, grid, ticks), field("Label:", label)),
    [{label: "Cancel", value: null}, {label: "OK", primary: true, action: () => dlgOp("graph", {id, [which + "axis"]:
      {auto: auto.box.checked, min: +min.value, max: +max.value, log: scale.value() === "log", grid: grid.box.checked, ticks: ticks.box.checked, label: label.value}})}]);
}

/* --------------------------------------------------------------- Sections */
async function dlgSectionSettings(id) {
  const s = plateById(id);
  if (s.kind === "group") return dlgGroupSettings(s.group);
  const name = textInput(s.name, {autofocus: true}), rep = check("Include in Report", s.report);
  return modal({plate: "Plate", graph: "Graph", notes: "Notes"}[s.kind] + " Settings", h("div", {class: "form"}, field("Name:", name), rep),
    [{label: "Cancel", value: null}, {label: "OK", primary: true, action: () => dlgOp("section_settings", {id, name: name.value, report: rep.box.checked})}]);
}

async function dlgSelectPlate(def) {
  const plates = S.doc.sections.filter(s => s.kind === "plate");
  const sel = select(plates.map(p => [p.id, p.name + (p.has_data ? "" : "  (not read)")]), def, {"aria-label": "Plate"});
  const kind = radios("pk", [["normal", "Normal"], ["preread", "Pre-read"]], "normal");
  const body = h("div", {class: "form"});
  const draw = () => {
    const p = plateById(+sel.value);
    const offer = p.settings.pre_read && p.settings.mode !== "spectrum";
    fill(body, plates.length > 1 ? field("Plate:", sel) : h("p", {}, p.name), offer ? kind : null,
      offer ? h("p", {class: "hint"}, p.has_preread ? "A pre-read has been taken for this plate." : "This plate asks for a pre-read: read the plate before adding sample, choosing Pre-read.") : null);
  };
  sel.addEventListener("change", draw);
  draw();
  return modal("Select Plate", body, [{label: "Cancel", value: null}, {label: "Read", primary: true, action: () => ({id: +sel.value, preread: !!body.querySelector("input[value=preread]:checked")})}], {noFocus: true});
}

async function dlgTextStyle(id) {
  const s = plateById(id);
  const cols = UI.cols[id] || [], sums = UI.sums[id] || [];
  if (!cols.length && !sums.length) return alertDlg("Select a column or a summary first. (In Notes, select text and use the format buttons above it.)");
  const names = ["Bold", "Italic", "Underline", "Outline", "Shadow", "Condensed", "Extended"];
  const boxes = names.map(n => check(n, false));
  const size = select([["", "Normal"], ["9", "9"], ["10", "10"], ["12", "12"], ["14", "14"], ["18", "18"], ["24", "24"]], "");
  const sample = h("div", {class: "stylesample"}, "ABCDEabcde 1234567890");
  const style = () => Object.fromEntries(names.map((n, k) => [n.toLowerCase(), boxes[k].box.checked]).concat([["size", size.value]]));
  const upd = () => Object.assign(sample.style, styleOf(style()));
  boxes.forEach(b => b.box.addEventListener("change", upd)); size.addEventListener("change", upd);
  return modal("Format Text", h("div", {class: "form"}, h("fieldset", {}, h("legend", {}, "Style"), h("div", {class: "row wrap"}, boxes)), field("Size:", size), sample),
    [{label: "Cancel", value: null}, {label: "OK", primary: true, action: () =>
      dlgOp("text_style", {id, columns: cols, summaries: sums, style: style()})}]);
}

async function dlgPasteData(id) {
  const box = h("textarea", {rows: 10, class: "formula", spellcheck: false, placeholder: "Paste 8 rows of 12 tab-separated numbers here (or a SOFTmax PRO text export)"});
  const r = await modal("Paste Plate Data", h("div", {class: "form"}, box, h("p", {class: "hint"}, "Copy the block of numbers in your spreadsheet, click in the box and paste.")),
    [{label: "Cancel", value: null}, {label: "Paste", primary: true, action: () => box.value.trim() ? box.value : false}], {cls: "wide"});
  if (r) await op("paste_data", {id, text: r});
}

/* -------------------------------------------------------------------- Help */
const FUNC_HELP = [
  ["Operators", "+  −  *  /  ^  Mod    =  <>  <  <=  >  >=    And  Or  Not"],
  ["Conditionals", "If(condition, value_if_true, value_if_false)   True   False"],
  ["Statistics", "Average(list)  Sum(list)  Min(list)  Max(list)  Stdev(list)  Stdevp(list)  Cv(list)  Cvp(list)  Count(list)"],
  ["Interpolation", "InterpX(plot, y_values)  InterpY(plot, x_values)  ParmA(plot) … ParmD(plot)  RSquared(plot)  Slope(x, y)  Intercept(x, y)"],
  ["Math", "Abs Acos Asin Atan Atan2(x,y) Ceil Cos Cosh Exp Fact Floor Fract Int Ln Log10 Pi Rand Round(n, digits) Sign Sin Sinh Sqrt Tan Tanh"],
  ["Text", "Concat(a,b)  Exact(a,b)  Left(t,n)  Right(t,n)  Mid(t,start,n)  Len(t)  Lower(t)  Upper(t)  Text(value)  Val(text)  Now  Today"],
  ["Lists", "Index(list)  Item(list, n)  ItemCount(list)  ItemIndex(list)"],
  ["Errors", "IsEmpty(v)  IsErr(v)  MakeErr(101…108)"],
  ["Group accessors", "!WellValues  !WellIDs  !SampleNames  !Concentration  !Factor  !Units  !GroupBlank"],
  ["Plate accessors", "!A1 … !H12 (reduced value of a well)   !L1 … !L6   !KinPlot   !ReadTimes   !SweepWavelengths   !PlateType   !FirstColumn   !LastColumn   !NumPointsRead   !NumWavelengthsRead   !ReadInterval   !TemperatureSetPoint   !StartSweep   !EndSweep   !StepSweep   !PreMixDuration   !IntervalMixDuration"],
  ["Names", "Mean  — a column or summary in this section.   Mean@Standards  — in another section.   'Std Curve'  — quote names containing spaces.   Plot#1@Graph#1  — a plot, for InterpX and friends.   Capitalisation does not matter."],
];
function dlgFormulaHelp() {
  return modal("Formula Help", h("div", {class: "help"}, FUNC_HELP.map(([t, d]) => [h("h3", {}, t), h("p", {}, d)]),
    h("h3", {}, "Examples"), h("p", {}, "Average(Values)     InterpX(Plot#1@Graph#1, Values)     If(MeanValue>1, \"high\", \"ok\")     MeanResult*!Factor")),
    [{label: "Close", primary: true, value: true}], {cls: "wide", noFocus: true});
}

function dlgAbout() {
  const i = S.instrument;
  return modal("About OD Studio", h("div", {class: "help about"},
    h("img", {src: "icon.svg", alt: "", class: "aboutlogo"}),
    h("p", {}, h("b", {}, "OD Studio"), " " + S.version + ". Plate-reader software for the Molecular Devices SpectraMax 250: a modern replacement for SOFTmax PRO 1.x, driving the reader through a protocol recovered from the original program."),
    h("p", {class: "muted"}, "SpectraMax and SOFTmax are trademarks of Molecular Devices, LLC. OD Studio is not affiliated with Molecular Devices."),
    h("h3", {}, "Instrument"),
    h("p", {}, i.connected ? `Instrument Type: ${i.model || "SpectraMax"}   •   ROM Version: ${i.version}   •   Port: ${i.port}` : "No Instrument connected" + (i.error ? " — " + i.error : ""))),
    [{label: "OK", primary: true, value: true}], {noFocus: true});
}

async function dlgTerminal() {
  const log = h("pre", {class: "termlog", "aria-live": "polite"});
  const box = textInput("", {placeholder: "Command, e.g. STATUS or TEMP 37", autofocus: true});
  const send = async () => {
    const c = box.value.trim();
    if (!c) return false;
    log.append("> " + c + "\n");
    const r = await op("raw_command", {command: c}, {quiet: true, onError: m => { log.append("! " + m + "\n"); }});
    if (r) log.append(r.reply.replace(/\r\n?/g, "\n").trim() + "\n");
    box.value = "";
    log.scrollTop = log.scrollHeight;
    return false;
  };
  return modal("Instrument Terminal", h("div", {}, log, box, h("p", {class: "hint"}, "Sends raw commands to the instrument. For troubleshooting.")),
    [{label: "Close", value: null}, {label: "Send", primary: true, action: send}], {cls: "wide"});
}

/* Factory service modes (SOFTmax PRO's Normal / Diagnostic / Align), with warnings. */
async function dlgService(action) {
  const align = action === "align";
  const ack = check(align ? "I understand that this may change my reader's calibration, and I accept the risk." : "I understand this is a factory service command.", false);
  const warn = h("div", {class: "service-warning", role: "alert"},
    h("p", {}, h("b", {}, align ? "Warning: this can change your instrument." : "Warning: factory service command.")),
    align
      ? h("div", {},
          h("p", {}, "Alignment runs the reader's factory alignment routine (command CALIGN). It may move the optics and store new alignment values in the reader's memory. If the result is wrong, the reader can give wrong readings until it is serviced."),
          h("p", {}, "It was meant for Molecular Devices technicians during manufacture and repair. Do not run it on a reader that works properly. It has not been tried on a real instrument with OD Studio, and its reply is not documented. It can take up to 2 minutes; take the plate out first."))
      : h("div", {},
          h("p", {}, "ADC Diagnostic asks the reader for the raw readings of its detector (command ADC_DIAG). It was meant for Molecular Devices technicians, and the format of its reply is not documented."),
          h("p", {}, "It should only read, not change anything, but this has not been checked on a real instrument. Take the plate out first, and use it only if you know why you need it.")),
    ack);
  const go = await modal(align ? "Factory Service: Alignment" : "Factory Service: ADC Diagnostic", warn,
    [{label: "Cancel", value: null},
     {label: align ? "Run Alignment" : "Run Diagnostic", danger: true, action: () => {
       if (!ack.box.checked) { toast("Tick the box to confirm first."); return false; }
       return true;
     }}], {noFocus: true});
  if (!go) return;
  const r = await op("service", {action, acknowledged: true});
  if (!r) return;
  const grid = r.grid ? h("table", {class: "grouptable"}, h("tr", {}, h("th", {}, ""), [...Array(12).keys()].map(c => h("th", {}, c + 1))),
    r.grid.map((row, i) => h("tr", {}, h("th", {}, ROWS[i]), row.map(v => h("td", {}, v === null ? "" : String(v)))))) : null;
  return modal(align ? "Alignment Result" : "ADC Diagnostic Result", h("div", {},
    align && r.value !== null && r.value !== undefined ? h("p", {}, "Value: ", h("b", {}, String(r.value))) : null,
    grid, h("p", {class: "hint"}, "Reply from the reader:"), h("pre", {class: "termlog"}, r.text || "(empty)")),
    [{label: "Copy", action: () => { navigator.clipboard && navigator.clipboard.writeText(r.text || ""); toast("Copied."); return false; }},
     {label: "Close", primary: true, value: null}], {cls: "wide", noFocus: true});
}

async function dlgErrorLog() {
  const r = await op("error_log", {}, {quiet: true});
  if (!r) return;
  const again = await modal("Instrument Error Log", h("pre", {class: "termlog"}, r.text || "No errors logged."),
    [{label: "Clear Error Log", value: "clear"}, {label: "Close", primary: true, value: null}], {noFocus: true});
  if (again === "clear") await op("error_log", {clear: true}, {quiet: true});
}

/* ------------------------------------------------------------------- logs */
function dlgHistory() {
  const hist = (S.docs.find(d => d.id === S.active) || {}).history || [];
  const lines = hist.slice().reverse().map(e => new Date(e.t * 1000).toLocaleString() + "  " + e.text).join("\n");
  return modal("Experiment History — " + S.title, h("div", {},
    h("p", {class: "hint"}, "What was done to this experiment, newest first. It is saved inside the file."),
    h("pre", {class: "termlog tall"}, lines || "Nothing recorded yet.")), [{label: "Close", primary: true, value: true}], {cls: "wide", noFocus: true});
}

async function dlgActivityLog() {
  const r = await op("activity_log", {lines: 1000}, {quiet: true});
  if (!r) return;
  const pre = h("pre", {class: "termlog tall"}, r.lines.length ? r.lines.slice().reverse().join("\n") : "Nothing recorded yet.");
  return modal("Activity Log", h("div", {},
    h("p", {class: "hint"}, "Everything the program did, newest first: readings, instrument commands, files, settings and errors. Stored as plain text in ", h("code", {}, r.path), "."),
    pre), [{label: "Copy", action: async () => { try { await navigator.clipboard.writeText(r.lines.join("\n")); toast("Copied"); } catch (e) { toast("Could not copy"); } return false; }},
           {label: "Close", primary: true, value: true}], {cls: "wide", noFocus: true});
}
