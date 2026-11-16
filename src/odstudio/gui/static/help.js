"use strict";
/* In-program help: Help > OD Studio Help. */

const HELP = [
  ["Getting started", `
<p>The window holds one or more <b>experiments</b>. Each experiment is a column of <b>sections</b>:</p>
<ul><li><b>Plate</b>: one microplate. Its instrument settings, template, raw data and reduction.</li>
<li><b>Group</b>: a table of the wells of one template group, with formula columns and summaries. Created automatically.</li>
<li><b>Graph</b>: plots of group columns, for example a standard curve with a fit.</li>
<li><b>Notes</b>: free text and summaries, for the report.</li></ul>
<p>Click a section to make it active. The menus called Plate, Group, Graph and Notes act on the active section. The triangle at the left of a section's title opens or closes it. Alt-click the triangle to open or close every section. Drag a section's title bar onto another section to reorder them.</p>
<p>A typical session: set the instrument up (Setup), put the plate in, press <b>Read</b>, define the template, choose the reduction, add a graph, then save and print.</p>`],
  ["Experiments and windows", `
<p>File &gt; New opens another experiment window. File &gt; Open opens a file in a new window, unless the current window is still empty.</p>
<p>The tabs under the status bar switch between windows. <b>Side by side</b> shows every window at once so you can compare them. Window &gt; Tabs goes back to one window at a time.</p>
<p>Each window can hold several plates, each with its own instrument settings. Use the <b>+ Plate</b> button at the bottom of an experiment, or Edit &gt; New Plate. Only one plate can be read at a time, because there is one instrument. You can keep working in the other windows while it reads.</p>`],
  ["Connecting the reader", `
<p>Connect the reader's serial port to the computer. A USB-to-RS-232 adapter works. Then choose the port in Edit &gt; Preferences &gt; Serial Port. The link runs at 9600 baud, 8 data bits, no parity, 1 stop bit.</p>
<p>The status bar shows a green dot and the instrument name when it is connected. A red dot means no instrument. Hover over it to see why.</p>
<p>To try the program without a reader, choose <b>Simulator</b> as the port. It answers like a SpectraMax and produces made-up absorbance values. "Simulator, realistic timing" makes reads take as long as a real plate.</p>
<p>If nothing answers: check that the reader is switched on, then check the cable type (straight-through or null-modem). Control &gt; Instrument Terminal sends raw commands, and Control &gt; Instrument Error Log shows the reader's own errors.</p>`],
  ["Reading a plate", `
<p>Press <b>Read</b> in the status bar (or Control &gt; Read, ${MOD}R). If the experiment has several plates you are asked which one to read. The default is the active plate, otherwise the first plate without data. Data appears as it arrives. The button becomes <b>Stop</b> while reading.</p>
<p>Reading again replaces the plate's data after asking. Changing the instrument settings of a plate that already has data deletes the data, also after asking. The template, reduction and graphs can be changed at any time without touching the raw data.</p>
<p>When the reading finishes, the drawer opens (switch this off in Preferences). The experiment can be saved and printed automatically: see Preferences.</p>`],
  ["Instrument Settings", `
<p>Plate &gt; Setup, or the <b>Setup</b> button of a plate.</p>
<ul><li><b>Mode</b>. <i>Endpoint</i>: one reading at 1 to 6 wavelengths. <i>Kinetic</i>: readings repeated at a fixed interval for the run time. <i>Spectrum</i>: a wavelength scan from Start to Stop in steps.</li>
<li><b>Wavelengths</b>: type a value or pick one from the list. Installed optical filters appear as F405 and so on (set them in Preferences &gt; Filters).</li>
<li><b>Strips</b>: drag across the plate picture to read only some columns.
 <ul><li>Shift-click extends the choice.</li>
 <li>${MOD === "\u2318" ? "\u2318" : "Ctrl"}-click or ${MOD === "\u2318" ? "\u2318" : "Ctrl"}-drag adds or removes columns, so they need not be next to each other.</li>
 <li>"all" selects the whole plate.</li></ul>
The reader reads one block of columns at a time, so with gaps it sweeps from the first to the last chosen column and the columns in between are left out of the data (shown hatched). Fewer columns read faster, which allows shorter kinetic intervals. The dialog shows how long one read takes and the shortest interval allowed.</li>
<li><b>Auto Mix</b>: shake before the first read and, in kinetic mode, before each later read.</li>
<li><b>Auto Calibrate</b>: the reader measures air before reading. In kinetic mode it does this before every read.</li>
<li><b>Pre-Read Plate</b>: read the plate once before adding the reagent. That reading is then subtracted from each well. Read it with Read &gt; Pre-read.</li>
<li><b>Start</b>: wait until the incubator reaches its set point, and/or wait a number of seconds before reading.</li></ul>`],
  ["Incubator and shaking", `
<p>The <b>Incubator</b> button sets the chamber temperature, 15 to 45 °C, or switches the incubator off. The status bar shows the current temperature and the set point. The temperature of every reading is stored with the data.</p>
<p><b>Automix</b> shakes the plate now. Press it again to stop. Shaking during a read is set under Auto Mix in Instrument Settings.</p>`],
  ["Template", `
<p>The template says what is in each well. Open it with the plate's <b>Template</b> button.</p>
<ol><li>Drag across wells to select them. Shift- or Ctrl-click adds to the selection. Click a row letter or column number to select it.</li>
<li>Choose a group, or make one with <b>New Group</b>. A group's descriptor is Concentration, Factor (dilution) or none, with units.</li>
<li>Type a sample name, or leave it empty for automatic names like Sta01, Sta02. Type the descriptor value if the group has one, then click <b>Assign</b>.</li></ol>
<p><b>Series</b> fills a block of wells with numbered samples whose values step by +, −, × or ÷. Top or Bottom: each row of the selection is one sample. Left or Right: each column is one sample.</p>
<p><b>Plate Blank</b> marks wells whose average is subtracted from every well of the plate. <b>Group Blank</b> marks wells whose average is subtracted only within that group. <b>Clear</b> empties the selected wells.</p>
<p>Plate &gt; Copy Template and Paste Template copy a layout to another plate, even in another window.</p>`],
  ["Reduction", `
<p>The reduction turns the raw readings of a well into one number. Open it with the plate's <b>Reduction</b> button.</p>
<ul><li><b>Wavelength combination</b>: L1, L2, L1−L2, Log10(L1/L2) and so on. Custom accepts any formula using !L1 … !L6.</li>
<li><b>Kinetic</b>:
 <ul><li><i>Vmax</i>: the steepest slope, in mOD/min, found with a sliding window of <i>Vmax Points</i> readings. Leave Vmax Points empty to use all readings, which gives the slope of the whole run.</li>
 <li><i>Time to Vmax</i>: the midpoint time of that steepest window.</li>
 <li><i>Onset Time</i>: when the OD has risen by the <i>Onset OD</i>.</li>
 <li><i>Custom</i>: a formula using !KinPlot (the readings) and !ReadTimes.</li></ul></li>
<li>More kinetic reductions:
 <ul><li><i>Time at Maximum</i>, <i>Time at Minimum</i>, <i>Time at 1/2 Maximum</i>;</li>
 <li><i>Area Under Curve</i> (OD × s);</li>
 <li><i>Slope</i>, a regression over all readings.</li></ul>
Custom formulas can also use !Vmax, !VmaxCorr, !TimeToVmax, !OnsetTime, !AUC, !Slope and the times at maximum, minimum and half maximum.</li>
<li><b>Spectrum</b>: the wavelength of the highest or lowest OD, the maximum or minimum OD, the area under the spectrum, or Custom.</li>
<li><b>Raw data</b>: optical density or % transmittance. Smoothing (3- or 9-point moving average) and setting the first kinetic reading to zero are also here.</li>
<li><b>Limits</b>: Lag Time and End Time, or the Start and End wavelengths, plus Min OD and Max OD. Readings outside them are ignored.</li>
<li><b>Blanks</b>: subtract the plate blank and the pre-read.</li></ul>
<p>Wells that cannot be reduced show a reason instead of a number:</p>
<ul><li>Limits?: not enough readings inside the limits.</li>
<li>Range?: the onset level was never reached.</li>
<li>Masked: the well is masked.</li>
<li>&gt;&gt;&gt;&gt;: the reader reported saturation.</li></ul>`],
  ["Display, Well Graph and Mask", `
<p><b>Raw</b> shows the readings. For endpoint plates that is the OD at each wavelength. For kinetic and spectrum plates it is a small plot, with the reduced value (for example Vmax) in the corner.</p>
<p><b>Reduced</b> shows the reduction result in one of several ways:</p>
<ul><li>Number.</li>
<li>Plots: the plot with the Vmax line.</li>
<li>Gray Scale: seven shades between Low and High.</li>
<li>Ranged: 0–9 between Low and High.</li>
<li>Threshold: − below Low, * in between, + above High.</li></ul>
<p>Choose these with <b>Display</b>.</p>
<p><b>Well Graph</b> enlarges the plots of the selected wells. You can also drag across wells and then double-click inside the selection: all their curves appear on one graph. Colour shows the plate column and symbol shape shows the row, so every well has its own pair. The Well Graph keeps updating while a kinetic or spectrum read is running.</p>
<p><b>Zooming any graph</b>:
<ul><li>drag a box to zoom into it;</li>
<li>Shift-drag to pan;</li>
<li>${MOD === "\u2318" ? "\u2318" : "Ctrl"}-scroll (or pinch on a trackpad) to zoom at the pointer;</li>
<li>+ and \u2212 zoom in and out;</li>
<li>double-click or Reset to see everything again.</li></ul>
A zoom is kept while new readings arrive. Below the graph is a table with Vmax, R², Vmax Points, Time to Vmax and Onset Time for each well (spectra: wavelength and OD of the maximum and minimum).</p>
<p><b>Mask</b> leaves the selected wells out of every calculation. Use it again to unmask them.</p>`],
  ["Groups, columns and summaries", `
<p>Each template group has a Group section: a table with one row per well, grouped by sample. Default columns are Sample, Concentration (or Factor), Wells, Values (the reduced well values), MeanValue, Std.Dev. and CV%.</p>
<p><b>+ Column</b> adds a column calculated by a formula, for example <code>Values*2</code> or <code>InterpX(Plot#1@Graph#1,Values)</code>. Double-click a column heading to edit it. Select columns by clicking their headings to hide, copy, paste or delete them (Edit &gt; Clear).</p>
<p><b>+ Summary</b> adds a single result under the table, for example <code>Average(MeanValue)</code>. Summaries can also go in Plate and Notes sections.</p>
<p><b>Replicates</b> shows or hides the per-well rows. <b>Formulas</b> shows each column's formula. Group &gt; Group Settings renames the group or changes its descriptor and units.</p>`],
  ["Standard curves", `
<ol><li>Put standards in a group with a Concentration descriptor (Template &gt; Series is the quickest way).</li>
<li>Edit &gt; New Graph. The first plot uses the first group: X = Concentration, Y = MeanValue. Change it with <b>Plots</b>.</li>
<li>Choose a <b>Fit</b>: Linear, Semi-Log, Log-Log, Quadratic or 4-Parameter. The equation, its parameters and R² appear under the graph.</li>
<li>In the unknowns' group choose Group &gt; Add Interpolated Results. This adds Result = InterpX(curve, Values) and MeanResult. With a Factor descriptor it also adds the dilution-corrected AdjResult.</li></ol>
<p>Graph Type offers scatter (connected or not), cluster bars and stacked bars. X Axis and Y Axis set the range, linear or log scale, gridlines, tick marks and labels.</p>`],
  ["Workflow", `
<p>A workflow runs steps on the reader in order, so long or multi-step experiments run by themselves. Open it with Control &gt; Workflow. Each experiment has one.</p>
<ul><li><b>Set Temperature</b>: switch the incubator to a temperature (0 switches it off), optionally waiting until the chamber gets there.</li>
<li><b>Shake</b> and <b>Delay</b>: for a given time.</li>
<li><b>Read</b>: read one of the experiment's plates with its own instrument settings.</li>
<li><b>Drawer</b>: open or close it.</li>
<li><b>Message</b>: pause and show a message, for example "add substrate", until you press Continue.</li>
<li><b>Cycle</b>: repeat the steps inside it every <i>interval</i> for the <i>run time</i>.</li></ul>
<p>Endpoint plates read inside a cycle collect one time point per cycle. They then reduce like a kinetic read, with Vmax, onset time and so on. This lets you follow slow reactions over hours with shaking or incubation between readings.</p>
<p><b>Run Workflow</b> starts it. <b>Pause</b> waits after the current step, and <b>Stop</b> ends it. The current step is highlighted, and the log below records what happened when. Manual instrument controls are locked while a workflow runs.</p>`],
  ["Curve fits and fit quality", `
<p>A graph's <b>Fit</b> menu offers several curves:</p>
<ul><li>Linear</li><li>Semi-Log</li><li>Log-Log</li><li>Quadratic</li><li>4-Parameter logistic</li><li>Michaelis–Menten</li></ul>
<p>Under the graph, each parameter has its value, standard error and confidence interval. The legend also shows R², n, the standard error of the estimate (Sy.x), and the EC50 (4-parameter) or Km (Michaelis–Menten).</p>
<p><b>Fit Options</b> sets:</p>
<ul><li><i>Weighting</i>: 1/Y, 1/Y², 1/X, 1/X², or 1/SD² from the Y error bars. Use it when the scatter grows with the signal, as in most ELISAs.</li>
<li><i>Confidence level</i>.</li>
<li><i>Bands</i>: shaded for where the curve lies, dashed for where new measurements fall.</li>
<li><i>Per parameter</i>: fitted, fitted from a starting value, or fixed at a value.</li></ul>
<p>In formulas, EC50(plot) and ParmErr(plot,"A") give the EC50 or Km and a parameter's standard error, next to ParmA … ParmD and RSquared.</p>`],
  ["Michaelis–Menten (Km and Vmax)", `
<p>Use the Assays &gt; Michaelis-Menten protocol, or build it yourself:</p>
<ol><li>Make a group whose descriptor is Concentration (the substrate concentration), and fill wells with a substrate series using Template &gt; Series.</li>
<li>Read the plate in Kinetic mode with Vmax as the reduction. Each well's Vmax is its initial rate. Mark no-substrate wells as plate blanks to subtract background.</li>
<li>Add a graph with X = Concentration and Y = Values, so every well is a point, or MeanValue for the means. Choose the Michaelis–Menten fit.</li></ol>
<p>Km and Vmax appear with their standard errors and confidence intervals. The protocol also puts them in the group's summaries.</p>`],
  ["Auto Export", `
<p>Edit &gt; Preferences &gt; Auto Export saves every plate after its reading, or after a workflow, to a folder. Choose:</p>
<ul><li><b>Format</b>: text, CSV or Excel.</li>
<li><b>Contents</b>: raw readings, reduced values, or both.</li>
<li><b>File name</b>: built from {experiment}, {plate}, {date}, {time}, {mode} and {n} (a running number).</li></ul>
<p>An existing file is never overwritten unless you allow it; _2, _3 … is added instead. File &gt; Export offers the same formats by hand.</p>`],
  ["Protocol library", `<p>The Assays menu lists ready-made protocols:</p>
<ul><li>Bradford (595 nm)</li><li>BCA (562 nm, 37 °C)</li><li>Lowry (750 nm)</li><li>ELISA with TMB (450 nm minus 650 nm, 4-parameter fit weighted 1/Y²)</li><li>Kinetic enzyme assay (Vmax and activity)</li><li>Michaelis–Menten</li></ul>
<p>Each opens with a template, reduction, standard curve, result columns, notes and a workflow. Adjust the concentrations and layout to your kit, then save it as your own protocol.</p>`],
  ["Activity log and history", `<p>Help &gt; Activity Log shows everything the program did, newest first:</p>
<ul><li>readings started, finished or stopped, with their settings;</li>
<li>instrument commands and settings changes;</li>
<li>files opened, saved and exported;</li>
<li>workflow steps and errors.</li></ul>
<p>It is a plain text file you can open in any editor. File &gt; Experiment History shows the same for the current experiment, and that history is saved inside its file.</p>`],
  ["Formulas", `<p>Formulas use the names of columns, summaries and plots, plus functions such as Average, Stdev, If, InterpX and Round. Help &gt; Formula Help lists them all.</p>
<p>Name@Section refers to something in another section. Put quotes around names with spaces: 'Std Curve'. Capitalisation does not matter.</p>`],
  ["Files", `
<ul><li><b>Data files</b> (.odx): the whole experiment with its data.</li>
<li><b>Protocols</b> (.odxp): settings, template, reduction, groups, graphs and notes, without data. Use File &gt; Save As Protocol.</li>
<li>Files saved before the program was renamed to OD Studio (.smx, .smxp) still open.</li>
<li><b>Default protocol</b>: Preferences &gt; New experiments. File &gt; New starts from it.</li>
<li><b>Assays folder</b>: the protocols in it are listed in the Assays menu for one-click use.</li>
<li><b>Export</b>: writes the raw data as tab-separated text that spreadsheets open. <i>Time</i> layout is one line per reading. <i>Plate</i> layout is an 8 × 12 block per reading. The layout and labels can be preset in Preferences.</li>
<li><b>Import</b>: File &gt; Open or Import Text Data reads such text files, including the ones the original SOFTmax PRO exported.</li>
<li><b>Paste</b>: copy an 8 × 12 block of numbers in a spreadsheet, click a plate, then Edit &gt; Paste.</li></ul>`],
  ["Printing and reports", `
<p>File &gt; Print prints every section that is included in the report, in order, from the active window. File &gt; Print "section" prints only the active section.</p>
<p>Each section's Settings has an "Include in Report" box. Sections that are left out show "not in report". Preferences sets whether the template, the file name, the time and page numbers are printed.</p>
<p>To make a PDF, choose "Save as PDF" in the print window.</p>`],
  ["Preferences", `<p>Edit &gt; Preferences holds:</p>
<ul><li>the serial port;</li>
<li>whether to connect at start-up;</li>
<li>whether to open the drawer after reading;</li>
<li>Autosave (folder and file prefix) and Autoprint;</li>
<li>the default protocol and the assays folder;</li>
<li>print options and the export format;</li>
<li>the wavelength shortcuts and the installed filters.</li></ul>`],
  ["Undo and recalculation", `<p>Edit &gt; Undo (${MOD}Z) undoes any change to the experiment, and Redo restores it. Every window has its own history.</p>
<p>Edit &gt; Suspend Recalculation stops results being updated while you make many changes. Recalculate Now (${MOD}=) updates them once.</p>`],
  ["Keyboard shortcuts", `<ul><li>${MOD}O open</li><li>${MOD}S save</li><li>${MOD}P print</li><li>${MOD}R read / stop</li><li>${MOD}Z undo (Shift for redo)</li><li>${MOD}A select all</li><li>${MOD}C copy</li><li>${MOD}V paste</li><li>${MOD}E expand all sections</li><li>${MOD}M minimize all sections</li><li>${MOD}= recalculate</li></ul>`],
];

function dlgHelp(topic) {
  let cur = topic || 0;
  const list = h("div", {class: "plotlist helplist", role: "listbox"});
  const page = h("div", {class: "help helppage"});
  const search = textInput("", {placeholder: "Search help", "aria-label": "Search help"});
  const draw = () => {
    const q = search.value.trim().toLowerCase();
    const hits = HELP.map((t, i) => [t, i]).filter(([t]) => !q || (t[0] + t[1]).toLowerCase().includes(q));
    if (!hits.some(([, i]) => i === cur) && hits.length) cur = hits[0][1];
    fill(list, hits.map(([t, i]) => h("div", {class: "fl-item" + (i === cur ? " on" : ""), onclick: () => { cur = i; draw(); }}, t[0])));
    page.innerHTML = hits.length ? `<h3>${HELP[cur][0]}</h3>` + HELP[cur][1] : "<p>No topic matches.</p>";
  };
  search.addEventListener("input", draw);
  draw();
  return modal("OD Studio Help", h("div", {}, search, h("div", {class: "plots"}, list, page)), [{label: "Close", primary: true, value: true}], {cls: "wide"});
}
