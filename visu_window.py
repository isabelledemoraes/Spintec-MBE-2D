"""
visu_window.py — MBE Control live dashboard (pywebview + Plotly).

Launch standalone:
    python visu_window.py

Or click "📊 Dashboard" in the main GUI — it launches this as a subprocess
and opens a native desktop window. No browser or localhost server needed.

Data source: the CSV and dialog log files already written by LogManager.
No coupling to the main process — pure file-based data sharing.
"""

import os
import glob
import json
import time
import threading
import tempfile
import atexit
from datetime import datetime

import webview
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import plotly.offline

# ── Configuration ─────────────────────────────────────────────────────────────

LOG_DIR     = "logs"
REFRESH_SEC = 5

# ── File helpers ──────────────────────────────────────────────────────────────

_df_cache: dict = {}   # {filepath: (mtime, DataFrame)}


def _csv_files() -> list:
    return sorted(
        (f for f in glob.glob(os.path.join(LOG_DIR, "*.csv"))
         if not os.path.basename(f).startswith("dialog_")),
        key=os.path.getmtime,
        reverse=True,
    )


def _load_csv(filepath: str):
    if not filepath or not os.path.exists(filepath):
        return None
    mtime = os.path.getmtime(filepath)
    if filepath in _df_cache and _df_cache[filepath][0] == mtime:
        return _df_cache[filepath][1]
    try:
        df = pd.read_csv(filepath, parse_dates=["timestamp"])
        _df_cache[filepath] = (mtime, df)
        return df
    except Exception:
        return None


def _dialog_log() -> str:
    files = sorted(
        glob.glob(os.path.join(LOG_DIR, "dialog_*.txt")),
        key=os.path.getmtime,
        reverse=True,
    )
    if not files:
        return "(no dialog log found)"
    try:
        with open(files[0], "r", encoding="utf-8") as f:
            lines = f.readlines()
        return "".join(reversed(lines[-400:]))
    except Exception:
        return "(could not read dialog log)"


# ── Column parsing ────────────────────────────────────────────────────────────

VAR_META = {
    "T":        ("Temperature PV",       "\u00b0C",    True),
    "P":        ("Power PV",             "%",          True),
    "SP_T":     ("Temperature Setpoint", "\u00b0C",    False),
    "SP_P":     ("Power Setpoint",       "%",          False),
    "Ramp_T":   ("Temp Ramp Rate",       "\u00b0C/min",False),
    "Ramp_P":   ("Power Ramp Rate",      "%/s",        False),
    "Pressure": ("Pressure",             "Torr",       True),
}

_PALETTE = [
    "#1f77b4", "#d62728", "#2ca02c", "#ff7f0e",
    "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
]


def _parse_columns(df) -> tuple:
    if df is None:
        return [], []
    instruments, vtypes = set(), set()
    for col in df.columns:
        if col == "timestamp":
            continue
        if col == "Pressure_P":
            instruments.add("Pressure")
            vtypes.add("Pressure")
            continue
        parts = col.split("_", 1)
        if len(parts) == 2:
            instruments.add(parts[0])
            vtypes.add(parts[1])
    return sorted(instruments), sorted(vtypes)


# ── Figure builder ────────────────────────────────────────────────────────────

def _build_figure(filepath, sel_instruments, sel_vtypes) -> go.Figure:
    df = _load_csv(filepath)
    if df is None or not sel_instruments or not sel_vtypes:
        fig = go.Figure()
        fig.update_layout(
            annotations=[{
                "text": "No data \u2014 select a log file above.",
                "showarrow": False, "xref": "paper", "yref": "paper",
                "x": 0.5, "y": 0.5, "font": {"size": 16, "color": "#aaa"},
            }],
            plot_bgcolor="white", paper_bgcolor="white",
        )
        return fig

    ordered_vtypes = [vt for vt in VAR_META if vt in sel_vtypes]
    if not ordered_vtypes:
        return go.Figure()

    subplot_titles = [
        f"{VAR_META[vt][0]} ({VAR_META[vt][1]})" for vt in ordered_vtypes
    ]
    fig = make_subplots(
        rows=len(ordered_vtypes), cols=1,
        shared_xaxes=True,
        subplot_titles=subplot_titles,
        vertical_spacing=max(0.02, 0.12 / len(ordered_vtypes)),
    )

    instr_sorted = sorted(sel_instruments)
    color_of     = {i: _PALETTE[k % len(_PALETTE)] for k, i in enumerate(instr_sorted)}
    legend_shown: set = set()

    for row, vtype in enumerate(ordered_vtypes, start=1):
        if vtype == "Pressure":
            col = "Pressure_P"
            if col in df.columns:
                fig.add_trace(
                    go.Scattergl(
                        x=df["timestamp"], y=df[col],
                        mode="lines", name="Pressure",
                        line={"color": "#7f7f7f", "width": 1.5},
                        showlegend="Pressure" not in legend_shown,
                    ),
                    row=row, col=1,
                )
                legend_shown.add("Pressure")
        else:
            for instr in instr_sorted:
                if instr == "Pressure":
                    continue
                col = f"{instr}_{vtype}"
                if col not in df.columns:
                    continue
                show = instr not in legend_shown
                fig.add_trace(
                    go.Scattergl(
                        x=df["timestamp"], y=df[col],
                        mode="lines", name=instr,
                        line={"color": color_of[instr], "width": 1.5},
                        showlegend=show,
                    ),
                    row=row, col=1,
                )
                if show:
                    legend_shown.add(instr)

        fig.update_yaxes(
            title_text=VAR_META[vtype][1],
            row=row, col=1,
            gridcolor="#ececec",
        )

    fig.update_xaxes(gridcolor="#ececec")
    fig.update_layout(
        margin={"l": 55, "r": 10, "t": 28, "b": 10},
        legend={
            "orientation": "h", "y": 1.04, "x": 0,
            "xanchor": "left", "yanchor": "bottom",
            "font": {"size": 12},
        },
        plot_bgcolor="white",
        paper_bgcolor="white",
        hovermode="x unified",
        uirevision="stable",
    )
    return fig


# ── HTML template ─────────────────────────────────────────────────────────────

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>MBE Dashboard</title>
<style>
* { box-sizing: border-box; }
body {
  margin: 0; font-family: system-ui, sans-serif;
  display: flex; flex-direction: column; height: 100vh; overflow: hidden;
  background: #fff;
}
#header {
  display: flex; align-items: center; padding: 8px 14px;
  background: #f4f4f4; border-bottom: 1px solid #ddd; flex-shrink: 0;
}
#header h3 { margin: 0; flex: 1; font-size: 16px; }
#last-update { font-size: 11px; color: #888; }
#controls {
  padding: 8px 14px; background: #fff;
  border-bottom: 1px solid #eee; flex-shrink: 0;
}
.ctrl-row {
  display: flex; align-items: center; margin-bottom: 5px;
}
.ctrl-row:last-child { margin-bottom: 0; }
.ctrl-label {
  font-weight: bold; margin-right: 8px; white-space: nowrap;
  font-size: 13px; min-width: 90px;
}
select { padding: 3px 6px; font-size: 13px; min-width: 260px; max-width: 480px; }
.check-group { display: flex; flex-wrap: wrap; gap: 2px 14px; font-size: 13px; }
.check-group label { display: flex; align-items: center; gap: 3px; cursor: pointer; }
#graph-container { flex: 1; min-height: 0; }
#main-graph { width: 100%; height: 100%; }
#log-section {
  flex-shrink: 0; padding: 6px 14px 8px;
  background: #f4f4f4; border-top: 1px solid #ddd;
}
#log-title { font-weight: bold; font-size: 12px; margin-bottom: 3px; }
#dialog-log {
  width: 100%; height: 130px;
  font-family: monospace; font-size: 11px;
  background: #1e1e1e; color: #d4d4d4;
  border: none; resize: vertical; padding: 8px;
  overflow-y: auto;
}
</style>
</head>
<body>

<div id="header">
  <h3>MBE Control &#8212; Live Dashboard</h3>
  <span id="last-update"></span>
</div>

<div id="controls">
  <div class="ctrl-row">
    <span class="ctrl-label">File:</span>
    <select id="file-sel" onchange="onFileChange()"></select>
  </div>
  <div class="ctrl-row">
    <span class="ctrl-label">Instruments:</span>
    <div class="check-group" id="instr-checks"></div>
  </div>
  <div class="ctrl-row">
    <span class="ctrl-label">Show:</span>
    <div class="check-group" id="vtype-checks"></div>
  </div>
</div>

<div id="graph-container">
  <div id="main-graph"></div>
</div>

<div id="log-section">
  <div id="log-title">&#128203; Dialog / Script Log</div>
  <textarea id="dialog-log" readonly></textarea>
</div>

%%PLOTLY_JS%%

<script>
var _currentFile = null;

function getSelInstruments() {
  return Array.from(
    document.querySelectorAll('#instr-checks input:checked')
  ).map(function(e) { return e.value; });
}

function getSelVtypes() {
  return Array.from(
    document.querySelectorAll('#vtype-checks input:checked')
  ).map(function(e) { return e.value; });
}

function pushUpdate() {
  window.pywebview.api.update(
    _currentFile, getSelInstruments(), getSelVtypes()
  );
}

function onFileChange() {
  _currentFile = document.getElementById('file-sel').value;
  Promise.all([
    window.pywebview.api.get_instruments(_currentFile),
    window.pywebview.api.get_vtypes(_currentFile),
  ]).then(function(results) {
    buildInstrumentChecks(results[0]);
    buildVtypeChecks(results[1]);
    pushUpdate();
  });
}

function buildInstrumentChecks(instruments) {
  var container = document.getElementById('instr-checks');
  container.innerHTML = '';
  instruments.forEach(function(name) {
    var label = document.createElement('label');
    var cb    = document.createElement('input');
    cb.type    = 'checkbox';
    cb.value   = name;
    cb.checked = true;
    cb.onchange = pushUpdate;
    label.appendChild(cb);
    label.appendChild(document.createTextNode('\u00a0' + name));
    container.appendChild(label);
  });
}

function buildVtypeChecks(vtypes) {
  var container = document.getElementById('vtype-checks');
  container.innerHTML = '';
  vtypes.forEach(function(vt) {
    var label = document.createElement('label');
    var cb    = document.createElement('input');
    cb.type    = 'checkbox';
    cb.value   = vt.value;
    cb.checked = vt.default;
    cb.onchange = pushUpdate;
    label.appendChild(cb);
    label.appendChild(document.createTextNode('\u00a0' + vt.label));
    container.appendChild(label);
  });
}

function init() {
  window.pywebview.api.get_files().then(function(files) {
    var sel = document.getElementById('file-sel');
    files.forEach(function(f) {
      var opt = document.createElement('option');
      opt.value       = f.value;
      opt.textContent = f.label;
      sel.appendChild(opt);
    });
    if (files.length > 0) {
      _currentFile = files[0].value;
      onFileChange();
    } else {
      pushUpdate();  // show "no data" placeholder
    }
  });
}

window.addEventListener('pywebviewready', init);
</script>
</body>
</html>
"""


# ── API exposed to JavaScript ─────────────────────────────────────────────────

class DashboardAPI:
    """Methods on this object are callable from JS as window.pywebview.api.*"""

    def __init__(self):
        self._window    = None   # set after create_window()
        self._filepath  = None
        self._sel_instr: list = []
        self._sel_vtype: list = []

    # -- Called by JS --------------------------------------------------------

    def get_files(self) -> list:
        files = _csv_files()
        return [{"label": os.path.basename(f), "value": f} for f in files]

    def get_instruments(self, filepath: str) -> list:
        df = _load_csv(filepath)
        instruments, _ = _parse_columns(df)
        return instruments

    def get_vtypes(self, filepath: str) -> list:
        df = _load_csv(filepath)
        _, vtypes = _parse_columns(df)
        result = []
        for vt, (label, unit, default) in VAR_META.items():
            if vt in vtypes:
                result.append({"value": vt,
                                "label": f"{label} ({unit})",
                                "default": default})
        return result

    def update(self, filepath, sel_instruments, sel_vtypes) -> None:
        self._filepath  = filepath
        self._sel_instr = sel_instruments or []
        self._sel_vtype = sel_vtypes or []
        self._push_update()

    # -- Internal ------------------------------------------------------------

    def _push_update(self) -> None:
        if self._window is None:
            return
        try:
            fig      = _build_figure(self._filepath, self._sel_instr, self._sel_vtype)
            fig_dict = json.loads(fig.to_json())
            data_js  = json.dumps(fig_dict["data"])
            layout_js = json.dumps(fig_dict["layout"])
            log_js   = json.dumps(_dialog_log())
            now_js   = json.dumps(f"Updated {datetime.now().strftime('%H:%M:%S')}")
            self._window.evaluate_js(
                f"Plotly.react('main-graph', {data_js}, {layout_js});"
                f"var el = document.getElementById('dialog-log');"
                f"el.value = {log_js}; el.scrollTop = 0;"
                f"document.getElementById('last-update').textContent = {now_js};"
            )
        except Exception:
            pass


# ── Periodic refresh ──────────────────────────────────────────────────────────

def _refresh_loop(api: DashboardAPI) -> None:
    """Push graph + log updates every REFRESH_SEC seconds."""
    while True:
        time.sleep(REFRESH_SEC)
        api._push_update()


# ── Entry point ───────────────────────────────────────────────────────────────

def run_dashboard() -> None:
    api = DashboardAPI()

    # Embed plotly.js inline so the window works with no network connection.
    plotly_js_src = plotly.offline.get_plotlyjs()
    html = _HTML_TEMPLATE.replace(
        "%%PLOTLY_JS%%", f"<script>{plotly_js_src}</script>"
    )

    # WebView2's NavigateToString has a ~2 MB limit — plotly.js alone is ~3 MB.
    # Work-around: write the HTML to a temp file and load via file:/// URL,
    # which uses Navigate (no size limit) instead of NavigateToString.
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    )
    tmp.write(html)
    tmp.close()
    atexit.register(os.unlink, tmp.name)   # clean up on process exit

    file_url = "file:///" + tmp.name.replace("\\", "/")

    window = webview.create_window(
        "MBE Dashboard",
        url=file_url,
        js_api=api,
        width=1200,
        height=860,
        min_size=(800, 600),
    )
    api._window = window

    threading.Thread(target=_refresh_loop, args=(api,), daemon=True).start()
    webview.start()


if __name__ == "__main__":
    run_dashboard()
