# Straight Rows — GNSS Degradation Watchdog

A Dockerised evaluator for a three-tractor farm near Casselton, ND. It replays 1 Hz tractor GNSS telemetry
together with Space Domain Awareness (SDA) feeds and

1. raises **live alarms** (WATCH → ALARM) as data arrives, never using future data;
2. runs a **live watchdog every 5 minutes of data time** that uses hindsight to re-localise onsets, merge
   fragments, reclassify with late evidence (RFI reports), and confirm or retract alarms with a reason;
3. serves everything over an **HTTP API** (plus a small built-in UI) while it runs;
4. writes a validated **`out/submission.json`** of confirmed detections when the replay ends.

| Class | Action |
|---|---|
| SPACE_WEATHER | HALT_OR_DEGRADED_MODE |
| JAMMING | HALT_AND_REPORT |
| SPOOFING | HALT_CROSSCHECK_AND_REPORT |
| EQUIPMENT_FAULT | SERVICE_MACHINE_OTHERS_CONTINUE |
| MULTIPATH | CONTINUE_FALSE_ALARM |

Target hardware: a ≤ $1k edge box (Jetson Orin Nano Super, arm64) or any laptop (amd64). NumPy only, no GPU.
A full 72 h replay takes about 2.5 minutes on the Orin Nano.

## 1. Setup and run

Before shipping, set `participant.github_handle` and `repository_url` in
`services/evaluator/config.yaml` (they ship as `TODO-DYLAN` placeholders; the export logs a red warning until
they are changed). The dataset lives in `data/` (never modified); all outputs go to `out/`.

### Docker (recommended)

```bash
docker build -t straight-rows ./services/evaluator

# batch: max speed, no server; this is the command that regenerates out/submission.json
docker run --rm -v "$PWD/data:/data:ro" -v "$PWD/out:/out" -v "$PWD/submission:/submission:ro" \
  straight-rows python main.py batch --data /data --out /out

# live: paced replay (60 s of data per wall second) with the API on :8000
docker run --rm -p 8000:8000 -v "$PWD/data:/data:ro" -v "$PWD/out:/out" -v "$PWD/submission:/submission:ro" \
  straight-rows python main.py live --data /data --out /out --speed 60 --port 8000
```

Then open <http://localhost:8000/ui>. The process stays up after the replay ends so the final state can be
browsed until Ctrl-C. `--speed max` runs the live mode without pacing.

### Without Docker (Python 3.11+)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r services/evaluator/requirements.txt
cd services/evaluator
python main.py batch --data ../../data --out ../../out
python main.py live  --data ../../data --out ../../out --speed 60 --port 8000
python -m pytest -q                       # 56 tests on synthetic signals with known answers
python main.py batch --dev --data ../../data --out ../../out   # + diagnostic against the development labels
```

### Files

```
data/                         read-only inputs (farm telemetry, SDA feeds, dev labels)
out/                          submission.json, alarm_log.jsonl, dev_report.md, dev_findings.md
submission/validate_submission.py   the starter-kit validator, run as the last export step
compose.yaml                  services: evaluator (live + API), batch, dev
services/evaluator/
  main.py                     CLI entry: batch | dev | live (+ --dev flag)
  config.yaml                 every number, with its source
  core/       srtypes.py dataclasses/enums · config.py · log.py (Rich console + alarm_log.jsonl)
  ingest/     load.py telemetry + SDA adapters · grid.py 30 s grid, features, working gate
  detect/     baseline.py reference + CUSUM (Siegmund) · scales.py ladders · inputs/ F1-F10
              (cn0.py F1-F5, fix.py + sattelites.py F6, link.py F7, cross_track.py F8, s4.py F9-F10)
  diagnose/   fusion.py leaky noisy-OR · lifecycle.py alarms · watchdog.py 5-min pass · evidence.py
  runtime/    engine.py replay loop · api.py FastAPI (+ /ui) · export.py submission + checks
  tools/      dev_check.py diagnostic scorer with per-episode explanations
  tests/      56 pytest tests on synthetic signals only
```

### Checking the pipeline against the development labels

```bash
docker compose run --rm dev                      # or: python main.py batch --dev --data ../../data --out ../../out
```

This replays everything, writes the normal outputs, then scores the confirmed alarms of the development period
(hours 0–48) against `data/labels/train_episodes.json` with the official matching rules and writes
`out/dev_report.md`: an accuracy rating (diagnostic score out of 40, macro-F1, per-class precision/recall), and for
every labelled episode whether it was matched or missed, the onset error, which findings fired during it, the
network's strongest causes, every overlapping alarm with its state and retraction reason, and a one-line verdict
(RIGHT, or WRONG with the stage that failed: detector, strength, attribution, extent/tractors, watchdog). Extra
confirmed detections that match no label are listed the same way. Since Decision v2.1 the labels are also read by
`tools/calibrate.py` (§5), which writes `config_trained.yaml`; `config.yaml` stays the physics version. To run the
trained version: `python main.py batch --config config_trained.yaml --data ../../data --out ../../out`.

## 2. Pinned dependencies

`services/evaluator/requirements.txt` pins exact versions recorded from what resolved on 2026-11-14:
numpy 2.3.3, pandas 2.3.3, PyYAML 6.0.3, rich 15.0.0, fastapi 0.162.7, uvicorn 0.54.0, starlette 1.10.0,
pydantic 2.14.3, httpx 0.28.1, pytest 9.2.0 (and their transitive pins). The Docker image is built from
`python:3.12-slim` (multi-arch, matches the repository's `.python-version`).

## 3. Regenerate the submission

```bash
docker run --rm -v "$PWD/data:/data:ro" -v "$PWD/out:/out" -v "$PWD/submission:/submission:ro" \
  straight-rows python main.py batch --data /data --out /out
python submission/validate_submission.py out/submission.json
```

`submission.json` is byte-identical whether the replay runs at max speed or paced (a test checks it): wall-clock
time never enters the output, confidences are rounded to 3 decimals and times to whole seconds.

## 4. Approach

**Grid and gate.** Everything lives on a global 30 s batch grid aligned to UTC. Per tractor, a working gate says
PARKED (speed < 0.5 m/s for ≥ 5 min) or WORKING (speed > 0.5 m/s for ≥ 30 s); it holds its last state while the
fix is AUTONOMOUS/NO_FIX or a cross-track drift is active, because a degraded or spoofed position can fake motion.
Findings are computed while parked too, but detections are clipped to working time and a fully parked alarm is
retracted (`PARKED`); an alarm that spans a parked night is split into one detection per working segment.

**Reference and CUSUM.** Level changes are detected with tabular CUSUMs on *batch means*, not raw 1 Hz data
(raw autocorrelated data over-alarms [R5–R7]). The reference (mean, std of clean batch means over the trailing
60 min) freezes while a run is in progress and its std is inflated for lag-1 autocorrelation (AR(1) factor,
capped at 3σ [R8, R9]). The decision interval comes from Siegmund's approximation, not from data: with k = 0.5 and
a design false-alarm rate of one per 24 h per stream (ARL₀ = 2880 batches), **h ≈ 6.80** (check: h = 5 gives
ARL₀ ≈ 469, matching published tables [R2]). The onset of a run is backtracked to the last reset [R3]. If fewer
than 30 clean batches exist, absolute fallback scales are used and flagged. Once a finding is open, an
**in-event reference** (mean/σ of the batch means since onset) drives two more CUSUMs (Fix 1): a *recovery*
CUSUM ends the event at its backtracked reset even when the level settles somewhere other than the pre-event
reference (a batch back inside the pre-event band ends it at once), and a *stacked-drop* CUSUM raises F11 when an
abrupt further step lands on top of the open event (a gradual deepening only re-references).

**Findings F1–F10** (each per tractor per batch, with a severity in [0, 1] from a 5-level ladder, §7.3 of the spec):
F1 C/N0 level drop (lower CUSUM on mean C/N0); F2 C/N0 oscillation (upper CUSUM on batch log-variance,
re-referenced to the in-event variance while a level event is open, so scintillation's own fading is in the
baseline and only extra oscillation fires);
F3 equal drop on all working tractors (spread ≤ 3 dB); F4 unequal drop on ≥ 2 tractors (spread > 6 dB, one
distance doubling under inverse-square spreading); F5 short dip (5 s finite moving average test [R4] on min C/N0 and on mean C/N0 against a local 2-min median
ending 30 s earlier, ≥ max(3 dB, 4σ), recovers within 60 s, exact 1 Hz extent at half depth; satellites must be
unchanged for the mean channel only); F6 fix or satellites
degraded while C/N0 is normal (chain-aware: a low C/N0 makes it part of F1, a fix drop within 4 min of a link
loss belongs to F7 [R20, R21]); F7 correction link down, judged on the trailing 5 min (≥ 30 s down in total or ≥ 3 separate outages; severity
from the correction-age ladder on the longest outage);
F8 cross-track drift (upper CUSUM on the batch median of cross-track error during moving, working passes,
headland turns masked; "no information" while the fix is DGPS or worse or inside the link→fix window, because a
non-RTK solution carries decimetre guidance error by itself; in RTK float only a median beyond the float tier's nominal 0.5 m accuracy counts [R18]); F9 space weather active (Kp ≥ 7, Dst ≤ −100 nT, S4 ≥ 0.3, or an open
space-weather advisory; GLOBAL); F10 an RFI report that is *available* (event time + 30 min, no publish field
exists), covers the tractor (semi-major axis as radius) and overlaps the batch (±30 min); F11 a stacked step on
top of an open event (abrupt extra drop: a jammer switching on inside a storm), which since Update v2 opens a
sub-episode instead of entering the network; F12 machine-specific, judged over a whole episode (below).

**Diagnosis network.** A two-layer leaky noisy-OR network in the style of QMR-DT [R22–R24]: six independent
causes (the five classes plus OTHER, prior 0.01 each), twelve findings conditionally independent given the causes,
links on QMR's five-level scale, leak 0.025 (0.20 for F9). Severity scales a present finding's link by
0.4 + 0.6·s. All 2⁶ = 64 cause sets are enumerated exactly per tractor per batch (log space). Decisions use Bayes
factors on the Kass–Raftery scale [R27]: BF ≥ 3 = WATCH, BF ≥ 20 = ALARM candidate, **and** the cause must be in
the most probable non-empty cause set (the explanation), which is what gives explaining-away and Occam pruning
without hand-written vetoes [R26]. OTHER in the explanation with no class alarm is an *unattributed* live alarm
that is never exported.

**Episodes first, causes second (Update v2).** The scorer counts episodes, so the episode is the unit of decision:
first find *when* something is wrong, then decide *what* it is, once per episode. Three tracks:

- *Signal* (SPACE_WEATHER, JAMMING, EQUIPMENT_FAULT). Per tractor per batch A = F1 ∨ F2 ∨ F6_raw ∨ F7 (F6_raw: fix
  degraded > 50% of the batch or satellites ≥ 2 below the reference, no chain rule). A tractor episode opens when A
  holds in 2 of the last 3 batches [R28] (onset = earliest backdated CUSUM onset) and closes after 10 A-false
  batches, which bridges pulsed sources. Overlapping tractor episodes form one *farm episode*. When it opens, every
  tractor's C/N0 level, log-variance and satellite references freeze at the farm onset (clean batches before it,
  no max-age); when it closes they are discarded and rebuilt from post-close batches (fallback scales until 30
  clean batches). Findings are judged over the episode: F1, F2, F6, F9 present when they hold in ≥ 1/3 of the
  episode's batches (F6 also by a flicker clause: ≥ 3 satellite drop-and-recover events while F12 holds); F7 on
  ≥ 30 s down or ≥ 3 outages within the episode; F3/F4 from each tractor's episode-median drop; F10 from any report
  available by now; F12 when every other working tractor had F1 in < 10% of the batches and no episode of its own.
  The network runs once per tractor on these (F5, F8 held absent, F11 missing); tractors are grouped by their most
  probable class, and each group at BF ≥ 20 becomes one detection (SPACE_WEATHER takes every working tractor). OTHER
  or weaker groups are logged as unattributed and never exported. Confidence = BF/(1+BF).
- *Stacked step (§4)*: inside an open farm episode, the in-event step CUSUM firing on ≥ 2 tractors within 2 batches
  opens a sub-episode, classified against the in-event reference; a class different from the parent's at BF ≥ 20
  is a separate detection (jamming switching on inside a storm), otherwise it is absorbed.
- *Position* (SPOOFING): the F8 drift CUSUM with a position-domain F12; an episode opens on SPOOF BF ≥ 20 in 2 of 3
  batches and may overlay any signal episode (a spoofer is built to look normal in the signal).
- *Short dip* (MULTIPATH): an F5 dip counts only when no other episode is open on that tractor within ±60 s; the
  network on {F5, F12 (other machines' C/N0 within 2σ over the dip ±10 s)}; the detection has the dip's exact
  1 Hz extent and is never merged. Every candidate is written to `out/dips.csv`.

**Decision v2.1 changes to the signal track.**
- *Headland turns are a known non-event.* A batch with a heading change > 30° (a row-end turn), or whose first 12 s
  follow one, is a *turn batch*: F2, F5 and the satellite half of F6 carry no information there, the batch stays
  out of the log-variance and satellite references, and it neither opens nor closes an episode. In the dev data
  1398 of 1409 dip candidates were turns (a ~17 s min-C/N0 dip with a satellite set change, ~8 s before to ~10 s after
  the turn, on every machine at once).
- *Only WORKING, non-turn batches count* toward opening (2 of the last 3) and closing (10 A-false); A = F1 ∨ F6_raw ∨ F7
  (F2 alone no longer opens or holds an episode; it still counts in classification).
- *Farm episodes by common onset*: a tractor episode joins a farm episode only if its onset is within 5 min of the farm
  onset (or the tractor is already a member and re-opens inside it); anything else is its own episode, still
  classified, never merged because of overlap.
- *Farm-level decision for SPACE_WEATHER and JAMMING*: one cause set for the whole farm episode; the log-likelihoods of
  the per-tractor findings (F1, F2, F6, F7) are summed over the tractors with F1 present, the shared ones (F3, F4, F9,
  F10, F12) count once, and F5/F8 (own tracks) carry no information. The one-machine classes (EQUIPMENT_FAULT) stay per
  tractor, one detection per machine.
- *Safety valve*: a tractor episode open 6 h without any class at BF ≥ 20 is force-closed as unattributed, logged,
  and that tractor's frozen reference is discarded and rebuilt.
- *Confidence* = the episode posterior under even prior odds, P = BF/(1+BF), from the episode-level evidence (each
  known sign present raises it, each missing one lowers it); the evidence says which signs were seen, e.g.
  `signs seen 4 of 5 (F1, F2, F3, F9; F6 missing), P = 0.999` (known signs = links ≥ 0.50 in the table).

Live, each open episode is one alarm ID per class group: WATCH while no class has BF ≥ 20, ALARM with the
provisional class once one does, `RECLASSIFIED` in the alarm's history when the class changes.

**Watchdog (every 5 min of data time, and once at the end).** Using only data ≤ now: (1) re-run closed episodes'
classification with everything now available (late RFI reports); (2) re-localise each boundary within ±10 min of
the episode's raw boundary by an exact single-change search on 1 Hz data — a two-segment step fit for JAMMING,
EQUIPMENT_FAULT and every end, a continuous hinge (flat-then-linear, CPOP's model restricted to one change [R30])
for SPOOFING and SPACE_WEATHER onsets; extent is decided separately from class [R31]; (3) merge consecutive
SPACE_WEATHER alarms ≤ 30 min apart (the other classes already bridge 5 min through the close rule); (4) shorter
than 2 batches → `NOT_SUSTAINED`, entirely parked → `PARKED`, otherwise clipped to working time and **CONFIRMED**.

**Evidence.** Built from the final span: the main measurement with values and ladder levels, cross-tractor
geometry (equal vs unequal drops, other machines unaffected), solution impact (fix state), SDA context with record
IDs and availability times, and the decision (the episode's class, Bayes factor and the episode-level findings
with their fractions, re-localisation).

## 5. Use of labelled data

`data/labels/train_episodes.json` (12 labelled episodes in the development period, 2026-05-04/05) is read by two
modules: `tools/dev_check.py` (`--dev`, the diagnostic score in `out/dev_report.md`) and, since Decision v2.1 (the
owner lifted the earlier no-tuning rule), `tools/calibrate.py` + `tools/calib_report.py`, which learn the trained
configuration. The evaluation window is never read by either. What was learned, and how (`out/calibration.md` has
every number):

- **Leaks** (P(finding | no cause)), measured directly on the background: WORKING dev batches outside every labelled
  span ± 10 min (16.9 h per tractor). Per batch: (k + 1) / (n + 2) over batches with information; per episode: the
  same over a sliding window as long as the median labelled episode (42 min), "present" when φ ≥ φ_present in it.
  Clamped to [0.005, 0.20]. F12 has no per-batch value, so it keeps the physics leak.
- **Links** (P(finding | cause)), shrunk toward the physics table with two pseudo-episodes:
  (k_cj + 2·q_cj) / (n_c + 2), where k_cj counts the class-c labelled episodes whose episode-level finding j was
  present (pipeline findings over the label's span and tractors) and q_cj is the physics value (blank = 0.025).
- **Thresholds**: `fusion.bf_alarm` ∈ {10, 20, 50} × `episodes.phi_present` ∈ {0.2, 0.333, 0.5} on cached episodes;
  `episodes.close_batches` ∈ {6, 10, 20}, `position.float_band_m` ∈ {0.3, 0.5, 0.8} and the turn threshold
  ∈ {20°, 30°, 45°} by coordinate descent on real replays. No other knob was searched.
- **Honest scoring**: everything learned is re-learned inside each leave-one-episode-out (LOEO) fold without the
  held-out episode; thresholds are chosen per fold on the other 11 episodes plus background extras.
- **Guardrail**: LOEO was more than 8 points below in-sample (23.0 vs 31.8), so the trained configuration keeps the
  learned links and leaks with the physics thresholds (the search had picked the physics thresholds anyway). The
  gap comes from the links (12 episodes, one to three per class); the learned leaks alone change nothing
  (29.5 in-sample and LOEO, the same as physics).
- **Not learned**: no new findings, tracks or classes; confidence is the network posterior, not recalibrated on 12
  episodes. MULTIPATH: the two labelled dips are indistinguishable from the ~1000 headland-turn dips (best feature
  pair separates 89% < 95%), so no gate was added and no MULTIPATH is exported.

| version | dev score /40 in-sample | LOEO |
|---|---|---|
| v1.1 | 18.9 | 18.9 (nothing learned) |
| v2.1-physics (`config.yaml`) | 29.5 | 29.5 (nothing learned) |
| v2.1-trained (`config_trained.yaml`) | 31.8 | 23.0 |

## 6. Known failure modes

- Single-tractor jamming without oscillation, an RFI report or a second affected tractor stays below ALARM
  (by design: a lone C/N0 drop is ambiguous and surfaces as an unattributed WATCH).
- Wide-area jamming that drops all tractors equally can look like space weather without SDA context.
- Spoofing that drags the position along the row (not across it) is invisible in cross-track error; spoofing that
  also collapses C/N0 is attributed to the C/N0 cause (F8 is suppressed while signal health is degraded).
- Absolute fallback scales assume a typical receiver (≥ 40 dB-Hz nominal).
- Physics links and leaks are principled guesses; the trained ones come from 12 episodes and overfit (LOEO, §5).
  Confidence is BF/(1+BF) of the episode-level classification, not a calibrated probability.
- One cause per tractor per signal episode: a link fault on one machine during a storm is reported as whichever
  cause wins on that machine, not both (only a stacked step splits a signal episode).
- A level that settles permanently below the pre-episode reference keeps an episode open until the 6 h safety
  valve closes it unattributed and rebuilds that tractor's reference.
- MULTIPATH is not exported: the labelled dips sit on headland turns and look like every other turn dip.
- A jam inside a storm (TRN-05) needs an abrupt stacked step on ≥ 2 tractors; the physics table misses it.
- A short multipath dip that straddles a batch boundary moves two batch means, opens a 2-batch signal episode, and
  is then excluded from the short-dip track (`out/dips.csv` lists such dips as excluded by a signal episode).
- Advisories carry no end time; the duration is parsed from the text (lower bound of "24–48 h"), else 24 h.
- The Jetson runs the NumPy path; the GPU is unused in v1.

## 7. Extra data that would help

Raw observables (AGC, correlator outputs, Doppler, pseudorange residuals, per-satellite C/N0) to separate
jamming from spoofing directly; a second antenna or an IMU/heading source for an independent position check;
the receiver's own correction-age field; RFI reports with publish time, ellipse orientation and a duration;
S4 measured at the farm rather than a regional proxy; a wheel-odometry speed to make the working gate independent
of GNSS.

## 8. Decisions and open items

- **ASK DYLAN:** `participant.github_handle` and `repository_url` in `config.yaml`.
- The ALARM rule uses the most probable *non-empty* cause set: with a 1% prior the empty set stays the literal MAP
  until a posterior exceeds 0.5, which would contradict the spec's own sanity targets (e.g. F5 ⇒ MULTIPATH).
- Update v2 replaces the frozen-reference max-age pause: references freeze per farm episode and are discarded at
  its close. Alarms spanning a parked night are no longer split (the 10-batch close rule ends an episode when the
  machines go quiet).
- Heading is derived from lat/lon; turns in this data are instantaneous row hops. F8 masks the turn samples;
  v2.1 marks whole turn batches (the turn, or ≤ 12 s after it) as no information for F2, F5 and the satellite half of F6.
- v2.1 deviations (owner to confirm): SPACE_WEATHER→F2 0.025 → 0.50 (S4 *is* C/N0 intensity fluctuation and the guide
  says "elevated scintillation"; with F2 summed per tractor in the farm-level decision, 0.025 called synthetic
  storms JAMMING); F6 is *no information* (not absent) while C/N0 is not normal, since its chain rule defines it only
  then (as absent it counted against SPACE_WEATHER once per tractor); the log-variance channel keeps the history a
  Fix 3 restart drops, so the episode freeze one batch later still has a pre-event reference (before, F2 had no
  reference for whole episodes); F5/F8 carry no information in the farm-level sum.
- F10 severity uses the report's own `confidence` field when present (else 1.0).
- Two link values beyond the v1.1 table: EQUIPMENT_FAULT→F2 0.20 (an intermittent antenna makes C/N0 noisy; needed
  by Fix 2's own test) and nothing else. An equal stacked step on all machines inside a storm leaves JAMMING at
  BF ≈ 2 under the table (F4/F2/F5/F10 absent), so Fix 3's test uses an unequal step.

## 9. References

| # | Reference | Used for |
|---|---|---|
| R1 | E. S. Page, "Continuous inspection schemes," *Biometrika* 41 (1954) | CUSUM recursion |
| R2 | MetricGate, CUSUM ARL tables and Siegmund approximation — <https://metricgate.com/blogs/cusum-small-shift-detection-r/>, <https://metricgate.com/docs/cusum-tabular-page-vmask/> | k = 0.5; ARL₀ ≈ 168 (h = 4), 465 (h = 5); solving h |
| R3 | CUSUM with backtracking onset — <https://arxiv.org/pdf/2605.19966> | onset = last reset before alarm |
| R4 | Egea-Roca et al., finite moving average test for transient change detection in GNSS — <https://collaborate.princeton.edu/en/publications/a-finite-moving-average-test-for-transient-change-detection-in-gn/> | F5 dip test |
| R5 | CUSUM / residual charts for autocorrelated data — <https://koreascience.kr/article/JAKO200810103470528.pdf> | why raw autocorrelated data over-alarms |
| R6 | Kim et al., distribution-free tabular CUSUM for autocorrelated data (IIE Trans. 2007) | autocorrelated CUSUM background |
| R7 | Runger & Willemain, "Model-based and model-free control of autocorrelated processes," *JQT* 27(4) 1995 | batch means over model residuals |
| R8 | UBM minimum batch size for AR(1) — <https://koreascience.kr/article/JAKO202003440879153.pdf> | why 30 s batches still need σ inflation |
| R9 | Effective sample size n(1−ρ)/(1+ρ) — <https://kls2177.github.io/Climate-and-Geophysical-Data-Analysis/chapters/Week5/n_eff.html> | σ inflation factor |
| R10 | Kujur, Khanafseh & Pervan, optimal INS monitor for spoofer tracking error — <https://rosap.ntl.bts.gov/view/dot/79801/dot_79801_DS1.pdf> | F8 cumulative drift monitor |
| R11 | Gonzalez et al., Dst storm classes — <https://arxiv.org/pdf/2404.09234> | Dst ladder |
| R12 | Septentrio CALIBRA, ION GNSS 2011 — <https://www.septentrio.com/sites/default/files/2019-06/Paper_2011_CALIBRA_Ionospheric_scintillation_Brazil_ION_GNSS.pdf> | S4 bands |
| R13 | Australian BoM S4 guidance — <https://www.sws.bom.gov.au/Satellite/6/3> | S4 < 0.3 negligible |
| R14 | NOAA SWPC Space Weather Scales — <https://www.swpc.noaa.gov/sites/default/files/images/NOAAscales.pdf> | Kp/G ladder |
| R15 | Inside GNSS, measuring GNSS signal strength — <https://insidegnss.com/measuring-gnss-signal-strength/> | absolute C/N0 ladder |
| R16 | US DOT Volpe GPS loss-of-lock analysis — <https://rosap.ntl.bts.gov/view/dot/32102/dot_32102_DS1.pdf> | ΔC/N0 ladder anchor |
| R17 | Solar radio burst C/N0 study, JSWSC 2024 — <https://www.swsc-journal.org/10.1051/swsc/2024034/pdf> | SW equal drops of 15–25 dB |
| R18 | John Deere StarFire accuracy; GPS SPS performance — <https://www.deere.ca/en/technology-products/precision-ag-technology/guidance/mobile-rtk/>, <https://gps.gov/systems/gps/performance> | fix and xte ladders |
| R19 | Textbook RAIM (4 to fix, 5 to detect, 6 to exclude) | satellite ladder |
| R20 | Swift Navigation RTK/NTRIP troubleshooting — <https://www.swiftnav.com/resource/blog/troubleshooting-the-most-common-rtk-ntrip-issues-a-step-by-step-guide> | coasting after link loss |
| R21 | Trimble R750 RTK propagation limit — <https://help.fieldsystems.trimble.com/r750/help-receiver-config-position.htm> | link → fix delay window |
| R22 | Heckerman, "A tractable inference algorithm for diagnosing multiple diseases" (UAI 1989) — <https://www.microsoft.com/en-us/research/uploads/prod/2016/11/A-Tractable-Algorithm-for-Diagnosing-Multiple-Diseases.pdf> | two-layer leaky noisy-OR |
| R23 | Heckerman & Shwe, diagnosis of multiple faults — <https://arxiv.org/pdf/1303.1463.pdf> | leak definitions |
| R24 | Shwe, Middleton et al., QMR frequency → probability mapping — <https://arxiv.org/pdf/1304.1141> | five link levels |
| R25 | Pradhan, Henrion et al., "The sensitivity of belief networks to imprecise probabilities," *AI* 85 (1996) | why coarse links are acceptable |
| R26 | Wellman & Henrion, "Explaining 'explaining away'," IEEE TPAMI 1993; Henrion & Druzdzel — <https://arxiv.org/pdf/1304.1082> | explaining away, Occam pruning |
| R27 | Kass & Raftery, "Bayes factors," JASA 90 (1995) — scale in <https://search.r-project.org/CRAN/refmans/pcal/html/bfactor_interpret_kr.html> | BF 3 / 20 thresholds |
| R28 | Abraham, sliding M-of-N detector (APL-UW TR 2504) — <https://apl.uw.edu/project/project.php?id=tr_2504> | 2-of-3 persistence |
| R29 | ISA-18.2 alarm vs alert — <https://www.exida.com/Blog/to_be_an_alarmor_not_to_be>; <https://blog.isa.org/solve-common-problems-industrial-automation-alarm-systems> | WATCH vs ALARM; off-delay |
| R30 | Fearnhead, Maidstone & Letchford, "Detecting changes in slope with an L0 penalty" (JCGS 2019) — <https://eprints.lancs.ac.uk/id/eprint/84114> | hinge model |
| R31 | Ebbers et al., Sound Event Bounding Boxes (Interspeech 2024) — <https://merl.com/publications/TR2024-118> | extent vs class confidence; gap merging |
| R32 | Rich console docs — <https://rich.readthedocs.io/en/stable/console.html> | colour in Docker logs |
