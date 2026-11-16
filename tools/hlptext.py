#!/usr/bin/env python3
"""Extract the plain text of a Windows 3.x .HLP file (enough to read SPF.HLP).

    python tools/hlptext.py SPF.HLP > help.txt
"""
import struct
import sys


def lz77(src: bytes) -> bytes:
    out = bytearray()
    i, n = 0, len(src)
    while i < n:
        flags = src[i]
        i += 1
        for bit in range(8):
            if i >= n:
                break
            if flags & (1 << bit):
                if i + 1 >= n:
                    i = n
                    break
                w = src[i] | (src[i + 1] << 8)
                i += 2
                length = (w >> 12) + 3
                dist = (w & 0xFFF) + 1
                for _ in range(length):
                    out.append(out[-dist] if dist <= len(out) else 0)
            else:
                out.append(src[i])
                i += 1
    return bytes(out)


class Hlp:
    def __init__(self, path):
        self.d = d = open(path, "rb").read()
        magic, dirstart = struct.unpack_from("<Ii", d, 0)
        assert magic == 0x00035F3F, hex(magic)
        self.files = {}
        self._read_dir(dirstart)

    def ifile(self, off):
        reserved, used, flags = struct.unpack_from("<iiB", self.d, off)
        return self.d[off + 9:off + 9 + used]

    def _read_dir(self, off):
        b = self.ifile(off)
        (magic, flags, pagesize) = struct.unpack_from("<HHH", b, 0)
        root, _, total, nlevels, nentries = struct.unpack_from("<hhhhi", b, 26)
        pages = b[38:]

        def walk(page, level):
            p = pages[page * pagesize:(page + 1) * pagesize]
            if level > 1:
                _, n, prev = struct.unpack_from("<Hhh", p, 0)
                o = 6
                walk(prev, level - 1)
                for _ in range(n):
                    e = p.index(b"\0", o)
                    o = e + 1
                    child = struct.unpack_from("<h", p, o)[0]
                    o += 2
                    walk(child, level - 1)
            else:
                _, n, prev, nxt = struct.unpack_from("<Hhhh", p, 0)
                o = 8
                for _ in range(n):
                    e = p.index(b"\0", o)
                    name = p[o:e].decode("latin1")
                    off2 = struct.unpack_from("<i", p, e + 1)[0]
                    o = e + 5
                    self.files[name] = off2

        walk(root, nlevels)

    def text(self):
        sysf = self.ifile(self.files["|SYSTEM"])
        magic, minor, major, gen, flags = struct.unpack_from("<HHHiH", sysf, 0)
        if minor <= 16:
            bsize, comp = 2048, False
        elif flags == 4:
            bsize, comp = 4096, True
        elif flags == 8:
            bsize, comp = 2048, True
        else:
            bsize, comp = 4096, False
        phrases = []
        if "|Phrases" in self.files:
            ph = self.ifile(self.files["|Phrases"])
            n = struct.unpack_from("<H", ph, 0)[0]
            if minor > 16:
                hdr = 8
                body = ph[hdr:]
                offs = struct.unpack_from(f"<{n + 1}H", body, 0)
                rest = lz77(body[2 * (n + 1):])
                base = 2 * (n + 1)
                phrases = [rest[offs[i] - base:offs[i + 1] - base] for i in range(n)]
            else:
                body = ph[4:]
                offs = struct.unpack_from(f"<{n + 1}H", body, 0)
                phrases = [body[offs[i]:offs[i + 1]] for i in range(n)]
        topic = self.ifile(self.files["|TOPIC"])
        blocks = []
        for o in range(0, len(topic), bsize):
            blk = topic[o:o + bsize]
            data = lz77(blk[12:]) if comp else blk[12:]
            blocks.append(data)
        dsize = 0x4000 if minor > 16 else bsize
        # concatenate; a TOPICLINK may span blocks
        buf = b"".join(b if minor <= 16 else b.ljust(dsize - 12, b"\0")[:dsize - 12] for b in blocks)
        step = (dsize - 12) if minor > 16 else None

        def expand(s: bytes) -> bytes:
            out = bytearray()
            i = 0
            while i < len(s):
                c = s[i]
                if 1 <= c <= 14 and i + 1 < len(s) and phrases:
                    num = 256 * (c - 1) + s[i + 1]
                    idx = num // 2
                    if idx < len(phrases):
                        out += phrases[idx]
                        if num & 1:
                            out += b" "
                    i += 2
                else:
                    out.append(c)
                    i += 1
            return bytes(out)

        pos = 0
        lines = []
        guard = 0
        while pos + 21 <= len(buf) and guard < 100000:
            guard += 1
            bs, dl2, prev, nxt, dl1, rtype = struct.unpack_from("<iiiiiB", buf, pos)
            if bs <= 0 or dl1 < 21 or dl1 > bs:
                break
            raw = buf[pos + dl1:pos + bs]
            if dl2 > bs - dl1:
                raw = expand(raw)
            parts = [p.decode("cp1252", "replace") for p in raw.split(b"\0") if p]
            if rtype == 2:
                lines.append("\n=== " + " ".join(parts) + " ===")
            elif rtype in (0x20, 0x23, 0x01):
                t = "".join(parts).strip()
                if t:
                    lines.append(t)
            if minor > 16:
                if nxt in (-1, 0):
                    break
                blk, off = nxt >> 14, nxt & 0x3FFF
                pos = blk * step + off - 12
            else:
                pos += bs if nxt <= 0 else nxt
                if nxt <= 0:
                    break
        return "\n".join(lines)


if __name__ == "__main__":
    h = Hlp(sys.argv[1])
    sys.stdout.write(h.text() + "\n")
