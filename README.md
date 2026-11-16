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
2. Find the port name:

   | System | How to find it | Looks like |
   |---|---|---|
   | Windows | Right-click **Start › Device Manager › Ports (COM & LPT)**: the adapter shows as e.g. *USB Serial Port (COM3)*.<br>Not listed? A ⚠ entry under **Other devices** means the adapter's driver is missing: install it.<br>The number changes with the USB socket, so keep using the same one. | `COM3` |
   | macOS | In Terminal, run `ls /dev/cu.*` before and after plugging the adapter in: the new entry is the reader.<br>Pick the `cu.` name (there is also a `tty.` one).<br>Nothing new? Install the driver from the adapter's maker. | `/dev/cu.usbserial-…` |
   | Linux | Right after plugging in, `sudo dmesg \| tail` shows *attached to ttyUSB0*.<br>A name that never changes: `ls /dev/serial/by-id/`.<br>Allow access once: `sudo usermod -aG dialout $USER` (`uucp` on Arch), then log out and back in. | `/dev/ttyUSB0` |

   **Not sure which one is the reader?** Unplug the adapter and open **Edit › Preferences**: the
   **Serial Port** list shows the ports the computer has now. Close it, plug the adapter in, and
   open Preferences again. The new entry is the reader. If nothing new appears, install the
   adapter's driver.

3. In OD Studio, open **Edit › Preferences**, pick the port under **Serial Port**, and click OK.
   The status bar shows the instrument and its temperature when it answers.
4. Put an empty plate in and do a first **Read** to check that everything works.

**If the reader does not answer**, check that it is switched on and that the port is right, then
try a **null-modem adapter**: some cables are wired straight-through and others crossed. For
details, run `odstudio -v -p PORT status`, which prints every byte exchanged (see
[docs/COMMAND_LINE.md](docs/COMMAND_LINE.md)).

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
