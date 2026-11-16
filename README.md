<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/logo/odstudio-dark.svg">
    <img src="docs/logo/odstudio-light.svg" alt="OD Studio logo" width="420">
  </picture>
</p>

<p align="center">
  <b>Open-source software for the Molecular Devices SpectraMax 250 microplate reader.</b><br>
  A modern replacement for SOFTmax PRO 1.x on Windows, macOS and Linux.
</p>

---

OD Studio runs absorbance reads on a **SpectraMax 250** plate reader from a current computer and
analyses them: **endpoint, kinetic and spectrum** reads, Vmax and other kinetic reductions,
standard curves (linear, 4-parameter logistic, Michaelis–Menten…), ELISA, Bradford, BCA and Lowry
assay templates, workflows, and export to Excel/CSV. It opens files saved by the original
SOFTmax PRO (`.pda`, `.ppr`, text exports) and includes a **simulator**, so you can try
everything without the instrument.

> [!WARNING]
> **Early release, not yet tested on a real instrument.** The serial protocol was
> reverse-engineered from the original software, and so far OD Studio has only been checked
> against its simulator. Try it with an empty plate first and compare results with a known
> sample. Reports from real instruments are very welcome; please
> [open an issue](https://github.com/constanrine5d/OD-Studio/issues).

## Install and start

You need **Python 3.9 or newer**, installed once from
[python.org/downloads](https://www.python.org/downloads/). Everything else is included and works
offline.

Download OD Studio with the green **Code › Download ZIP** button (or `git clone`) and unzip it.
Then:

| System | Start OD Studio |
|---|---|
| **Windows** | Double-click **`OD Studio.bat`**. When installing Python, keep "py launcher" selected. |
| **macOS** | Double-click **`OD Studio.command`**. The first time, right-click it › **Open** › **Open** (macOS asks because it was downloaded). |
| **Linux** | In a terminal in the folder: `bash "OD Studio.command"` |

The program opens in your web browser. It runs only on your own computer and nothing is sent
anywhere. To try it, choose **Edit › Preferences › Serial Port › Simulator**. **Help › OD Studio
Help** explains each part.

## Connect the SpectraMax 250

The reader talks over an **RS-232 serial port** (9600 baud, 8N1; OD Studio sets this itself).
Most modern computers have no serial port, so use a **USB-to-serial adapter** (FTDI-based ones
are the most trouble-free).

1. Switch the reader on and connect the reader's serial cable to the adapter, and the adapter to
   the computer.
2. Find the port the reader is on, as described in [Find the port](#find-the-port) below.
3. In OD Studio, open **Edit › Preferences**, pick the port under **Serial Port** (or type it in
   the box below the list), and click OK.
   The status bar shows the instrument and its temperature when it answers.
4. Put an empty plate in and do a first **Read** to check that everything works.

**If the reader does not answer**, check that it is switched on and that the port is right, then
try a **null-modem adapter**: some cables are wired straight-through and others crossed. For
details, run `odstudio -v -p PORT status`, which prints every byte exchanged (see
[docs/COMMAND_LINE.md](docs/COMMAND_LINE.md)).

### Find the port

The easiest way: **Edit › Preferences › Serial Port** lists every serial port the computer can
see, with the adapter's name, for example `COM3 — USB Serial Port` or
`/dev/cu.usbserial-A10K4XYZ — FT232R USB UART`. The list is read each time Preferences opens.

**Not sure which one is the reader?** Open Preferences with the adapter unplugged and note the
list. Close it, plug the adapter in, wait a few seconds and open Preferences again. The new entry
is the reader. If none appears, the adapter's driver is missing (see your system below).

<details>
<summary><b>Windows</b>: port names look like <code>COM3</code></summary>

1. Right-click the **Start** button and choose **Device Manager**.
2. Expand **Ports (COM & LPT)**. The adapter shows as, for example, *USB Serial Port (COM3)* or
   *Prolific USB-to-Serial Comm Port (COM5)*. The name in brackets is the port.
3. Not there? Look under **Other devices** for an entry with a yellow warning sign. That means
   the driver is missing: install it from the adapter's maker (FTDI, Prolific or WCH/CH340,
   depending on the chip), then unplug and replug the adapter.
4. Windows gives an adapter a different number in each USB socket, so always use the same
   socket. To fix the number, open the port's **Properties › Port Settings › Advanced › COM Port
   Number**.

Or, in PowerShell:

```powershell
Get-CimInstance Win32_PnPEntity | Where-Object Name -match '\(COM\d+\)' | Select-Object Name
```

A computer with a serial socket built in usually has it as `COM1`.
</details>

<details>
<summary><b>macOS</b>: port names look like <code>/dev/cu.usbserial-…</code></summary>

1. Open **Terminal** and list the serial ports with the adapter unplugged:

   ```bash
   ls /dev/cu.*
   ```

2. Plug the adapter in and run the same command again. The new line is the reader, for example
   `/dev/cu.usbserial-A10K4XYZ`, `/dev/cu.usbserial-1420` or `/dev/cu.wchusbserial1420`.
   Use the `cu.` name, not the `tty.` one. `Bluetooth-Incoming-Port` and `debug-console` are
   never the reader.
3. No new line? Check that macOS sees the adapter at all: hold **Option**, open the
   **Apple menu › System Information › USB**. If the adapter is listed there but no port
   appears, install the driver from the adapter's maker. Recent macOS versions include drivers
   for the most common adapter chips.
</details>

<details>
<summary><b>Linux</b>: port names look like <code>/dev/ttyUSB0</code></summary>

1. Plug the adapter in, then run:

   ```bash
   sudo dmesg | tail
   ```

   A line such as `... now attached to ttyUSB0` names the port (`/dev/ttyUSB0`).
   Or compare `ls /dev/ttyUSB* /dev/ttyACM*` before and after plugging it in.
2. `ttyUSB0` may become `ttyUSB1` when other adapters are plugged in. A name that never changes
   is listed by:

   ```bash
   ls -l /dev/serial/by-id/
   ```

   for example `/dev/serial/by-id/usb-FTDI_FT232R_USB_UART_A10K4XYZ-if00-port0`. Type that
   into the box under the Serial Port list in Preferences.
3. Allow your user to use serial ports (once), then log out and back in:

   ```bash
   sudo usermod -aG dialout $USER
   ```

   On Arch-based systems the group is `uucp` instead of `dialout`. `groups` shows whether
   you are in it.

A computer with a serial socket built in usually has it as `/dev/ttyS0`.
</details>

If you installed the command line ([docs/COMMAND_LINE.md](docs/COMMAND_LINE.md)),
`odstudio ports` prints the same list as Preferences.

## Documentation

- [Feature list](docs/FEATURES.md): every SOFTmax PRO 1.x feature and where to find it, plus
  what OD Studio adds.
- [Command line, Python and simulator](docs/COMMAND_LINE.md): scripted reads, the Python API,
  and a simulator for other programs.
- [Serial protocol](docs/PROTOCOL.md): how the SpectraMax 250 is driven, with open questions.

## Disclaimer

- **Not affiliated with Molecular Devices.** SpectraMax and SOFTmax are trademarks of
  Molecular Devices, LLC, used here only to say which instrument and files OD Studio works with.
  OD Studio is independent and not endorsed or supported by Molecular Devices.
- **No original software is included.** OD Studio is new code. The protocol was worked out from
  the original program only to make the instrument usable on current computers
  (interoperability); no part of SOFTmax PRO is distributed here.
- **For research use only.** OD Studio is not a medical device and is not validated for
  diagnostic or clinical use, or for regulated work (GLP/GMP, 21 CFR Part 11). Check results
  against known samples before relying on them.
- **No warranty.** The software is provided "as is", without warranty of any kind, and you use
  it with your instrument at your own risk. See the [LICENSE](LICENSE).

## License

[MIT](LICENSE). Includes pySerial 3.5 (BSD-3-Clause, see `vendor/serial/LICENSE.txt`). The logo
lettering is Poppins (SIL Open Font License); the logo files are in [docs/logo](docs/logo).
