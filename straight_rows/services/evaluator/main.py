#!/usr/bin/env python3
"""Straight Rows GNSS degradation watchdog.

  python main.py batch --data ../../data --out ../../out                 # max speed, no server; regenerates submission.json
  python main.py batch --dev --data ../../data --out ../../out           # same, then score against the dev labels
  python main.py dev   --data ../../data --out ../../out                 # alias for batch --dev
  python main.py live  --data ../../data --out ../../out --speed 60 --port 8000   # paced replay + HTTP API
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path

from core.config import load_config
from runtime.engine import Engine
from runtime.export import ExportError
from core.log import EventSink, setup_logging

log = logging.getLogger("sr.main")


def parse_speed(s: str) -> float | None:
    if str(s).lower() in ("max", "inf", "0"):
        return None
    v = float(s)
    return None if v <= 0 else v


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    for name in ("batch", "dev", "live"):
        p = sub.add_parser(name)
        p.add_argument("--data", required=True)
        p.add_argument("--out", required=True)
        p.add_argument("--config", default=None)
        p.add_argument("--validator", default=None, help="path to validate_submission.py")
        p.add_argument("--log-level", default="INFO")
        p.add_argument("--allow-empty", action="store_true", help="write submission.json even with no detections")
        p.add_argument("--dev", action="store_true", help="after the replay, score the pipeline against data/labels "
                                                             "(diagnostic only; writes out/dev_report.md)")
        if name == "live":
            p.add_argument("--speed", default="60", help="data seconds per wall second, or 'max'")
            p.add_argument("--port", type=int, default=8000)
            p.add_argument("--host", default="0.0.0.0")
    args = ap.parse_args(argv)
    setup_logging(args.log_level)
    cfg = load_config(args.config)
    cfg["_allow_empty"] = bool(args.allow_empty)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    sink = EventSink(out_dir)

    if args.mode in ("batch", "dev"):
        dev = args.dev or args.mode == "dev"
        if dev:
            cfg["_allow_empty"] = True
        eng = Engine(cfg, args.data, out_dir, speed=None, validator=args.validator, sink=sink, mode=args.mode)
        rc = 0
        try:
            eng.run()
        except ExportError as e:
            log.error("export failed: %s", e)
            rc = 2
        finally:
            sink.close()
        if dev and eng.b_done:
            from tools.dev_check import run_dev_check
            run_dev_check(eng, cfg, Path(args.data), out_dir)
        return rc

    # live
    import uvicorn
    from runtime.api import create_app

    speed = parse_speed(args.speed)
    eng = Engine(cfg, args.data, out_dir, speed=speed, validator=args.validator, sink=sink, mode="live")
    th = threading.Thread(target=_run_engine, args=(eng,), name="engine", daemon=True)
    th.start()
    app = create_app(eng)
    log.info("API on http://%s:%d  (ui: /ui, health: /health, stream: /stream)", args.host, args.port)
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    except KeyboardInterrupt:
        pass
    finally:
        eng.stop_event.set()
        sink.close()
    return 0 if eng.error is None else 2


def _run_engine(eng: Engine) -> None:
    try:
        eng.run()
        log.info("replay finished; the API stays up until Ctrl-C")
    except ExportError as e:
        log.error("export failed: %s", e)
    except Exception:  # noqa: BLE001
        log.exception("engine crashed")


if __name__ == "__main__":
    sys.exit(main())
