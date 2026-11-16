#!/usr/bin/env python3
"""Function-aware disassembly of one NE code segment (Borland C++ 16-bit, large model).

    python tools/nedis.py SPF.EXE SEG [ASIPORT2.DLL] > segNN.asm

Splits the segment into functions (flow-following from each prologue), prints
inline string/data regions as data instead of garbage instructions, resolves
relocation targets (internal seg:off, imported ordinals -> names) and annotates
immediates that point at C strings in the same segment.
"""
from __future__ import annotations

import re
import struct
import sys

import capstone

sys.path.insert(0, __file__.rsplit("/", 1)[0])
from ne import NE  # noqa: E402

USER_ORD = {
    200: "OpenComm", 201: "SetCommState", 202: "GetCommState", 203: "GetCommError",
    204: "ReadComm", 205: "WriteComm", 206: "TransmitCommChar", 207: "CloseComm",
    208: "SetCommEventMask", 209: "GetCommEventMask", 210: "SetCommBreak",
    211: "ClearCommBreak", 212: "UngetCommChar", 213: "BuildCommDCB",
    214: "EscapeCommFunction", 215: "FlushComm", 13: "GetTickCount", 1: "MessageBox",
    420: "wsprintf", 421: "wvsprintf", 430: "lstrcmp", 471: "lstrcmpi",
}
KERNEL_ORD = {88: "lstrcpy", 89: "lstrcat", 90: "lstrlen", 115: "OutputDebugString",
              57: "GetProfileInt", 58: "GetProfileString", 59: "WriteProfileString"}


def export_names(path: str) -> dict:
    """ordinal -> name from an NE DLL's resident + non-resident name tables."""
    d = open(path, "rb").read()
    h = struct.unpack_from("<I", d, 0x3C)[0]
    res = h + struct.unpack_from("<H", d, h + 0x26)[0]
    nonres = struct.unpack_from("<I", d, h + 0x2C)[0]
    out = {}
    for p in (res, nonres):
        first = True
        while d[p]:
            n = d[p]
            name = d[p + 1:p + 1 + n].decode("latin1")
            o = struct.unpack_from("<H", d, p + 1 + n)[0]
            if not first:
                out[o] = name
            first = False
            p += n + 3
    return out


def main(argv):
    ne = NE(argv[1])
    idx = int(argv[2])
    asi = export_names(argv[3]) if len(argv) > 3 else {}
    code = ne.seg_bytes(idx)
    rm = ne.reloc_map(idx)

    def reloc_name(r):
        t = r.target(ne)
        m = re.match(r"(\w+)\.#(\d+)", t)
        if m:
            mod, o = m.group(1), int(m.group(2))
            tab = {"ASIPORT2": asi, "USER": USER_ORD, "KERNEL": KERNEL_ORD}.get(mod, {})
            if o in tab:
                return f"{mod}.{tab[o]}"
        return t

    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_16)
    md.detail = True
    # function starts: Borland prologue  (mov ax,ss; nop; inc bp; push bp; mov bp,sp)
    # or plain (inc bp; push bp; mov bp,sp) / (push bp; mov bp,sp)
    starts = set()
    for pat in (b"\x8c\xd0\x90\x45\x55\x8b\xec", b"\x8c\xd0\x45\x55\x8b\xec"):
        p = -1
        while (p := code.find(pat, p + 1)) >= 0:
            starts.add(p)
    # internal far-call targets into this segment from any segment
    for s in ne.segments:
        sb = None
        for r in s.relocs:
            if (r.flags & 3) == 0 and (r.a & 0xFF) == idx and r.src_type == 3:
                if sb is None:
                    sb = ne.seg_bytes(s.index)
                # only far *calls* (opcode 9A) are code targets; far data pointers are not
                if any(o >= 1 and sb[o - 1] == 0x9A for o in r.sites):
                    starts.add(r.b)
    starts = sorted(x for x in starts if x < len(code))
    covered = bytearray(len(code))
    lines = {}

    def cstr_at(v):
        if not (0 < v < len(code) - 1) or covered[v]:
            return None
        if code[v - 1] != 0:
            return None
        e = v
        while e < len(code) and e - v < 120 and (0x20 <= code[e] < 0x7F or code[e] in (9, 10, 13)):
            e += 1
        if e < len(code) and code[e] == 0 and e - v >= 1:
            return code[v:e].decode("latin1").replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
        return None

    pending_notes = []
    for st in starts:
        work = [st]
        while work:
            pc = work.pop()
            while pc < len(code) and not covered[pc]:
                ins = next(md.disasm(code[pc:pc + 16], pc), None)
                if ins is None:
                    break
                for o in range(pc, pc + ins.size):
                    covered[o] = 1
                note = ""
                for o in range(pc, pc + ins.size):
                    if o in rm:
                        note += f"  ; -> {reloc_name(rm[o])}"
                lines[pc] = (ins, note)
                m = ins.mnemonic
                mt = re.match(r"word ptr cs:\[bx \+ (0x[0-9a-f]+)\]", ins.op_str)
                if m == "jmp" and mt:
                    # switch jump table: entries are in-segment code offsets
                    t = int(mt.group(1), 16)
                    k = 0
                    while t + 1 < len(code) and k < 256:
                        tgt = struct.unpack_from("<H", code, t)[0]
                        if not (0 < tgt < len(code)) or covered[t] or abs(tgt - pc) > 0x1000:
                            break
                        covered[t] = covered[t + 1] = 2
                        work.append(tgt)
                        t += 2
                        k += 1
                    break
                if m.startswith("j") or m in ("loop", "loope", "loopne", "jcxz"):
                    try:
                        tgt = int(ins.op_str, 16)
                        if 0 <= tgt < len(code):
                            work.append(tgt)
                    except ValueError:
                        pass
                    if m == "jmp":
                        break
                if m in ("ret", "retf", "iret"):
                    break
                if m == "call" and ins.size == 3:
                    try:
                        work.append(int(ins.op_str, 16))
                    except ValueError:
                        pass
                pc += ins.size

    out = []
    pc = 0
    while pc < len(code):
        if pc in lines:
            ins, note = lines[pc]
            if pc in starts:
                out.append("")
                out.append(f"; ---------------- func {idx}:{pc:04x} ----------------")
            if not note:
                for v in re.findall(r"0x[0-9a-f]+", ins.op_str):
                    s = cstr_at(int(v, 16))
                    if s is not None and ins.mnemonic in ("push", "mov", "lea", "add", "cmp"):
                        note = f'  ; "{s}"'
                        break
            out.append(f"{idx}:{pc:04x}  {ins.mnemonic} {ins.op_str}{note}")
            pc += ins.size
        else:
            e = pc
            while e < len(code) and e not in lines:
                e += 1
            blob = code[pc:e]
            # split into strings / raw
            q = 0
            while q < len(blob):
                z = q
                while z < len(blob) and (0x20 <= blob[z] < 0x7F or blob[z] in (9, 10, 13)):
                    z += 1
                if z > q and z < len(blob) and blob[z] == 0:
                    s = blob[q:z].decode("latin1").replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
                    out.append(f'{idx}:{pc + q:04x}  .asciz "{s}"')
                    q = z + 1
                else:
                    z = min(len(blob), q + 16)
                    # stop raw run at the next plausible string start
                    out.append(f"{idx}:{pc + q:04x}  .db {blob[q:z].hex(' ')}")
                    q = z
            pc = e
    print("\n".join(out))


if __name__ == "__main__":
    main(sys.argv)
