# Feature Request: Parallel Log Visualization Interface

## Context

Currently, the log visualization is embedded within the control software UI and only allows monitoring **one variable at a time** in the same interface.

## Objective

Implement a **standalone, parallel visualization window** that launches independently of the control software and displays **multiple variables simultaneously** using Plotly.

## Suggested Technology: Plotly + pywebview

Use **Plotly** with **[pywebview](https://pywebview.flowrl.com/)** to render the visualization in a native desktop window, avoiding any localhost/browser connection issues. Plotly figures should be rendered as **standalone HTML** (using `plotly.offline.plot` with `output_type='div'`) and injected directly into the pywebview window — no local web server or network connection required.

This approach is preferred over Plotly Dash because:
- Dash requires a localhost server, which may be blocked by system or network policies.
- pywebview renders content in a native OS window with no browser or port involved.
- It keeps the solution self-contained and avoids firewall/permission issues.

## Requirements

- The visualization window must be **fully independent** from the control software — launched as a separate process so that neither blocks the other.
- **Multiple variables must be visible simultaneously** in a multi-panel layout (no tab switching needed).
- The window should update in **real time** as new data arrives.
- **No localhost server, no browser tab** — the window must be a standalone desktop window.

## Acceptance Criteria

- [ ] A pywebview window is launched as a **separate process** (e.g., via `multiprocessing.Process`) when visualization is requested.
- [ ] Plotly figures are rendered as **self-contained HTML** injected into the pywebview window — no network calls, no localhost.
- [ ] The window displays **multiple variable plots at once** (configurable, at minimum 2 simultaneously) in a grid or stacked layout.
- [ ] Plots update in **real time** by periodically regenerating the HTML and refreshing the webview content.
- [ ] The control software and the visualization window run **independently** — closing or interacting with one does not affect the other.

## Notes

- Use `multiprocessing.Process` to launch the pywebview window without blocking the main control loop.
- Shared data between the control software and the visualization process can be passed via `multiprocessing.Queue` or shared memory.
- To update plots in real time, regenerate the Plotly HTML string and call `webview.windows[0].load_html(new_html)` from within the subprocess.
- Allow the user to configure which variables to display and the layout of panels.