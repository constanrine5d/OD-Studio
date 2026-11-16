#!/usr/bin/env python3
"""Minimal NE (16-bit Windows "New Executable") reader + disassembler helper.

Used to reverse-engineer SPF.EXE (SOFTmax PRO 1.x for Windows 3.1).

    python tools/ne.py info  SPF.EXE              # header + segment table
    python tools/ne.py find  SPF.EXE "REMOTE ON"  # locate string -> seg:off
    python tools/ne.py dis   SPF.EXE SEG START END   # disassemble seg (1-based), hex offsets
    python tools/ne.py xref  SPF.EXE SEG OFF      # find 'push/mov imm16 == OFF' inside SEG
    python tools/ne.py dump  SPF.EXE SEG START END   # hexdump

Requires `capstone` for `dis` / `xref`.
"""
from __future__ import annotations

import struct
import sys
from dataclasses import dataclass, field


@dataclass
class Reloc:
    src_type: int      # 2=seg16, 3=far ptr 16:16, 5=off16
    flags: int         # low 2 bits: 0 internal, 1 import ordinal, 2 import name, 3 osfixup
    offset: int        # first offset in chain
    a: int
    b: int
    additive: bool
    sites: list = field(default_factory=list)

    def target(self, ne: "NE") -> str:
        kind = self.flags & 3
        if kind == 0:
            seg = self.a & 0xFF
            if seg == 0xFF:
                return f"entry#{self.b}"
            return f"seg{seg}:{self.b:04x}"
        if kind == 1:
            return f"{ne.modules[self.a - 1]}.#{self.b}"
        if kind == 2:
            return f"{ne.modules[self.a - 1]}.{ne.imp_name(self.b)}"
        return f"osfixup{self.a}"


@dataclass
class Segment:
    index: int
    file_off: int
    file_len: int
    flags: int
    min_alloc: int
    relocs: list = field(default_factory=list)

    @property
    def is_data(self) -> bool:
        return bool(self.flags & 1)


class NE:
    def __init__(self, path: str):
        self.path = path
        self.data = open(path, "rb").read()
        d = self.data
        if d[:2] != b"MZ":
            raise ValueError("not MZ")
        self.ne_off = struct.unpack_from("<I", d, 0x3C)[0]
        h = self.ne_off
        if d[h:h + 2] != b"NE":
            raise ValueError("not NE")
        (self.entry_tab_off, self.entry_tab_len) = struct.unpack_from("<HH", d, h + 4)
        self.flags = struct.unpack_from("<H", d, h + 0x0C)[0]
        self.auto_data_seg = struct.unpack_from("<H", d, h + 0x0E)[0]
        self.ip, self.cs = struct.unpack_from("<HH", d, h + 0x14)
        self.sp, self.ss = struct.unpack_from("<HH", d, h + 0x18)
        self.nseg, self.nmod = struct.unpack_from("<HH", d, h + 0x1C)
        (self.seg_tab, self.res_tab, self.resname_tab, self.modref_tab,
         self.impname_tab) = struct.unpack_from("<HHHHH", d, h + 0x22)
        self.align = struct.unpack_from("<H", d, h + 0x32)[0] or 9
        self.segments: list[Segment] = []
        for i in range(self.nseg):
            off, ln, fl, mn = struct.unpack_from("<HHHH", d, h + self.seg_tab + 8 * i)
            fo = off << self.align
            fl_len = ln or (0x10000 if off else 0)
            self.segments.append(Segment(i + 1, fo, fl_len, fl, mn or 0x10000))
        self.modules = []
        for i in range(self.nmod):
            no = struct.unpack_from("<H", d, h + self.modref_tab + 2 * i)[0]
            self.modules.append(self.imp_name(no))
        for s in self.segments:
            if s.flags & 0x100 and s.file_off:
                self._read_relocs(s)

    def imp_name(self, off: int) -> str:
        p = self.ne_off + self.impname_tab + off
        n = self.data[p]
        return self.data[p + 1:p + 1 + n].decode("latin1")

    def _read_relocs(self, s: Segment) -> None:
        d = self.data
        p = s.file_off + s.file_len
        n = struct.unpack_from("<H", d, p)[0]
        p += 2
        seg = self.seg_bytes(s.index)
        for _ in range(n):
            st, fl, off, a, b = struct.unpack_from("<BBHHH", d, p)
            p += 8
            r = Reloc(st, fl, off, a, b, bool(fl & 4))
            if r.additive:
                r.sites = [off]
            else:
                o = off
                seen = set()
                while o != 0xFFFF and o not in seen and o + 1 < len(seg):
                    seen.add(o)
                    r.sites.append(o)
                    o = struct.unpack_from("<H", seg, o)[0]
            s.relocs.append(r)

    def seg_bytes(self, idx: int) -> bytes:
        s = self.segments[idx - 1]
        return self.data[s.file_off:s.file_off + s.file_len]

    def reloc_map(self, idx: int) -> dict:
        """offset-in-segment -> (Reloc) for each fixup site."""
        m = {}
        for r in self.segments[idx - 1].relocs:
            for o in r.sites:
                m[o] = r
        return m

    def locate(self, file_off: int):
        for s in self.segments:
            if s.file_off <= file_off < s.file_off + s.file_len:
                return s.index, file_off - s.file_off
        return None

    def cstr(self, idx: int, off: int, maxlen: int = 200) -> str:
        b = self.seg_bytes(idx)
        e = b.find(b"\0", off, off + maxlen)
        if e < 0:
            e = off + maxlen
        return b[off:e].decode("latin1")

    # ------------------------------------------------------------------
    def disasm(self, idx: int, start: int, end: int, strings_seg: int | None = None):
        import capstone
        md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_16)
        md.detail = False
        md.skipdata = True
        code = self.seg_bytes(idx)
        rm = self.reloc_map(idx)
        sseg = strings_seg or idx
        sbytes = self.seg_bytes(sseg)
        out = []
        for ins in md.disasm(code[start:end], start):
            note = ""
            for o in range(ins.address, ins.address + ins.size):
                if o in rm:
                    note += f"  ; -> {rm[o].target(self)}"
            # annotate immediate that looks like a string offset
            if ins.mnemonic in ("push", "mov", "lea") and not note:
                for tok in ins.op_str.replace(",", " ").split():
                    if tok.startswith("0x"):
                        try:
                            v = int(tok, 16)
                        except ValueError:
                            continue
                        if 0x10 <= v < len(sbytes) - 2:
                            s = printable_at(sbytes, v)
                            if s:
                                note += f'  ; "{s}"'
                                break
            out.append(f"{idx}:{ins.address:04x}  {ins.bytes.hex():<16} {ins.mnemonic} {ins.op_str}{note}")
        return out


def printable_at(b: bytes, off: int, minlen: int = 3):
    e = off
    while e < len(b) and e - off < 80 and 0x20 <= b[e] < 0x7F:
        e += 1
    if e - off >= minlen and e < len(b) and b[e] == 0 and (off == 0 or b[off - 1] == 0 or True):
        # only accept if preceded by NUL (start of a C string)
        if off > 0 and b[off - 1] != 0:
            return None
        return b[off:e].decode("latin1")
    return None


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    cmd, path = argv[1], argv[2]
    ne = NE(path)
    if cmd == "info":
        print(f"entry {ne.cs}:{ne.ip:04x}  autodata seg{ne.auto_data_seg}  modules={ne.modules}")
        for s in ne.segments:
            print(f"seg{s.index:<3} file=0x{s.file_off:06x} len=0x{s.file_len:04x} flags=0x{s.flags:04x} "
                  f"{'DATA' if s.is_data else 'CODE'} alloc=0x{s.min_alloc:04x} relocs={len(s.relocs)}")
    elif cmd == "find":
        needle = argv[3].encode("latin1")
        p = -1
        while True:
            p = ne.data.find(needle, p + 1)
            if p < 0:
                break
            loc = ne.locate(p)
            print(f"file 0x{p:06x} -> " + (f"seg{loc[0]}:{loc[1]:04x}" if loc else "(outside segments)"))
    elif cmd == "dis":
        idx, a, b = int(argv[3]), int(argv[4], 16), int(argv[5], 16)
        sseg = int(argv[6]) if len(argv) > 6 else None
        print("\n".join(ne.disasm(idx, a, b, sseg)))
    elif cmd == "dump":
        idx, a, b = int(argv[3]), int(argv[4], 16), int(argv[5], 16)
        seg = ne.seg_bytes(idx)
        for o in range(a, b, 16):
            chunk = seg[o:min(o + 16, b)]
            print(f"{idx}:{o:04x}  {chunk.hex(' '):<47}  " +
                  "".join(chr(c) if 32 <= c < 127 else "." for c in chunk))
    elif cmd == "xref":
        idx, off = int(argv[3]), int(argv[4], 16)
        code = ne.seg_bytes(idx)
        pat = struct.pack("<H", off)
        p = -1
        while True:
            p = code.find(pat, p + 1)
            if p < 0:
                break
            print(f"{idx}:{p:04x}  prev bytes {code[max(0, p - 3):p].hex()}")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
