"""Workflow timeline: a list of steps run in order on the instrument.

Steps (dicts with a "type"):

    temperature  {"celsius": 37.0 (0 = incubator off), "wait": bool}
    shake        {"seconds": 10}
    delay        {"seconds": 60}
    read         {"plate": <plate section id>}
    drawer       {"open": bool}
    message      {"text": "..."}             pauses until the operator presses Continue
    cycle        {"interval": s, "run_time": s, "steps": [...]}

A cycle repeats its steps every ``interval`` seconds for ``run_time`` seconds
(run_time // interval + 1 times).  Endpoint plates read inside a cycle collect
one time point per cycle, so slow kinetics over hours (with shaking, incubation
and operator steps in between) reduce like an ordinary kinetic read.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Optional

from .. import document as D
from ..errors import SpectraMaxError

STEP_TYPES = ("temperature", "shake", "delay", "read", "drawer", "message", "cycle")
LABELS = {"temperature": "Set Temperature", "shake": "Shake", "delay": "Delay", "read": "Read",
          "drawer": "Drawer", "message": "Message", "cycle": "Cycle"}


class WorkflowError(ValueError):
    pass


def _num(v, name, lo=None, hi=None, integer=False):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise WorkflowError(f"{name} must be a number") from None
    if (lo is not None and x < lo) or (hi is not None and x > hi):
        rng = f"{lo:g} to {hi:g}" if hi is not None else f"at least {lo:g}"
        raise WorkflowError(f"{name} must be {rng}")
    return int(round(x)) if integer else x


def normalize(steps: list, doc: dict, inside_cycle: bool = False, ids: Optional[set] = None) -> list:
    """Validate and clean a step list; raises WorkflowError with a readable message."""
    plates = {s["id"]: s for s in doc["sections"] if s["kind"] == "plate"}
    ids = set() if ids is None else ids
    out = []
    for i, st in enumerate(steps or []):
        t = st.get("type")
        where = f"Step {i + 1}" + (" inside the cycle" if inside_cycle else "")
        if t not in STEP_TYPES:
            raise WorkflowError(f"{where}: unknown step type {t!r}")
        sid = st.get("sid")
        if not isinstance(sid, int) or sid in ids:
            sid = max(ids | {0}) + 1
        ids.add(sid)
        c = {"type": t, "sid": sid}
        if t == "temperature":
            celsius = _num(st.get("celsius", 0), f"{where}: temperature", 0, 45)
            if 0 < celsius < 15:
                raise WorkflowError(f"{where}: temperature must be 0 (off) or 15 to 45 °C")
            c.update(celsius=celsius, wait=bool(st.get("wait")) and celsius > 0)
        elif t in ("shake", "delay"):
            c["seconds"] = _num(st.get("seconds", 5), f"{where}: seconds", 1, 7 * 24 * 3600, True)
        elif t == "read":
            pid = st.get("plate")
            try:
                pid = int(pid)
            except (TypeError, ValueError):
                pid = None
            if pid not in plates:
                raise WorkflowError(f"{where}: choose the plate to read")
            if inside_cycle and plates[pid]["settings"]["mode"] != "endpoint":
                raise WorkflowError(f"{where}: plates read inside a cycle must be set to Endpoint "
                                    f"(each cycle adds one time point); {plates[pid]['name']} is "
                                    f"{plates[pid]['settings']['mode']}")
            c["plate"] = pid
        elif t == "drawer":
            c["open"] = bool(st.get("open", True))
        elif t == "message":
            c["text"] = str(st.get("text") or "Press Continue when ready.")[:500]
        elif t == "cycle":
            if inside_cycle:
                raise WorkflowError(f"{where}: a cycle cannot contain another cycle")
            c["interval"] = _num(st.get("interval", 600), f"{where}: interval", 1, None, True)
            c["run_time"] = _num(st.get("run_time", 3600), f"{where}: run time", 0, 30 * 24 * 3600, True)
            c["steps"] = normalize(st.get("steps") or [], doc, True, ids)
        out.append(c)
    return out


def describe(st: dict, doc: dict) -> str:
    t = st["type"]
    if t == "temperature":
        return ("Incubator off" if not st["celsius"] else f"Set temperature {st['celsius']:.1f} °C"
                + (" and wait" if st.get("wait") else ""))
    if t == "shake":
        return f"Shake {st['seconds']} s"
    if t == "delay":
        return f"Wait {D.fmt_hms(st['seconds'])}"
    if t == "read":
        try:
            return f"Read {D.section_by_id(doc, st['plate'])['name']}"
        except KeyError:
            return "Read (deleted plate)"
    if t == "drawer":
        return "Open drawer" if st["open"] else "Close drawer"
    if t == "message":
        return f"Message: {st['text']}"
    return f"Cycle every {D.fmt_hms(st['interval'])} for {D.fmt_hms(st['run_time'])}"


def cycle_count(st: dict) -> int:
    return int(st["run_time"] // st["interval"]) + 1


class _Stop(Exception):
    pass


class Runner:
    """Runs one workflow at a time in a background thread."""

    def __init__(self, app):
        self.app = app
        self.state: Optional[dict] = None
        self.pause_flag = threading.Event()
        self.resume_event = threading.Event()
        self.ack_event = threading.Event()

    # ------------------------------------------------------------ control
    def start(self, ds, section: dict, confirm: bool) -> dict:
        app = self.app
        if self.state and self.state["status"] in ("running", "paused", "message"):
            raise ValueError("A workflow is already running.")
        steps = normalize(section.get("steps"), ds.doc)
        if not steps:
            raise ValueError("The workflow has no steps. Add some with the buttons below the timeline.")
        sm = app.inst.need()
        reads = self._plates(steps)
        for pid in reads:                       # refuse what the connected reader cannot do
            app.inst.check(app._read_settings(D.section_by_id(ds.doc, pid)["settings"]))
        model = app.inst.info.get("model") or "reader"
        for kind, what, ability in (("temperature", "has no incubator", "temperature"),
                                    ("shake", "cannot shake the plate", "shake")):
            if any(stp.get("type") == kind for stp in self._flat(steps)) and not app.inst.can(ability):
                raise ValueError(f"The {model} {what}; remove the {kind} steps from the workflow.")
        with_data = [D.section_by_id(ds.doc, p)["name"] for p in reads if D.section_by_id(ds.doc, p).get("data")]
        if with_data and not confirm:
            return {"confirm": f"The workflow will replace the data in {', '.join(with_data)}. Continue?"}
        self.pause_flag.clear()
        self.ack_event.clear()
        self.resume_event.set()
        app.inst.stop_flag.clear()
        self.state = {"doc": ds.id, "section": section["id"], "status": "running", "step": None,
                      "sid": None, "cycle": None, "started": time.time(), "message": None, "next": None}
        app.inst.reading = {"workflow": True, "id": None, "doc": ds.id, "name": section["name"], "got": 0,
                            "total": 0, "phase": "Workflow", "wait": None, "started": time.time()}
        with app.lock:
            ds.undo.append(json.dumps(ds.doc))
            del ds.undo[:-30]
            ds.redo.clear()
            section["log"] = []
            for pid in reads:
                plate = D.section_by_id(ds.doc, pid)
                plate["data"] = None
                plate["mask"] = []
            ds.dirty = True
        app._touch(ds)
        threading.Thread(target=self._run, args=(ds, section, steps, sm), daemon=True).start()
        return {"started": True}

    def pause(self):
        if self.state and self.state["status"] == "running":
            self.pause_flag.set()
            self.resume_event.clear()
            self.state["status"] = "pausing"
            self.app._bump()

    def resume(self):
        if self.state and self.state["status"] in ("paused", "pausing"):
            self.pause_flag.clear()
            self.resume_event.set()
            self.state["status"] = "running"
            self.app._bump()

    def acknowledge(self):
        self.ack_event.set()

    # ------------------------------------------------------------- engine
    @staticmethod
    def _flat(steps) -> list:
        out = []
        for st in steps:
            out.append(st)
            if st["type"] == "cycle":
                out += Runner._flat(st["steps"])
        return out

    @staticmethod
    def _plates(steps) -> list:
        out = []
        for st in steps:
            if st["type"] == "read" and st["plate"] not in out:
                out.append(st["plate"])
            if st["type"] == "cycle":
                out += [p for p in Runner._plates(st["steps"]) if p not in out]
        return out

    def _log(self, ds, section, text):
        t = time.time() - self.state["started"]
        section.setdefault("log", []).append(f"+{D.fmt_hms(t)}  {text}")
        del section["log"][:-1000]
        self.app.activity(f"Workflow: {text}", ds)
        self.app._touch(ds)

    def _checkpoint(self):
        """Honour Stop and Pause between steps."""
        inst = self.app.inst
        if inst.stop_flag.is_set():
            raise _Stop()
        if self.pause_flag.is_set():
            self.state["status"] = "paused"
            inst.reading["phase"] = "Workflow paused"
            self.app._bump()
            while not self.resume_event.wait(0.2):
                if inst.stop_flag.is_set():
                    raise _Stop()
            self.state["status"] = "running"
            inst.reading["phase"] = "Workflow"
            self.app._bump()

    def _sleep_until(self, end, label):
        inst = self.app.inst
        while True:
            self._checkpoint()
            left = end - time.time()
            if left <= 0:
                return
            inst.reading["wait"] = f"{label} {D.fmt_hms(left + 0.999)}"
            self.state["next"] = end
            self.app._bump()
            time.sleep(min(0.25, left))

    def _run(self, ds, section, steps, sm):
        app = self.app
        app._tl.cur = ds
        outcome, note = "done", ""
        self._log(ds, section, "Started")
        try:
            self._steps(ds, section, steps, sm, cycle=None)
        except _Stop:
            outcome, note = "stopped", "Stopped"
        except (SpectraMaxError, WorkflowError, ValueError, KeyError) as e:
            outcome, note = "error", str(e)
        except Exception as e:  # keep the app alive
            outcome, note = "error", f"Unexpected error: {e}"
        self._log(ds, section, {"done": "Finished", "stopped": "Stopped by the user",
                                "error": f"Failed: {note}"}[outcome])
        self.state.update(status=outcome, step=None, sid=None, message=None, next=None, error=note)
        app.inst.reading = None
        app._touch(ds)
        if outcome == "done":
            plates = []
            for pid in self._plates(steps):
                try:
                    plates.append(D.section_by_id(ds.doc, pid))
                except KeyError:
                    continue
            app.after_read(ds, plates)
            if app.prefs.get("autoprint"):
                app.event("print")
            app.event("message", "Workflow finished.")
        elif outcome == "error":
            app.event("error", f"Workflow stopped: {note}")
        else:
            app.event("message", "Workflow stopped.")

    def _steps(self, ds, section, steps, sm, cycle):
        for st in steps:
            self._checkpoint()
            self._do(ds, section, st, sm, cycle)

    def _do(self, ds, section, st, sm, cycle):
        app, inst = self.app, self.app.inst
        doc = ds.doc
        text = describe(st, doc)
        self.state.update(step=text, sid=st["sid"])
        inst.reading.update(phase="Workflow", wait=text)
        app._bump()
        t = st["type"]
        if t != "cycle":
            self._log(ds, section, text + (f"  (cycle {cycle['i'] + 1} of {cycle['n']})" if cycle else ""))
        if t == "temperature":
            with inst.lock:
                sm.set_temperature(st["celsius"] or 0)
                inst._refresh()
            app._bump()
            if st.get("wait") and st["celsius"]:
                while True:
                    self._checkpoint()
                    with inst.lock:
                        _, actual = sm.temperature()
                    inst.info["temperature"] = actual
                    inst.reading["wait"] = f"Waiting for {st['celsius']:.1f} °C (now {actual:.1f})"
                    app._bump()
                    if abs(actual - st["celsius"]) <= 0.5:
                        break
                    self._sleep_until(time.time() + 2, "Checking temperature in")
        elif t == "shake":
            with inst.lock:
                sm.shake(True)
            inst.info["shaking"] = True
            try:
                self._sleep_until(time.time() + st["seconds"], "Shaking")
            finally:
                with inst.lock:
                    sm.shake(False)
                inst.info["shaking"] = False
        elif t == "delay":
            self._sleep_until(time.time() + st["seconds"], "Waiting")
        elif t == "drawer":
            with inst.lock:
                sm.open_drawer() if st["open"] else sm.close_drawer()
                inst._refresh()
        elif t == "message":
            self.ack_event.clear()
            self.state.update(status="message", message=st["text"])
            inst.reading["wait"] = "Waiting for the operator"
            app._bump()
            while not self.ack_event.wait(0.2):
                if inst.stop_flag.is_set():
                    raise _Stop()
            self.state.update(status="running", message=None)
            self._log(ds, section, "Operator pressed Continue")
        elif t == "read":
            plate = D.section_by_id(doc, st["plate"])
            rs = app._read_settings(plate["settings"])
            inst.reading.update(id=plate["id"], name=plate["name"], got=0, total=rs.expected_plates,
                                phase="Reading")
            acc = None
            if cycle is not None:
                acc = {"base": plate.get("data"), "elapsed": time.time() - cycle["t0"]}
            outcome, message = app._read_thread(ds, sm, plate, rs, "data", accumulate=acc, in_workflow=True)
            inst.reading.update(id=None, name=section["name"], phase="Workflow")
            if outcome == "stopped":
                raise _Stop()
            if outcome == "error":
                raise SpectraMaxError(message)
        elif t == "cycle":
            n = cycle_count(st)
            t0 = time.time()
            for i in range(n):
                start = t0 + i * st["interval"]
                if i:
                    self._sleep_until(start, f"Cycle {i + 1} of {n} in")
                    late = time.time() - start
                    if late > 1:
                        self._log(ds, section, f"Cycle {i + 1} started {late:.0f} s late: the steps take "
                                               f"longer than the interval")
                self.state["cycle"] = f"{i + 1} of {n}"
                self._log(ds, section, f"Cycle {i + 1} of {n}")
                self._steps(ds, section, st["steps"], sm, {"i": i, "n": n, "t0": t0})
            self.state["cycle"] = None
