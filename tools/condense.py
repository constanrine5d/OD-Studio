#!/usr/bin/env python3
"""Condense a nedis.py listing into something closer to pseudo-code.

    python tools/condense.py segNN.asm [names.txt] > segNN.txt

* drops Borland prologue/epilogue, stack-check, exception-frame bookkeeping
* folds argument pushes into the following call:  CALL target(arg1, arg2, ...)
  (args printed in C order, i.e. reverse push order; far pointers folded)
* names.txt: optional "segN:off name" lines to rename call targets
"""
import re
import sys

NOISE = [
    r"^mov ax, ss$", r"^nop $", r"^nop$", r"^inc bp$", r"^push bp$", r"^mov bp, sp$",
    r"^push ds$", r"^mov ds, ax$", r"^wait ?$", r"^pop ds$", r"^pop bp$", r"^dec bp$",
    r"^lea sp, \[bp - \d+\]$", r"^lea sp, \[bp - 0x[0-9a-f]+\]$",
    r"^(add|adc|sub|sbb) word ptr ss:\[0x1[02]\], \d+$",
    r"^mov word ptr ss:\[0x14\], ax$", r"^add sp, .*$", r"^pop cx$",
]
NOISE = [re.compile(x) for x in NOISE]


def load_names(path):
    names = {}
    if path:
        for ln in open(path):
            ln = ln.split("#")[0].strip()
            if ln:
                k, v = ln.split(None, 1)
                names[k] = v
    return names


def main(argv):
    names = load_names(argv[2] if len(argv) > 2 else None)
    raw = [l.rstrip("\n") for l in open(argv[1])]
    # split into functions
    funcs, cur = [], []
    for l in raw:
        if l.startswith("; ----"):
            if cur:
                funcs.append(cur)
            cur = [l]
        else:
            cur.append(l)
    if cur:
        funcs.append(cur)
    out = []
    for f in funcs:
        out.extend(condense(f, names))
    print("\n".join(out))


def fmt_arg(a):
    return a


def condense(lines, names):
    # find exception-state variable: [bp - X] assigned >=3 different small immediates
    cnt = {}
    for l in lines:
        m = re.search(r"mov word ptr \[bp - (0x[0-9a-f]+|\d+)\], (0x[0-9a-f]+|\d+)$", l.split("  ;")[0])
        if m:
            cnt.setdefault(m.group(1), set()).add(m.group(2))
    state_vars = {k for k, v in cnt.items() if len(v) >= 2 and all(int(x, 0) % 8 == 6 for x in v)}
    out = []
    stack = []  # pending pushes (text)
    pend_ax = None  # last 'lea ax,[..]' / 'mov ax, X' text for push ax
    i = 0
    n = len(lines)
    last_reg = {}
    while i < n:
        l = lines[i]
        i += 1
        if not l.strip():
            continue
        if l.startswith(";"):
            flush(stack, out)
            out.append("")
            out.append(l)
            last_reg = {}
            continue
        m = re.match(r"^(\d+:[0-9a-f]{4})  (.*)$", l)
        if not m:
            out.append(l)
            continue
        addr, body = m.group(1), m.group(2)
        note = ""
        if "  ; " in body:
            body, note = body.split("  ; ", 1)
        body = body.rstrip()
        if body.startswith(".db"):
            continue
        if body.startswith(".asciz"):
            flush(stack, out)
            out.append(f"{addr}  {body}")
            continue
        if any(p.match(body) for p in NOISE) and "osfixup" not in note or body.startswith("wait"):
            continue
        if body == "nop" or body == "nop ":
            continue
        m2 = re.match(r"mov word ptr \[bp - (0x[0-9a-f]+|\d+)\], (0x[0-9a-f]+|\d+)$", body)
        if m2 and m2.group(1) in state_vars:
            continue
        if re.match(r"mov ax, word ptr \[bp - (0x[0-9a-f]+)\]$", body) and i < n and "ss:[0x14]" in lines[i]:
            continue
        # stack check: mov ax,N / xor ax,ax followed by call seg1:50c4
        if (re.match(r"(mov ax, (0x[0-9a-f]+|\d+)|xor ax, ax)$", body) and i < n and "seg1:50c4" in lines[i]):
            i += 1
            continue
        # exception frame registration: mov ax,X ; mov bx,Y ; lcall seg146:0000
        if body.startswith("mov ax, 0xffff") and i + 1 < n and "seg146:0000" in lines[i + 1]:
            i += 2
            continue
        # track register loads for push folding
        m3 = re.match(r"lea ax, \[(bp [-+] (?:0x[0-9a-f]+|\d+))\]$", body)
        if m3:
            last_reg["ax"] = "&" + loc(m3.group(1))
            continue
        m3 = re.match(r"mov ax, word ptr \[(bp [-+] (?:0x[0-9a-f]+|\d+))\]$", body)
        if m3 and i < n and re.search(r"  (add ax, |push ax)", lines[i]):
            last_reg["ax"] = loc(m3.group(1))
            continue
        m3 = re.match(r"add ax, (0x[0-9a-f]+|\d+)$", body)
        if m3 and "ax" in last_reg and i < n and "push" in lines[i]:
            last_reg["ax"] = f"{last_reg['ax']}+{m3.group(1)}"
            continue
        if body.startswith("push "):
            op = body[5:]
            if op == "ax" and "ax" in last_reg:
                stack.append(last_reg.pop("ax"))
            elif op in ("ss", "cs", "ds"):
                stack.append(op.upper())
            else:
                mm = re.match(r"word ptr \[(bp [-+] (?:0x[0-9a-f]+|\d+))\]$", op)
                if mm:
                    stack.append(loc(mm.group(1)))
                elif note.startswith('"'):
                    stack.append(note)
                elif note.startswith("-> "):
                    stack.append(note[3:])
                else:
                    stack.append(op.replace("word ptr ", "").replace("dword ptr ", "d"))
            continue
        if body.startswith(("lcall", "call")):
            tgt = note[3:] if note.startswith("-> ") else body.split(None, 1)[1]
            if body.startswith("call ") and not note:
                seg = addr.split(":")[0]
                try:
                    tgt = f"seg{seg}:{int(body.split()[1], 16):04x}"
                except ValueError:
                    pass
            tgt = names.get(tgt, tgt)
            args = fold(stack[::-1])
            stack.clear()
            out.append(f"{addr}  CALL {tgt}({', '.join(args)})")
            last_reg = {}
            continue
        flush(stack, out)
        last_reg.pop("ax", None) if re.search(r"\bax\b", body.split(",")[0]) else None
        out.append(f"{addr}  {body}" + (f"  ; {note}" if note else ""))
    flush(stack, out)
    return out


def loc(s):
    m = re.match(r"bp ([-+]) (0x[0-9a-f]+|\d+)", s)
    v = int(m.group(2), 0)
    return ("A%02x" if m.group(1) == "+" else "L%02x") % v


def fold(args):
    """args in C order; fold far-pointer pairs (off, seg)."""
    res = []
    j = 0
    while j < len(args):
        a = args[j]
        nxt = args[j + 1] if j + 1 < len(args) else None
        if nxt in ("SS", "CS", "DS"):
            res.append(a if nxt != "DS" else f"DS:{a}")
            j += 2
            continue
        # this pointer: A06, A08 ; generic far ptr arg Axx, Axx+2
        m1 = re.match(r"^([AL])([0-9a-f]{2})(\+.*)?$", a)
        m2 = re.match(r"^([AL])([0-9a-f]{2})$", nxt or "")
        if m1 and m2 and m1.group(1) == m2.group(1):
            v1, v2 = int(m1.group(2), 16), int(m2.group(2), 16)
            if (m1.group(1) == "A" and v2 == v1 + 2) or (m1.group(1) == "L" and v2 == v1 - 2):
                name = "this" if (m1.group(1) == "A" and v1 == 6) else f"*{a.split('+')[0]}"
                if m1.group(3):
                    name += m1.group(3)
                res.append(name)
                j += 2
                continue
        res.append(a)
        j += 1
    return res


def flush(stack, out):
    if stack:
        out.append("          PUSH " + ", ".join(stack))
        stack.clear()


if __name__ == "__main__":
    main(sys.argv)
