"""ACCURACY_UPDATE v2.3 P1: learn the recurring multipath sites on dev and freeze them in multipath_sites.yaml.

    python -m tools.fit_sites --data ../../data

A cell (0.0002 deg x 0.0002 deg) is a site when it has >= 20 working passes on dev and >= 50% of them had a dip
(a dip on that tractor within the pass +- 10 s). Reports are keyed by the cell where a dip reaches its largest
deficit. (Counting a dip only in its peak cell halves the rate: each headland turn hops 12 m sideways through two
15 m cells.) Dev only (before the evaluation window); deterministic.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import yaml

from core.config import load_config
from detect.site_multipath import find_dips, params, pass_rates

HERE = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data", default="../../data")
    ap.add_argument("--out", default=str(HERE / "multipath_sites.yaml"))
    args = ap.parse_args(argv)
    from ingest.load import load_telemetry
    cfg = load_config(None)
    p = params(cfg)
    t_end = float(cfg["window"]["start_s"])
    tele = load_telemetry(Path(args.data), cfg)
    depth: dict = {}
    for tr in tele.tractors:
        for d in find_dips(tele, tr, p):
            if d["peak_t"] < t_end:
                depth.setdefault(d["cell"], []).append(d["depth_db"])
    pr = pass_rates(tele, p, t_end)
    pas = {k: v[0] for k, v in pr.items()}
    dips = {k: v[1] for k, v in pr.items()}
    cells = sorted(set(pas) | set(dips))
    lat_all = [k[0] for k in pas]
    top, bot = max(lat_all), min(lat_all)
    sites = []
    for k in cells:
        n, m = pas.get(k, 0), dips.get(k, 0)
        rate = m / n if n else 0.0
        if n >= p["min_passes"] and rate >= p["min_rate"]:
            name = "north headland turn" if k[0] >= top - 2 else ("south headland turn" if k[0] <= bot + 2 else "recurring site")
            sites.append({"cell": [k[0], k[1]], "lat": [round(k[0] * p["cell"], 6), round((k[0] + 1) * p["cell"], 6)],
                          "lon": [round(k[1] * p["cell"], 6), round((k[1] + 1) * p["cell"], 6)], "passes": n, "dips": m,
                          "rate": round(rate, 4), "median_depth_db": round(float(np.median(depth[k])), 2) if k in depth else None,
                          "peak_dips": len(depth.get(k, [])), "name": name})
    def band(rows):
        n = sum(pas.get(k, 0) for k in rows)
        return round(sum(dips.get(k, 0) for k in rows) / n, 4) if n else None
    # the turn-row cells of each headland: cells with passes in the top / bottom two lat rows of the working area
    north = [k for k in pas if k[0] >= top - 2]
    south = [k for k in pas if k[0] <= bot + 2]
    tot_n = sum(s["passes"] for s in sites)
    tot_d = sum(s["dips"] for s in sites)
    doc = {"what": "recurring multipath sites learned on dev (ACCURACY_UPDATE v2.3 P1); frozen, eval/live read it",
           "params": {k: v for k, v in p.items() if k not in ("sites", "enabled")},
           "dev_end": cfg["window"]["start"],
           "telemetry_sha": hashlib.sha256((Path(args.data) / "farm" / "tractor_telemetry.csv.gz").read_bytes()).hexdigest()[:16],
           "n_cells_seen": len(cells), "n_sites": len(sites), "site_passes": tot_n, "site_dips": tot_d,
           "north_headland_rate": band(north), "south_headland_rate": band(south),
           "sites": sites}
    Path(args.out).write_text(yaml.safe_dump(doc, sort_keys=False, width=160))
    print(f"wrote {args.out}: {len(sites)} site cells, {tot_d}/{tot_n} dips/passes; north band {doc['north_headland_rate']}, "
          f"south band {doc['south_headland_rate']}")
    for s in sites[:40]:
        print(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
