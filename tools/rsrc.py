#!/usr/bin/env python3
"""Dump a classic Mac resource fork (SOFTmax PRO's SPF.RSR is one, via Altura Mac2Win).

    python tools/rsrc.py SPF.RSR types          # resource types and counts
    python tools/rsrc.py SPF.RSR menus          # MENU resources
    python tools/rsrc.py SPF.RSR strings        # STR# and STR resources
    python tools/rsrc.py SPF.RSR dialogs        # DLOG/ALRT titles + DITL item texts
    python tools/rsrc.py SPF.RSR raw TYPE ID    # hex dump of one resource
"""
import struct
import sys


def load(path):
    d = open(path, "rb").read()
    doff, moff, dlen, mlen = struct.unpack_from(">IIII", d, 0)
    m = d[moff:moff + mlen]
    tl, nl = struct.unpack_from(">HH", m, 24)
    ntypes = struct.unpack_from(">H", m, tl)[0] + 1
    res = {}
    for i in range(ntypes):
        t, n, ro = struct.unpack_from(">4sHH", m, tl + 2 + 8 * i)
        for j in range(n + 1):
            rid, noff, attr_off = struct.unpack_from(">hHI", m, tl + ro + 12 * j)
            off = attr_off & 0xFFFFFF
            ln = struct.unpack_from(">I", d, doff + off)[0]
            name = ""
            if noff != 0xFFFF:
                p = nl + noff
                name = m[p + 1:p + 1 + m[p]].decode("mac_roman", "replace")
            res.setdefault(t.decode("mac_roman"), {})[rid] = (name, d[doff + off + 4:doff + off + 4 + ln])
    return res


def pstr(b, o):
    n = b[o]
    return b[o + 1:o + 1 + n].decode("mac_roman", "replace"), o + 1 + n


def menus(res):
    for rid, (name, b) in sorted(res.get("MENU", {}).items()):
        title, o = pstr(b, 14)
        print(f"MENU {rid}: {title!r}")
        while o < len(b) and b[o]:
            item, o = pstr(b, o)
            icon, key, mark, style = b[o:o + 4]
            o += 4
            k = f"  [Cmd-{chr(key)}]" if 32 < key < 127 and key != 0x1B else ("  [submenu %d]" % mark if key == 0x1B else "")
            print(f"    {item}{k}")


def strings(res):
    for rid, (name, b) in sorted(res.get("STR#", {}).items()):
        n = struct.unpack_from(">H", b, 0)[0]
        o = 2
        print(f"STR# {rid} {name!r}")
        for i in range(n):
            if o >= len(b):
                break
            s, o = pstr(b, o)
            print(f"   {i + 1}: {s!r}")
    for rid, (name, b) in sorted(res.get("STR ", {}).items()):
        print(f"STR {rid}: {pstr(b, 0)[0]!r}")


def ditl(b):
    out = []
    n = struct.unpack_from(">h", b, 0)[0] + 1
    o = 2
    for i in range(n):
        if o + 14 > len(b):
            break
        o += 4
        rect = struct.unpack_from(">4h", b, o)
        o += 8
        typ = b[o] & 0x7F
        ln = b[o + 1]
        data = b[o + 2:o + 2 + ln]
        o += 2 + ln + (ln & 1)
        kind = {4: "button", 5: "checkbox", 6: "radio", 7: "control", 8: "static", 16: "edit",
                32: "icon", 64: "picture", 0: "user"}.get(typ, f"type{typ}")
        txt = data.decode("mac_roman", "replace") if typ in (4, 5, 6, 8, 16) else ""
        out.append((kind, txt))
    return out


def dialogs(res):
    for kind in ("DLOG", "ALRT"):
        for rid, (name, b) in sorted(res.get(kind, {}).items()):
            if kind == "DLOG":
                did = struct.unpack_from(">h", b, 18)[0]
                title = pstr(b, 20)[0]
            else:
                did = struct.unpack_from(">h", b, 8)[0]
                title = ""
            print(f"{kind} {rid} {name!r} title={title!r} ditl={did}")
            d = res.get("DITL", {}).get(did)
            if d:
                for k, t in ditl(d[1]):
                    if t.strip() or k in ("edit", "control", "user"):
                        print(f"      {k}: {t!r}")


def main(a):
    res = load(a[1])
    cmd = a[2]
    if cmd == "types":
        for t, items in sorted(res.items()):
            print(t, len(items), sum(len(v[1]) for v in items.values()))
    elif cmd == "menus":
        menus(res)
    elif cmd == "strings":
        strings(res)
    elif cmd == "dialogs":
        dialogs(res)
    elif cmd == "raw":
        b = res[a[3]][int(a[4])][1]
        for o in range(0, len(b), 16):
            c = b[o:o + 16]
            print(f"{o:04x}  {c.hex(' '):<47}  " + "".join(chr(x) if 32 <= x < 127 else "." for x in c))


if __name__ == "__main__":
    main(sys.argv)
