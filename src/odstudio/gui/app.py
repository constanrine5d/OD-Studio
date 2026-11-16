"""Application state behind the GUI: the open document, the instrument, preferences.

Every user action is an ``op`` (a method named ``op_<name>``) invoked through
:meth:`App.op`.  The front end is a thin view on :meth:`App.state`.
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .. import __version__
from .. import document as D
from .. import exports, textio
from . import workflow as W
from ..driver import ReadSettings, SpectraMax
from ..errors import SpectraMaxError
from ..fits import FIT_KINDS, FIT_LABELS
from ..formula import FUNCTIONS, check as check_formula
from ..readers import READER_KINDS, open_reader, unsupported
from ..tmax import min_interval as tmax_min_interval
from ..transport import list_ports

DATA_EXT = ".odx"
OLD_DATA_EXT = ".smx"           # before the rename to OD Studio; still opened
LIBRARY_DIR = Path(__file__).resolve().parent.parent / "protocols"
PROTOCOL_EXT = ".odxp"
OLD_PROTOCOL_EXT = ".smxp"
PROTOCOL_EXTS = (PROTOCOL_EXT, OLD_PROTOCOL_EXT)
DOCUMENT_EXTS = (DATA_EXT, PROTOCOL_EXT, OLD_DATA_EXT, OLD_PROTOCOL_EXT)
UNDO_LIMIT = 30
HISTORY_LIMIT = 500
LOG_MAX_BYTES = 2_000_000


def _plate_name(app, a) -> str:
    try:
        return app.sec(a.get("id"))["name"]
    except Exception:
        return "a plate"


# What goes into the activity log after a command succeeds: op name -> text(app, args, result).
LOGGED = {
    "new": lambda app, a, r: "New experiment window",
    "open": lambda app, a, r: f"Opened {a.get('path')}",
    "open_assay": lambda app, a, r: f"Opened protocol {a.get('name')} from the assays folder",
    "open_library": lambda app, a, r: f"Opened library protocol {a.get('name')}",
    "save": lambda app, a, r: (f"Saved {'protocol ' if a.get('protocol') else ''}{(r or {}).get('path')}"
                               if (r or {}).get("path") else None),
    "revert": lambda app, a, r: "Reverted to the saved file",
    "close": lambda app, a, r: "Closed an experiment window",
    "export": lambda app, a, r: f"Exported data to {(r or {}).get('path')}",
    "import": lambda app, a, r: f"Imported {a.get('path')} into {_plate_name(app, a)}",
    "instrument_settings": lambda app, a, r: (None if (r or {}).get("confirm") else
                                             f"Instrument settings of {_plate_name(app, a)} changed: "
                                             + D.settings_summary(app.sec(a.get('id'))["settings"])),
    "reduction": lambda app, a, r: f"Reduction of {_plate_name(app, a)} changed",
    "mask": lambda app, a, r: f"Masked/unmasked wells {', '.join(a.get('wells') or [])} on {_plate_name(app, a)}",
    "template_assign": lambda app, a, r: (f"Template of {_plate_name(app, a)}: {a.get('type', 'sample')} "
                                          f"{a.get('group') or ''} {', '.join(a.get('wells') or [])}"),
    "template_series": lambda app, a, r: f"Template of {_plate_name(app, a)}: series in {a.get('group')}",
    "paste_template": lambda app, a, r: f"Template pasted into {_plate_name(app, a)}",
    "paste_data": lambda app, a, r: None if (r or {}).get("confirm") else f"Data pasted into {_plate_name(app, a)}",
    "clear_data": lambda app, a, r: f"Data of {_plate_name(app, a)} deleted",
    "delete_section": lambda app, a, r: "Deleted a section",
    "add_section": lambda app, a, r: f"Added a {a.get('kind')} section",
    "column_save": lambda app, a, r: f"Column {a.get('name') or ''} of {a.get('group')} set to {a.get('formula')}",
    "summary_save": lambda app, a, r: f"Summary {a.get('name') or ''} set to {a.get('formula')}",
    "graph": lambda app, a, r: "Graph settings changed",
    "workflow_save": lambda app, a, r: "Workflow changed",
    "prefs": lambda app, a, r: "Preferences changed",
    "connect": lambda app, a, r: f"Connected to {app.inst.info.get('model') or 'instrument'} on {app.inst.info.get('port')}",
    "disconnect": lambda app, a, r: "Disconnected from the instrument",
    "drawer": lambda app, a, r: f"Drawer {'opened' if app.inst.info.get('drawer', '').startswith('OPEN') else 'closed'}",
    "incubator": lambda app, a, r: (f"Incubator set to {float(a.get('temperature', 0)):.1f} \u00b0C"
                                    if a.get("on") else "Incubator switched off"),
    "shake": lambda app, a, r: "Shaking started" if app.inst.info.get("shaking") else "Shaking stopped",
    "raw_command": lambda app, a, r: f"Sent command {a.get('command')!r}",
    "stop": lambda app, a, r: "Stop pressed",
}


def _wavelength(w):
    """405 -> 405; "F405" (an installed filter) stays a string."""
    t = str(w).strip().upper()
    if t.startswith("F"):
        return "F" + str(int(float(t[1:])))
    return int(float(t))


class UserError(Exception):
    """Something to show the user as a message, not a crash."""


class _Stopped(BaseException):
    pass


def config_dir() -> Path:
    base = os.environ.get("ODSTUDIO_HOME") or os.environ.get("SPECTRAMAX_HOME")
    p = Path(base) if base else Path.home() / ".odstudio"
    if not base and not p.exists():
        old = Path.home() / ".spectramax"          # settings from before the rename to OD Studio
        if old.is_dir():
            try:
                shutil.copytree(old, p)
            except OSError:
                pass
    p.mkdir(parents=True, exist_ok=True)
    return p


def default_prefs() -> dict:
    docs = Path.home() / "Documents" / "OD Studio"
    return {
        "port": "", "reader": "auto", "autoconnect": True,
        "autosave": False, "autosave_folder": str(docs), "autosave_prefix": "Data",
        "autoprint": False,
        "default_protocol": False, "default_protocol_path": "",
        "print_template": False, "print_page_number": True, "print_time": True,
        "print_filename": True,
        "export_layout": "time", "export_labels": True,
        "auto_export": False, "auto_export_folder": str(docs / "Exports"),
        "auto_export_name": "{experiment}_{plate}_{date}_{time}", "auto_export_format": "txt",
        "auto_export_content": "raw", "auto_export_overwrite": False, "auto_export_counter": 0,
        "assays_folder": str(docs / "Assays"),
        "wavelength_presets": [260, 280, 405, 450, 490, 650], "filters": [],
        "open_drawer_after_read": True, "show_status": True, "last_dir": str(docs),
    }


class Instrument:
    """Thread-safe wrapper around the driver with a background status poll."""

    def __init__(self, changed: Callable[[], None]):
        self.changed = changed
        self.on_lost: Optional[Callable[[str], None]] = None
        self.lock = threading.RLock()
        self.sm: Optional[SpectraMax] = None
        self.reader_options: Callable[[], dict] = lambda: {}
        self.info = {"connected": False, "port": "", "model": "", "version": "", "error": "",
                     "temperature": None, "setpoint": 0.0, "drawer": "", "state": "",
                     "options": None, "shaking": False}
        self.reading: Optional[dict] = None
        self.stop_flag = threading.Event()
        self._alive = True
        threading.Thread(target=self._poll, daemon=True).start()

    def connect(self, port: str) -> None:
        self.disconnect()
        if not port:
            return
        with self.lock:
            try:
                sm = open_reader(port, display=("Remote Control", ""), **self.reader_options())
            except SpectraMaxError as e:
                self.info.update(connected=False, port=port, error=str(e))
                self.changed()
                raise UserError(str(e)) from None
            info = sm.info
            self.sm = sm
            caps = sm.capabilities
            self.info.update(connected=True, port=port, model=info.model or caps["model"],
                             version=info.version, error="", shaking=False, options=caps)
            self._refresh()
        self.changed()

    def disconnect(self) -> None:
        with self.lock:
            if self.sm is not None:
                try:
                    self.sm.disconnect()
                except Exception:
                    pass
            self.sm = None
            self.info.update(connected=False, temperature=None, drawer="", state="", error="")
        self.changed()

    def _refresh(self) -> None:
        setpoint, actual = self.sm.temperature()
        st = self.sm.status()
        self.info.update(temperature=actual, setpoint=setpoint, drawer=st.drawer, state=st.state)

    def _poll(self) -> None:
        while self._alive:
            time.sleep(2.0)
            if self.sm is None or self.reading:
                continue
            if not self.lock.acquire(timeout=0.1):
                continue
            try:
                if self.sm is None:
                    continue
                before = dict(self.info)
                try:
                    self._refresh()
                except SpectraMaxError as e:
                    self.info.update(connected=False, error=f"Lost contact with the instrument: {e}")
                    if self.on_lost:
                        self.on_lost(str(e))
                    try:
                        self.sm.disconnect()
                    except Exception:
                        pass
                    self.sm = None
                if before != self.info:
                    self.changed()
            finally:
                self.lock.release()

    def check(self, rs: ReadSettings) -> None:
        """Refuse a read the connected reader cannot do; drop extras it lacks (shaking, calibration)."""
        if self.sm is not None:
            caps = self.sm.capabilities
            why = unsupported(caps, rs)
            if why:
                raise UserError(why)
            if not caps["shake"]:
                rs.shake_before = rs.shake_between = 0
            if not caps["calibrate"]:
                rs.autocalibrate = False

    def can(self, what: str) -> bool:
        return self.sm is None or bool(self.sm.capabilities.get(what))

    def need(self) -> SpectraMax:
        if self.sm is None:
            raise UserError("No instrument connected. Choose the serial port in Preferences.")
        if self.reading:
            raise UserError("The instrument is busy reading a plate.")
        return self.sm

    def call(self, fn: Callable):
        self.need()                                   # refuse at once while a plate is being read
        if not self.lock.acquire(timeout=10):
            raise UserError("The instrument is busy. Try again in a moment.")
        try:
            sm = self.need()
            try:
                r = fn(sm)
                self._refresh()
            except SpectraMaxError as e:
                raise UserError(str(e)) from None
        finally:
            self.lock.release()
        self.changed()
        return r


class DocState:
    """One open experiment window."""

    def __init__(self, did: int, doc: dict, path: Optional[str]):
        self.id = did
        self.doc = doc
        self.path = path
        self.dirty = False
        self.undo: list = []
        self.redo: list = []
        self.suspended = False
        self.rev = 1
        self.view = None
        self.view_rev = 0
        self.origin = ""          # name of an imported file, until saved
        self.history: list = list(doc.pop("history", []) or [])   # what happened to this experiment

    @property
    def title(self) -> str:
        if self.path:
            return os.path.basename(self.path)
        return self.origin or "Untitled"

    @property
    def pristine(self) -> bool:
        return (not self.path and not self.dirty and not self.undo and
                not any(s.get("data") for s in self.doc["sections"] if s["kind"] == "plate"))


def _delegate(name):
    return property(lambda self: getattr(self.cur, name),
                    lambda self, v: setattr(self.cur, name, v))


class App:
    # The current window is chosen per request (thread-local), so code below can
    # keep using self.doc / self.path / ... for "the window this request is about".
    doc = _delegate("doc")
    path = _delegate("path")
    dirty = _delegate("dirty")
    undo = _delegate("undo")
    redo = _delegate("redo")
    suspended = _delegate("suspended")

    def __init__(self, port: Optional[str] = None, load_prefs: bool = True):
        self.lock = threading.RLock()
        self.rev = 1
        self.events: list = []
        self._event_id = 0
        self.prefs = default_prefs()
        self._persist = load_prefs
        if load_prefs:
            try:
                self.prefs.update(json.loads((config_dir() / "prefs.json").read_text()))
            except (OSError, ValueError):
                pass
        if port is not None:
            self.prefs["port"] = port
        self._tl = threading.local()
        self.docs: dict = {}
        self.order: list = []
        self.active: Optional[int] = None
        self._next_doc = 1
        self.clip: dict = {}
        self.inst = Instrument(self._bump)
        self.inst.reader_options = lambda: {"kind": self.prefs.get("reader", "auto"),
                                            "filters": self.prefs.get("filters") or []}
        self.inst.on_lost = lambda msg: self.activity(f"Lost contact with the instrument: {msg}")
        self.workflow = W.Runner(self)
        self.activity(f"OD Studio {__version__} started")
        self._new_from_default(new_window=True)
        if self.prefs["port"] and self.prefs.get("autoconnect", True):
            threading.Thread(target=self._autoconnect, daemon=True).start()

    # ------------------------------------------------------------ plumbing
    def _bump(self) -> None:
        self.rev += 1

    @property
    def cur(self) -> DocState:
        ds = getattr(self._tl, "cur", None)
        if ds is not None and ds.id in self.docs:
            return ds
        return self.docs[self.active]

    def _touch(self, ds: Optional[DocState] = None) -> None:
        (ds or self.cur).rev += 1
        self._bump()

    def _autoconnect(self) -> None:
        try:
            self.inst.connect(self.prefs["port"])
        except UserError:
            pass

    def event(self, kind: str, text: str = "", **kw) -> None:
        self._event_id += 1
        self.events.append({"id": self._event_id, "type": kind, "text": text, **kw})
        self.events = self.events[-20:]
        self._bump()

    def activity(self, text: str, ds: Optional[DocState] = None) -> None:
        """Record what the program did: in the activity log file and the experiment's history."""
        if not text:
            return
        now = time.time()
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        if ds is not None:
            ds.history.append({"t": now, "text": text})
            del ds.history[:-HISTORY_LIMIT]
            where = f"[{ds.title}] "
        else:
            where = ""
        try:
            log = config_dir() / "activity.log"
            if log.exists() and log.stat().st_size > LOG_MAX_BYTES:
                os.replace(log, config_dir() / "activity.1.log")
            with open(log, "a", encoding="utf-8") as f:
                f.write(f"{stamp}  {where}{text}\n")
        except OSError:
            pass

    def op_activity_log(self, lines: int = 400):
        log = config_dir() / "activity.log"
        try:
            text = log.read_text(encoding="utf-8").splitlines()[-int(lines):]
        except OSError:
            text = []
        return {"path": str(log), "lines": text}

    def save_prefs(self) -> None:
        if self._persist:
            try:
                (config_dir() / "prefs.json").write_text(json.dumps(self.prefs, indent=1))
            except OSError:
                pass

    def edit(self, fn: Callable, undoable: bool = True):
        """Run a document mutation with undo support."""
        with self.lock:
            snap = json.dumps(self.doc)
            try:
                r = fn()
            except BaseException:
                self.doc = json.loads(snap)          # leave nothing half-changed
                raise
            if undoable:
                self.undo.append(snap)
                del self.undo[:-UNDO_LIMIT]
                self.redo.clear()
            self.dirty = True
            self._touch()
            return r

    def sec(self, sid, kind: Optional[str] = None) -> dict:
        try:
            s = D.section_by_id(self.doc, int(sid))
        except (KeyError, TypeError, ValueError):
            raise UserError("That section no longer exists.") from None
        if kind and s["kind"] != kind:
            raise UserError(f"That is not a {kind} section.")
        return s

    def view(self, ds: Optional[DocState] = None) -> dict:
        ds = ds or self.cur
        with self.lock:
            if ds.view is None or (ds.view_rev != ds.rev and not ds.suspended):
                ds.view = D.compute(ds.doc)
                ds.view_rev = ds.rev
            return ds.view

    def title(self) -> str:
        return self.cur.title

    def poll(self) -> dict:
        return {"rev": self.rev, "instrument": self.inst.info, "reading": self.inst.reading,
                "workflow": self.workflow.state, "events": self.events}

    def state(self) -> dict:
        with self.lock:
            docs = [{"id": ds.id, "doc": self.view(ds), "title": ds.title, "path": ds.path,
                     "dirty": ds.dirty, "can_undo": bool(ds.undo), "can_redo": bool(ds.redo),
                     "suspended": ds.suspended, "history": ds.history[-200:]}
                    for ds in (self.docs[i] for i in self.order)]
            act = next(d for d in docs if d["id"] == self.active)
            return {"rev": self.rev, "docs": docs, "active": self.active,
                    # the active window's fields, kept for convenience
                    "doc": act["doc"], "title": act["title"], "path": act["path"], "dirty": act["dirty"],
                    "can_undo": act["can_undo"], "can_redo": act["can_redo"],
                    "suspended": act["suspended"], "prefs": self.prefs, "instrument": self.inst.info,
                    "reading": self.inst.reading, "workflow": self.workflow.state, "events": self.events,
                    "version": __version__,
                    "clip": sorted(self.clip), "assays": self._assays(), "library": self._library(),
                    "fits": [[k, FIT_LABELS[k]] for k in FIT_KINDS], "functions": FUNCTIONS,
                    "weightings": [[k, v] for k, v in D.WEIGHTINGS.items()],
                    "kinetic_kinds": [[k, v[0], v[1]] for k, v in D.KINETIC_KINDS.items()],
                    "spectrum_kinds": [[k, v[0], v[1]] for k, v in D.SPECTRUM_KINDS.items()],
                    "group_kinds": list(D.GROUP_KINDS)}

    def op(self, name: str, args: dict):
        fn = getattr(self, "op_" + name, None)
        if fn is None:
            raise UserError(f"Unknown command {name!r}")
        args = dict(args or {})
        did = args.pop("doc", None)
        if did is not None and int(did) not in self.docs:
            raise UserError("That window has been closed.")
        self._tl.cur = self.docs[int(did)] if did is not None else None
        try:
            before = self.cur
            result = fn(**args)
            if name in LOGGED:
                try:
                    text = LOGGED[name](self, args, result)
                except Exception:
                    text = None
                if text:
                    self.activity(text, None if name in ("prefs", "connect", "disconnect") else
                                  (self.cur if name in ("new", "open", "open_assay", "open_library", "revert") else
                                   (before if before.id in self.docs else None)))
            return result
        except (ValueError, KeyError) as e:
            raise UserError(str(e).strip("'\"") or "Invalid value") from None
        except SpectraMaxError as e:
            raise UserError(str(e)) from None
        except OSError as e:
            raise UserError(f"{e.strerror or e}: {e.filename or ''}".strip(": ")) from None

    # ---------------------------------------------------------------- files
    def _load(self, doc: dict, path: Optional[str], new_window: Optional[bool] = None) -> DocState:
        """Show ``doc`` in a window: a new one, or the current one if it is still empty."""
        if doc.get("format") not in D.FORMATS:
            raise UserError("This is not an OD Studio document.")
        doc["format"] = D.FORMAT
        if doc.get("version", 1) > D.VERSION:
            raise UserError("The file could not be opened because it was written with a later version.")
        for sec in doc.get("sections", []):
            if sec.get("kind") == "notes":          # files are untrusted: notes are shown as HTML
                sec["html"] = sanitize_html(str(sec.get("html") or ""))
        with self.lock:
            if new_window is None:
                new_window = not (self.active in self.docs and self.cur.pristine)
            if new_window:
                ds = DocState(self._next_doc, doc, path)
                self._next_doc += 1
                self.docs[ds.id] = ds
                pos = self.order.index(self.cur.id) + 1 if self.active in self.docs else len(self.order)
                self.order.insert(pos, ds.id)
            else:
                ds = self.cur
                ds.history = list(doc.pop("history", []) or [])
                ds.doc, ds.path, ds.dirty = doc, path, False
                ds.undo.clear()
                ds.redo.clear()
                ds.view = None
            self.active = ds.id
            self._tl.cur = ds
            self._touch(ds)
            return ds

    def _new_from_default(self, new_window: Optional[bool] = None) -> None:
        doc = None
        p = self.prefs.get("default_protocol_path")
        if self.prefs.get("default_protocol") and p and os.path.isfile(p):
            try:
                doc = D.strip_data(json.loads(Path(p).read_text()))
            except (OSError, ValueError):
                doc = None
        self._load(doc or D.new_document(), None, new_window)

    def _busy(self) -> None:
        if self.inst.reading:
            raise UserError("Can't do that while reading a plate.")

    def op_new(self):
        self._new_from_default(new_window=True)
        return {"doc": self.active}

    def op_close(self, force: bool = False):
        ds = self.cur
        r = self.inst.reading
        if r and r.get("doc") == ds.id:
            raise UserError("Can't close the window while reading a plate.")
        with self.lock:
            i = self.order.index(ds.id)
            self.order.remove(ds.id)
            del self.docs[ds.id]
            self._tl.cur = None
            if not self.order:
                self.active = None
                self._new_from_default(new_window=True)
            else:
                self.active = self.order[max(0, i - 1)] if self.active == ds.id else self.active
            self._bump()

    def op_activate(self):
        self.active = self.cur.id
        self._bump()

    def op_move_window(self, index: int):
        ds = self.cur
        self.order.remove(ds.id)
        self.order.insert(max(0, min(int(index), len(self.order))), ds.id)
        self._bump()

    def op_open(self, path: str, as_protocol: bool = False):
        ext = os.path.splitext(path)[1].lower()
        if ext in (".pda", ".ppr"):
            try:
                from .. import legacy
            except ImportError:
                raise UserError("Files saved by the original SOFTmax PRO cannot be opened by this "
                                "version. Open one of its exported .txt files instead.") from None
            try:
                info = legacy.read_pda(path)
            except ValueError as e:
                raise UserError(f"{os.path.basename(path)}: {e}") from None
            doc = D.document_from_legacy(info)
            if ext == ".ppr" or as_protocol:
                doc = D.strip_data(doc)
            self._load(doc, None)
            self.cur.origin = os.path.splitext(os.path.basename(path))[0] + " (SOFTmax PRO)"
            self.dirty = True
            self._touch()
            notes = list(info.get("warnings") or []) + list(doc.pop("import_notices", []))
            if notes:
                self.event("message", f"Opened {os.path.basename(path)}. Note: " + "; ".join(notes[:3]) + ".")
            self.prefs["last_dir"] = os.path.dirname(os.path.abspath(path))
            self.save_prefs()
            return
        if ext in (".dat", ".pro"):
            raise UserError("Files from the older DOS SOFTmax cannot be opened. Export them as text first.")
        if ext in (".txt", ".tsv", ".csv"):
            data = textio.import_text(self._read_text(path))
            doc = D.new_document()
            plate = doc["sections"][0]
            self._apply_import(plate, data)
            plate["name"] = re.sub(r"[^\w#. -]", "", os.path.splitext(os.path.basename(path))[0]) or plate["name"]
            self._load(doc, None)
            self.cur.origin = os.path.basename(path)
            self.dirty = True
            self._touch()
        else:
            doc = json.loads(Path(path).read_text(encoding="utf-8"))
            is_protocol = ext in PROTOCOL_EXTS or as_protocol
            self._load(D.strip_data(doc) if is_protocol else doc, None if is_protocol else path)
        self.prefs["last_dir"] = os.path.dirname(os.path.abspath(path))
        self.save_prefs()

    @staticmethod
    def _read_text(path: str) -> str:
        raw = Path(path).read_bytes()
        for enc in ("utf-8", "mac_roman"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("latin-1")

    @staticmethod
    def _apply_import(plate: dict, data: dict) -> None:
        st = plate["settings"]
        st["mode"] = data["mode"]
        st["first_col"], st["last_col"] = data["first_col"], data["last_col"]
        st["columns"] = None
        n = len(data["values"])
        if data["mode"] == "spectrum" and n:
            st["start"], st["stop"] = data["sweep"][0], data["sweep"][-1]
            st["step"] = (data["sweep"][1] - data["sweep"][0]) if n > 1 else 10
        else:
            nw = len(data["values"][0])
            wls = (st["wavelengths"] + [405] * 6)[:nw]
            st["wavelengths"] = wls
            data["wavelengths"] = wls
            if data["mode"] == "kinetic" and n > 1:
                st["interval"] = max(1, int(round(data["times"][1] - data["times"][0])))
                st["run_time"] = int(round(data["times"][-1]))
        plate["data"] = data
        plate["preread"] = None
        plate["mask"] = []

    def _write(self, path: str, protocol: bool) -> str:
        root, ext = os.path.splitext(path)
        want = PROTOCOL_EXT if protocol else DATA_EXT
        if ext.lower() != want:
            path = root + (ext if ext.lower() not in DOCUMENT_EXTS else "") + want
        with self.lock:
            doc = D.strip_data(self.doc) if protocol else dict(self.doc, history=self.cur.history[-HISTORY_LIMIT:])
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            tmp = path + ".tmp"
            Path(tmp).write_text(json.dumps(doc), encoding="utf-8")
            os.replace(tmp, path)
        return path

    def op_save(self, path: Optional[str] = None, protocol: bool = False):
        path = path or self.path
        if not path:
            return {"need_path": True}
        path = self._write(path, protocol)
        if not protocol:
            self.path, self.dirty = path, False
        self.prefs["last_dir"] = os.path.dirname(os.path.abspath(path))
        self.save_prefs()
        self.event("message", f"Saved {os.path.basename(path)}")
        return {"path": path}

    def op_revert(self):
        """File > Revert: reload the last saved version of this window's file."""
        self._busy_here()
        if not self.path:
            raise UserError("This experiment has not been saved yet.")
        doc = json.loads(Path(self.path).read_text(encoding="utf-8"))
        self._load(doc, self.path, new_window=False)

    def _export_items(self, ds: DocState, plates: list) -> list:
        view = self.view(ds)
        views = {s["id"]: s for s in view["sections"]}
        return [(p, views[p["id"]], ds.title) for p in plates if p["id"] in views]

    def op_export(self, path: str, id=None, layout: Optional[str] = None, labels: Optional[bool] = None,
                  format: Optional[str] = None, content: Optional[str] = None):
        plates = [self.sec(id, "plate")] if id else [s for s in self.doc["sections"] if s["kind"] == "plate"]
        plates = [p for p in plates if p.get("data")]
        if not plates:
            raise UserError("There is no data to export.")
        layout = layout or self.prefs["export_layout"]
        labels = self.prefs["export_labels"] if labels is None else labels
        fmt = format or os.path.splitext(path)[1].lstrip(".").lower() or "txt"
        if fmt not in exports.FORMATS:
            fmt = "txt"
        path = exports.export(path, self._export_items(self.cur, plates), fmt, content or "raw", layout, labels)
        self.event("message", f"Exported {os.path.basename(path)}")
        return {"path": path}

    def _auto_export(self, ds: DocState, plate: dict) -> str:
        P = self.prefs
        folder = P.get("auto_export_folder") or P["autosave_folder"]
        fmt = P.get("auto_export_format") or "txt"
        name = exports.expand_name(P.get("auto_export_name") or exports.DEFAULT_NAME, experiment=ds.title,
                                   plate=plate["name"], mode=plate["settings"]["mode"],
                                   n=self._export_counter(folder))
        path = exports.unique_path(folder, name, exports.FORMATS[fmt], bool(P.get("auto_export_overwrite")))
        return exports.export(path, self._export_items(ds, [plate]), fmt, P.get("auto_export_content") or "raw",
                              P.get("export_layout") or "time", P.get("export_labels", True))

    def _export_counter(self, folder: str) -> int:
        self.prefs["auto_export_counter"] = int(self.prefs.get("auto_export_counter") or 0) + 1
        self.save_prefs()
        return self.prefs["auto_export_counter"]

    def op_import(self, id, path: str):
        plate = self.sec(id, "plate")
        self._busy_plate(plate)
        data = textio.import_text(self._read_text(path))
        self.edit(lambda: self._apply_import(plate, data))

    def op_list_dir(self, path: str = ""):
        if not path:
            path = self.prefs.get("last_dir") or str(Path.home())
        p = Path(path).expanduser()
        if not p.is_dir():
            p = Path.home()
        dirs, files = [], []
        try:
            for e in sorted(p.iterdir(), key=lambda x: x.name.lower()):
                if e.name.startswith("."):
                    continue
                try:
                    if e.is_dir():
                        dirs.append(e.name)
                    else:
                        st = e.stat()
                        files.append({"name": e.name, "size": st.st_size, "mtime": st.st_mtime})
                except OSError:
                    continue
        except OSError as e:
            raise UserError(f"Cannot read that folder: {e.strerror}") from None
        roots = [str(Path.home()), str(Path.home() / "Documents"), str(Path.home() / "Desktop")]
        if sys.platform.startswith("win"):
            roots += [f"{c}:\\" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.exists(f"{c}:\\")]
        else:
            roots += ["/Volumes"] if os.path.isdir("/Volumes") else []
        return {"path": str(p), "parent": str(p.parent) if p.parent != p else "",
                "dirs": dirs, "files": files, "roots": [r for r in roots if os.path.isdir(r)],
                "sep": os.sep}

    def op_make_dir(self, path: str):
        os.makedirs(path, exist_ok=True)

    def _assays(self) -> list:
        folder = self.prefs.get("assays_folder") or ""
        try:
            return sorted(f for f in os.listdir(folder)
                          if f.lower().endswith(DOCUMENT_EXTS))
        except OSError:
            return []

    def _library(self) -> list:
        try:
            return json.loads((LIBRARY_DIR / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def op_open_library(self, name: str):
        """Assays > Protocol Library: open a built-in protocol in a new window."""
        entry = next((e for e in self._library() if e["name"] == name or e["file"] == name), None)
        if entry is None:
            raise UserError(f"There is no library protocol called {name!r}.")
        doc = json.loads((LIBRARY_DIR / entry["file"]).read_text(encoding="utf-8"))
        self._load(D.strip_data(doc), None)
        self.cur.origin = entry["name"]
        self.dirty = True
        self._touch()

    def op_open_assay(self, name: str):
        self.op_open(os.path.join(self.prefs["assays_folder"], name), as_protocol=True)

    # ----------------------------------------------------------------- edit
    def _busy_plate(self, plate: dict) -> None:
        r = self.inst.reading
        if r and r.get("doc") == self.cur.id and (r.get("workflow") or r.get("id") == plate["id"]):
            raise UserError(f"Can't change {plate['name']} while it is being read.")

    def _busy_here(self) -> None:
        r = self.inst.reading
        if r and r.get("doc") == self.cur.id:
            raise UserError("Can't do that while this window is reading a plate.")

    def op_undo(self):
        self._busy_here()
        with self.lock:
            if self.undo:
                self.redo.append(json.dumps(self.doc))
                self.doc = json.loads(self.undo.pop())
                self.dirty = True
                self._touch()

    def op_redo(self):
        self._busy_here()
        with self.lock:
            if self.redo:
                self.undo.append(json.dumps(self.doc))
                self.doc = json.loads(self.redo.pop())
                self.dirty = True
                self._touch()

    def op_add_section(self, kind: str, after=None):
        if kind not in ("plate", "graph", "notes"):
            raise UserError("Unknown section type")

        def run():
            s = D.add_section(self.doc, kind, after=int(after) if after else None)
            if kind == "graph":
                groups = list(self.doc["groups"])
                g = groups[0] if groups else None
                cols = [c["name"] for c in self.doc["groups"][g]["columns"]] if g else []
                s["plots"].append({"name": "Plot#1", "group": g,
                                   "x": "Concentration" if "Concentration" in cols else (cols[0] if cols else None),
                                   "y": "MeanValue" if "MeanValue" in cols else (cols[-1] if cols else None),
                                   "xerr": None, "yerr": None})
            return {"id": s["id"]}
        return self.edit(run)

    def op_delete_section(self, id):
        s = self.sec(id)
        if self.inst.reading and self.inst.reading.get("id") == s["id"]:
            raise UserError("Can't delete the plate that is being read.")
        if s["kind"] == "plate" and len([x for x in self.doc["sections"] if x["kind"] == "plate"]) == 1:
            raise UserError("An experiment needs at least one Plate section.")
        self.edit(lambda: D.delete_section(self.doc, s["id"]))

    def op_move_section(self, id, index: int):
        s = self.sec(id)

        def run():
            secs = self.doc["sections"]
            secs.remove(s)
            secs.insert(max(0, min(int(index), len(secs))), s)
        self.edit(run)

    def op_section_settings(self, id, name: Optional[str] = None, report: Optional[bool] = None,
                            open: Optional[bool] = None):
        s = self.sec(id)

        def run():
            if name is not None and name.strip() != s["name"]:
                new = name.strip()
                if not new:
                    raise ValueError("This is a required field and cannot be left empty.")
                if s["kind"] == "group":
                    D.rename_group(self.doc, s["group"], new)
                else:
                    if D.section_by_name(self.doc, new):
                        raise ValueError("This name is already being used.  Please enter a different name.")
                    D.rename_references(self.doc, s["name"], new, kind="section")
                    s["name"] = new
            if report is not None:
                s["report"] = bool(report)
            if open is not None:
                s["open"] = bool(open)
        self.edit(run, undoable=open is None)

    def op_all_open(self, open: bool):
        def run():
            for s in self.doc["sections"]:
                s["open"] = bool(open)
        self.edit(run, undoable=False)

    def op_suspend(self, on: bool):
        self.suspended = bool(on)
        self._touch()

    def op_recalculate(self):
        with self.lock:
            ds = self.cur
            ds.rev += 1
            ds.view = D.compute(ds.doc)
            ds.view_rev = ds.rev
            self._bump()

    def op_prefs(self, **changes):
        unknown = set(changes) - set(self.prefs)
        if unknown:
            raise UserError(f"Unknown preference {sorted(unknown)[0]}")
        if "autosave_prefix" in changes and re.search(r'[\\/:*?"<>|]', changes["autosave_prefix"]):
            raise UserError("Invalid character in autosave file prefix.")
        if "filters" in changes:          # by filter-wheel position 1..6; 0 = empty position
            try:
                f = [int(w or 0) for w in changes["filters"]][:6]
            except (TypeError, ValueError):
                raise UserError("Filters must be wavelengths in nm.") from None
            if any(w and not 200 <= w <= 1000 for w in f):
                raise UserError("Filters must be between 200 and 1000 nm.")
            changes["filters"] = f
        if "reader" in changes and changes["reader"] not in READER_KINDS:
            raise UserError("Unknown reader type.")
        if "wavelength_presets" in changes:
            changes["wavelength_presets"] = [int(w) for w in changes["wavelength_presets"] if w][:6]
        port_changed = any(k in changes and changes[k] != self.prefs[k] for k in ("port", "reader")) or (
            "filters" in changes and changes["filters"] != self.prefs["filters"]
            and self.inst.info.get("options", {}) and (self.inst.info["options"] or {}).get("family") == "tmax")
        if port_changed:
            self._busy()
        self.prefs.update(changes)
        self.save_prefs()
        self._bump()
        if port_changed:
            self.inst.connect(self.prefs["port"])

    def op_ports(self):
        return {"ports": [{"device": d, "description": desc} for d, desc in list_ports()]}

    # ------------------------------------------------------- plate: settings
    def op_instrument_settings(self, id, settings: dict, confirm: bool = False):
        plate = self.sec(id, "plate")
        self._busy_plate(plate)
        st = {**D.default_settings(), **plate["settings"], **settings}
        st["wavelengths"] = [_wavelength(w) for w in st["wavelengths"]][:6]
        for k in ("run_time", "interval", "first_col", "last_col", "mix_before_s", "mix_between_s",
                  "start", "stop", "step"):
            st[k] = int(st[k])
        if "columns" in settings or ("first_col" not in settings and "last_col" not in settings):
            cols = settings.get("columns", st.get("columns"))
        else:
            cols = None                                       # a plain first/last range was given
        if cols is not None:
            try:
                cols = sorted({int(c) for c in cols})
            except (TypeError, ValueError):
                raise UserError("Columns must be numbers from 1 to 12.") from None
            if not cols or cols[0] < 1 or cols[-1] > 12:
                raise UserError("Choose at least one column from 1 to 12.")
            st["first_col"], st["last_col"] = cols[0], cols[-1]
            cols = None if cols == list(range(cols[0], cols[-1] + 1)) else cols
        st["columns"] = cols
        rs = self._read_settings(st)
        try:
            rs.validate()
        except ValueError as e:
            raise UserError(str(e)[0].upper() + str(e)[1:] + ".") from None
        if st["mode"] == "kinetic" and st["run_time"] < st["interval"]:
            raise UserError("Run time is less than interval.")
        need = self.min_interval(st)
        if st["mode"] == "kinetic" and st["interval"] < need:
            raise UserError(f"Read interval is too short. Minimum {need} seconds.")
        st["delay"] = max(0, int(st.get("delay") or 0))
        st["wait_temp"] = bool(st.get("wait_temp"))
        changed = any(st[k] != plate["settings"].get(k) for k in st)
        if changed and plate.get("data") and not confirm:
            return {"confirm": "Changing the Instrument Settings requires a plate with no data.\n"
                               f"Delete data in “{plate['name']}”?"}

        def run():
            if changed:
                plate["data"] = None
                plate["preread"] = None
            plate["settings"] = st
        self.edit(run)

    def min_interval(self, st: dict) -> int:
        """Shortest kinetic interval for these settings on the connected reader."""
        caps = self.inst.info.get("options") if self.inst.info.get("connected") else None
        if caps and caps.get("family") == "tmax":
            return tmax_min_interval(len(st["wavelengths"]) == 2, bool(st.get("mix_between")) and caps["shake"])
        return D.read_seconds(st)

    @staticmethod
    def _read_settings(st: dict, preread: bool = False) -> ReadSettings:
        common = dict(first_column=st["first_col"], num_columns=st["last_col"] - st["first_col"] + 1,
                      shake_before=st["mix_before_s"] if st["mix_before"] else 0,
                      autocalibrate=bool(st["autocalibrate"]))
        if preread or st["mode"] == "endpoint":
            wls = st["wavelengths"] if st["mode"] != "spectrum" else [st["start"]]
            return ReadSettings("endpoint", tuple(wls), **common)
        if st["mode"] == "kinetic":
            return ReadSettings("kinetic", tuple(st["wavelengths"]), interval=st["interval"],
                                reads=D.expected_reads(st),
                                shake_between=st["mix_between_s"] if st["mix_between"] else 0, **common)
        return ReadSettings("spectrum", (st["start"],), start=st["start"], stop=st["stop"],
                            step=st["step"], **common)

    def op_reduction(self, id, reduction: dict):
        plate = self.sec(id, "plate")
        red = {**plate["reduction"], **reduction}
        for key in ("wavelength", "kinetic_formula", "spectrum_formula"):
            msg = check_formula(red[key]) if red.get(key) else "Empty expression"
            if msg:
                raise UserError(f"{msg} in the {key.replace('_', ' ')} formula")
        for key in ("lag_time", "end_time", "min_od", "max_od", "vmax_points", "onset_od",
                    "start_nm", "end_nm"):
            if red.get(key) in ("", None):
                red[key] = None if key not in ("lag_time", "onset_od") else (0 if key == "lag_time" else 0.1)
            else:
                red[key] = float(red[key])
        red["smoothing"] = int(red.get("smoothing") or 0)
        if red["smoothing"] not in (0, 3, 9):
            raise UserError("Smoothing must be none, 3 or 9 points.")
        red["zero_first"] = bool(red.get("zero_first"))
        if red.get("raw_mode") not in ("od", "transmittance"):
            red["raw_mode"] = "od"
        if red["vmax_points"] is not None and red["vmax_points"] < 2:
            raise UserError("Vmax Points must be 2 or more.")
        self.edit(lambda: plate.update(reduction=red))

    def op_display(self, id, display: dict):
        plate = self.sec(id, "plate")
        disp = {**plate["display"], **display}
        disp["low"], disp["high"] = float(disp["low"]), float(disp["high"])
        self.edit(lambda: plate.update(display=disp), undoable=False)

    def op_mask(self, id, wells: list):
        plate = self.sec(id, "plate")

        def run():
            cur = set(plate.get("mask") or [])
            wells_ = [w for w in wells if w in D.ALL_WELLS]
            if wells_ and all(w in cur for w in wells_):
                cur -= set(wells_)
            else:
                cur |= set(wells_)
            plate["mask"] = sorted(cur, key=D.ALL_WELLS.index)
        self.edit(run)

    # ------------------------------------------------------- plate: template
    def op_template_assign(self, id, wells: list, type: str = "sample", group: Optional[str] = None,
                           sample: str = "", value=None):
        plate = self.sec(id, "plate")
        v = None if value in ("", None) else float(value)
        self.edit(lambda: D.assign_wells(self.doc, plate, wells, group, sample.strip(), v, type))

    def op_template_series(self, id, wells: list, group: str, first_sample: str = "",
                           start_from: str = "top", start_value=None, op: str = "*", step=2):
        plate = self.sec(id, "plate")
        v = None if start_value in ("", None) else float(start_value)
        self.edit(lambda: D.series_fill(self.doc, plate, wells, group, first_sample.strip(),
                                        start_from, v, op, float(step)))

    def op_group_new(self, name: str, kind: str = "", descriptor: str = "", units: str = "",
                     report: bool = True):
        def run():
            if name.strip().lower() in (g.lower() for g in self.doc["groups"]):
                raise ValueError("This name is already being used.  Please enter a different name.")
            D.ensure_group(self.doc, name.strip(), kind, descriptor, units)
            D.section_by_name(self.doc, name.strip())["report"] = bool(report)
        self.edit(run)

    def op_group_settings(self, group: str, name: Optional[str] = None, descriptor: Optional[str] = None,
                          units: Optional[str] = None, report: Optional[bool] = None):
        def run():
            g = self.doc["groups"][group]
            if descriptor is not None:
                g["descriptor"] = descriptor
            if units is not None:
                g["units"] = units
            sec = D.section_by_name(self.doc, group)
            if report is not None and sec:
                sec["report"] = bool(report)
            if name is not None and name.strip() != group:
                D.rename_group(self.doc, group, name)
        self.edit(run)

    def op_copy_template(self, id):
        plate = self.sec(id, "plate")
        self.clip["template"] = {"template": copy.deepcopy(plate["template"]),
                                 "groups": {t["group"]: copy.deepcopy(self.doc["groups"][t["group"]])
                                            for t in plate["template"].values() if t.get("group")}}
        self._bump()

    def op_paste_template(self, id):
        plate = self.sec(id, "plate")
        clip = self.clip.get("template")
        if not clip:
            raise UserError("Copy a template first (Plate > Copy Template).")

        def run():
            for name, g in clip["groups"].items():
                if name not in self.doc["groups"]:
                    D.ensure_group(self.doc, name)
                    self.doc["groups"][name].update(copy.deepcopy(g))
            plate["template"] = copy.deepcopy(clip["template"])
        self.edit(run)

    def op_copy_data(self, id):
        plate = self.sec(id, "plate")
        if not plate.get("data"):
            raise UserError("This plate has no data to copy.")
        self.clip["data"] = {"data": copy.deepcopy(plate["data"]),
                             "settings": copy.deepcopy(plate["settings"])}
        self._bump()
        d = plate["data"]
        return {"text": textio.export_text(d, "plate", False) if d["mode"] != "endpoint" else
                "\n".join("\t".join("" if v is None else f"{v:.3f}" for v in row)
                          for row in d["values"][0][0])}

    def op_paste_data(self, id, text: Optional[str] = None, confirm: bool = False):
        plate = self.sec(id, "plate")
        self._busy_plate(plate)
        if plate.get("data") and not confirm:
            return {"confirm": f"Replace data in “{plate['name']}”?"}
        if text and text.strip():
            rows = [r.split("\t") for r in re.split(r"\r\n|\r|\n", text.strip("\r\n")) if r.strip() or True]
            rows = [r for r in rows if any(c.strip() for c in r)]
            if len(rows) == 8 and all(len(r) <= 13 for r in rows):
                grid = []
                for r in rows:
                    cells = (r + [""] * 12)[:12]
                    try:
                        grid.append([float(c.replace(",", ".")) if c.strip() else None for c in cells])
                    except ValueError:
                        raise UserError("Pasted data must be numbers in an 8 x 12 block.") from None
                self.edit(lambda: D.paste_grid(plate, grid))
                return
            data = textio.import_text(text)
            self.edit(lambda: self._apply_import(plate, data))
            return
        clip = self.clip.get("data")
        if not clip:
            raise UserError("There is no plate data to paste.")

        def run():
            plate["data"] = copy.deepcopy(clip["data"])
            plate["settings"] = copy.deepcopy(clip["settings"])
            plate["preread"] = None
            plate["mask"] = []
        self.edit(run)

    def op_clear_data(self, id):
        plate = self.sec(id, "plate")
        self._busy_plate(plate)
        self.edit(lambda: plate.update(data=None, preread=None, mask=[]))

    # ------------------------------------------------- columns and summaries
    def _summaries(self, id) -> list:
        s = self.sec(id)
        return self.doc["groups"][s["group"]]["summaries"] if s["kind"] == "group" else s["summaries"]

    def op_check_formula(self, id=None, formula: str = "", names: bool = True):
        msg = D.validate_formula(self.doc, int(id) if id else None, formula) if names \
            else check_formula(formula)
        return {"error": msg}

    def op_summary_save(self, id, formula: str, name: str = "", description: str = "",
                        decimals: int = 3, hide_name: bool = False, old: Optional[str] = None):
        msg = check_formula(formula)
        if msg:
            raise UserError(msg)
        items = self._summaries(id)

        def run():
            nm = name.strip() or f"Summary#{len(items) + 1}"
            clash = [x for x in items if x["name"].lower() == nm.lower() and x["name"] != old]
            if clash:
                raise ValueError("This name is already being used.  Please enter a different name.")
            new = {"name": nm, "description": description, "formula": formula,
                   "decimals": int(decimals), "hide_name": bool(hide_name)}
            for i, x in enumerate(items):
                if x["name"] == old:
                    items[i] = {**x, **new}
                    if old != nm:
                        sec = self.sec(id)
                        D.rename_references(self.doc, old, nm,
                                            home=sec["group"] if sec["kind"] == "group" else sec["name"])
                    return
            items.append({**new, "style": {}})
        self.edit(run)

    def op_summary_delete(self, id, name: str):
        items = self._summaries(id)
        self.edit(lambda: items.__setitem__(slice(None), [x for x in items if x["name"] != name]))

    def op_column_save(self, group: str, formula: str, name: str = "", decimals=None,
                       old: Optional[str] = None, after: Optional[str] = None):
        msg = check_formula(formula)
        if msg:
            raise UserError(msg)

        def run():
            cols = self.doc["groups"][group]["columns"]
            nm = name.strip() or f"Col#{len(cols) + 1}"
            if any(c["name"].lower() == nm.lower() and c["name"] != old for c in cols):
                raise ValueError("This name is already being used.  Please enter a different name.")
            dec = None if decimals in ("", None) else int(decimals)
            for c in cols:
                if c["name"] == old:
                    c.update(name=nm, formula=formula, decimals=dec)
                    if old != nm:
                        self._rename_refs(group, old, nm)
                        D.rename_references(self.doc, old, nm, home=group)
                    return
            new = {"name": nm, "formula": formula, "decimals": dec, "hidden": False, "style": {}}
            idx = next((i + 1 for i, c in enumerate(cols) if c["name"] == after), len(cols))
            cols.insert(idx, new)
        self.edit(run)

    def _rename_refs(self, group: str, old: str, new: str) -> None:
        for s in self.doc["sections"]:
            if s["kind"] == "graph":
                for p in s["plots"]:
                    if p.get("group") == group:
                        for k in ("x", "y", "xerr", "yerr"):
                            if p.get(k) == old:
                                p[k] = new

    def op_column_delete(self, group: str, names: list):
        def run():
            g = self.doc["groups"][group]
            g["columns"] = [c for c in g["columns"] if c["name"] not in names]
        self.edit(run)

    def op_column_hide(self, group: str, names: list, hidden: bool = True):
        def run():
            for c in self.doc["groups"][group]["columns"]:
                if c["name"] in names:
                    c["hidden"] = bool(hidden)
        self.edit(run)

    def op_column_copy(self, group: str, names: list):
        cols = [copy.deepcopy(c) for c in self.doc["groups"][group]["columns"] if c["name"] in names]
        if not cols:
            raise UserError("Select a column first.")
        self.clip["columns"] = cols
        self._bump()

    def op_column_paste(self, group: str, after: Optional[str] = None):
        clip = self.clip.get("columns")
        if not clip:
            raise UserError("Copy a column first.")

        def run():
            cols = self.doc["groups"][group]["columns"]
            idx = next((i + 1 for i, c in enumerate(cols) if c["name"] == after), len(cols))
            for c in clip:
                c = copy.deepcopy(c)
                base, k = c["name"], 2
                while any(x["name"].lower() == c["name"].lower() for x in cols):
                    c["name"] = f"{base}{k}"
                    k += 1
                cols.insert(idx, c)
                idx += 1
        self.edit(run)

    def op_group_view(self, group: str, hide_replicates: Optional[bool] = None,
                      show_formulas: Optional[bool] = None, show_all: bool = False):
        def run():
            g = self.doc["groups"][group]
            if hide_replicates is not None:
                g["hide_replicates"] = bool(hide_replicates)
            if show_formulas is not None:
                g["show_formulas"] = bool(show_formulas)
            if show_all:
                g["hide_replicates"] = False
                for c in g["columns"]:
                    c["hidden"] = False
        self.edit(run, undoable=False)

    def op_text_style(self, id, style: dict, columns: Optional[list] = None,
                      summaries: Optional[list] = None):
        s = self.sec(id)
        allowed = {k: style[k] for k in ("bold", "italic", "underline", "outline", "shadow", "condensed",
                                         "extended", "size", "font", "color") if k in style}

        def run():
            if s["kind"] == "group":
                for c in self.doc["groups"][s["group"]]["columns"]:
                    if columns and c["name"] in columns:
                        c["style"] = {**c.get("style", {}), **allowed}
            for x in self._summaries(id):
                if summaries and x["name"] in summaries:
                    x["style"] = {**x.get("style", {}), **allowed}
        self.edit(run)

    def op_add_interpolation(self, group: str, graph: str, plot: str):
        """Convenience: add Result = InterpX(plot, Values) and its mean to a group."""
        def ref(n):
            return n if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_#.%]*", n) else f"'{n}'"

        def run():
            g = self.doc["groups"][group]
            names = {c["name"].lower() for c in g["columns"]}
            add = [("Result", f"InterpX({ref(plot)}@{ref(graph)},Values)", 3),
                   ("MeanResult", "Average(Result)", 3)]
            if g.get("descriptor") == "Factor":
                add.append(("AdjResult", "MeanResult*!Factor", 3))
            for n, f, d in add:
                if n.lower() not in names:
                    g["columns"].append({"name": n, "formula": f, "decimals": d, "hidden": False, "style": {}})
        self.edit(run)

    # ---------------------------------------------------------- notes, graph
    def op_notes(self, id, html: Optional[str] = None, show_formulas: Optional[bool] = None):
        s = self.sec(id, "notes")

        def run():
            if html is not None:
                s["html"] = sanitize_html(html)
            if show_formulas is not None:
                s["show_formulas"] = bool(show_formulas)
        self.edit(run)

    def op_graph(self, id, **changes):
        s = self.sec(id, "graph")
        ok = {"plots", "fit", "type", "connect", "symbols", "height", "xaxis", "yaxis", "fit_options"}
        if set(changes) - ok:
            raise UserError("Unknown graph option")

        def run():
            if "plots" in changes:
                names = set()
                plots = []
                for i, p in enumerate(changes["plots"]):
                    nm = (p.get("name") or "").strip() or f"Plot#{i + 1}"
                    if nm.lower() in names:
                        raise ValueError("This name is already being used.  Please enter a different name.")
                    names.add(nm.lower())
                    was = p.get("old")
                    if was and was != nm and any(q["name"] == was for q in s["plots"]):
                        D.rename_references(self.doc, was, nm, home=s["name"], kind="plot")
                    plots.append({"name": nm, "group": p.get("group") or None, "x": p.get("x") or None,
                                  "y": p.get("y") or None, "xerr": p.get("xerr") or None,
                                  "yerr": p.get("yerr") or None})
                s["plots"] = plots
            if "fit" in changes:
                if changes["fit"] not in FIT_KINDS:
                    raise ValueError("Unknown fit")
                s["fit"] = changes["fit"]
            if "type" in changes:
                if changes["type"] not in ("scatter", "cluster", "stack"):
                    raise ValueError("Unknown graph type")
                s["type"] = changes["type"]
            for k in ("connect", "symbols"):
                if k in changes:
                    s[k] = bool(changes[k])
            if "height" in changes:
                s["height"] = max(20, min(400, int(changes["height"])))
            if "fit_options" in changes:
                o = {**D.default_fit_options(), **(s.get("fit_options") or {}), **changes["fit_options"]}
                if o["weighting"] not in D.WEIGHTINGS:
                    raise ValueError("Unknown weighting")
                o["conf"] = float(o["conf"])
                if not 0.5 <= o["conf"] < 1:
                    raise ValueError("The confidence level must be between 50 and 99.9 %.")
                o["bands"] = bool(o["bands"])
                for k in ("fixed", "seeds"):
                    o[k] = {p.upper(): float(v) for p, v in (o.get(k) or {}).items()
                            if v not in ("", None) and p.upper() in "ABCDEFGHIJ"}
                s["fit_options"] = o
            for k in ("xaxis", "yaxis"):
                if k in changes:
                    ax = {**s[k], **changes[k]}
                    ax["min"], ax["max"] = float(ax["min"]), float(ax["max"])
                    if not ax["auto"] and ax["max"] <= ax["min"]:
                        raise ValueError("Max must be greater than Min.")
                    s[k] = ax
        self.edit(run)

    # ------------------------------------------------------------ instrument
    def op_connect(self, port: Optional[str] = None):
        self._busy()
        self.inst.connect(port if port is not None else self.prefs["port"])

    def op_disconnect(self):
        self._busy()
        self.inst.disconnect()

    def op_drawer(self, open: Optional[bool] = None):
        want = (not self.inst.info["drawer"].startswith("OPEN")) if open is None else open
        self.inst.call(lambda sm: sm.open_drawer() if want else sm.close_drawer())

    def op_incubator(self, on: bool, temperature: float = 37.0):
        if not self.inst.can("temperature"):
            raise UserError(f"The {self.inst.info['model']} has no incubator.")
        self.inst.call(lambda sm: sm.set_temperature(float(temperature) if on else 0))

    def op_shake(self, on: Optional[bool] = None):
        if not self.inst.can("shake"):
            raise UserError(f"The {self.inst.info['model']} cannot shake the plate.")
        want = (not self.inst.info["shaking"]) if on is None else bool(on)
        self.inst.call(lambda sm: sm.shake(want))
        self.inst.info["shaking"] = want
        self._bump()

    def op_raw_command(self, command: str):
        return {"reply": self.inst.call(lambda sm: sm.command(command)) or "OK"}

    def op_error_log(self, clear: bool = False):
        if clear:
            self.inst.call(lambda sm: sm.clear_errors())
            return {"text": ""}
        return {"text": self.inst.call(lambda sm: sm.errors()).replace("\r", "\n")}

    def next_plate(self, selected=None) -> int:
        plates = [s for s in self.doc["sections"] if s["kind"] == "plate"]
        if selected and any(p["id"] == int(selected) for p in plates):
            return int(selected)
        for p in plates:
            if not p.get("data"):
                return p["id"]
        return plates[0]["id"]

    def op_read(self, id=None, preread: bool = False, confirm: bool = False):
        plate = self.sec(id if id else self.next_plate(), "plate")
        sm = self.inst.need()
        st = plate["settings"]
        rs = self._read_settings(st, preread)
        self.inst.check(rs)
        key = "preread" if preread else "data"
        if plate.get(key) and not confirm:
            return {"confirm": f"Replace data in “{plate['name']}”?"}
        total = rs.expected_plates
        self.inst.stop_flag.clear()
        ds = self.cur
        self.inst.reading = {"id": plate["id"], "doc": ds.id, "name": plate["name"], "got": 0, "total": total,
                             "phase": "Starting", "preread": preread, "started": time.time(),
                             "wait": None}
        with self.lock:
            self.undo.append(json.dumps(self.doc))
            del self.undo[:-UNDO_LIMIT]
            self.redo.clear()
            plate[key] = None
            if not preread:
                plate["display"]["view"] = "raw"
                plate["mask"] = []
            self.dirty = True
        self._touch()
        threading.Thread(target=self._read_thread, args=(ds, sm, plate, rs, key), daemon=True).start()
        return {"started": True}

    def _read_thread(self, ds: DocState, sm: SpectraMax, plate: dict, rs: ReadSettings, key: str,
                     accumulate: Optional[dict] = None, in_workflow: bool = False) -> tuple:
        """Read a plate.  ``accumulate`` = {"base": data, "elapsed": s}: append this endpoint
        reading to earlier ones as one more time point (workflow cycles).  Returns (outcome, message)."""
        self._tl.cur = ds
        got: list = []
        inst = self.inst
        st = dict(plate["settings"])
        if key == "preread":
            st["mode"] = "endpoint"
        setpoint = inst.info.get("setpoint") or 0

        def check():
            if inst.stop_flag.is_set():
                raise _Stopped()

        def on_plate(p, i, total):
            got.append(p)
            with self.lock:
                new = D.data_from_plates(got, st, setpoint)
                if accumulate is not None:
                    new = D.append_reading(accumulate.get("base"), new, accumulate["elapsed"])
                plate[key] = new
            inst.reading.update(got=i, phase="Reading")
            inst.info["temperature"] = p.temperature
            self._touch(ds)
            check()

        def on_status(status):
            inst.reading["phase"] = "Calibrating" if "CAL" in status.state else "Reading"
            inst.info.update(drawer=status.drawer, state=status.state)
            self._bump()
            check()

        def wait_before():
            """Optional: wait for the incubator, then a fixed delay (Instrument Settings)."""
            target = inst.info.get("setpoint") or 0
            if st.get("wait_temp") and target > 0:
                deadline = time.time() + 3 * 3600
                while True:
                    check()
                    _, actual = sm.temperature()
                    inst.info["temperature"] = actual
                    inst.reading.update(phase="Waiting for temperature", wait=f"{actual:.1f} \u2192 {target:.1f} \u00b0C")
                    self._bump()
                    if abs(actual - target) <= 0.5:
                        break
                    if time.time() > deadline:
                        raise SpectraMaxError("The incubator did not reach its set point within 3 hours.")
                    for _ in range(20):
                        check()
                        time.sleep(0.1)
            delay = int(st.get("delay") or 0)
            end = time.time() + delay
            while time.time() < end:
                check()
                inst.reading.update(phase="Starting in", wait=f"{int(end - time.time()) + 1} s")
                self._bump()
                time.sleep(0.2)
            inst.reading.update(phase="Starting", wait=None)

        outcome, message = "done", ""
        what = "pre-read" if key == "preread" else st["mode"] + " read"
        self.activity(f"Started {what} of {plate['name']} ({D.settings_summary(plate['settings'])})", ds)
        with inst.lock:
            try:
                if not in_workflow:
                    wait_before()
                sm.start_read(rs)
                self._bump()
                sm.collect(rs, on_plate=on_plate, on_status=on_status,
                           poll_interval=0.05 if str(sm.port).startswith("sim") else 0.64)
                if self.prefs.get("open_drawer_after_read", True) and not in_workflow:
                    sm.open_drawer()
            except _Stopped:
                outcome, message = "stopped", "Reading stopped."
            except SpectraMaxError as e:
                outcome, message = "error", str(e)
            except Exception as e:  # keep the app alive whatever happens
                outcome, message = "error", f"Unexpected error: {e}"
            try:
                inst._refresh()
            except SpectraMaxError:
                pass
        if in_workflow:
            self._touch(ds)
            n_got = len(got)
            if outcome == "done":
                self.activity(f"Finished {what} of {plate['name']}: {n_got} reading{'s' if n_got != 1 else ''}", ds)
            else:
                self.activity(f"{what.capitalize()} of {plate['name']} {outcome}: {message}", ds)
            return outcome, message
        saved = None
        if outcome == "done" and key == "data" and self.prefs.get("autosave"):
            try:
                saved = ("message", f"Autosaved {os.path.basename(self._autosave())}")
            except OSError as e:
                saved = ("error", f"Autosave failed: {e.strerror or e}")
        inst.reading = None
        self._touch(ds)
        n_got = len(got)
        self.activity({"done": f"Finished {what} of {plate['name']}: {n_got} reading{'s' if n_got != 1 else ''}",
                       "stopped": f"Stopped {what} of {plate['name']} after {n_got} reading{'s' if n_got != 1 else ''}",
                       "error": f"{what.capitalize()} of {plate['name']} failed: {message}"}[outcome], ds)
        if outcome == "error":
            self.event("error", message)
            return outcome, message
        if outcome == "stopped":
            self.event("message", message)
            return outcome, message
        if saved:
            self.event(*saved)
        if outcome == "done" and key == "data" and self.prefs.get("auto_export"):
            try:
                out = self._auto_export(ds, plate)
                self.activity(f"Auto-exported {plate['name']} to {out}", ds)
                self.event("message", f"Exported {os.path.basename(out)}")
            except (OSError, ValueError) as e:
                self.activity(f"Auto export of {plate['name']} failed: {e}", ds)
                self.event("error", f"Auto export failed: {getattr(e, 'strerror', None) or e}")
        if key == "data" and self.prefs.get("autoprint"):
            self.event("print")
        self.event("message", "Pre-read finished." if key == "preread" else "Reading finished.")
        return outcome, message

    def after_read(self, ds: DocState, plates: list) -> None:
        """After a workflow: Auto Export each plate it read, and Autosave the experiment once."""
        self._tl.cur = ds
        if self.prefs.get("auto_export"):
            for plate in plates:
                try:
                    out = self._auto_export(ds, plate)
                    self.activity(f"Auto-exported {plate['name']} to {out}", ds)
                except (OSError, ValueError) as e:
                    self.event("error", f"Auto export failed: {getattr(e, 'strerror', None) or e}")
        if self.prefs.get("autosave"):
            try:
                self.event("message", f"Autosaved {os.path.basename(self._autosave())}")
            except OSError as e:
                self.event("error", f"Autosave failed: {e.strerror or e}")

    def _autosave(self) -> str:
        folder = self.prefs["autosave_folder"]
        os.makedirs(folder, exist_ok=True)
        prefix = self.prefs.get("autosave_prefix") or "Data"
        n = 1
        while os.path.exists(os.path.join(folder, f"{prefix}{n:03d}{DATA_EXT}")):
            n += 1
        path = self._write(os.path.join(folder, f"{prefix}{n:03d}{DATA_EXT}"), False)
        if not self.path:
            self.path, self.dirty = path, False
        return path

    def op_stop(self):
        if self.inst.reading:
            self.inst.stop_flag.set()
            self.inst.reading["phase"] = "Stopping"
            self.workflow.resume_event.set()
            self._bump()

    # ---------------------------------------------------------------- workflow
    def op_workflow_open(self):
        """Control > Workflow: the experiment's Workflow section (created if needed)."""
        for s in self.doc["sections"]:
            if s["kind"] == "workflow":
                return {"id": s["id"]}
        return self.edit(lambda: {"id": D.add_section(self.doc, "workflow", after=None, index=0)["id"]})

    def op_workflow_save(self, id, steps: list):
        sec = self.sec(id, "workflow")
        clean = W.normalize(steps, self.doc)
        self.edit(lambda: sec.update(steps=clean))

    def op_workflow_run(self, id, confirm: bool = False):
        return self.workflow.start(self.cur, self.sec(id, "workflow"), confirm)

    def op_workflow_pause(self):
        self.workflow.pause()

    def op_workflow_resume(self):
        self.workflow.resume()

    def op_workflow_continue(self):
        self.workflow.acknowledge()


_TAG = re.compile(r"<(/?)([a-zA-Z0-9]+)([^>]*)>")
_ALLOWED = {"b", "i", "u", "br", "p", "div", "span", "ul", "ol", "li", "h1", "h2", "h3", "font",
            "strong", "em", "sub", "sup"}


def sanitize_html(html: str) -> str:
    """Notes are rich text; keep basic formatting tags only."""
    html = re.sub(r"(?is)<(script|style|iframe|object)\b.*?</\1>", "", html)

    def fix(m):
        tag = m.group(2).lower()
        if tag not in _ALLOWED:
            return ""
        attrs = ""
        if not m.group(1):
            for name, val in re.findall(r'([a-zA-Z-]+)\s*=\s*"([^"]*)"', m.group(3)):
                name = name.lower()
                if name in ("style", "size", "face", "color") and "url(" not in val.lower() \
                        and "expression" not in val.lower():
                    attrs += f' {name}="{val}"'
        return f"<{m.group(1)}{tag}{attrs}>"
    return _TAG.sub(fix, html)[:200000]
