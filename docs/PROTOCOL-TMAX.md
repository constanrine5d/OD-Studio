# ThermoMax / UVmax / Vmax / Emax serial protocol

The older Molecular Devices filter readers, as SOFTmax PRO 1.x for Windows 3.1 drives them
(`binary/SOFTPRO/SPF.EXE`, source file `SOURCES\TMAXINTE.CPP`). Companion to
[PROTOCOL.md](PROTOCOL.md) (SpectraMax 250 / "Flash"), with the same conventions. OD Studio's
driver is `src/odstudio/tmax.py` and its simulator `src/odstudio/tmaxsim.py`.

> **Worked out from the host program only.** SOFTmax PRO contains no simulator for these readers
> and none has been tested yet, so everything the reader itself sends is inferred from what the
> host expects. [Open questions](#9-open-questions) lists what a real reader has to settle; the
> driver handles the biggest one (byte order of the data) by detecting it.

Addresses `66:21a8` = code segment 66, offset 0x21a8. `DS:xxxx` = data segment 148.
Vtable slots are byte offsets into the vtable (`slot 0x148` = `call es:[bx+0x148]`).

**Confidence marks**

- **[code]** read directly from the disassembly
- **[inferred]** deduced from code structure, naming, or the Mac heritage of the code, not proven
- **[unknown]** cannot be determined from the binary; see [Open questions](#9-open-questions)

---

## 0. Where things are

| Seg | Class (class-name string) | Model | Type id (slot 0xbc) | Vtable (seg 64) | ctor (slot 0x154) |
|---|---|---|---|---|---|
| 66 | `CSTMaxInterface` (`66:0000`) | **ThermoMax** | 4 (`66:1dd0`) | `64:238d` | `66:00f0` |
| 67 | `CSUVMaxInterface` (`67:0804`) | **UVmax** | 3 (`67:0ab0`) | `64:222d` | `67:0831` |
| 67 | `CSVMaxInterface` (`67:0b4e`) | **Vmax** | 2 (`67:0d38`) | `64:20c9` | `67:0b7a` |
| 63 | `CSEMaxInterface` (`63:1746`) | **Emax** | 1 (`63:1930`) | `64:1f61` | `63:1772` |

[code] Class chain (class descriptors `DS:0acc → 0bfc → 0bf2 → 0b4c → 0b2e`, ctors chain the same way):
`CSSerialComm (63) → CSReaderInterface (65) → CSTMaxInterface (66, ThermoMax) → CSUVMaxInterface → CSVMaxInterface → CSEMaxInterface`.
So the **base class is the ThermoMax**, and each further model removes features.

[code] Factory `64:020c` (CSReader slot 0x60), jump table `64:097d`: type 1 installs vtables
238d→222d→20c9→**1f61**, type 2 →…→**20c9**, type 3 →…→**222d**, type 4 →**238d**, type 5/6 Flash,
type 7 `CSTestInterface` (`64:24e9`), type 8 `CSFlashSim` (`64:1e29`).

The Emax code lives at the end of segment 63 (`63:1746`–`63:1b44`), not in 66/67.

### Dead code: the Mac asynchronous serial path

`66:08f6` builds Mac-style parameter blocks at `DS:3a9c`/`DS:3ad6` with a context at `DS:3b10`
whose `ioCompletion` pointers go to `67:0000 0075 0197 0357 0490 0664`. Those routines call
`7:0287 / 7:0351 (PBRead) / 7:0397 (PBWrite) / 7:03dd / 7:0423`, which in this build are stubs in
`PLATFORM\DUMMY.CPP` that only raise `assert(FALSE)` [code]. `63:167d` (the "start async" call)
just sets `DS:0ab4 = 1`, a byte nothing reads [code]. **On Windows the whole async chain never
runs**; all I/O goes through the synchronous routines described below. The dead chain is still
useful as a description of the original Macintosh timing (§3.6) and is cited where it confirms
the synchronous code.

---

## 1. Serial line

| | TMax family | Flash (SpectraMax 250) |
|---|---|---|
| Port open | same `CSSerialComm` code: `OpenComm("COMn", in=8192, out=512)` `63:113a` | same |
| DCB | `BuildCommDCB("COMn:9600,n,8,1")` + `SetCommState`, no further patching (`63:0ff9`, `63:103c`, `63:113a`) [code] | same |
| Baud / format | **9600 baud, 8 data bits, no parity, 1 stop bit** [code] | 9600 8N1 |
| Handshake | none configured (no XON/XOFF, no RTS/CTS, no DTR/DSR changes) [code] | none |
| Ports | COM1 if the preference is 1, otherwise COM2 (`63:15ce`) [code] | same |
| BREAK | used for resynchronisation: `SetCommBreak`, 60 ms, `ClearCommBreak`, 60 ms (`63:1525`, `63:1574` uses `GetTickCount`, ms) [code] | not used by Flash code |

Confirmation from the Mac heritage: the TMax ctor calls slot 0x68 (`63:1622`) with
`0x4C0A, 0x100` (`66:01b5`–`66:01cf`), storing them at `this+0x26/+0x28`. `0x4C0A` is exactly the
Mac Serial Manager `SerReset` config word `stop10 (0x4000) + noParity (0) + data8 (0x0C00) +
baud9600 (10)`; 0x100 is presumably the `SerSetBuf` size. The Windows code never reads these
fields; it always applies the fixed string above. [code for values, inferred for meaning]

The ctor then calls slot 0x6c (`63:0f32`: build port string, close, open). [code]

---

## 2. Connection, detection, identification

### 2.1 Who drives it

[code] `CSReaderChore` (`65:0078`) runs every 40 ticks (~0.64 s, measured from the end of the
previous run). It asks the interface state (`this+0x3c`, slot 0xe0):

- state 0 (not connected) or 1 (idle) → `CSReader::Idle` `64:1198`
- state 2 (endpoint) or 3 (kinetic) → interface slot 0x94 (poll, §6)

`64:1198`:

1. remember state;
2. `try { iface->slot 0x110 (66:1fc0) ; if (state was 0) CSReader::Detect (64:0cea) }`
   `catch (e) { if e not in {101, 102, 106} → CSReader::Detect }`;
3. always `CSReader slot 0x64` (`64:0b5a`) → interface slot 0x94 (poll) + UI notification.

The initial interface type comes from the preferences (`64:1a67`, Director slot 0x50).

### 2.2 Connect (`66:1fc0`, slot 0x110), all four models

When state == 0 [code]:

```
FlushComm(in) ; FlushComm(out)                       (slot 0x7c, 63:124e)
send "\r"   echo-checked, echo timeout 6 ticks       (DS:0bb0 temporarily = 6)
send "LCL"  echo-checked (sent with slot 0x14c directly, no retry)
slot 0x144  identity:
    ThermoMax (66:1b4a): VER exchange, see 2.4
    UVmax/Vmax/Emax (67:099a, 67:0c22, 63:181a): no I/O; description "--\r<Model>\r"
state = 1
slot 0x120  read temperatures:
    ThermoMax (66:17ba): RDS, RDT, STS  (see 4)
    others (67:08be): no I/O, both temperatures "0.0"
send "LCL"  via slot 0x148 (flush + echo + retry)
```

When state == 1 (already connected) only the last two steps run: **this is the idle traffic
every ~0.64 s** (§6).

On any exception: `DS:0bb0 = 60`, state = 0, rethrow (`66:2120` handler) [code].

### 2.3 Detection (`64:0cea`, CSReader slot 0x68)

Runs right after the first successful connect (or after an unexpected error). Byte by byte,
assuming the current interface is one of the TMax family [code]:

| Step | Host action | Result |
|---|---|---|
| 1 | `type = 1`; query current type (slot 0xbc) | |
| 2 | `iface->slot 0xf8("")` (send nothing). For a TMax interface this is `66:2276`: FlushComm, zero characters sent, reply is the constant `"ERIES1\r>"` (`66:225a`) | `MakeUpper`, `Find("OK")` → not found. Exceptions swallowed. (With a Flash interface this is `!<CR>`; `OK` → type 5.) |
| 3 | if `type == 5` or current type == 5 → go to step 8 | |
| 4 | `slot 0xf8("VER")` (flush, `V` `E` `R` echo-checked, retry once via BREAK), then `slot 0xfc` → line 1 | |
| 5 | parse line 1: `NextToken(line, "; ")` (token discarded), `model = NextToken(line, " VER ")`, `model = Left(model, 2)`; then `slot 0xfc` → line 2 (discarded) | |
| 5x | if step 4/5 throws: `model = "SMAX"` when the error code (`DS:43f4`) is **301**, else `"error"` (handler `64:0f80`) | |
| 6 | exact, case-sensitive compare (`CString::equals_sz`): `"TH"` → **4 ThermoMax**; `"UV"` → **3 UVmax**; `"Em"` → **1 Emax**; `"SMAX"` → **5 SpectraMax** | |
| 7 | none matched: `slot 0xf8("SHK")`. Success → **2 Vmax**. Exception → **1 Emax** (handler `64:108e`) | |
| 8 | if new type ≠ current type and current type ∉ {7, 8}: store as preferred type (Director slot 0x54) and recreate the interface (CSReader slot 0x60). The new interface starts in state 0 and is connected on the next chore tick, then detection runs again and confirms. | |

Notes:

- `NextToken` (`115:036c`): finds the exact separator; token = text before it with leading
  spaces removed; source = text after it with trailing spaces removed. Separator missing →
  empty token, source unchanged. [code]
- So line 1 must look like `<anything>; <MODEL> VER <version>…` where `<MODEL>` starts with
  `TH`, `UV` or `Em`. A Vmax is recognised by **not** matching and accepting `SHK`; an Emax by
  rejecting `SHK` (BEL error or timeout). [code]
- If the current interface is the Flash and `!<CR>` gets no `OK`, step 3 jumps to step 8 with
  type 1: the host switches to the **Emax** interface and the next tick re-detects with the
  echo protocol. (Only if the Flash connect raised an error other than 101/102/106, otherwise
  detection is not called at all; see 2.1.) [code]
- The `SHK` sent during detection leaves automix enabled on a Vmax; it is re-sent before every
  read anyway. [code]

### 2.4 VER reply (ThermoMax identity, `66:1b4a`)

[code] `VER` is sent (slot 0x148), then two lines are read with slot 0xfc (each up to and
including LF). Line 1 is parsed:

```
<vendor/other>; <model> VER <version>\r ...\n        line 1
<anything>\n                                          line 2 (ignored)
```

- `NextToken(line, "; ")` → first part (overwritten), `NextToken(line, " VER ")` → `model`,
  `NextToken(line, "\r")` → `version`.
- Description stored at `this+0x4a` = `version + "\r" + model + "\r"` (shown in the UI).
- Subclasses never send VER outside detection; their description is `"--\r<Model>\r"` with
  `<Model>` = `UVmax` (`67:093c`), `Vmax` (`67:0bc6`), `Emax` (`63:17be`).

Exact vendor text, model spelling (e.g. `THERMOmax`), version format and the second line are
[unknown]; the host only needs `"; "`, `" VER "` and the first two model characters.

---

## 3. Framing

### 3.1 Host → instrument: character echo, no terminator

`66:23b2` (slot 0x14c, "send with echo") [code]:

```
n = strlen(cmd)
for i in 0..n-1:
    WriteComm(cmd[i])                                 (one byte per write, slot 0x80)
    timeout = (i == n-1) ? DS:0bb0 : 6 ticks          (DS:0bb0 is normally 60)
    c = ReadChar(timeout)                             (slot 0xf4 = 65:05d4)
    if c != cmd[i]:
        if c == BEL (0x07): break
        Fail(350)                                     (echo mismatch)
if c == BEL:
    read up to 20 chars, 60 ticks each, stopping at CR (0x0D)
    read 1 more char, 60 ticks                        (expected LF)
    Fail(300 + atoi(text))
```

- **No CR, no LF, no prefix is appended.** A command is exactly the characters listed in §4.
  The firmware must therefore recognise commands by their fixed length (3-letter mnemonic plus
  fixed-length arguments). [code for host side; inferred for firmware]
- **Every character is echoed.** Non-final characters must be echoed within 6 ticks (~0.1 s).
  The final character's echo may take up to `DS:0bb0` ticks: 60 (~1 s) normally, 1800 (~29 s)
  for `"SKO3 "`, 6 for the lone CR of the connect sequence. This strongly suggests the firmware
  echoes the last character only after the command has been accepted/executed. [code / inferred]
- The Mac async path (dead) did the same: write 1 char (60-tick write timeout), read echo
  (120 ticks), compare, `BEL` → -399, mismatch → -350 (`67:0664`, `67:0490`); it also sent no
  terminator. [code]

`66:2276` (slot 0xf8, "send command", used by slot 0x148 `66:21a8`) [code]:

```
FlushComm(in & out)                         once
attempt = 0
loop:
    attempt += 1
    try   { send_with_echo(cmd) ; done }
    catch { if attempt < 2: resync() (slot 0xa8) and retry ; else rethrow }
reply = "ERIES1\r>"                          constant, meaningless (66:225a)
```

So every command is tried at most twice, with a BREAK resynchronisation between.

### 3.2 Resynchronisation (`66:11bc`, slot 0xa8)

[code]

```
BREAK: SetCommBreak, 60 ms, ClearCommBreak, 60 ms          (63:1525)
wait 6 ticks (~0.1 s, ASIPORT2.TICKCOUNT)
FlushComm
write CR (0x0D)
c = ReadChar(60 ticks)
if c != BEL:  Fail(101) if c == 0x00 else Fail(105)
read up to 20 chars until CR (60 ticks each), then 1 more char (60 ticks)
(no error raised: success)
```

So after a BREAK the instrument is expected to answer a lone CR with `BEL <digits> CR <1 char>`.
This contrasts with the connect sequence, where a lone CR must be **echoed**. [code; the firmware
reason is unknown — perhaps "CR after BREAK" is an error state]

### 3.3 Instrument → host

| Kind | Commands | Framing | Read by |
|---|---|---|---|
| none (echo only) | RMT LCL OPN CLS SHK DSH SWVn DWVn,m TMPxx.x CON CFF "SKO3 " | echo of each char; nothing else | `66:23b2` |
| text line | VER (2 lines), RDT, RDS, STS | bytes up to and including LF, CR/LF kept in the string | `66:1e98` (slot 0xfc) |
| data block | RDP RNE STK RDR NXK RNR | 1 lead byte + 192 binary bytes | `66:2d96` (slot 0x130) |
| error | any | `BEL` instead of an echo (or instead of the lead byte), then ASCII decimal error number, CR, 1 more char | `66:23b2`, `66:2d96` |

**Line reader** `66:1e98`: `reply = ""`; repeat `c = ReadChar(60 ticks)`; append; until
`c == LF (0x0A)`. No length limit. On timeout: error 105 if something was received, else 101
(handler `66:1f0c`). [code]

**Data block reader** `66:2d96` [code]:

```
c = ReadChar(1800 ticks)                      lead byte
if c == BEL: read up to 20 chars to CR (60 ticks each); read 1 more char (60 ticks);
             Fail(300 + atoi)
(else the lead byte is discarded — its value is never examined)
for i in 0..95:
    buf[2i]   = ReadChar(1800 ticks)
    buf[2i+1] = ReadChar(1800 ticks)
slot 0x12c(this, 1)      ThermoMax: RDT exchange (§4); others: temperature "0.0"
```

There is no checksum, no terminator and no trailing CR/LF expected after the 192 bytes.

### 3.4 Timing

- Tick = `ASIPORT2.TICKCOUNT` = `GetTickCount() >> 4` = 16 ms (see PROTOCOL.md). [code]
  60 ticks ≈ 0.96 s; 6 ≈ 0.1 s; 1800 ≈ 28.8 s.
- `ReadChar` (`65:05d4`): deadline = now + timeout; polls `cbInQue` (slot 0x88) and reads one byte
  (slot 0x84); none by the deadline → **Fail(101)**. [code]
- Idle chore every ~0.64 s; kinetic timer every 1000 ms (§7). [code]

### 3.5 Error codes raised by this interface

| Code | Meaning | Where |
|---|---|---|
| 101 | timeout, nothing received | `65:067d`, `66:12ac`, `66:1f0c` |
| 105 | partial line / wrong reply to resync CR | `66:12b4`, `66:1f0c` |
| 106 | port not open | `63:13ba`, `63:1490` |
| **300 + n** | instrument sent `BEL n CR x` | `66:251d`, `66:2e59` |
| 301 | also: feature not supported by this model (`Fail(0x12d)`) | `63:1965`, `63:1abe`, `66:10fe` |
| 350 | echoed character differs | `66:2499` |
| 351 | requested wavelength is not one of the 6 configured filters | `66:2954` |
| 951 | more than one wavelength requested (TMax reads one, or one dual pair) | `66:108e` |

The Mac async path used Mac OS error numbers: −350 echo mismatch, −352 ring full, −399 BEL
received (`67:00c4`, `67:00e9`, `67:04f9`). `66:0598` still maps a stored −399 to "read the
number after BEL, Fail(atoi + 300)" — the origin of the +300 offset. On Windows the stored code
is already `300+n`. [code]

### 3.6 Original Mac timing (dead code, informative)

The async chain for one kinetic read (`67:0664 → 0490 → 0357 → 0197/0000 → 0490 → 0075 →
0000`) [code]:

1. write each command char (60 ticks), read its echo (120 ticks);
2. after the last echo, read **1 lead byte** with a 1860-tick (~31 s) timeout; BEL → error;
3. read **192 bytes** into the ring line (1800 ticks);
4. if the temperature command `DS:0bb4` is non-empty (ThermoMax: `"RDT"`, `66:01fe`; subclasses
   clear it, `67:0856`): send it with echo, read **1 lead byte** (31 s), then exactly **7 bytes**
   into `line+0xc0` (1800 ticks);
5. advance the ring write index (8 lines of 0xc7 = 199 bytes).

This matches the Windows code (lead byte + 192 bytes; RDT reply = 1 ignored char + temperature)
and pins the RDT reply length at **8 bytes** (1 + 7) on the original firmware. [code / inferred]

---

## 4. Commands

Command strings `66:002c`–`66:0089`, far pointers at `DS:0b58`–`DS:0bac`. All are sent with the
echo protocol of §3.1, usually via slot 0x148 (flush + retry).

| Cmd | Args / exact bytes | Sent by | Reply | Meaning |
|---|---|---|---|---|
| `RMT` | — | before any state change (`66:0310`, `0452`, `13d8`, `25aa`) | echo | remote mode [inferred] |
| `LCL` | — | after state changes; **idle keep-alive every ~0.64 s** (`66:1fc0`) | echo | local mode [inferred] |
| `OPN` | — | open drawer `66:0310` (RMT OPN LCL), also automatically after every read | echo | drawer open; host sets `this+0x58 = 1` |
| `CLS` | — | close drawer `66:0452` (RMT CLS LCL) | echo | drawer close; `this+0x58 = 0` |
| `SHK` | — | before a read if "automix before first read" is on (`66:25aa`); after kinetic read 1 if "between reads" is on (`66:0bc0`); detection | echo (Emax: error) | automix on [inferred] |
| `DSH` | — | same places when the option is off | echo | automix off [inferred] |
| `"SKO3 "` | 5 bytes `53 4B 4F 33 20` | manual shake button, slot 0xb8 `66:194a`, only if arg ≠ 0 | echo; **last echo (the space) may take up to 1800 ticks** | shake now; "3" and the space are [unknown] (duration?) |
| `SWV<a>` | 4 bytes, `a` = `'1'`+filter index | wavelength setup, single (`66:282f`) | echo | select filter `a` (1–6) |
| `DWV<a>,<b>` | 6 bytes, `a`,`b` = `'1'`+index | wavelength setup, dual (`66:2794`) | echo | measure filter `a`, reference filter `b` |
| `RDP` | — | endpoint read, calibrate on (`66:0de0`) | data block | read plate [inferred: with calibration] |
| `RNE` | — | endpoint read, calibrate off (`66:0e1b`) | data block | read plate, no calibration [inferred] |
| `STK` | — | kinetic read 1, single wavelength (`66:0b46`) | data block | start kinetic [inferred] |
| `NXK` | — | kinetic reads 2…N, single (`66:3225`, buffer `DS:345a`) | data block | next kinetic [inferred] |
| `RDR` | — | kinetic read 1, dual (`66:0b0b`) | data block | |
| `RNR` | — | kinetic reads 2…N, dual | data block | |
| `TMP<t>` | `"TMP"` + `Left(sprintf("%#4.1f", T), 4)`, e.g. `TMP37.0` (7 bytes) | ThermoMax, set incubator (`66:13d8`) | echo | setpoint |
| `CON` | — | after TMP | echo | incubator on |
| `CFF` | — | instead of TMP/CON when the setpoint is 0 / incubator off | echo | incubator off |
| `RDT` | — | ThermoMax: idle tick; after every data block | line | actual temperature |
| `RDS` | — | ThermoMax: idle tick | line | setpoint |
| `STS` | — | ThermoMax: idle tick (from `66:17ba` only) | line | status bits |
| `VER` | — | detection; ThermoMax connect | 2 lines | identity |

### 4.1 Wavelengths / filters (`66:2762`, slot 0x138; `66:28d2`, slot 0x13c)

[code]

- These readers have a **6-position filter wheel and no monochromator**. The wavelengths of the
  six positions are not asked from the instrument: they come from the user's
  *Preferences → Filters…* dialog (DLOG 1003, fields "1:"…"6:"), read through
  Director slot 0x48 (`66:2910`) as six 16-bit integers.
- `FilterIndex(wl)` returns the 0-based position whose configured wavelength equals `wl`
  exactly; none → Fail(351).
- Single: `"SWV1"` with byte 3 += index. Dual (plate flag `+0x22`, `seg35:0d8e`): `"DWV1,1"`,
  byte 3 += index(wavelength[0]), byte 5 += index(wavelength[1]).
- `DS:0b56` = 5 (single) or 10 (dual): seconds subtracted from "now" to timestamp the start of
  kinetic read 1, i.e. the expected read duration. [code; meaning inferred]
- Only one wavelength (or one dual pair) per run: `66:1006` fails with 951 if the plate asks for
  more than one wavelength. [code]

### 4.2 Temperature (ThermoMax only)

[code]

- Range offered to the UI (slot 0xcc, `66:19d5`): incubator present, **15.0 – 42.0 °C**
  (`DS:0bd6`, `DS:0bda`). Other models (`67:0882`): no incubator, 0, 0.
- Set (`66:13d8`, slot 0xac; called from `66:25aa` before every read and from the UI):
  `RMT`, then `CFF` if `atof(text) == 0`, else `TMP` + first 4 chars of the text, then `CON`;
  then `LCL`. The text comes from `sprintf("%#4.1f", setpoint)` (`66:2544`), or `"0"` when the
  plate has the incubator off. Subclasses: slot 0xac is empty (`67:08f7`).
- Read (`66:17ba`, slot 0x120): `RDS` → line → `Mid(1, 4)` → setpoint text (`this+0x5a`);
  `RDT` → line → `Mid(1, 4)` → actual text (`this+0x5e`); then `STS`.
  The **first character of each reply line is skipped**, characters 1–4 are the value, the rest
  up to LF is ignored. Example consistent with the code: `?37.0??\n` (8 bytes; § 3.6).
- After each data block (`66:2b22`, slot 0x12c arg 1): `RDT` → line → `Mid(1,4)` → `this+0x5e`.
  The value is stored with the read (`atof`, `66:2a93`, `seg35:07d4`).

### 4.3 STS (`66:1626`, slot 0x140, ThermoMax only)

[code] `STS` → line; `b = line[0] - '0'` (the first character, **not** skipped):

| bit | effect |
|---|---|
| 0 | `this+0x59` = 1 if set else 0 — "incubator on", returned by slot 0xd0 and recorded with the plate (`64:15d6`) [meaning inferred] |
| 1 | if set: drawer open (`this+0x58 = 1`) |
| 2 | if set: drawer closed (`this+0x58 = 0`); checked after bit 1, so it wins |

If neither bit 1 nor 2 is set the drawer state is unchanged.

### 4.4 Data block → plate (`66:2965`, slot 0x128)

[code]

- 192 bytes = **96 signed 16-bit integers, row-major**: word `k = 12·row + (col−1)`, i.e.
  A1 A2 … A12 B1 … H12 (row 0–7 = A–H, col 1–12).
- Each word is read with `fild word` (`66:2a19`) → **little-endian** on this x86 build
  (first byte = low byte). The bytes are copied without swapping (`66:2cba`, `66:2e6f`). See
  open question 1: the code was ported from the 68k Mac, where the same code reads big-endian.
- `value == -1000` (`DS:0bde`) → replaced by the global double `DS:432a` (a "no value" code,
  set at run time) [code; value unknown]. The host itself writes −1000 (`0xFC18`) into all 96
  words of kinetic reads it had to skip (`66:2f59`, slot 0x150).
- Otherwise **OD = value / 1000.0** (`DS:0be6`). Units on the wire: **milli-OD**, range
  −32.768 … +32.767 OD.
- Only columns `first … first+count−1` (plate fields `+0x16`, `+0x18`, 1-based) are stored, but
  all 192 bytes are always read. The TMax capability flags disable partial-plate strips, so the
  dialog forces first = 1, count = 12 (`88:0399`). [code]
- No saturation/over-range marker is handled other than −1000. [code]

---

## 5. Per-model differences

### 5.1 Slot overrides

Slots identical in all four vtables are inherited from `CSTMaxInterface` (66) or the bases (63/65).
Differences [code]:

| Slot | Role (Flash equivalent) | ThermoMax (66) | UVmax | Vmax | Emax (63) |
|---|---|---|---|---|---|
| 0x00 | class name | `66:0010` | `67:0815` | `67:0b5e` | `63:1756` |
| 0x04 | dtor | `66:0263` | `67:085f` | `67:0ba3` | `63:179b` |
| 0x94 | poll | `66:0598` | = | = | `63:19ae` (no kinetic logic) |
| 0xac | set temperature | `66:13d8` TMP/CON/CFF | `67:08f7` no-op | = | = |
| 0xb8 | manual shake (`SHAKE NOW`) | `66:194a` `"SKO3 "` | = | = | `63:1965` **Fail(301)** |
| 0xbc | type + description | 4 | 3 | 2 | 1 |
| 0xcc | incubator range | `66:19d5` yes, 15–42 °C | `67:0882` none | = | = |
| 0xd4 | capability flags | `66:2ef0` | `67:0ae5` | = | `63:1adc` |
| 0x11c | start kinetic read | `66:08f6` | = | = | `63:1abe` **Fail(301)** |
| 0x120 | read temperatures/status | `66:17ba` RDS RDT STS | `67:08be` "0.0","0.0", no I/O | = | = |
| 0x12c | temperature after a read | `66:2b22` RDT | `67:0910` "0.0" | = | = |
| 0x144 | identity | `66:1b4a` VER | `67:099a` "UVmax" | `67:0c22` "Vmax" | `63:181a` "Emax" |
| 0x154… | ctors (each class appends its own ctor slot: UVmax 0x154, Vmax 0x158, Emax 0x15c) | `66:00f0` | `67:0831` | `67:0b7a` | `63:1772` |

**UVmax and Vmax are protocol-identical**; they differ only in the type number and name. The
"UV" capability is just which filters the user configures. [code]

### 5.2 Capability block (`this+0x2a`, copied by slot 0xe8, used by the Instrument Settings dialog `88:02fc`)

| Field | Flash `68:18bb` | ThermoMax | UVmax / Vmax | Emax | Meaning |
|---|---|---|---|---|---|
| `+0x2a` byte | 1 | **1** | 0 | 0 | incubator [inferred] |
| `+0x2b` byte | 1 | 1 | 1 | **0** | kinetic mode [inferred; Emax fails 0x11c] |
| `+0x2c` byte | 1 | 0 | 0 | 0 | spectrum mode (gates items 0x24/0x25) [inferred] |
| `+0x2d` byte | 1 | 0 | 0 | 0 | partial-plate strips; 0 forces strips 1–12 (`88:036b`) [inferred] |
| `+0x2e` byte | 1 | 1 | 1 | **0** | automix [inferred; Emax fails 0xb8] |
| `+0x2f` byte | 1 | 0 | 0 | 0 | [unknown] |
| `+0x30` word | 6 | 2 | 2 | 2 | max wavelengths (dual = 2) [inferred] |
| `+0x32` word | 6 | 6 | 6 | 6 | filter positions [inferred] |
| `+0x34` word | 240 | 0 | 0 | 0 | monochromator min nm [inferred] |
| `+0x36` word | 800 | 0 | 0 | 0 | monochromator max nm [inferred] |

### 5.3 Summary

| | ThermoMax | UVmax | Vmax | Emax |
|---|---|---|---|---|
| Endpoint | yes | yes | yes | yes |
| Kinetic | yes | yes | yes | **no** (301) |
| Spectrum | no (301) | no | no | no |
| Single / dual wavelength | both | both | both | both |
| Filters | 6, user-configured | 6 | 6 | 6 |
| Automix SHK/DSH | yes | yes | yes | **SHK rejected** (detection relies on it). The read path (`66:25aa`, inherited) still sends `SHK` or `DSH` [code]; with the automix flag `+0x2e` = 0 the dialog presumably keeps automix off, so an Emax normally gets `DSH` — whether it accepts `DSH` is [unknown] |
| Manual shake `SKO3 ` | yes | yes | yes | no (301) |
| Incubator 15–42 °C, TMP/CON/CFF/RDT/RDS | **yes** | no | no | no |
| STS drawer readback | **yes** | no (drawer state = last command) | no | no |
| Min. kinetic interval (slot 0xc4, `66:1e05`) | 6 s single, 9 s single+automix, 14 s dual, 19 s dual+automix | same | same | n/a |

---

## 6. Idle polling and drawer

### 6.1 Idle traffic (state 1), every ~0.64 s [code]

- UVmax / Vmax / Emax: `LCL` (flushed, echoed). Nothing else.
- ThermoMax: `RDS`→line, `RDT`→line, `STS`→line, `LCL`.
- Then the poll (slot 0x94) runs; in state 1 it does no I/O except the quirk in 6.3.

### 6.2 Drawer

- Open `66:0310`: `RMT`, `OPN`, `LCL`; `this+0x58 = 1`. Close `66:0452`: `RMT`, `CLS`, `LCL`;
  `+0x58 = 0`. Getter `66:054b` returns `+0x58`. The ctor starts with "open" (`66:0112`). [code]
- Only the ThermoMax updates `+0x58` from the instrument (STS bits 1/2). [code]
- The host never sends `CLS` before a read; the firmware presumably closes the drawer itself
  [inferred]. After every endpoint read, after the last kinetic read, and after an aborted or
  failed read the host sends `RMT OPN LCL` (`66:0fa7`, `66:07ea`, handlers `66:110d`, `66:0815`). [code]

### 6.3 Quirk: repeated drawer-open after a kinetic run

[code, consequence inferred] The poll's completion test (`66:0769`) runs in **every** state:
`if (N < readNo) or stop == 2 or (stop == 1 and no error) → finish`, where `N = this+0x44` and
`readNo = this+0x40` are only ever set by the kinetic start (`66:0941`, `66:094e`) and never
reset. After a kinetic run `readNo = N + 1`, so every subsequent idle poll repeats the finish
actions, including `RMT OPN LCL`, until another kinetic run starts. A simulator should tolerate
`RMT OPN LCL` every ~0.64 s after a kinetic run; a new driver should not copy this.

---

## 7. Reads

### 7.1 Common preparation (`66:1006`, slot 0xa4 → `66:25aa`, slot 0x124) [code]

1. Plate checks: more than one wavelength → 951. Mode 0 endpoint, 1 kinetic, other → 301.
2. `RMT`
3. `SHK` if "automix before first read" (plate `+0x5c`), else `DSH`.
4. Temperature: ThermoMax `RMT`, (`CFF` | `TMPxx.x` `CON`), `LCL`; others nothing.

### 7.2 Endpoint (`66:0d58`, slot 0x118) [code]

state = 2

5. `SWV<a>` / `DWV<a>,<b>`
6. `RDP` if auto-calibrate (plate `+0x70`, the "Calibrate" popup) else `RNE`
7. data block (lead + 192 bytes, timeout 1800 ticks per byte); ThermoMax then `RDT` → line
8. convert, store, timestamp = host clock (`ASIPORT2.GETDATETIME`, seconds)
9. state = 1; `RMT` `OPN` `LCL`

### 7.3 Kinetic (`66:08f6`, slot 0x11c) — **timed by the host**

[code]

- `N = runTime / interval + 1` (plate `+0x48`, `+0x4c`, seconds; `66:0938`); 16-bit field.
- state = 3, `readNo = 1`.
- `SWV`/`DWV`; then **`STK`** (single) or **`RDR`** (dual) → data block (+`RDT`) = read 1.
- `SHK` if "automix between reads" (plate `+0x66`), else `DSH`.
- `t_last = now − DS:0b56` (now minus 5 or 10 s), `readNo = 2`.
- Start a Windows timer, **1000 ms** (`63:170c`, callback `63:1697` → CSReader slot 0xec →
  interface slot 0x10c `66:2ff8`).

Each timer tick (`66:2ff8`):

1. If an earlier tick stored an error (`DS:3b2e < 0`) → return.
2. If `cbInQue ≥ 192`: read the data block (lead + 192, + ThermoMax `RDT`) into ring line
   `DS:3b58` (8 lines × 199 bytes at `DS:3464`); copy the 7-char temperature text to
   `line+0xc0`; advance the write index.
3. `elapsed_ms = (now − t_last) · 1000` (1-second resolution).
   - `elapsed < interval·1000` → return.
   - `elapsed ≤ 1.1·interval·1000 + 1000` → send **`NXK`** (single) / **`RNR`** (dual) (flush,
     echo, retry); `t_last = now`.
   - later than that → do **not** send; mark reads missed: while `now − t_last > interval`:
     fill a ring line with −1000, `t_last += interval`, advance. The next tick sends normally.
4. Exceptions → `DS:3b2e = −code` (handler `66:30c8`); the poll raises it.

The poll (every ~0.64 s, `66:0598`) moves at most one ring line per call into the plate
(slot 0x128 → `66:2cba`, temperature from `line+0xc0`, `Left(…, 4)`), increments `readNo`,
and when `readNo > N` (or the user stopped) kills the timer, sets state 1 and sends
`RMT OPN LCL`.

Consequences for an implementation:

- The instrument receives one read command per interval and answers each with one data block.
  It never needs to know the interval or the number of reads. Nothing is sent to stop a run.
- The host checks for data before deciding to send the next command, but sends regardless of
  whether the previous block has arrived; the send starts with `FlushComm`, so a block still
  arriving would be lost.
- Minimum interval enforced by the dialog: 6 / 9 / 14 / 19 s (§5.3, "Read interval is too
  short. Minimum N seconds."); maximum effectively 32767 reads.

---

## 8. Example transcripts

Notation: `H:` host→instrument, `I:` instrument→host, `⟲` = the instrument echoes each host
byte immediately (non-final bytes within 0.1 s, final byte within 1 s unless noted).
`<lead>` = one byte of unknown value (not BEL). `<192>` = 96 × int16, row-major A1…H12,
mOD, little-endian as this build reads it. `·` separates commands; there are no terminators.

### 8.1 Vmax: connect, detect, endpoint read

Preferences say Vmax; plate: 405 nm in filter position 3, single wavelength, automix off,
calibrate on.

```
-- chore tick, state 0 → connect (66:1fc0)
[FlushComm]
H: 0D                         I: 0D            (echo within 6 ticks)
H: 4C 43 4C  "LCL"  ⟲
[FlushComm]
H: "LCL" ⟲
-- detection (64:0cea)
[FlushComm]                                    ("" probe: nothing sent)
[FlushComm]
H: "VER" ⟲
I: "<vendor>; Vmax VER <ver>\r\n"              line 1 (Left 2 = "Vm": no match)  [format unknown]
I: "<…>\n"                                     line 2
[FlushComm]
H: "SHK" ⟲                                     accepted → Vmax (2), no change
-- idle, every ~0.64 s
H: "LCL" ⟲
-- user clicks Read (66:1006 → 66:25aa → 66:0d58)
H: "RMT" ⟲ · "DSH" ⟲ · "SWV3" ⟲ · "RDP" ⟲
I: <lead> <192 bytes>                          each byte within 28.8 s
H: "RMT" ⟲ · "OPN" ⟲ · "LCL" ⟲
-- idle again
H: "LCL" ⟲ ...
```

ThermoMax variant: the connect adds `VER` (2 lines) after the first `LCL`, then
`RDS`→line, `RDT`→line, `STS`→line before the second `LCL`; detection sees `TH…`; the idle
tick is `RDS RDT STS LCL`; the read becomes
`RMT DSH RMT TMP37.0 CON LCL SWV3 RDP <lead><192> RDT→line RMT OPN LCL`
(or `… RMT CFF LCL …` with the incubator off).

### 8.2 ThermoMax kinetic read

Plate: 405 nm (position 3) single, run 2:00, interval 0:30 → N = 5; automix before first
read on, between reads off; incubator 37.0 °C.

```
t=0     H: "RMT" ⟲ · "SHK" ⟲ · "RMT" ⟲ · "TMP37.0" ⟲ · "CON" ⟲ · "LCL" ⟲
        H: "SWV3" ⟲
        H: "STK" ⟲                                         read 1
        I: <lead> <192>                                    (~5 s, per DS:0b56)
        H: "RDT" ⟲         I: "?37.0??\n"                  ? = unknown bytes; host keeps chars 1..4
        H: "DSH" ⟲                                          automix between reads off
        (now ≈ 5 s; t_last = now − 5 s ≈ 0; 1 s timer starts; chore no longer sends idle traffic)
t≈30 s  H: "NXK" ⟲                                         read 2 (now − t_last ≥ 30 s); t_last = 30
t≈35 s  I: <lead> <192>       → next 1 s tick sees ≥192 bytes:
        H: "RDT" ⟲         I: "?37.1??\n"
t≈60 s  H: "NXK" ⟲  …                                      reads 3, 4, 5 likewise (t≈90, 120)
after read 5 has been moved into the plate by the poll:
        H: "RMT" ⟲ · "OPN" ⟲ · "LCL" ⟲
then every ~0.64 s:  H: RDS RDT STS LCL  (+ RMT OPN LCL, see 6.3)
```

UVmax/Vmax: identical without `RMT TMP/CFF CON LCL` and without the `RDT` exchanges. Dual
wavelength: `DWV3,5`, first read `RDR`, later reads `RNR`.

Error example: if a command is rejected, the instrument sends `07 '1' '2' 0D 0A` instead of
an echo; the host raises error 312, after first trying once more following a BREAK + CR
resync (§3.2).

---

## 9. Open questions

Only a real instrument (or a capture of the original Mac/Win software talking to one) can
answer these:

1. **Byte order of the 192-byte block.** This Windows build reads little-endian; the code is a
   straight port of the 68k Mac version, which would read the same bytes big-endian. Either the
   firmware sends low byte first, or this path never worked on Windows. A driver should make it
   configurable (or pick the order that gives plausible values, −1000…4000 mOD).
2. **The lead byte** before the data block (and before the RDT reply in the Mac code): its value,
   and whether it arrives right after the command echo or when the read finishes.
3. **VER text**: exact line 1 (`<…>; <model> VER <version>`), model spellings (only `TH`, `UV`,
   `Em` prefixes are known), content of line 2. Which instrument answers VER with error 1
   (→ host error 301 → treated as SpectraMax)?
4. **RDT/RDS reply**: meaning of the first character and of the 3 bytes after the 4-digit value
   (Mac code implies exactly 8 bytes total, ending in LF).
5. **STS** bit meanings beyond those the host uses (bit 0 incubator, bit 1 open, bit 2 closed);
   other characters on the line.
6. **When the last echo is sent**: immediately, or after the command completes (OPN/CLS motion,
   TMP). The host allows ~1 s for most commands and ~29 s for `SKO3 `.
7. **Command parsing without terminator**: does the firmware rely on fixed lengths (TMP + 4
   chars, SWV + 1, DWV + 3, `SKO` + 2?) What do `3` and the space in `"SKO3 "` mean?
8. **CR handling**: the connect expects a lone CR to be echoed; the resync after BREAK expects
   `BEL n CR x` in reply to a lone CR.
9. **Error numbers** n in `BEL n CR`: list and meanings (host shows 300+n). What is the
   character after CR (LF assumed)?
10. Semantics of `RDP` vs `RNE` (calibration?), `STK/NXK` vs `RDR/RNR`, `SHK/DSH` (automix
    on/off and its duration), `RMT/LCL`.
11. Does the firmware ever send −1000 (0xFC18) or another code for over-range / saturated wells?
12. Does the reader close the drawer by itself on a read command?
13. Read duration (host assumes 5 s single, 10 s dual) and how many bytes a busy instrument
    accepts while reading.
14. Does an Emax really reject `SHK` (detection depends on it), and how does an Emax behave on
    the `SHK`/`DSH` sent before every read?
15. Confirm the post-kinetic `RMT OPN LCL` repetition (§6.3) with the original software.

---

## Appendix A: interface vtable slots used here (TMax column = `CSTMaxInterface`)

| Slot | TMax impl | Function |
|---|---|---|
| 0x68 | `63:1622` | store serial config/buffer (`+0x26`, `+0x28`) |
| 0x6c | `63:0f32` | (re)open port |
| 0x7c | `63:124e` | FlushComm in+out |
| 0x80 | `63:13fa` | WriteComm(CString) |
| 0x84 | `63:12a6` | ReadComm |
| 0x88 | `63:14b3` | bytes in receive queue |
| 0x8c | `63:1525` | send BREAK |
| 0x94 | `66:0598` | poll |
| 0x98 / 0x9c / 0xa0 | `66:0310` / `0452` / `054b` | open / close / drawer state |
| 0xa4 | `66:1006` | start read |
| 0xa8 | `66:11bc` | resync (BREAK + CR) |
| 0xac | `66:13d8` | set temperature |
| 0xb0 | `66:1715` | get setpoint/actual temperature strings |
| 0xb8 | `66:194a` | manual shake |
| 0xbc | `66:1dd0` | type id + description |
| 0xc0 | `65:03c5` | filter list from instrument: six zeros (TMax doesn't report filters) |
| 0xc4 | `66:1e05` | minimum kinetic interval |
| 0xcc | `66:19d5` | incubator present + range |
| 0xd0 | `66:1a29` | incubator-on flag (`+0x59`) |
| 0xd4 | `66:2ef0` | fill capability block |
| 0xe0 | `65:049c` | state (`+0x3c`: 0 none, 1 idle, 2 endpoint, 3 kinetic) |
| 0xf0 | `65:02aa` | stop request (`+0x3e`: 1 stop, 2 abort) |
| 0xf4 | `65:05d4` | ReadChar(timeout ticks) |
| 0xf8 | `66:2276` | send command (flush, echo, 1 retry) |
| 0xfc | `66:1e98` | read line to LF |
| 0x10c | `66:2ff8` | kinetic 1-s timer tick |
| 0x110 | `66:1fc0` | connect / idle refresh |
| 0x118 | `66:0d58` | endpoint read |
| 0x11c | `66:08f6` | kinetic start |
| 0x120 | `66:17ba` | read RDS/RDT/STS |
| 0x124 | `66:25aa` | read preparation (RMT, SHK/DSH, temperature) |
| 0x128 | `66:2965` | data → plate |
| 0x12c | `66:2b22` | temperature after a read |
| 0x130 | `66:2d96` | read lead + 192 bytes |
| 0x134 | `66:2cba` | take 192 bytes from ring |
| 0x138 | `66:2762` | SWV/DWV |
| 0x13c | `66:28d2` | wavelength → filter index |
| 0x140 | `66:1626` | STS |
| 0x144 | `66:1b4a` | identity (VER) |
| 0x148 | `66:21a8` | send command, ignore reply |
| 0x14c | `66:23b2` | send with per-char echo |
| 0x150 | `66:2f59` | fill ring line with −1000 |

## Appendix B: data-segment globals

| Address | Use |
|---|---|
| `DS:0b56` | 5 / 10 — assumed read duration, s (single / dual) |
| `DS:0b58`–`0bac` | far pointers to the command strings |
| `DS:0bb0` (long) | timeout for the final echo, ticks (60; 6 during connect CR; 1800 during `SKO3 `) |
| `DS:0bb4` | temperature command for the Mac async path (`"RDT"` ThermoMax, `""` others) |
| `DS:0bd6`, `0bda` (float) | 15.0, 42.0 °C |
| `DS:0bde` (double) | −1000.0 missing-value sentinel |
| `DS:0be6` (float) | 1000.0 mOD → OD |
| `DS:0bea` (double) | 1.1 kinetic lateness factor |
| `DS:3b2a` | kinetic interval, ms |
| `DS:3b2e` | stored background error (negative) |
| `DS:3b58` / `3b5a` | ring write / read index (mod 8) |
| `DS:3464` | ring: 8 × 199 bytes (192 data + 7 temperature text) |
| `DS:344c` | time of the last kinetic read command, s |
| `DS:345a` | next-read command (`NXK` / `RNR`) |
| `DS:3b68` | send attempt counter |
