# Feature Request: Parallel Log Visualization Interface

## Context

Currently, the log visualization is embedded within the control software UI and only allows monitoring **one variable at a time** in the same interface.

## Objective

Implement a **standalone, parallel visualization window** that launches independently of the control software. This new interface should allow simultaneous monitoring of:

- **Multiple variables** displayed at the same time (e.g., side-by-side plots or a multi-panel layout)
- **The dialog/message log** alongside the variable data

## Suggested Technology: Plotly Dash

Use **Plotly** (via [Plotly Dash](https://dash.plotly.com/)) for the visualization layer. Plotly Dash is well-suited for this use case because:

- It renders interactive, real-time charts (zoom, pan, hover tooltips) with no frontend code required.
- It runs as a lightweight local web server, making it fully **independent of the control software** — the dashboard opens in a browser tab and has no coupling to the main process.
- Multi-panel layouts are straightforward using `dcc.Graph` components arranged in a grid.
- Live data updates are supported natively via `dcc.Interval` for polling or websocket callbacks.

## Requirements

- The visualization window must be **fully independent** from the control software — it should launch as a separate process or window, so that closing or interacting with one does not affect the other.
- Both the variable streams and the dialog log should be **visible simultaneously**, without needing to switch between views.
- The window should update in **real time** as new data or messages arrive.
- The interface should remain usable even if the control software is minimized or busy.

## Acceptance Criteria

- [ ] A Plotly Dash server is launched as a **separate process** (e.g., via `multiprocessing` or a subprocess) when the visualization window is requested.
- [ ] The dashboard opens automatically in the default browser (or a dedicated window via `webview`/`pywebview` if a standalone window is preferred over a browser tab).
- [ ] The dashboard displays **multiple variable plots at once** (configurable, at minimum 2 simultaneously) using `dcc.Graph`.
- [ ] The **dialog/message log** is displayed in the same dashboard alongside the variable panels (e.g., as a scrollable text area or a `dash_table`).
- [ ] Plots and the log update in **real time** using `dcc.Interval` callbacks or a shared data store.
- [ ] The two interfaces (control software + Dash server) operate independently — neither blocks the other.

## Notes

- A `multiprocessing.Process` is recommended to launch the Dash server without blocking the main control loop.
- Shared state between the control software and the Dash app can be handled via a `multiprocessing.Queue`, shared memory, or a lightweight local data store (e.g., an in-memory SQLite DB or a simple file).
- If a true standalone window (not a browser tab) is required, consider wrapping the Dash app with [`pywebview`](https://pywebview.flowrl.com/).
- Allow the user to configure which variables to display and the layout of the panels.