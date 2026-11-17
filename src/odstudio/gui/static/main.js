"use strict";
/* Main window: menus, status bar, commands, polling, printing, shortcuts. */

const cardCache = new Map();

function activeSection() { return S.doc.sections.find(s => s.id === UI.active) || null; }
function activeOf(kind) { const a = activeSection(); return a && a.kind === kind ? a : null; }
function plates() { return S.doc.sections.filter(s => s.kind === "plate"); }

function render() {
  if (!S) return;
  aliasActive();
  if (!S.doc.sections.some(s => s.id === UI.active)) UI.active = (S.doc.sections[0] || {}).id || null;
  document.title = (S.dirty ? "\u2022 " : "") + S.title + " \u2014 OD Studio";
  renderMenus();
  renderStatus();
  renderTabs();
  const main = document.getElementById("doc");
  const tiled = UI.layout === "tiled" && S.docs.length > 1;
  main.className = tiled ? "tiled" : "single";
  const shown = tiled ? S.docs : S.docs.filter(d => d.id === S.active);
  const wins = shown.map(d => withDoc(d, () => renderWindow(d)));
  for (const k of [...winCache.keys()]) if (!S.docs.some(d => d.id === k)) winCache.delete(k);
  for (const k of [...cardCache.keys()]) if (!S.docs.some(d => k.startsWith(d.id + ":"))) cardCache.delete(k);
  if (main.children.length !== wins.length || wins.some((w, i) => main.children[i] !== w)) {
    const y = window.scrollY;
    main.replaceChildren(...wins);
    window.scrollTo(0, y);
  }
  for (const fn of LIVE) { try { fn(); } catch (e) { console.error(e); } }
}

const winCache = new Map();
function renderWindow(d) {
  if (!d.doc.sections.some(s => s.id === UI.active)) UI.active = (d.doc.sections[0] || {}).id || null;
  let w = winCache.get(d.id);
  if (!w) {
    const node = h("div", {class: "window", "data-doc": d.id});
    // Switch the active window before the click lands; re-render after the click has been handled,
    // so the pressed button is not replaced under the pointer.
    const activate = () => { if (S.active !== d.id) { setActiveDoc(d.id); setTimeout(render, 0); } };
    node.addEventListener("mousedown", activate, true);
    node.addEventListener("focusin", activate, true);
    const addbar = h("div", {class: "addbar noprint"}, h("span", {class: "muted"}, "Add to this experiment:"),
      tb("+ Plate", () => cmdNewSection("plate"), {title: "Another plate, with its own instrument settings"}),
      tb("+ Graph", () => cmdNewSection("graph")), tb("+ Notes", () => cmdNewSection("notes")));
    w = {node, addbar, headSig: null, head: null, probSig: null, prob: null};
    winCache.set(d.id, w);
  }
  w.node.classList.toggle("active-win", d.id === S.active);
  const reading = S.reading && S.reading.doc === d.id;
  const headSig = JSON.stringify([d.dirty, d.title, !!reading]);
  if (w.headSig !== headSig) {
    w.headSig = headSig;
    w.head = h("div", {class: "win-head noprint"},
      h("b", {}, (d.dirty ? "• " : "") + d.title),
      reading ? h("span", {class: "reading"}, "reading") : null,
      h("span", {class: "spacer"}),
      h("button", {type: "button", class: "tb", title: "Close this window", "aria-label": "Close " + d.title, onclick: () => cmdCloseWindow(d.id)}, "✕"));
  }
  const probSig = JSON.stringify(d.doc.problems);
  if (w.probSig !== probSig) {
    w.probSig = probSig;
    w.prob = d.doc.problems.length ? h("div", {class: "problems noprint", role: "alert"}, d.doc.problems.map(p => h("div", {}, p))) : null;
  }
  const focused = document.activeElement && document.activeElement.closest ? document.activeElement.closest("section.card") : null;
  const kids = [w.head];
  if (w.prob) kids.push(w.prob);
  for (const s0 of d.doc.sections) {
    const s = UI.printing ? Object.assign({}, s0, {open: true}) : s0;
    const key = d.id + ":" + s.id;
    const rd = S.reading && S.reading.doc === d.id && S.reading.id === s.id ? S.reading : 0;
    const sig = JSON.stringify([s, UI.sel[s.id], UI.cols[s.id], UI.sums[s.id], rd, S.prefs.print_template, s.kind === "graph" || s.kind === "plate" ? d.doc.groups : 0,
      s.kind === "workflow" ? [S.workflow, S.reading && S.reading.wait, S.instrument.connected, d.doc.sections.filter(x => x.kind === "plate").map(x => [x.id, x.name, x.settings])] : 0]);
    let c = cardCache.get(key);
    if (!c || c.sig !== sig) {
      if (c && (DRAG_END || (focused === c.node && (s.kind === "notes" || s.kind === "workflow")))) {
        // keep the card while the user is dragging or typing in it; it is rebuilt afterwards
        c.stale = true;
      } else {
        c = {sig, node: sectionCard(s), html: s.html};
        cardCache.set(key, c);
      }
    }
    c.node.classList.toggle("active", d.id === S.active && UI.active === s.id);
    kids.push(c.node);
  }
  kids.push(w.addbar);
  for (const k of [...cardCache.keys()]) if (k.startsWith(d.id + ":") && !d.doc.sections.some(s => k === d.id + ":" + s.id)) cardCache.delete(k);
  const now = [...w.node.children];
  if (now.length !== kids.length || kids.some((k, i) => now[i] !== k)) w.node.replaceChildren(...kids);
  return w.node;
}

function renderTabs() {
  const bar = document.getElementById("tabs");
  fill(bar, S.docs.map(d => h("div", {class: "tab" + (d.id === S.active ? " on" : ""), role: "tab", "aria-selected": String(d.id === S.active)},
      h("button", {type: "button", class: "tab-title", title: d.path || "Not saved yet", onclick: () => { setActiveDoc(d.id); render(); scrollToWindow(d.id); }},
        (d.dirty ? "\u2022 " : "") + d.title, S.reading && S.reading.doc === d.id ? h("span", {class: "dotlive", title: "Reading"}) : null),
      h("button", {type: "button", class: "tab-x", title: "Close", "aria-label": "Close " + d.title, onclick: () => cmdCloseWindow(d.id)}, "\u2715"))),
    h("button", {type: "button", class: "tab-new", title: "New experiment window", onclick: cmdNew}, "+"),
    h("span", {class: "spacer"}),
    h("span", {class: "seg"},
      tb("Tabs", () => setLayout("tabs"), {on: UI.layout !== "tiled", title: "Show one window at a time"}),
      tb("Side by side", () => setLayout("tiled"), {on: UI.layout === "tiled", title: "Show all windows next to each other"})));
}

function setLayout(l) {
  UI.layout = l;
  try { localStorage.setItem("odstudio.layout", l); } catch (e) { /* ignore */ }
  render();
}

function scrollToWindow(id) {
  const w = winCache.get(id);
  if (w && UI.layout === "tiled") w.node.scrollIntoView({behavior: "smooth", inline: "nearest", block: "nearest"});
}

function updateToolbars() {
  for (const s of S.doc.sections) {
    const c = cardCache.get(S.active + ":" + s.id);
    if (!c || !s.open) continue;
    const t = c.node.querySelector(".toolbar");
    if (t) fill(t, ...toolbarFor(s).filter(Boolean));
    c.sig = "";            // rebuild on next full render so the selection is baked in
  }
  renderMenus();
}

/* ------------------------------------------------------------------- menus */
function menuDefs() {
  const a = activeSection(), plate = activeOf("plate"), group = activeOf("group"), graph = activeOf("graph"), notes = activeOf("notes");
  const reading = !!S.reading, conn = S.instrument.connected;
  const cols = a ? (UI.cols[a.id] || []) : [], sums = a ? (UI.sums[a.id] || []) : [], sel = plate ? (UI.sel[plate.id] || []) : [];
  const I = (label, action, o) => Object.assign({label, action}, o || {});
  const defs = [
    ["File", [
      I("New", cmdNew), I("Open\u2026", cmdOpen, {key: MOD + "O"}), I("Close", () => cmdCloseWindow(S.active)), "-",
      I("Save", () => cmdSave(false), {key: MOD + "S"}), I("Save As\u2026", () => cmdSave(true)), I("Save As Protocol\u2026", cmdSaveProtocol),
      I("Revert to Saved", async () => { if (await confirmDlg("Revert to the last version saved?", "Revert", true)) op("revert"); }, {enabled: !!S.path && S.dirty}), "-",
      I("Import Text Data\u2026", cmdImport, {enabled: !!plate && !reading}), I("Export\u2026", cmdExport, {enabled: plates().some(p => p.has_data)}), "-",
      I("Experiment History\u2026", dlgHistory), "-",
      I(a ? `Print “${a.name}”` : "Print Current", () => doPrint(a.id), {enabled: !!a}), I("Print…", () => doPrint(null), {key: MOD + "P"}), "-",
      I("Quit", cmdQuit)]],
    ["Edit", [
      I("Undo", () => op("undo"), {key: MOD + "Z", enabled: S.can_undo && !reading}), I("Redo", () => op("redo"), {enabled: S.can_redo && !reading}), "-",
      I("Cut", cmdCut, {enabled: !!(group && cols.length)}), I("Copy", cmdCopy, {enabled: !!(plate && plate.has_data) || !!(group && cols.length)}),
      I("Paste", cmdPaste, {enabled: !!plate || !!group}), I("Clear", cmdClear, {enabled: !!(plate && plate.has_data) || cols.length > 0 || sums.length > 0}),
      I("Select All", cmdSelectAll, {enabled: !!plate || !!group}), "-",
      I(a ? `Delete “${a.name}”` : "Delete Section", cmdDeleteSection, {enabled: !!a && !reading}), "-",
      I("New Plate", () => cmdNewSection("plate")), I("New Graph", () => cmdNewSection("graph")), I("New Notes", () => cmdNewSection("notes")), I("New Workflow", cmdWorkflow), "-",
      I("Text Style…", () => dlgTextStyle(a.id), {enabled: !!a && (cols.length > 0 || sums.length > 0)}), I("Preferences…", dlgPrefs), "-",
      I("Suspend Recalculation", () => op("suspend", {on: !S.suspended}), {checked: S.suspended}), I("Recalculate Now", () => op("recalculate"))]],
    ["View", [
      I("Minimize", () => op("all_open", {open: false})), I("Expand", () => op("all_open", {open: true})), "-",
      I(S.prefs.show_status ? "Hide Status" : "Show Status", () => op("prefs", {show_status: !S.prefs.show_status})), "-",
      ...S.doc.sections.map(s => I(s.name, () => goSection(s.id), {checked: s.id === UI.active}))]],
    ["Control", [
      I("Instrument Setup\u2026", () => dlgInstrument((plate || plates()[0]).id), {enabled: !reading}),
      I("Workflow\u2026", cmdWorkflow), "-",
      reading ? I("Stop", () => op("stop"), {key: MOD + "R"}) : I("Read", cmdRead, {key: MOD + "R", enabled: conn}),
      I(S.instrument.drawer === "OPEN" ? "Close Drawer" : "Open Drawer", () => op("drawer"), {enabled: conn && !reading}),
      I("Incubator…", dlgIncubator, {enabled: conn && !reading && canDo("temperature")}),
      I("Automix (shake now)", () => op("shake"), {enabled: conn && !reading && canDo("shake"), checked: S.instrument.shaking}), "-",
      conn ? I("Disconnect", () => op("disconnect"), {enabled: !reading}) : I("Connect", () => S.prefs.port ? op("connect") : dlgPrefs()),
      I("Instrument Terminal…", dlgTerminal, {enabled: conn && !reading}), I("Instrument Error Log…", dlgErrorLog, {enabled: conn && !reading}), "-",
      I("Factory Service: ADC Diagnostic…", () => dlgService("diagnostic"), {enabled: conn && !reading && canDo("service")}),
      I("Factory Service: Alignment…", () => dlgService("align"), {enabled: conn && !reading && canDo("service")})]],
  ];
  if (plate) defs.push(["Plate", [
    I("Template…", () => dlgTemplate(plate.id)), I("Reduction…", () => dlgReduction(plate.id)), "-",
    I("Display…", () => dlgDisplay(plate.id)), I("Graph\u2026", () => dlgWellGraph(plate.id, sel), {enabled: sel.length > 0 && dataMode(plate) !== "endpoint" && plate.has_data}),
    I("Mask", () => op("mask", {id: plate.id, wells: sel}), {enabled: sel.length > 0 && plate.has_data}), "-",
    I("Copy Template", async () => { if (await op("copy_template", {id: plate.id})) toast("Template copied"); }), I("Paste Template", () => op("paste_template", {id: plate.id}), {enabled: S.clip.includes("template")}), "-",
    I("Create Summary\u2026", () => dlgCalc("summary", {id: plate.id})), I("Edit Summary", () => editSelected(plate), {enabled: sums.length === 1}), "-",
    I("Plate Settings…", () => dlgSectionSettings(plate.id))]]);
  if (group) defs.push(["Group", [
    I("Create Column", () => dlgCalc("column", {id: group.id, group: group.group, after: cols[cols.length - 1]})), I("Create Summary", () => dlgCalc("summary", {id: group.id})), "-",
    I("Edit Column", () => editSelected(group), {enabled: cols.length === 1}), I("Edit Summary", () => editSelected(group), {enabled: sums.length === 1 && !cols.length}), "-",
    I("Hide", () => op("column_hide", {group: group.group, names: cols}), {enabled: cols.length > 0}),
    I(group.hide_replicates ? "Show Replicates" : "Hide Replicates", () => op("group_view", {group: group.group, hide_replicates: !group.hide_replicates})),
    I("Show All", () => op("group_view", {group: group.group, show_all: true})),
    I(group.show_formulas ? "Hide Formulas" : "Show Formulas", () => op("group_view", {group: group.group, show_formulas: !group.show_formulas})), "-",
    I("Add Interpolated Results…", () => cmdInterpolate(group)), I("Group Settings…", () => dlgGroupSettings(group.group))]]);
  if (graph) defs.push(["Graph", [
    I("Plots…", () => dlgPlots(graph.id)), I("Graph Type…", () => dlgGraphType(graph.id)), I("X Axis…", () => dlgAxis(graph.id, "x")), I("Y Axis…", () => dlgAxis(graph.id, "y")), "-",
    I("Graph Settings…", () => dlgSectionSettings(graph.id))]]);
  if (notes) defs.push(["Notes", [
    I("Create Summary…", () => dlgCalc("summary", {id: notes.id})), I("Edit Summary", () => editSelected(notes), {enabled: sums.length === 1}), "-",
    I(notes.show_formulas ? "Hide Formulas" : "Show Formulas", () => op("notes", {id: notes.id, show_formulas: !notes.show_formulas})), "-",
    I("Notes Settings…", () => dlgSectionSettings(notes.id))]]);
  defs.push(["Assays", [...(S.library || []).map(e => I(e.name, () => op("open_library", {name: e.name}))),
    S.library && S.library.length ? "-" : null,
    I("Set Folder\u2026", async () => { const d = await fileDialog({mode: "folder", title: "Assays folder", dir: S.prefs.assays_folder}); if (d) op("prefs", {assays_folder: d}); }),
    S.assays.length ? "-" : null, ...S.assays.map(f => I(f.replace(/\.(odx|smx)p?$/i, ""), () => op("open_assay", {name: f})))]]);
  defs.push(["Window", [I("New Window", cmdNew), I("Close Window", () => cmdCloseWindow(S.active)), "-",
    I("Tabs", () => setLayout("tabs"), {checked: UI.layout !== "tiled"}), I("Side by Side", () => setLayout("tiled"), {checked: UI.layout === "tiled"}), "-",
    ...S.docs.map(d => I((d.dirty ? "\u2022 " : "") + d.title, () => { setActiveDoc(d.id); render(); scrollToWindow(d.id); }, {checked: d.id === S.active}))]]);
  defs.push(["Help", [I("OD Studio Help\u2026", () => dlgHelp()), I("Formula Help\u2026", dlgFormulaHelp), "-", I("Activity Log\u2026", dlgActivityLog), "-", I("About OD Studio\u2026", dlgAbout)]]);
  return defs;
}

function renderMenus() {
  if (!S) return;
  const bar = document.getElementById("menubar");
  const defs = menuDefs();
  fill(bar, h("span", {class: "brand"}, h("img", {src: "icon.svg", alt: ""}), "OD Studio"),
    ...defs.map(([name]) => h("button", {type: "button", class: "menu-title", "aria-haspopup": "menu",
      onclick: e => { const was = e.currentTarget.classList.contains("on"); closeMenus(); if (!was) showMenu(e.currentTarget, menuDefs().find(d => d[0] === name)[1]); },
      onmouseenter: e => { if (openMenu && !e.currentTarget.classList.contains("on")) showMenu(e.currentTarget, menuDefs().find(d => d[0] === name)[1]); }}, name)),
    h("span", {class: "spacer"}), h("span", {class: "doctitle", title: S.path || "Not saved yet"}, (S.dirty ? "• " : "") + S.title),
    S.suspended ? h("span", {class: "pill warn"}, "Recalculation suspended") : null);
}

function renderStatus() {
  const bar = document.getElementById("statusbar");
  bar.hidden = !S.prefs.show_status;
  const i = S.instrument, r = S.reading;
  const temp = i.temperature === null || i.temperature === undefined ? "--" : i.temperature.toFixed(1);
  fill(bar, 
    h("button", {type: "button", class: "inst " + (i.connected ? "ok" : "off"), title: i.connected ? `Connected on ${i.port}. Firmware: ${i.version}` : (i.error || "Click to choose the serial port"),
      onclick: () => i.connected ? dlgAbout() : dlgPrefs()},
      h("span", {class: "dot"}), i.connected ? (i.model || "SpectraMax") : "No instrument"),
    canDo("temperature") ? h("div", {class: "temp", title: "Temperature in the plate chamber"}, h("b", {}, temp), " °C", i.setpoint > 0 ? h("span", {class: "muted"}, ` → ${i.setpoint.toFixed(1)}`) : null) : null,
    r ? h("button", {type: "button", class: "read stop", onclick: () => op("stop")}, "Stop")
      : h("button", {type: "button", class: "read primary", disabled: !i.connected, onclick: cmdRead}, "Read"),
    canDo("temperature") ? h("button", {type: "button", disabled: !i.connected || !!r, onclick: dlgIncubator, title: "Incubator"}, "Incubator" + (i.setpoint > 0 ? " On" : "")) : null,
    canDo("shake") ? h("button", {type: "button", class: i.shaking ? "on" : "", disabled: !i.connected || !!r, onclick: () => op("shake"), title: "Shake the plate now"}, i.shaking ? "Stop Mixing" : "Automix") : null,
    h("button", {type: "button", disabled: !i.connected || !!r, onclick: () => op("drawer")}, i.drawer === "OPEN" ? "Close Drawer" : "Open Drawer"),
    r && r.workflow ? h("div", {class: "progress", role: "status"}, h("span", {}, `Workflow${S.workflow && S.workflow.cycle ? ", cycle " + S.workflow.cycle : ""}: ${r.id ? `reading ${r.name} ${r.got} of ${r.total}` : (r.wait || r.phase)}`))
    : r ? h("div", {class: "progress", role: "status"}, h("span", {}, `${r.phase} ${r.name}: ${r.got} of ${r.total}` + (r.wait ? ` (${r.wait})` : "")), h("progress", {max: r.total, value: r.got}))
      : (!i.connected && i.error ? h("div", {class: "progress err"}, i.error) : null));
}

function goSection(id) {
  UI.active = id;
  const s = S.doc.sections.find(x => x.id === id);
  if (s && !s.open) op("section_settings", {id, open: true}); else render();
  const c = cardCache.get(S.active + ":" + id);
  if (c) c.node.scrollIntoView({behavior: "smooth", block: "start"});
}

/* ---------------------------------------------------------------- commands */
async function saveGuard(what) {
  if (!S.dirty) return true;
  const r = await modal("OD Studio", h("p", {class: "msg"}, `Save changes to “${S.title}” before ${what}?`),
    [{label: "Cancel", value: "cancel"}, {label: "No", value: "no"}, {label: "Yes", primary: true, value: "yes"}], {noFocus: true});
  if (r === "yes") return cmdSave(false);
  return r === "no";
}

async function cmdNew() { await op("new"); }

async function cmdCloseWindow(id) {
  setActiveDoc(id);
  render();
  if (S.reading && S.reading.doc === id) return alertDlg("Can't close the window while reading a plate.");
  if (await saveGuard("closing")) await op("close", {doc: id});
}

async function cmdOpen() {
  const path = await fileDialog({title: "Open", exts: [".odx", ".odxp", ".smx", ".smxp", ".txt", ".pda", ".ppr"]});
  if (path) await op("open", {path});
}

async function cmdSave(as) {
  let path = as ? null : S.path;
  if (!path) {
    path = await fileDialog({mode: "save", title: "Save this document as:", name: S.title === "Untitled" ? "" : S.title.replace(/ \(SOFTmax PRO\)$/, "").replace(/\.(odx|smx|txt|tsv)$/i, ""), ext: ".odx", exts: [".odx"]});
    if (!path) return false;
  }
  return !!(await op("save", {path}));
}

async function cmdSaveProtocol() {
  const path = await fileDialog({mode: "save", title: "Save protocol (settings and template, no data) as:", ext: ".odxp", exts: [".odxp"], dir: S.prefs.assays_folder});
  if (path) await op("save", {path, protocol: true});
}

async function cmdImport() {
  const p = activeOf("plate");
  const path = await fileDialog({title: "Import text data into " + p.name, exts: [".txt", ".tsv"]});
  if (!path) return;
  if (p.has_data && !(await confirmDlg(`Replace data in “${p.name}”?`, "Replace"))) return;
  await op("import", {id: p.id, path});
}

async function cmdExport() {
  const p = activeOf("plate");
  const fmt = radios("xf", [["txt", "Text (tab-delimited)"], ["csv", "CSV (one row per well and reading)"], ["xlsx", "Excel workbook (.xlsx)"]], "txt");
  const content = radios("xc", [["raw", "Raw readings"], ["reduced", "Reduced values"], ["both", "Both"]], "raw");
  const lay = radios("xl", [["time", "Time (one line per reading)"], ["plate", "Plate (8 \u00d7 12 blocks)"]], S.prefs.export_layout);
  const labels = check("Include Labels", S.prefs.export_labels);
  const which = radios("xw", [["one", p && p.has_data ? p.name : "Active plate"], ["all", "All plates"]], p && p.has_data ? "one" : "all");
  const ok = await modal("Export", h("div", {class: "form"}, plates().length > 1 && p && p.has_data ? h("fieldset", {}, h("legend", {}, "Export"), which) : null,
      h("fieldset", {}, h("legend", {}, "File type"), fmt), h("fieldset", {}, h("legend", {}, "Contents"), content),
      h("fieldset", {}, h("legend", {}, "Text layout"), lay, labels)),
    [{label: "Cancel", value: null}, {label: "Continue", primary: true, value: true}], {noFocus: true});
  if (!ok) return;
  const ext = "." + fmt.value();
  const path = await fileDialog({mode: "save", title: "Export Data to:", ext, exts: [ext], name: S.title.replace(/\.(odx|smx|txt)$/i, "").replace(/ \(SOFTmax PRO\)$/, "") + ext});
  if (path) await op("export", {path, id: which.value() === "one" && p && p.has_data ? p.id : null, layout: lay.value(), labels: labels.box.checked, format: fmt.value(), content: content.value()});
}

async function cmdQuit() {
  if (S.reading && !(await confirmDlg("A plate is being read. Stop the reading and quit?", "Quit", true))) return;
  for (const d of S.docs.filter(x => x.dirty)) {
    setActiveDoc(d.id);
    render();
    if (!(await saveGuard("quitting"))) return;
  }
  if (S.reading) await op("stop", {}, {quiet: true});
  await fetch("/api/op", {method: "POST", headers: {"Content-Type": "application/json", "X-ODStudio": "1"}, body: JSON.stringify({op: "quit", args: {}})}).catch(() => {});
  clearInterval(pollTimer);
  fill(document.body, h("div", {class: "bye"}, h("img", {src: "icon.svg", alt: "", class: "byelogo"}), h("h1", {}, "OD Studio has quit"), h("p", {}, "You can close this window.")));
}

async function cmdNewSection(kind) {
  const r = await op("add_section", {kind, after: UI.active});
  if (!r) return;
  UI.active = r.id;
  render();
  const c = cardCache.get(S.active + ":" + r.id);
  if (c) c.node.scrollIntoView({behavior: "smooth", block: "center"});
  if (kind === "graph") dlgPlots(r.id);
}

async function cmdWorkflow() {
  const r = await op("workflow_open");
  if (r) goSection(r.id);
}

async function cmdDeleteSection() {
  const a = activeSection();
  const extra = a.kind === "group" ? " Its wells will be removed from the templates." : "";
  if (await confirmDlg(`Do you want to Delete “${a.name}”?` + extra, "Delete", true)) await op("delete_section", {id: a.id});
}

async function cmdRead() {
  const ps = plates();
  const act = activeOf("plate");
  const def = act || ps.find(p => !p.has_data) || ps[0];
  let target = {id: def.id, preread: false};
  if (ps.length > 1 || (def.settings.pre_read && def.settings.mode !== "spectrum")) {
    target = await dlgSelectPlate(def.id);
    if (!target) return;
  }
  UI.active = target.id;
  await op("read", target);
}

async function cmdCopy() {
  const plate = activeOf("plate"), group = activeOf("group");
  if (group && (UI.cols[group.id] || []).length) { if (await op("column_copy", {group: group.group, names: UI.cols[group.id]})) toast("Column copied"); return; }
  if (plate) {
    const r = await op("copy_data", {id: plate.id});
    if (!r) return;
    try { await navigator.clipboard.writeText(r.text); toast("Plate data copied (also to the system clipboard)"); }
    catch (e) { toast("Plate data copied"); }
  }
}

async function cmdPaste() {
  const plate = activeOf("plate"), group = activeOf("group");
  if (group) { const cols = UI.cols[group.id] || []; await op("column_paste", {group: group.group, after: cols[cols.length - 1] || null}); return; }
  if (!plate) return;
  let text = "";
  try { text = await navigator.clipboard.readText(); } catch (e) { text = ""; }
  const rows = text.trim().split(/\r\n|\r|\n/);
  const looksLikeData = rows.length >= 8 && rows[0].split("\t").length >= 12;
  if (looksLikeData) return void (await op("paste_data", {id: plate.id, text}));
  if (S.clip.includes("data")) return void (await op("paste_data", {id: plate.id}));
  await dlgPasteData(plate.id);
}

async function cmdCut() {
  const g = activeOf("group");
  if (!g || !(UI.cols[g.id] || []).length) return;
  if (await op("column_copy", {group: g.group, names: UI.cols[g.id]})) await cmdClear(true);
}

async function cmdClear(skipConfirm) {
  const a = activeSection(), cols = UI.cols[a.id] || [], sums = UI.sums[a.id] || [];
  if (a.kind === "group" && cols.length) { await op("column_delete", {group: a.group, names: cols}); UI.cols[a.id] = []; return; }
  if (sums.length) { for (const n of sums) await op("summary_delete", {id: a.id, name: n}); UI.sums[a.id] = []; return; }
  if (a.kind === "plate" && a.has_data && (skipConfirm === true || await confirmDlg(`Delete data in “${a.name}”?`, "Delete", true))) await op("clear_data", {id: a.id});
}

function cmdSelectAll() {
  const a = activeSection();
  if (a.kind === "plate") UI.sel[a.id] = WELLS.slice();
  if (a.kind === "group") UI.cols[a.id] = a.columns.filter(c => !c.hidden).map(c => c.name);
  render();
}

async function cmdInterpolate(group) {
  const opts = [];
  for (const s of S.doc.sections) if (s.kind === "graph") for (const p of s.plots) opts.push([s.name + "\u0000" + p.name, `${p.name} in ${s.name}` + (p.fit && p.fit.ok ? ` (${p.fit.label})` : "")]);
  if (!opts.length) return alertDlg("Create a graph with a standard curve first (Edit > New Graph), then choose a Fit.");
  const sel = select(opts, opts[0][0]);
  const ok = await modal("Add Interpolated Results", h("div", {class: "form"}, field("Standard curve:", sel),
    h("p", {class: "hint"}, "Adds the columns Result = InterpX(curve, Values) and MeanResult to this group.")), [{label: "Cancel", value: null}, {label: "Add", primary: true, value: true}], {noFocus: true});
  if (!ok) return;
  const [graph, plot] = sel.value.split("\u0000");
  await op("add_interpolation", {group: group.group, graph, plot});
}

/* ---------------------------------------------------------------- printing */
function printHead(title) {
  const parts = [];
  if (S.prefs.print_filename) parts.push(S.title);
  if (title) parts.push(title);
  if (S.prefs.print_time) parts.push(new Date().toLocaleString());
  fill(document.getElementById("printhead"), h("div", {}, parts.join("   •   ")));
}

function templateTable(p) {
  const t = h("table", {class: "tplprint printonly"}, h("caption", {}, p.name + ": Template"));
  t.append(h("tr", {}, h("th", {}), ...Array.from({length: 12}, (_, c) => h("th", {}, c + 1))));
  for (let r = 0; r < 8; r++) t.append(h("tr", {}, h("th", {}, ROWS[r]), ...Array.from({length: 12}, (_, c) => { const x = p.template[ROWS[r] + (c + 1)]; return h("td", {}, x ? x.sample + (x.value !== null && x.value !== undefined ? " " + x.value : "") : ""); })));
  return t;
}

function doPrint(sectionId) {
  UI.printing = {only: sectionId};
  cardCache.clear();
  render();
  printHead(sectionId ? (S.doc.sections.find(s => s.id === sectionId) || {}).name : "");
  for (const w of winCache.values()) w.node.classList.toggle("printskip", +w.node.dataset.doc !== S.active);
  const pg = S.prefs.print_page_number ? h("style", {id: "pgnum"}, "@page { @bottom-right { content: \"Page \" counter(page) \" of \" counter(pages); font: 9pt system-ui, sans-serif; color: #555; } }") : null;
  if (pg) document.head.append(pg);
  for (const s of S.doc.sections) {
    const node = cardCache.get(S.active + ":" + s.id).node;
    node.classList.toggle("printskip", sectionId ? s.id !== sectionId : !s.report);
    if (s.kind === "plate" && S.prefs.print_template && Object.keys(s.template).length) node.append(templateTable(s));
  }
  const done = () => { window.removeEventListener("afterprint", done); UI.printing = null; if (pg) pg.remove(); for (const w of winCache.values()) w.node.classList.remove("printskip"); cardCache.clear(); render(); };
  window.addEventListener("afterprint", done);
  setTimeout(() => window.print(), 50);
}

function printOnly(node, title) {
  printHead(title);
  const solo = h("div", {class: "printsolo"}, h("h2", {}, title), node.cloneNode(true));
  document.body.append(solo);
  document.body.classList.add("solo");
  const done = () => { window.removeEventListener("afterprint", done); solo.remove(); document.body.classList.remove("solo"); };
  window.addEventListener("afterprint", done);
  setTimeout(() => window.print(), 50);
}

/* ----------------------------------------------------------------- startup */
let wfMsgKey = null;
async function workflowPrompt(force) {
  const w = S.workflow;
  if (!w || w.status !== "message") { wfMsgKey = null; return; }
  const key = w.started + ":" + w.sid;
  if (wfMsgKey === key && !force) return;
  if (document.querySelector("dialog.wfprompt")) return;
  wfMsgKey = key;
  const r = await modal("Workflow", h("div", {}, h("p", {class: "msg big"}, w.message), h("p", {class: "hint"}, "The workflow is paused until you continue.")),
    [{label: "Stop Workflow", danger: true, value: "stop"}, {label: "Continue", primary: true, value: "go"}], {noFocus: true, cls: "wfprompt"});
  if (r === "stop") await op("stop");
  else if (r === "go") await op("workflow_continue");
  // closed with Escape: the Workflow section's "Continue\u2026" button shows it again
}

function handleEvents() {
  for (const e of S.events || []) {
    if (e.id <= UI.lastEvent) continue;
    UI.lastEvent = e.id;
    if (e.type === "message") toast(e.text);
    else if (e.type === "error") alertDlg(e.text);
    else if (e.type === "print") doPrint(null);
  }
}

let pollTimer = null, polling = false;
async function poll() {
  if (polling) return;
  polling = true;
  try {
    const r = await (await fetch("/api/poll")).json();
    document.body.classList.remove("offline");
    if (r.rev !== S.rev) { await fetchState(); render(); handleEvents(); workflowPrompt(); }
    else if (S.workflow && ["running", "paused", "pausing", "message"].includes(S.workflow.status)) { renderStatus(); workflowPrompt(); }
  } catch (e) {
    document.body.classList.add("offline");
  } finally { polling = false; }
}

document.addEventListener("keydown", e => {
  if (!(e.ctrlKey || e.metaKey) || !S) return;
  if (document.querySelector("dialog[open]")) return;
  const typing = e.target.closest && e.target.closest("input, textarea, [contenteditable=true]");
  const k = e.key.toLowerCase();
  const run = fn => { e.preventDefault(); fn(); };
  if (k === "s") return run(() => cmdSave(false));
  if (k === "o") return run(cmdOpen);
  if (k === "p") return run(() => doPrint(null));
  if (k === "r") return run(() => S.reading ? op("stop") : (S.instrument.connected ? cmdRead() : alertDlg("No instrument connected. Choose the serial port in Preferences.")));
  if (typing) return;
  if (k === "z") return run(() => op(e.shiftKey ? "redo" : "undo"));
  if (k === "y") return run(() => op("redo"));
  if (k === "e") return run(() => op("all_open", {open: true}));
  if (k === "m") return run(() => op("all_open", {open: false}));
  if (k === "=") return run(() => op("recalculate"));
  if (k === "a" && activeSection() && activeSection().kind !== "notes") return run(cmdSelectAll);
  if (k === "c" && !window.getSelection().toString()) return run(cmdCopy);
  if (k === "v" && (activeOf("plate") || activeOf("group"))) return run(cmdPaste);
});
UI.narrow = window.innerWidth < 520;
window.addEventListener("resize", () => {
  const n = window.innerWidth < 520;
  if (n !== UI.narrow) { UI.narrow = n; cardCache.clear(); render(); }
});
window.addEventListener("beforeunload", e => { if (S && S.docs.some(d => d.dirty)) { e.preventDefault(); e.returnValue = ""; } });

(async function start() {
  await fetchState();
  UI.lastEvent = Math.max(0, ...(S.events || []).map(e => e.id));
  render();
  workflowPrompt();
  pollTimer = setInterval(poll, 700);
  if (!S.prefs.port) toast("No serial port chosen yet. Open Edit > Preferences, or pick the Simulator to try things out.");
})();
