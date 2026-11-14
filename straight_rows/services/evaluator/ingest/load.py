"""Telemetry and SDA adapters (spec §3). Formats DISCOVERED from the real files."""
from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from core.config import parse_iso
from core.srtypes import FIX_RANK

log = logging.getLogger("sr.load")


# --------------------------------------------------------------------------- telemetry
@dataclass
class TractorSeries:
    cn0: np.ndarray        # dB-Hz, NaN where missing
    cn0_min: np.ndarray
    sats: np.ndarray       # float, NaN where missing
    fix_rank: np.ndarray   # float 0..4, NaN where missing
    link_ok: np.ndarray    # float 1/0, NaN where missing
    xte: np.ndarray        # metres, signed
    speed: np.ndarray      # m/s
    lat: np.ndarray
    lon: np.ndarray


@dataclass
class Telemetry:
    t: np.ndarray                      # int64 epoch seconds, common 1 Hz axis
    tractors: list[str]
    series: dict[str, TractorSeries]
    units: dict[str, str] = field(default_factory=dict)

    @property
    def t_start(self) -> int:
        return int(self.t[0])

    @property
    def t_end(self) -> int:
        """One past the last sample."""
        return int(self.t[-1]) + 1

    def truncate(self, t_cut: float) -> "Telemetry":
        """Copy with all samples at or after t_cut removed (causality tests)."""
        n = int(np.searchsorted(self.t, t_cut, side="left"))
        ser = {k: TractorSeries(**{f: getattr(v, f)[:n] for f in v.__dataclass_fields__}) for k, v in self.series.items()}
        return Telemetry(self.t[:n], list(self.tractors), ser, dict(self.units))


def _unit_from_name(name: str, kind: str) -> tuple[str, float]:
    """Return (unit label, multiplier to SI) from a column name suffix."""
    n = name.lower()
    if kind == "xte":
        if n.endswith("_cm") or "_cm_" in n:
            return "cm", 0.01
        if n.endswith("_mm"):
            return "mm", 0.001
        return "m", 1.0
    if kind == "speed":
        if "kmh" in n or "kph" in n or "km_h" in n:
            return "km/h", 1 / 3.6
        if "mph" in n:
            return "mph", 0.44704
        return "m/s", 1.0
    return "", 1.0


def load_telemetry(data_dir: str | Path, cfg: dict[str, Any]) -> Telemetry:
    path = Path(data_dir) / "farm" / "tractor_telemetry.csv.gz"
    cols = cfg["columns"]
    df = pd.read_csv(path)
    need = [cols[k] for k in ("ts", "tractor", "cn0_mean", "cn0_min", "sats", "fix", "link", "xte", "speed", "lat", "lon")]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"telemetry is missing columns {missing}; have {list(df.columns)}")
    ts = pd.to_datetime(df[cols["ts"]], utc=True)
    tsec = (ts.astype("int64") // 1_000_000_000).to_numpy()
    xte_unit, xte_mul = _unit_from_name(cols["xte"], "xte")
    spd_unit, spd_mul = _unit_from_name(cols["speed"], "speed")
    units = {"xte": xte_unit, "speed": spd_unit, "cn0": "dB-Hz"}
    log.info("telemetry: %d rows, %s..%s; units detected xte=%s speed=%s (converted to m and m/s)",
             len(df), ts.min().isoformat(), ts.max().isoformat(), xte_unit, spd_unit)
    log.info("telemetry first record: %s", df.iloc[0].to_dict())

    t0, t1 = int(tsec.min()), int(tsec.max())
    t_axis = np.arange(t0, t1 + 1, dtype=np.int64)
    n = len(t_axis)
    tractors = sorted(df[cols["tractor"]].astype(str).unique().tolist())
    fix_map = {k: float(v) for k, v in FIX_RANK.items()}
    link_raw = df[cols["link"]]
    if link_raw.dtype == object:
        lk = link_raw.astype(str).str.lower().map(
            {"true": 1.0, "false": 0.0, "link_ok": 1.0, "link_down": 0.0, "1": 1.0, "0": 0.0, "ok": 1.0, "down": 0.0})
    else:
        lk = link_raw.astype(float)
    df["_link"] = lk
    df["_fix"] = df[cols["fix"]].astype(str).map(fix_map)
    unknown_fix = df[cols["fix"]][df["_fix"].isna()].unique()
    if len(unknown_fix):
        log.warning("unknown fix_state values %s -> treated as missing", list(unknown_fix)[:5])

    series: dict[str, TractorSeries] = {}
    for tr in tractors:
        g = df[df[cols["tractor"]].astype(str) == tr]
        idx = tsec[g.index.to_numpy()] - t0
        ok = (idx >= 0) & (idx < n)
        idx = idx[ok]
        g = g.iloc[np.nonzero(ok)[0]]
        # last write wins on duplicate timestamps
        def put(col: np.ndarray) -> np.ndarray:
            a = np.full(n, np.nan)
            a[idx] = col
            return a
        series[tr] = TractorSeries(
            cn0=put(g[cols["cn0_mean"]].to_numpy(float)),
            cn0_min=put(g[cols["cn0_min"]].to_numpy(float)),
            sats=put(g[cols["sats"]].to_numpy(float)),
            fix_rank=put(g["_fix"].to_numpy(float)),
            link_ok=put(g["_link"].to_numpy(float)),
            xte=put(g[cols["xte"]].to_numpy(float) * xte_mul),
            speed=put(g[cols["speed"]].to_numpy(float) * spd_mul),
            lat=put(g[cols["lat"]].to_numpy(float)),
            lon=put(g[cols["lon"]].to_numpy(float)),
        )
        gaps = int(np.isnan(series[tr].cn0).sum())
        if gaps:
            log.warning("%s: %d missing seconds on the 1 Hz axis", tr, gaps)
    return Telemetry(t=t_axis, tractors=tractors, series=series, units=units)


# --------------------------------------------------------------------------- SDA feeds
@dataclass(frozen=True)
class Advisory:
    id: str
    start: float
    end: float
    type: str
    text: str
    svn: str | None = None
    end_source: str = ""


@dataclass(frozen=True)
class RfiReport:
    id: str
    t_event: float
    t_avail: float
    lat: float | None
    lon: float | None
    radius_km: float
    confidence: float
    text: str
    band: str = ""
    farm_wide: bool = False


@dataclass
class HeldValue:
    value: float | None
    ts: float | None
    missing: bool
    reason: str = ""


def _series(records: list[dict], key: str) -> list[tuple[float, float]]:
    out = []
    for r in records:
        if key not in r or r.get("ts") is None:
            continue
        out.append((parse_iso(r["ts"]), float(r[key])))
    out.sort()
    return out


def _pick_key(rec: dict, candidates: list[str]) -> str | None:
    for c in candidates:
        if c in rec:
            return c
    return None


def _parse_advisory_end(start: float, rec: dict, cfg_adv: dict) -> tuple[float, str]:
    for k in ("end", "end_ts", "valid_until", "expires", "until"):
        if rec.get(k):
            return parse_iso(rec[k]), f"field:{k}"
    text = str(rec.get("text", ""))
    m = re.search(r"(\d{2})(\d{2})\s*-\s*(\d{2})(\d{2})\s*Z", text)
    if m:
        day0 = start - (start % 86400)
        s = day0 + int(m.group(1)) * 3600 + int(m.group(2)) * 60
        e = day0 + int(m.group(3)) * 3600 + int(m.group(4)) * 60
        if e <= s:
            e += 86400
        return max(e, start), "text:window"
    m = re.search(r"(\d+)\s*(?:-|–|to)\s*(\d+)\s*h", text)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        hrs = max(a, b) if str(cfg_adv.get("duration_pick", "max")) == "max" else min(a, b)
        return start + hrs * 3600, f"text:{a}-{b}h->{hrs}h"
    m = re.search(r"(\d+)\s*h\b", text)
    if m:
        return start + int(m.group(1)) * 3600, f"text:{m.group(1)}h"
    return start + float(cfg_adv.get("default_hours", 24)) * 3600, "default"


@dataclass
class SdaFeeds:
    kp: list[tuple[float, float]]
    dst: list[tuple[float, float]]
    s4: list[tuple[float, float]]
    advisories: list[Advisory]
    rfi: list[RfiReport]
    cfg: dict[str, Any]

    # ---- held values with staleness
    def _held(self, ser: list[tuple[float, float]], now: float, cadence_s: float) -> HeldValue:
        if not ser:
            return HeldValue(None, None, True, "no records")
        ts = [p[0] for p in ser]
        i = int(np.searchsorted(ts, now, side="right")) - 1
        if i < 0:
            return HeldValue(None, None, True, "before first record")
        t, v = ser[i]
        if now - t > self.cfg["sda"]["stale_factor"] * cadence_s:
            return HeldValue(None, t, True, "stale")
        return HeldValue(v, t, False)

    def indices_at(self, now: float) -> dict[str, HeldValue]:
        c = self.cfg["sda"]
        return {
            "kp": self._held(self.kp, now, c["kp_cadence_min"] * 60),
            "dst": self._held(self.dst, now, c["dst_cadence_min"] * 60),
            "s4": self._held(self.s4, now, c["s4_cadence_min"] * 60),
        }

    def advisories_open(self, now: float, space_weather_only: bool = True) -> list[Advisory]:
        out = []
        for a in self.advisories:
            if a.start <= now < a.end and (not space_weather_only or a.type == "SPACE_WEATHER"):
                out.append(a)
        return out

    def rfi_available(self, now: float) -> list[RfiReport]:
        return [r for r in self.rfi if r.t_avail <= now]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def load_sda(data_dir: str | Path, cfg: dict[str, Any]) -> SdaFeeds:
    d = Path(data_dir) / "sda"
    fcfg = cfg["findings"]

    with open(d / "sgi_indices.json") as f:
        sgi = json.load(f)
    kp_recs = sgi.get("kp", [])
    dst_recs = sgi.get("dst", [])
    kp_key = _pick_key(kp_recs[0], ["kp", "Kp", "value"]) if kp_recs else None
    dst_key = _pick_key(dst_recs[0], ["dst_nT", "dst", "Dst", "value"]) if dst_recs else None
    kp = _series(kp_recs, kp_key) if kp_key else []
    dst = _series(dst_recs, dst_key) if dst_key else []
    log.info("sgi_indices: %d Kp (first %s), %d Dst (first %s)", len(kp), kp_recs[:1], len(dst), dst_recs[:1])

    with open(d / "scintillation_series.json") as f:
        sc = json.load(f)
    s4_recs = sc.get("series", sc.get("s4", []))
    s4_key = _pick_key(s4_recs[0], ["s4", "S4", "value"]) if s4_recs else None
    s4 = _series(s4_recs, s4_key) if s4_key else []
    log.info("scintillation_series: %d S4 records (first %s), region=%s", len(s4), s4_recs[:1], sc.get("region"))

    with open(d / "constellation_advisories.json") as f:
        adv = json.load(f)
    advisories: list[Advisory] = []
    for r in adv.get("advisories", []):
        start_key = _pick_key(r, ["start", "ts", "issued", "time"])
        start = parse_iso(r[start_key]) if start_key else float("nan")
        end, src = _parse_advisory_end(start, r, fcfg["advisory"])
        typ = str(r.get("type", "UNKNOWN")).upper()
        if "SPACE" in typ or "STORM" in typ or "GEOMAG" in typ:
            typ = "SPACE_WEATHER"
        a = Advisory(str(r.get("id", f"ADV-{len(advisories)+1}")), start, end, typ, str(r.get("text", "")), r.get("svn"), src)
        advisories.append(a)
        if typ == "SPACE_WEATHER":
            log.info("advisory %s SPACE_WEATHER open %s -> %s (end from %s)", a.id, _iso(a.start), _iso(a.end), src)
        else:
            log.info("advisory %s type=%s (not space weather; logged only): %s", a.id, typ, a.text[:80])
    log.info("constellation_advisories first record: %s", adv.get("advisories", [None])[0])

    with open(d / "rfi_reports.json") as f:
        rf = json.load(f)
    rfi: list[RfiReport] = []
    rcfg = fcfg["rfi"]
    for r in rf.get("reports", []):
        t_event = parse_iso(r[_pick_key(r, ["ts", "time", "event_ts", "start"])])
        pub_key = _pick_key(r, ["published", "publish_ts", "received", "arrival", "available", "issued_ts"])
        if pub_key:
            t_avail = parse_iso(r[pub_key])
            src = f"field:{pub_key}"
        else:
            t_avail = t_event + rcfg["assumed_delay_min"] * 60
            src = f"event+{rcfg['assumed_delay_min']}min"
        lat = r.get("centerLat", r.get("lat"))
        lon = r.get("centerLon", r.get("lon"))
        rad_key = _pick_key(r, ["radius_km", "semiMajor_km", "semi_major_km", "radiusKm"])
        radius = float(r[rad_key]) if rad_key else float(rcfg["default_radius_km"])
        farm_wide = lat is None or lon is None
        conf = float(r.get("confidence", 1.0))
        rep = RfiReport(str(r.get("id")), t_event, t_avail, None if farm_wide else float(lat),
                        None if farm_wide else float(lon), radius, conf, str(r.get("text", "")), str(r.get("band", "")), farm_wide)
        rfi.append(rep)
        log.info("rfi %s event %s available %s (%s) radius %.2f km%s conf %.2f", rep.id, _iso(t_event), _iso(t_avail), src,
                 radius, " FARM-WIDE (no position)" if farm_wide else "", conf)
    log.info("rfi_reports first record: %s", rf.get("reports", [None])[0])
    rfi.sort(key=lambda x: x.t_event)
    return SdaFeeds(kp, dst, s4, advisories, rfi, cfg)


def _iso(t: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
