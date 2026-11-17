# Feature checklist: SOFTmax PRO 1.x → OD Studio

The list of original features was taken from the program itself: every menu, dialog and string
table in `SPF.RSR` (`python tools/rsrc.py binary/SOFTPRO/SPF.RSR menus|dialogs|strings`) and the
full text of its help file (`python tools/hlptext.py binary/SOFTPRO/SPF.HLP`).

**Status key:** ✅ done · ◐ done differently or partly · ❌ not available

**How each row was checked:** rows marked ✅ are covered by the automated tests in `tests/` or
were exercised in the running program against the simulator. Nothing here has been checked
against a real instrument yet (see `README.md`).

## Not available yet

| Original feature | Status | Notes |
|---|---|---|
| Open old SOFTmax (DOS-era) `.dat` / `.pro` files | ❌ | No sample files to work from. |
| Readers other than the SPECTRAmax (Emax, Vmax, UVmax, THERMOmax) | ✅ | Found automatically, or chosen in Preferences › Reader. Not yet tried on real hardware; see [PROTOCOL-TMAX.md](PROTOCOL-TMAX.md). |
| Normal / Diagnostic / Align popup in Instrument Settings | ✅ | Control › Factory Service: ADC Diagnostic / Alignment, behind warnings (Alignment needs an explicit acknowledgement). Not tried on a real reader. |
| Registration dialog, serial-number check | ❌ | Not needed. |

## File menu

| Original | Status | Here |
|---|---|---|
| New (opens another experiment window) | ✅ | File › New; starts from the Default Protocol when one is set |
| Several experiment windows open at once | ✅ | Tabs, or Window › Side by Side to see them next to each other; each window has its own plates and settings |
| Open… | ✅ | Opens `.odx` data files, `.odxp` protocols (and `.smx`/`.smxp` from before the rename), exported `.txt` files, **and the original program's `.pda` data and `.ppr` protocol files**, in a new window |
| Old `.pda` / `.ppr` contents | ✅ | Read: all plates, raw data (checked against the original's exports), pre-reads, instrument settings, templates, groups with their columns and summaries, notes, graph plots, axis labels and curve-fit type, wavelength formulas, the full reduction (kinetic type, Vmax points, lag/end time, OD limits, onset OD, plate blank, pre-read, relative OD), plate display type and range, and masked wells. Checked by reproducing the original's stored reduced values. Not read: graph colours and symbols, column number formats (they do not change results). |
| Close | ✅ | File › Close closes the window (asks to save) |
| Save, Save As… | ✅ | `.odx` files (plain JSON) |
| Protocol files (no data) | ✅ | File › Save As Protocol… (`.odxp`) |
| Export… (tab-delimited, Time or Plate format, with or without labels) | ✅ | File › Export…; same two layouts as the original |
| Page Setup… | ◐ | Paper size and orientation are chosen in the print window |
| Print "section" | ✅ | File › Print "name" |
| Print… (all sections included in the report, in order) | ✅ | File › Print… |
| Quit | ✅ | File › Quit |

## Edit menu

| Original | Status | Here |
|---|---|---|
| Undo | ✅ | Undo and Redo, for every change to the experiment (not only text) |
| Cut / Copy / Paste / Clear | ✅ | Plate data, group columns, summaries, notes text |
| Paste an 8 × 12 block of numbers into a plate | ✅ | Edit › Paste (reads the system clipboard, or shows a paste box) |
| Select All | ✅ | Wells of a plate, columns of a group |
| Delete Section | ✅ | Edit › Delete "name" |
| New Plate / New Graph / New Notes | ✅ | Edit menu |
| Text Style… | ✅ | Bold, italic, underline, outline, shadow, condensed, extended and size for columns and summaries; Notes have a format bar |
| Preferences… | ✅ | See Preferences below |
| Suspend Recalculation / Recalculate Now | ✅ | Edit menu |

## View menu

| Original | Status | Here |
|---|---|---|
| Minimize / Expand | ✅ | View menu; Alt-click a triangle does all sections |
| Hide / Show Status | ✅ | View menu |
| Section list | ✅ | View menu jumps to a section |
| Reorder sections by dragging the title bar | ✅ | Drag a section header onto another |

## Control menu and status bar

| Original | Status | Here |
|---|---|---|
| Instrument icon with connection state | ✅ | Green/red dot and instrument name |
| Temperature display | ✅ | Chamber temperature and set point |
| Read / Stop | ✅ | Button and Control › Read |
| Select Plate dialog (several plates; Pre-read or Normal) | ✅ | Shown when there is more than one plate or a pre-read is requested |
| Data appears while reading | ✅ | The plate fills in read by read |
| Open / Close Drawer | ✅ | Button and menu; drawer opens after a reading (can be switched off) |
| Incubator… | ✅ | Off / On with temperature |
| Automix button (shake now) | ✅ | Button and menu |
| Instrument Setup… | ✅ | See below |
| Hidden menu: Simulator, Chat Window, Read / Clear Error Log | ✅ | Port "Simulator" in Preferences; Control › Instrument Terminal…, Instrument Error Log… |

## Instrument Settings dialog

| Original | Status | Here |
|---|---|---|
| Mode: Endpoint, Kinetic, Spectrum | ✅ | |
| 1–6 wavelengths with common values offered | ✅ | Shortcut list is editable in Preferences |
| Kinetic Run Time and Interval, number of readings shown | ✅ | |
| "Run time is less than interval", "Read interval is too short. Minimum N seconds." | ✅ | Same read-time estimate as the original, shown live in the dialog |
| Spectrum Start / Stop / Step | ✅ | |
| Strips… (contiguous columns on a picture of the plate) | ✅ | Drag across the whole-plate picture |
| Installed filters in the wavelength list (F405 …) | ✅ | Preferences › Filters |
| "Auto-read after" and "begin reading at temperature" | ✅ | Start: wait for the set point, and/or start N seconds after Read |
| Auto Mix: Before First Read, Between Reads, seconds | ✅ | |
| Auto Calibrate On / Off | ✅ | |
| Pre-Read Plate | ✅ | |
| Changing settings deletes the plate's data after a warning | ✅ | |

## Plate section

| Original | Status | Here |
|---|---|---|
| 8 × 12 data display, settings summary beside it | ✅ | |
| Template… editor: groups, samples, descriptors, Assign, Clear | ✅ | |
| New group / edit group (name, descriptor, units, include in report) | ✅ | Group Settings dialog |
| Series… (first sample, start value, step operator, fill from Top/Bottom/Left/Right) | ✅ | |
| Plate blanks and group blanks | ✅ | Plate Blank and Group Blank buttons |
| Group colours and labels on the plate | ✅ | Always shown |
| Reduction…: wavelength combination presets and Custom formula | ✅ | Same preset lists per wavelength count |
| Kinetic: Vmax, Time to Vmax, Onset Time, Custom | ✅ | |
| Limits: Lag Time, End Time, Min OD, Max OD, Vmax Points, Onset OD | ✅ | |
| Spectrum: MaxLambda, MinLambda, Custom; Start / End limits | ✅ | |
| Subtract plate blank; pre-read blanking | ✅ | |
| Display…: Raw / Reduced; Number, Plots, Gray Scale, Ranged, Threshold; Low / High | ✅ | |
| Graph… (Well Graph of selected wells, options, Print) | ✅ | Double-click a well, or Well Graph |
| Well Graph legend: Vmax, Time to Vmax, Onset Time, R², Vmax Points, Wavelength Max/Min | ✅ | Table under the graph |
| Reduced value (e.g. Vmax) visible in each well | ✅ | Shown under each well's plot, in Raw and Reduced views |
| Mask | ✅ | |
| Copy Template / Paste Template (creates missing groups) | ✅ | |
| Plate Settings… (name, include in report) | ✅ | |
| Summaries appended to a plate | ✅ | Plate › Create Summary… |

## Group section

| Original | Status | Here |
|---|---|---|
| Created automatically for each template group | ✅ | |
| Default columns (Sample, Concentration, Wells, Values, MeanValue, Std.Dev., CV%) | ✅ | |
| Create / Edit Column (name, formula, decimal places) | ✅ | Double-click a column heading |
| Create / Edit Summary (name, description, formula, decimals, hide name) | ✅ | |
| Hide, Hide Replicates, Show All | ✅ | |
| Show / Hide Formulas | ✅ | |
| Autosize | ◐ | Columns always fit their contents |
| Group Settings… | ✅ | |
| Interpolating unknowns from a standard curve | ✅ | Formula `InterpX(...)`; Group › Add Interpolated Results… writes it for you |

## Graph section

| Original | Status | Here |
|---|---|---|
| Plots… (name, group, X, Y, error bars; New / Delete) | ✅ | |
| Fit: No Fit, Linear, Semi-Log, Log-Log, Quadratic, 4-Parameter | ✅ | Parameters and R² in the legend |
| Graph Type…: Scatter (connect points, symbols), Cluster Bar, Stack Bar, height % | ✅ | |
| X Axis… / Y Axis…: auto-range, min, max, linear/log, gridlines, tick marks, label | ✅ | |
| Graph Settings… | ✅ | |

## Notes section

| Original | Status | Here |
|---|---|---|
| Free text | ✅ | With bold, italic, underline, size, lists |
| Create / Edit Summary, Show Formulas | ✅ | |
| Notes Settings… | ✅ | |

## Formulas

| Original | Status | Here |
|---|---|---|
| Operators `+ - * / ^ Mod`, comparisons, `And Or Not` | ✅ | |
| `If`, `True`, `False` | ✅ | |
| Math functions (Abs … Tanh, Pi, Rand, Round, Fact, …) | ✅ | All 26 listed in the original help |
| Statistics: Average, Cv, Cvp, Max, Min, Stdev, Stdevp, Sum | ✅ | |
| Interpolation: Intercept, Slope, InterpX, InterpY, ParmA–D, RSquared | ✅ | |
| Text: Concat, Exact, Left, Len, Lower, Mid, Now, Right, Text, Today, Upper, Val | ✅ | |
| Other: Count, Index, Item, ItemCount, ItemIndex | ✅ | |
| Errors: IsEmpty, IsErr, MakeErr; values Name?, Masked, No Fit, Range?, Limits?, … | ✅ | |
| Accessors (`!WellValues`, `!Concentration`, `!KinPlot`, `!PlateType`, …) | ✅ | All listed in the help and in STR# 7000 |
| `Name@Section`, quoted names, case-insensitive names | ✅ | |
| Circular-reference warning | ✅ | |
| Formula Help | ✅ | Help › Formula Help… |

## Preferences

| Original | Status | Here |
|---|---|---|
| Serial Port | ✅ | Lists the computer's ports, plus the simulator |
| Reader | ✅ | Automatic, SpectraMax 250, ThermoMax, UVmax, Vmax or Emax |
| Autosave with folder and file prefix | ✅ | Saves `Prefix001.odx`, … after each reading |
| Autoprint | ✅ | Opens the print window after each reading |
| Default Protocol | ✅ | |
| Print Template / Page Number / Time / Filename | ✅ | |
| Export format: Time / Plate, Include Labels | ✅ | |
| Filters | ✅ | Six installed filter wavelengths |
| Print Driver Banding | ❌ | Not needed on modern printers |

## Assays and Help menus

| Original | Status | Here |
|---|---|---|
| Assays › Set Folder… and the list of protocols in it | ✅ | |
| Help › SOFTmax Pro Help | ✅ | Help › OD Studio Help (searchable, written for this program) and Formula Help |
| About (instrument type, ROM version) | ✅ | Help › About |


## Added beyond SOFTmax PRO 1.x

Chosen from SoftMax Pro 7's feature list; all covered by the tests in `tests/`.

| Feature | Where |
|---|---|
| Michaelis–Menten fit (Km, Vmax), e.g. per-well Vmax against substrate concentration | Graph › Fit |
| Fit quality: standard error and confidence interval per parameter, R², n, Sy.x, EC50 / Km | Under each graph; formulas `EC50(plot)`, `ParmErr(plot,"A")` |
| Confidence and prediction bands | Graph › Fit Options |
| Weighting 1/Y, 1/Y², 1/X, 1/X², 1/SD² | Graph › Fit Options |
| Fixed or seeded parameters | Graph › Fit Options |
| Kinetic: Time at Max / Min / ½ Max, Area Under Curve, Slope; `!Vmax`, `!VmaxCorr` … in custom formulas | Plate › Reduction |
| Spectrum: Maximum / Minimum OD, Area Under Curve | Plate › Reduction |
| % Transmittance, smoothing (3 or 9 points), first reading set to zero | Plate › Reduction › Raw data |
| Export to CSV and Excel (.xlsx); raw, reduced or both | File › Export |
| Auto Export after every reading or workflow, with a file-name pattern | Edit › Preferences |
| Workflow timeline: temperature (with wait), shake, delay, read, drawer, operator message, cycles; pause / resume / stop; run log | Control › Workflow |
| Slow kinetics from cycles of endpoint reads | Workflow › Cycle |
| Protocol library: Bradford, BCA, Lowry, ELISA (TMB), kinetic enzyme assay, Michaelis–Menten | Assays menu |
| Activity log (plain text) and per-experiment history saved in the file | Help › Activity Log, File › Experiment History |
| Starts without internet; only Python is needed | `OD Studio.command` / `OD Studio.bat` |
| Any set of columns for every mode, not only a contiguous block (Shift / ⌘ / Ctrl in the strip picker) | Instrument Settings › Strips |
| Many wells on one graph: drag to select, double-click; colour = column, symbol = row | Plate (Well Graph) |
| Live Well Graph while a kinetic or spectrum read runs | Plate (Well Graph) |
| Zoom and pan in every graph (box zoom, Shift-drag, ⌘/Ctrl-scroll or pinch, +/−, Reset) | Graph sections, Well Graph |
| Works in narrow windows and on phone-sized screens | everywhere |
| Find Reader: asks the ports you choose whether a reader is there; remembers the reader's USB adapter and finds it again under a new port name | Control › Find Reader…, Edit › Preferences |
| The older ThermoMax, UVmax, Vmax and Emax filter readers (found automatically; not yet tried on real hardware) | Edit › Preferences › Reader, Filters |
