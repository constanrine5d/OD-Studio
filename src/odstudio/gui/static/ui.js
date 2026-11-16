"use strict";
/* Core helpers: DOM builder, server calls, toasts, modal dialogs, file chooser. */

const ROWS = "ABCDEFGH";
const MOD = /Mac|iPhone|iPad/.test(navigator.platform) ? "\u2318" : "Ctrl+";
const WELLS = [];
for (let r = 0; r < 8; r++) for (let c = 1; c <= 12; c++) WELLS.push(ROWS[r] + c);

let S = null;                       // last state from the server
let CUR = null;                     // id of the window being rendered / acted on
const UI = {lastEvent: 0, printing: null, docs: {}, layout: "tabs"};
try { UI.layout = localStorage.getItem("odstudio.layout") || "tabs"; } catch (e) { /* private mode */ }
/* Selection and the active section are remembered per window. */
function docUI() { return UI.docs[CUR] || (UI.docs[CUR] = {active: null, sel: {}, cols: {}, sums: {}}); }
for (const k of ["active", "sel", "cols", "sums"]) Object.defineProperty(UI, k, {get: () => docUI()[k], set: v => { docUI()[k] = v; }});

/* Point S.doc, S.title, ... at the active window. */
function aliasActive() {
  const d = S.docs.find(x => x.id === S.active) || S.docs[0];
  S.active = d.id;
  for (const k of ["doc", "title", "path", "dirty", "can_undo", "can_redo", "suspended"]) S[k] = d[k];
  CUR = d.id;
}
function applyState(st, follow) {
  if (S && st.rev < S.rev) return;                  // an older reply arriving late
  const keep = S && !follow ? S.active : null;
  S = st;
  if (keep !== null && S.docs.some(d => d.id === keep)) S.active = keep;
  aliasActive();
}
function setActiveDoc(id) {
  if (S.active === id) return;
  S.active = id;
  aliasActive();
  fetch("/api/op", {method: "POST", headers: {"Content-Type": "application/json", "X-ODStudio": "1"},
                    body: JSON.stringify({op: "activate", args: {doc: id}})}).catch(() => {});
}
/* Run fn with S.doc / CUR pointing at window d (used to draw inactive windows). */
function withDoc(d, fn) {
  const saved = [S.doc, CUR];
  S.doc = d.doc; CUR = d.id;
  try { return fn(); } finally { [S.doc, CUR] = saved; }
}
const FOLLOW = new Set(["new", "open", "open_assay", "close"]);

function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const k in attrs || {}) {
    const v = attrs[k];
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(e.style, v);
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "html") e.innerHTML = v;
    else if (k in e && k !== "list" && typeof v !== "string") e[k] = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const kid of kids.flat(Infinity)) if (kid !== null && kid !== undefined && kid !== false) e.append(kid);
  return e;
}

/* replaceChildren that ignores null/false and flattens arrays */
function fill(el, ...kids) {
  el.replaceChildren(...kids.flat(Infinity).filter(k => k !== null && k !== undefined && k !== false));
  return el;
}

function toast(text, kind) {
  const t = h("div", {class: "toast " + (kind || ""), role: kind === "error" ? "alert" : "status"}, text);
  document.getElementById("toasts").append(t);
  setTimeout(() => t.remove(), kind === "error" ? 9000 : 3500);
}

async function fetchState() {
  const r = await fetch("/api/state");
  applyState(await r.json(), false);
  return S;
}

/* Run a command on the server.  Returns the result, or null if it failed
   (the error has already been shown). */
async function op(name, args, opts) {
  opts = opts || {};
  args = Object.assign({}, args || {});
  // An edit made inside a section belongs to that section's window, even if another
  // window became active in the meantime (e.g. a blur fired by clicking elsewhere).
  const t = window.event && window.event.target;
  const host = t && t.closest ? t.closest("[data-doc]") : null;
  if (host && !("doc" in args) && !t.closest("dialog")) args.doc = +host.dataset.doc;
  let r;
  try {
    const res = await fetch("/api/op", {method: "POST", headers: {"Content-Type": "application/json", "X-ODStudio": "1"},
                                        body: JSON.stringify({op: name, args: Object.assign({doc: S.active}, args || {})})});
    r = await res.json();
  } catch (e) {
    toast("The program is not responding. Is it still running?", "error");
    return null;
  }
  if (r.state) { applyState(r.state, FOLLOW.has(name) && r.ok); if (!opts.quiet) render(); if (typeof handleEvents === "function") handleEvents(); }
  if (!r.ok) {
    if (opts.onError) opts.onError(r.error); else alertDlg(r.error);
    return null;
  }
  const res = r.result || {};
  if (res.confirm) {
    if (await confirmDlg(res.confirm, opts.confirmLabel || "Replace")) return op(name, Object.assign({}, args, {confirm: true}), opts);
    return null;
  }
  return res;
}

/* ------------------------------------------------------------------ modals */
function modal(title, body, buttons, opts) {
  opts = opts || {};
  return new Promise(resolve => {
    const dlg = h("dialog", {class: "modal " + (opts.cls || "")});
    const done = v => { dlg.close(); dlg.remove(); resolve(v); };
    let busy = false;
    const err = h("div", {class: "dlg-error", role: "alert"});
    const foot = h("div", {class: "dlg-foot"}, err, h("span", {class: "spacer"}));
    for (const b of buttons) {
      foot.append(h("button", {class: b.primary ? "primary" : (b.danger ? "danger" : ""), type: "button",
        onclick: async e => {
          if (!b.action) return done(b.value === undefined ? null : b.value);
          if (busy) return;                        // ignore a second click while the first is working
          busy = true;
          const btn = e.currentTarget;
          btn.disabled = true;
          err.textContent = "";
          try {
            const v = await b.action();
            if (v !== undefined && v !== false) return done(v);
          } catch (ex) { err.textContent = ex.message || String(ex); }
          busy = false;
          btn.disabled = false;
        }}, b.label));
    }
    dlg.append(h("form", {method: "dialog", onsubmit: e => { e.preventDefault(); const p = foot.querySelector("button.primary"); if (p) p.click(); }},
      h("h2", {}, title), h("div", {class: "dlg-body"}, body), foot));
    dlg.addEventListener("cancel", e => { e.preventDefault(); done(null); });
    dlg.setError = t => { err.textContent = t || ""; };
    body.dlg = dlg;
    document.getElementById("modals").append(dlg);
    dlg.showModal();
    const first = dlg.querySelector("[autofocus]") || dlg.querySelector("input:not([type=checkbox]):not([type=radio]), select, textarea");
    if (first && !opts.noFocus) { first.focus(); if (first.select) first.select(); }
  });
}

function alertDlg(text) {
  return modal("OD Studio", h("p", {class: "msg"}, text), [{label: "OK", primary: true, value: true}], {noFocus: true});
}

function confirmDlg(text, yes, danger) {
  return modal("OD Studio", h("p", {class: "msg"}, text),
    [{label: "Cancel", value: false}, {label: yes || "OK", primary: !danger, danger: !!danger, value: true}], {noFocus: true});
}

/* Call op from inside a dialog button: errors appear in the dialog, which stays open. */
async function dlgOp(name, args, confirmLabel) {
  let msg = null;
  const r = await op(name, args, {onError: m => { msg = m; }, confirmLabel});
  if (r === null) { if (msg) throw new Error(msg); return false; }
  return r || true;
}

/* form helpers */
function field(label, control, hint) {
  return h("label", {class: "field"}, h("span", {class: "lbl"}, label), control, hint ? h("span", {class: "hint"}, hint) : null);
}
function numInput(value, attrs) {
  return h("input", Object.assign({type: "number", value: value === null || value === undefined ? "" : value, step: "any"}, attrs || {}));
}
function textInput(value, attrs) {
  return h("input", Object.assign({type: "text", value: value === null || value === undefined ? "" : value, spellcheck: false, autocomplete: "off"}, attrs || {}));
}
function check(label, checked, attrs) {
  const box = h("input", Object.assign({type: "checkbox", checked: !!checked}, attrs || {}));
  const el = h("label", {class: "check"}, box, " " + label);
  el.box = box;
  return el;
}
function select(options, value, attrs) {
  const s = h("select", attrs || {});
  for (const o of options) {
    const [v, label] = Array.isArray(o) ? o : [o, o];
    s.append(h("option", {value: v, selected: String(v) === String(value)}, label));
  }
  return s;
}
function radios(name, options, value, onchange) {
  const box = h("div", {class: "radios", role: "radiogroup"});
  for (const [v, label] of options) {
    box.append(h("label", {class: "check"}, h("input", {type: "radio", name, value: v, checked: v === value, onchange: () => onchange && onchange(v)}), " " + label));
  }
  box.value = () => (box.querySelector("input:checked") || {}).value;
  return box;
}
function numOrNull(el) { const v = el.value.trim(); return v === "" ? null : Number(v); }

function parseTime(text) {
  text = String(text).trim();
  if (text === "") return NaN;
  const parts = text.split(":").map(Number);
  if (parts.some(p => !Number.isFinite(p))) return NaN;
  return parts.reduce((a, b) => a * 60 + b, 0);
}
function fmtTime(sec) {
  sec = Math.round(sec);
  const hh = Math.floor(sec / 3600), mm = Math.floor(sec % 3600 / 60), ss = sec % 60;
  return (hh ? hh + ":" + String(mm).padStart(2, "0") : mm) + ":" + String(ss).padStart(2, "0");
}
/* Mode of a plate's data (a workflow cycle turns endpoint reads into kinetic data). */
function dataMode(p) { return (p.info && p.info.mode) || p.settings.mode; }
function fmtNum(v, d) { return v === null || v === undefined ? "" : Number(v).toFixed(d === undefined ? 3 : d); }

/* ------------------------------------------------------------ file chooser */
/* mode: "open" | "save" | "folder".  Resolves to a full path or null. */
async function fileDialog(opts) {
  const mode = opts.mode || "open";
  let cur = null;
  const list = h("div", {class: "filelist", role: "listbox", tabindex: "0"});
  const pathBox = textInput("", {class: "pathbox", "aria-label": "Folder"});
  const nameBox = textInput(opts.name || "", {"aria-label": "File name", placeholder: "File name"});
  const roots = h("div", {class: "roots"});
  const exts = opts.exts || null;
  const showAll = check("Show all files", false);
  const join = (dir, name) => dir.endsWith(cur.sep) ? dir + name : dir + cur.sep + name;
  let pick = null;

  async function go(path) {
    const r = await op("list_dir", {path}, {quiet: true, onError: m => { body.dlg && body.dlg.setError(m); }});
    if (!r) return;
    cur = r;
    pathBox.value = r.path;
    fill(roots, ...r.roots.map(p => h("button", {type: "button", class: "chip", onclick: () => go(p)}, p.split(/[\\/]/).filter(Boolean).pop() || p)));
    fill(list);
    if (r.parent) list.append(h("div", {class: "fl-item dir", ondblclick: () => go(r.parent), onclick: e => mark(e.currentTarget, null)}, "↑  .."));
    for (const d of r.dirs) list.append(h("div", {class: "fl-item dir", ondblclick: () => go(join(r.path, d)), onclick: e => mark(e.currentTarget, null)}, "📁  " + d));
    if (mode !== "folder") {
      for (const f of r.files) {
        const okExt = !exts || showAll.box.checked || exts.some(x => f.name.toLowerCase().endsWith(x));
        if (!okExt) continue;
        list.append(h("div", {class: "fl-item", onclick: e => { mark(e.currentTarget, f.name); nameBox.value = f.name; },
          ondblclick: () => { nameBox.value = f.name; body.dlg.querySelector("button.primary").click(); }},
          h("span", {}, f.name), h("span", {class: "fl-meta"}, new Date(f.mtime * 1000).toLocaleDateString() + "  " + (f.size > 1e6 ? (f.size / 1e6).toFixed(1) + " MB" : Math.ceil(f.size / 1024) + " KB"))));
      }
    }
    if (!list.children.length) list.append(h("div", {class: "fl-empty"}, "This folder is empty."));
  }
  function mark(el, name) { for (const x of list.children) x.classList.remove("on"); el.classList.add("on"); pick = name; }
  pathBox.addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); go(pathBox.value); } });
  showAll.box.addEventListener("change", () => go(cur.path));

  const body = h("div", {class: "filedlg"},
    h("div", {class: "row"}, pathBox, h("button", {type: "button", onclick: async () => {
      const name = await promptDlg("New folder", "Folder name:", "");
      if (name) { await op("make_dir", {path: join(cur.path, name)}, {quiet: true}); go(join(cur.path, name)); }
    }}, "New folder")),
    roots, list,
    mode === "folder" ? null : h("div", {class: "row"}, nameBox, exts ? showAll : null));
  const p = modal(opts.title || "Open", body, [
    {label: "Cancel", value: null},
    {label: opts.button || (mode === "save" ? "Save" : mode === "folder" ? "Choose this folder" : "Open"), primary: true, action: async () => {
      if (mode === "folder") return cur.path;
      const name = nameBox.value.trim();
      if (!name) throw new Error("Choose a file or type a name.");
      const full = join(cur.path, name);
      if (mode === "save") {
        const withExt = opts.ext && !/\.[A-Za-z0-9]+$/.test(name) ? name + opts.ext : name;
        if (cur.files.some(f => f.name.toLowerCase() === withExt.toLowerCase()) &&
            !(await confirmDlg(`“${withExt}” already exists. Replace it?`, "Replace", true))) return false;
      }
      return full;
    }}], {cls: "wide"});
  await go(opts.dir || "");
  return p;
}

function promptDlg(title, label, value) {
  const box = textInput(value);
  return modal(title, h("div", {}, field(label, box)), [{label: "Cancel", value: null}, {label: "OK", primary: true, action: () => box.value.trim() || false}]);
}

/* -------------------------------------------------------------- dropdowns */
let openMenu = null;
function closeMenus() { if (openMenu) { openMenu.remove(); openMenu = null; document.querySelectorAll(".menu-title.on").forEach(e => e.classList.remove("on")); } }
document.addEventListener("click", e => { if (!e.target.closest(".menu-title")) closeMenus(); });
document.addEventListener("keydown", e => { if (e.key === "Escape") closeMenus(); });

function showMenu(anchor, items) {
  closeMenus();
  const m = h("div", {class: "menu", role: "menu"});
  for (const it of items) {
    if (!it) continue;
    if (it === "-") { m.append(h("div", {class: "menu-sep"})); continue; }
    m.append(h("button", {type: "button", class: "menu-item", role: "menuitem", disabled: it.enabled === false,
      onclick: () => { closeMenus(); it.action(); }},
      h("span", {class: "mi-check"}, it.checked ? "✓" : ""), h("span", {class: "mi-label"}, it.label), h("span", {class: "mi-key"}, it.key || "")));
  }
  const r = anchor.getBoundingClientRect();
  m.style.left = Math.min(r.left, window.innerWidth - 280) + "px";
  m.style.top = r.bottom + "px";
  document.body.append(m);
  anchor.classList.add("on");
  openMenu = m;
}
