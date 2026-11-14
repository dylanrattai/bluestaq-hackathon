"""Rich console logging and the JSONL alarm log (spec §7.8) [R32]."""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.logging import RichHandler

_console: Console | None = None


def setup_logging(level: str = "INFO") -> Console:
    global _console
    force = os.environ.get("FORCE_COLOR") not in (None, "", "0") and not os.environ.get("NO_COLOR")
    _console = Console(force_terminal=True if force else None, no_color=bool(os.environ.get("NO_COLOR")))
    handler = RichHandler(console=_console, show_path=False, rich_tracebacks=False, markup=True, show_time=True)
    logging.basicConfig(level=level, format="%(message)s", datefmt="%H:%M:%S", handlers=[handler], force=True)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    return _console


def iso(t: float | None, seconds: bool = True) -> str:
    if t is None:
        return "-"
    dt = datetime.fromtimestamp(float(t), tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def hms(t: float | None) -> str:
    if t is None:
        return "-"
    return datetime.fromtimestamp(float(t), tz=timezone.utc).strftime("%H:%M:%SZ")


def dhms(t: float | None) -> str:
    if t is None:
        return "-"
    return datetime.fromtimestamp(float(t), tz=timezone.utc).strftime("%m-%d %H:%M:%SZ")


STATE_STYLE = {"WATCH": "yellow", "ALARM": "bold red", "CONFIRMED": "bold green", "RETRACTED": "dim"}


class EventSink:
    """Fan-out for alarm/watchdog events: console line, JSONL file, in-memory feed, SSE subscribers."""

    def __init__(self, out_dir: str | os.PathLike | None, max_feed: int = 500):
        self.log = logging.getLogger("sr.alarm")
        self.path = Path(out_dir) / "alarm_log.jsonl" if out_dir else None
        self._fh = None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = open(self.path, "w")
        self.feed: list[dict] = []
        self.max_feed = max_feed
        self.subscribers: list[Any] = []
        self.lock = threading.Lock()

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None

    def subscribe(self, q: Any) -> None:
        with self.lock:
            self.subscribers.append(q)

    def unsubscribe(self, q: Any) -> None:
        with self.lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def emit(self, rec: dict, console: bool = True, quiet: bool = False) -> None:
        rec = dict(rec)
        rec.setdefault("t_wall", datetime.now(tz=timezone.utc).isoformat(timespec="seconds"))
        if self._fh:
            self._fh.write(json.dumps(rec, default=str) + "\n")
            self._fh.flush()
        if quiet:
            return                    # v2.3 P1: suppressed nuisance alerts go to the JSONL log only
        with self.lock:
            self.feed.append(rec)
            if len(self.feed) > self.max_feed:
                del self.feed[: len(self.feed) - self.max_feed]
            subs = list(self.subscribers)
        for q in subs:
            try:
                q.put_nowait(rec)
            except Exception:
                pass
        if console:
            self._console_line(rec)

    def _console_line(self, rec: dict) -> None:
        kind = rec.get("kind", "alarm")
        if kind == "watchdog":
            self.log.info("[cyan]%s  WATCHDOG  %d alarm(s) touched  %s[/cyan]", hms(rec.get("t_data")),
                          rec.get("n_touched", 0), "; ".join(rec.get("actions", [])[:6]))
            return
        st = rec.get("to", "")
        style = STATE_STYLE.get(st, "white")
        trs = ",".join(rec.get("tractors", []))
        bf = rec.get("bf")
        conf = rec.get("confidence")
        reason = rec.get("reason", "")
        extra = f"  reason={reason}" if reason and reason not in ("RAISED", "ESCALATED") else ""
        bf_s = f"BF={bf:.0f}" if isinstance(bf, (int, float)) else ""
        conf_s = f"conf={conf:.2f}" if isinstance(conf, (int, float)) else ""
        self.log.info("[%s]%s  %-9s %-16s %-8s %-8s %-9s %s%s[/%s]", style, hms(rec.get("t_data")), st,
                      rec.get("cls", ""), trs, bf_s, conf_s, rec.get("alarm_id", ""), extra, style)
