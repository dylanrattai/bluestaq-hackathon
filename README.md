# Straight Rows — GNSS Degradation Watchdog

Solution by **dylanrattai** for the Bluestaq × NDSU *Straight Rows* hackathon (November 13–14, 2026):
detect degraded GPS guidance on a simulated three-tractor fleet, attribute the cause (space weather, jamming,
spoofing, equipment fault, multipath) from farm telemetry plus Space Domain Awareness feeds, and recommend an
operator action with checkable evidence.

**Everything about the solution is in [`straight_rows/README.md`](straight_rows/README.md):** setup, the exact
command that regenerates `submission.json`, pinned dependencies, approach, use of the labelled data, dev-set
evaluation, known failure modes, and what extra data would help.

## Quick reproduce

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -r starter/requirements.txt               # only for get_data.py
python starter/scripts/get_data.py                              # official dataset -> starter/data/
cd straight_rows && ln -s ../starter/data data
pip install -r services/evaluator/requirements.txt              # exact pins, Python 3.12
cd services/evaluator && python main.py batch --data ../../data --out ../../out
diff ../../out/submission.json <(git show HEAD:straight_rows/out/submission.json) && echo reproduced
```

(Docker alternative: `cd straight_rows && docker compose run --rm batch`.)

## Layout

| Path | Contents |
|---|---|
| `straight_rows/` | The solution: evaluator service (Python), web UI (React), outputs in `out/` |
| `straight_rows/out/submission.json` | The submitted detections for the evaluation window |
| `starter/` | The official starter kit as provided (guide PDF, baseline, validator, `get_data.py`) |

All challenge data is synthetic and unclassified; the dataset itself is not committed (download as above).
