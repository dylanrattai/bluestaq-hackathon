# Straight Rows web UI

React + TypeScript (Vite) front end for the evaluator's HTTP API. It has two pages:

- **Operator** (`#/`): satellite map with the field outline, trails, RFI circles and an optional multipath-site
  overlay; farm status with the recommended action; tractor cards; ALARM and CONFIRMED detections with evidence
  and history; a 72 h timeline.
- **Judge view** (`#/extended`), with four tabs:
  1. Data flow: click a block for its What / Why / How and the research behind it.
  2. Walkthroughs: three real episodes from the recorded run.
  3. Live engine: per-tractor findings and Bayes factors, every alarm state, watchdog runs, the feed, and a noisy-OR
     sandbox.
  4. Limits & research: weaknesses, led by multipath; the dev score table; references.

  Keys: `1`–`4` switch tabs, `Q`/`W`/`E` jump to a walkthrough, and `←`/`→` or space step through it.

## Run

```bash
docker compose up evaluator webui      # from straight_rows/; open http://localhost:5173
```

Without Docker, run `npm install && npm run dev`. Vite proxies the API paths to `API_TARGET`
(default `http://localhost:8000`), so the evaluator needs no CORS changes.

## LIVE and DEMO

The header pill shows which mode is running.

- **LIVE**: polls `/status` and `/watchdog` and `/feed` every 2 s, and `/alarms?include_suppressed=1` every 5 s.
  `/webui/meta` is read once. Map trails build up from successive polls.
- **DEMO**: used when the API is unreachable. The UI replays `public/demo/snapshot.json` on a client clock that
  you can play, pause, seek and speed up. It keeps probing the API every 10 s and switches to LIVE when it answers.
  Alarm states are the last logged event at each time, so the replay never shows anything the engine did not log.
  Per-batch findings and Bayes factors are not recorded, so the Live engine tab needs LIVE for those.

The walkthroughs always use the snapshot, so they work in both modes.

### Regenerate the snapshot after changing the engine

```bash
cd services/evaluator
python -m runtime.webui --data ../../data --out ../../out    # about 1 min; --out is only read for dev_report.md
```

This runs its own max-speed replay in a temporary folder. It never writes to `out/`. A finished run is needed
because the alarm log has no event when an episode closes normally, so final spans come from the engine.

## Webui hooks in the evaluator

Every hook is read-only and marked `webui hook` in the code:

| where | what |
|---|---|
| `runtime/webui.py` (new) | `tractor_now` (position and receiver state at data time), `meta` (window, field outline from the 1st–99th percentile of moving samples, RFI reports with availability times, the live fusion tables, multipath sites, dev scores), and the snapshot writer |
| `runtime/engine.py` `snapshot()` | adds `lat`, `lon`, `cn0`, `cn0_min`, `sats`, `fix`, `speed` and `link_ok` per tractor to `/status` |
| `runtime/api.py` | `GET /webui/meta` |
| `tests/test_api.py` | `test_webui_hooks` |

API field names are mapped in one place, `src/normalize.ts`. `src/noisyor.ts` is a port of `diagnose/fusion.py` that
reads the engine's own links and leaks from `/webui/meta`. Its Bayes factors match the Python engine exactly
(checked on six finding sets). It computes the network for a single tractor, so the engine's farm-level BFs are larger.
