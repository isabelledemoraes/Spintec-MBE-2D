# connection_monitor.py
"""
Instrument connection status (issue #12). No Qt: unit-testable on its own.

The existing communication code reports each try here: the polling workers
after every poll cycle (their first cycles are the startup check), the shutter
controller after every NI-DAQ write. Nothing here talks to an instrument.

An instrument is LOST after MAX_FAILURES consecutive failed tries (the delay
between tries is the worker's polling interval). The next success marks it
RECOVERED; it stays in the alert until acknowledge().

Thread-safe: workers report from their threads, the GUI reads snapshot(),
drain_events() and banner_text() from its 1 s timer.
"""
import threading
import time
from dataclasses import dataclass, replace

MAX_FAILURES = 10

CHECKING = "checking"      # no answer yet since startup, not lost yet
CONNECTED = "connected"
FAILING = "failing"        # failed tries, fewer than max_failures
LOST = "lost"
RECOVERED = "recovered"    # answered again after LOST, not yet acknowledged


def _hm(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def _hms(ts: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(ts))


@dataclass
class InstrumentStatus:
    name: str
    max_failures: int
    failures: int = 0                  # consecutive failed tries
    first_failure: float | None = None
    last_ok: float | None = None
    last_error: str = ""
    lost_since: float | None = None    # time of the first failed try; None = not lost
    recovered_at: float | None = None  # None = nothing to acknowledge

    @property
    def state(self) -> str:
        if self.lost_since is not None:
            return LOST
        if self.failures:
            return FAILING
        if self.last_ok is None:
            return CHECKING
        if self.recovered_at is not None:
            return RECOVERED
        return CONNECTED

    @property
    def checked(self) -> bool:
        """The startup check is over for this instrument (it answered or it is lost)."""
        return self.last_ok is not None or self.lost_since is not None


def panel_text(st: InstrumentStatus) -> str:
    """Line shown in the instrument's panel ("" when nothing to show)."""
    if st.state == LOST:
        last_ok = _hms(st.last_ok) if st.last_ok is not None else "never since startup"
        return f"⚠ No communication — last OK: {last_ok}"
    if st.recovered_at is not None:
        return f"✓ Communication recovered at {_hms(st.recovered_at)}"
    return ""


def frame_style(base: str, object_name: str, lost: bool) -> str:
    """Qt style sheet of a panel: base style for the panel and its children, red border when lost."""
    style = f"* {{ {base} }}"
    if lost:
        style += f" QFrame#{object_name} {{ border: 2px solid #dc3545; }}"
    return style


class ConnectionMonitor:
    def __init__(self, names=(), max_failures=None, clock=time.time):
        self._lock = threading.Lock()
        self._clock = clock
        self._default_max = MAX_FAILURES if max_failures is None else max_failures
        self._status: dict[str, InstrumentStatus] = {}
        self._events: list[str] = []
        self._startup_reported = False
        for name in names:
            self.add(name)

    def add(self, name: str, max_failures: int | None = None):
        """Register an instrument (in banner order). max_failures overrides the default."""
        with self._lock:
            self._status[name] = InstrumentStatus(
                name, self._default_max if max_failures is None else max_failures)

    # ── Reports from the communication code ──────────────────────────────
    def report_success(self, name: str):
        with self._lock:
            st = self._status[name]
            now = self._clock()
            if st.lost_since is not None:
                self._events.append(
                    f"{name}: communication RECOVERED (lost since {_hms(st.lost_since)}).")
                st.recovered_at = now
                st.lost_since = None
            st.failures = 0
            st.first_failure = None
            st.last_ok = now
            self._check_startup_done()

    def report_failure(self, name: str, error):
        with self._lock:
            st = self._status[name]
            now = self._clock()
            st.last_error = str(error)
            if st.lost_since is not None:
                return
            st.failures += 1
            if st.first_failure is None:
                st.first_failure = now
            if st.failures >= st.max_failures:
                self._set_lost(st, st.first_failure,
                               f"{st.failures} consecutive failed tries")
            self._check_startup_done()

    def mark_lost(self, name: str, reason: str):
        """Lost at once, without counting tries (e.g. the port could not be opened)."""
        with self._lock:
            st = self._status[name]
            st.last_error = reason
            if st.lost_since is None:
                self._set_lost(st, self._clock(), reason)
            self._check_startup_done()

    def _set_lost(self, st: InstrumentStatus, since: float, why: str):
        st.lost_since = since
        st.recovered_at = None
        last_ok = _hms(st.last_ok) if st.last_ok is not None else "never since startup"
        self._events.append(
            f"{st.name}: communication LOST ({why}; last error: {st.last_error}). "
            f"Last OK: {last_ok}.")

    def _check_startup_done(self):
        if self._startup_reported or not all(s.checked for s in self._status.values()):
            return
        self._startup_reported = True
        lost = [s.name for s in self._status.values() if s.lost_since is not None]
        if lost:
            self._events.append(
                f"Startup check done: {len(lost)} of {len(self._status)} instruments "
                f"not answering: {', '.join(lost)}.")
        else:
            self._events.append(
                f"Startup check done: all {len(self._status)} instruments answer.")

    # ── Read by the GUI ──────────────────────────────────────────────────
    def startup_done(self) -> bool:
        with self._lock:
            return self._startup_reported

    def snapshot(self) -> dict[str, InstrumentStatus]:
        with self._lock:
            return {name: replace(st) for name, st in self._status.items()}

    def drain_events(self) -> list[str]:
        """Lost / recovered / startup messages since the last call, for the log."""
        with self._lock:
            events, self._events = self._events, []
            return events

    def banner_text(self) -> str | None:
        """Alert line, or None when there is nothing to show."""
        with self._lock:
            parts = []
            for st in self._status.values():
                if st.lost_since is not None:
                    parts.append(f"{st.name} (since {_hm(st.lost_since)})")
                elif st.recovered_at is not None:
                    parts.append(f"{st.name} (recovered at {_hm(st.recovered_at)})")
        if not parts:
            return None
        return "⚠ Communication lost: " + ", ".join(parts)

    def has_recovered(self) -> bool:
        with self._lock:
            return any(st.recovered_at is not None and st.lost_since is None
                       for st in self._status.values())

    def acknowledge(self) -> list[str]:
        """Remove recovered instruments from the alert; still-lost ones stay. Returns their names."""
        with self._lock:
            names = []
            for st in self._status.values():
                if st.recovered_at is not None and st.lost_since is None:
                    st.recovered_at = None
                    names.append(st.name)
            return names
