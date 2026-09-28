"""
visu_server.py — MBE Control live dashboard (Plotly Dash).

Launch standalone:
    python visu_server.py

Or click "📊 Dashboard" in the main GUI — it launches this as a subprocess
and opens http://localhost:8050 in the default browser.

Data source: the CSV and dialog log files already written by LogManager.
No coupling to the main process — any file manager / browser works.
"""

import os
import glob
from datetime import datetime

import dash
from dash import dcc, html, Input, Output
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd

# ── Configuration ─────────────────────────────────────────────────────────────

LOG_DIR = "logs"
PORT    = 8050

# ── File helpers ──────────────────────────────────────────────────────────────

# Simple mtime-keyed cache so we don't re-parse 77 MB files every 5 seconds.
_df_cache: dict = {}   # {filepath: (mtime, DataFrame)}


def _csv_files() -> list[str]:
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
        # Most-recent lines at the top so you don't have to scroll down.
        return "".join(reversed(lines[-400:]))
    except Exception:
        return "(could not read dialog log)"


# ── Column parsing ────────────────────────────────────────────────────────────

# Each CSV column is either "timestamp", "Pressure_P", or "{Instrument}_{VarType}".
# VarTypes: T, P, SP_T, SP_P, Ramp_T, Ramp_P

VAR_META = {
    # vtype        display label               y-axis unit  shown by default
    "T":      ("Temperature PV",        "°C",       True),
    "P":      ("Power PV",              "%",        True),
    "SP_T":   ("Temperature Setpoint",  "°C",       False),
    "SP_P":   ("Power Setpoint",        "%",        False),
    "Ramp_T": ("Temp Ramp Rate",        "°C/min",   False),
    "Ramp_P": ("Power Ramp Rate",       "%/s",      False),
    "Pressure": ("Pressure",            "Torr",     True),
}

# 8 distinct colours — one per instrument
_PALETTE = [
    "#1f77b4",  # blue    (Au)
    "#d62728",  # red     (CaF2)
    "#2ca02c",  # green   (Se1)
    "#ff7f0e",  # orange  (Se2)
    "#9467bd",  # purple  (Substrate)
    "#8c564b",  # brown   (SnSe)
    "#e377c2",  # pink
    "#7f7f7f",  # grey
]


def _parse_columns(df) -> tuple[list, list]:
    """Return (sorted_instruments, sorted_vtypes) present in the DataFrame."""
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


# ── Dash app ──────────────────────────────────────────────────────────────────

app = dash.Dash(__name__, title="MBE Dashboard")
app.layout = html.Div([

    # ── Header ────────────────────────────────────────────────────────────────
    html.Div([
        html.H3("MBE Control — Live Dashboard",
                style={"margin": "0", "flex": "1", "fontSize": "16px"}),
        html.Span(id="last-update",
                  style={"fontSize": "11px", "color": "#888", "alignSelf": "center"}),
    ], style={
        "display": "flex", "alignItems": "center",
        "padding": "8px 14px", "background": "#f4f4f4",
        "borderBottom": "1px solid #ddd",
    }),

    # ── Controls ──────────────────────────────────────────────────────────────
    html.Div([

        # Row 1: file picker
        html.Div([
            html.Label("File:", style={"fontWeight": "bold", "marginRight": "6px",
                                       "whiteSpace": "nowrap"}),
            dcc.Dropdown(id="file-dd", clearable=False,
                         style={"flex": "1", "minWidth": "260px", "maxWidth": "480px"}),
            html.Button("⟳", id="refresh-btn", n_clicks=0,
                        title="Refresh file list",
                        style={"marginLeft": "6px", "padding": "3px 10px",
                               "cursor": "pointer"}),
        ], style={"display": "flex", "alignItems": "center",
                  "marginBottom": "7px"}),

        # Row 2: instrument filter
        html.Div([
            html.Label("Instruments:", style={"fontWeight": "bold",
                                              "marginRight": "8px",
                                              "whiteSpace": "nowrap"}),
            dcc.Checklist(id="instr-filter", inline=True,
                          inputStyle={"marginRight": "3px"},
                          labelStyle={"marginRight": "14px", "fontSize": "13px"}),
        ], style={"display": "flex", "alignItems": "center",
                  "marginBottom": "5px"}),

        # Row 3: variable type filter
        html.Div([
            html.Label("Show:", style={"fontWeight": "bold",
                                       "marginRight": "8px", "whiteSpace": "nowrap"}),
            dcc.Checklist(id="vtype-filter", inline=True,
                          inputStyle={"marginRight": "3px"},
                          labelStyle={"marginRight": "14px", "fontSize": "13px"}),
        ], style={"display": "flex", "alignItems": "center"}),

    ], style={
        "padding": "10px 14px", "background": "#fff",
        "borderBottom": "1px solid #eee",
    }),

    # ── Charts ────────────────────────────────────────────────────────────────
    dcc.Graph(
        id="main-graph",
        style={"flex": "1", "minHeight": "300px"},
        config={"displayModeBar": True, "scrollZoom": True,
                "modeBarButtonsToRemove": ["select2d", "lasso2d"]},
    ),

    # ── Dialog log ────────────────────────────────────────────────────────────
    html.Div([
        html.Div("📋 Dialog / Script Log",
                 style={"fontWeight": "bold", "fontSize": "12px",
                        "marginBottom": "3px"}),
        dcc.Textarea(
            id="dialog-log",
            readOnly=True,
            style={
                "width": "100%", "height": "160px",
                "fontFamily": "monospace", "fontSize": "11px",
                "background": "#1e1e1e", "color": "#d4d4d4",
                "border": "none", "resize": "vertical", "padding": "8px",
                "boxSizing": "border-box",
            },
        ),
    ], style={
        "padding": "8px 14px", "background": "#f4f4f4",
        "borderTop": "1px solid #ddd",
    }),

    # ── Auto-refresh ticker ───────────────────────────────────────────────────
    dcc.Interval(id="tick", interval=5_000, n_intervals=0),

], style={
    "fontFamily": "system-ui, sans-serif",
    "height": "100vh",
    "display": "flex",
    "flexDirection": "column",
    "overflow": "hidden",
})


# ── Callbacks ─────────────────────────────────────────────────────────────────

@app.callback(
    Output("file-dd", "options"),
    Output("file-dd", "value"),
    Input("refresh-btn", "n_clicks"),
    Input("tick", "n_intervals"),
)
def cb_file_list(_clicks, _tick):
    files = _csv_files()
    opts  = [{"label": os.path.basename(f), "value": f} for f in files]
    val   = files[0] if files else None
    return opts, val


@app.callback(
    Output("instr-filter", "options"),
    Output("instr-filter", "value"),
    Output("vtype-filter", "options"),
    Output("vtype-filter", "value"),
    Input("file-dd", "value"),
)
def cb_filters(filepath):
    df = _load_csv(filepath)
    instruments, vtypes = _parse_columns(df)

    instr_opts = [{"label": i, "value": i} for i in instruments]
    instr_val  = instruments   # all selected by default

    vtype_opts, vtype_val = [], []
    # Preserve display order defined in VAR_META
    for vt, (label, unit, default) in VAR_META.items():
        if vt not in vtypes:
            continue
        vtype_opts.append({"label": f"{label} ({unit})", "value": vt})
        if default:
            vtype_val.append(vt)

    return instr_opts, instr_val, vtype_opts, vtype_val


@app.callback(
    Output("main-graph", "figure"),
    Output("dialog-log", "value"),
    Output("last-update", "children"),
    Input("tick", "n_intervals"),
    Input("file-dd", "value"),
    Input("instr-filter", "value"),
    Input("vtype-filter", "value"),
)
def cb_graph(_tick, filepath, sel_instruments, sel_vtypes):
    now_str = f"Updated {datetime.now().strftime('%H:%M:%S')}"
    dialog  = _dialog_log()

    df = _load_csv(filepath)
    if df is None or not sel_instruments or not sel_vtypes:
        empty = go.Figure()
        empty.update_layout(
            annotations=[{"text": "No data — select a log file above.",
                           "showarrow": False, "xref": "paper", "yref": "paper",
                           "x": 0.5, "y": 0.5, "font": {"size": 16, "color": "#aaa"}}],
            plot_bgcolor="white", paper_bgcolor="white",
        )
        return empty, dialog, now_str

    # Only keep vtypes present in VAR_META and actually requested
    ordered_vtypes = [vt for vt in VAR_META if vt in (sel_vtypes or [])]
    n_rows = len(ordered_vtypes)
    if n_rows == 0:
        return go.Figure(), dialog, now_str

    subplot_titles = [
        f"{VAR_META[vt][0]} ({VAR_META[vt][1]})" for vt in ordered_vtypes
    ]
    fig = make_subplots(
        rows=n_rows, cols=1,
        shared_xaxes=True,
        subplot_titles=subplot_titles,
        vertical_spacing=max(0.02, 0.12 / n_rows),
    )

    instr_sorted = sorted(sel_instruments)
    color_of     = {i: _PALETTE[k % len(_PALETTE)]
                    for k, i in enumerate(instr_sorted)}
    # Track which instrument names have already been added to the legend
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
        uirevision="stable",   # keeps zoom/pan when data refreshes
    )

    return fig, dialog, now_str


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"[Dashboard] Serving at http://127.0.0.1:{PORT}")
    print(f"[Dashboard] Reading logs from: {os.path.abspath(LOG_DIR)}")
    app.run(debug=False, port=PORT, host="127.0.0.1")
