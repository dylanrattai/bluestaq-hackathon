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
confirmed detections that match no label are listed the same way. The labels are read by this tool only; nothing is
tuned from them.

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
non-RTK solution carries decimetre guidance error by itself; in RTK float only a median beyond 0.3 m counts); F9 space weather active (Kp ≥ 7, Dst ≤ −100 nT, S4 ≥ 0.3, or an open
space-weather advisory; GLOBAL); F10 an RFI report that is *available* (event time + 30 min, no publish field
exists), covers the tractor (semi-major axis as radius) and overlaps the batch (±30 min); F11 a stacked step on
top of an open event (abrupt extra drop: a jammer switching on inside a storm); F12 machine-specific (at least one
of this tractor's F1/F5/F6/F7/F8 findings is shown by no other working tractor, and for F1 the others are
C/N0-clean): farm-scale causes share findings across machines, one-machine faults do not.

**Diagnosis network.** A two-layer leaky noisy-OR network in the style of QMR-DT [R22–R24]: six independent
causes (the five classes plus OTHER, prior 0.01 each), twelve findings conditionally independent given the causes,
links on QMR's five-level scale, leak 0.025 (0.20 for F9). Severity scales a present finding's link by
0.4 + 0.6·s. All 2⁶ = 64 cause sets are enumerated exactly per tractor per batch (log space). Decisions use Bayes
factors on the Kass–Raftery scale [R27]: BF ≥ 3 = WATCH, BF ≥ 20 = ALARM candidate, **and** the cause must be in
the most probable non-empty cause set (the explanation), which is what gives explaining-away and Occam pruning
without hand-written vetoes [R26]. OTHER in the explanation with no class alarm is an *unattributed* live alarm
that is never exported.

**Lifecycle.** ALARM needs the candidate in 2 of the last 3 batches [R28] (MULTIPATH: 1). The onset is the
earliest backdated CUSUM onset among the contributing findings (MULTIPATH: the dip's exact 1 Hz start). Multi-
machine causes (SPACE_WEATHER, JAMMING) group tractors into one alarm; SPACE_WEATHER includes every working
tractor. An alarm closes after one batch below BF 3 (off-delay [R29]); the end is when the statistic returned to 0.

**Watchdog (every 5 min of data time, and once at the end).** Using only data ≤ now: (1) re-localise each
boundary within ±10 min by an exact single-change search on 1 Hz data — a two-segment step fit for JAMMING,
EQUIPMENT_FAULT and every end, a continuous hinge (flat-then-linear, CPOP's model restricted to one change [R30])
for SPOOFING and SPACE_WEATHER onsets; extent is decided separately from class confidence [R31]; (2) merge
same-class alarms closer than the class gap (SW 30, JAM 10, SPOOF 5, EQUIP 10 min; MULTIPATH never);
(3) re-run the network over the alarm's span with everything now available (late RFI reports): if the class has
left the explanation for most of the span it is retracted `EXPLAINED_BY_OTHER_CAUSE` or `RECLASSIFIED` into the
class that now wins; (4) shorter than 2 batches → `NOT_SUSTAINED`, entirely parked → `PARKED`, otherwise
**CONFIRMED** (a later pass may still retract it); a MULTIPATH alarm lying inside a JAMMING alarm on the same
tractor is retracted as explained by the jammer's pulses. Confidence is the median over the alarm's batches of
BF/(1+BF), the posterior under even prior odds (the 1% prior is kept for choosing the cause set).

**Evidence.** Built from the final span: the main measurement with values and ladder levels, cross-tractor
geometry (equal vs unequal drops, other machines unaffected), solution impact (fix state), SDA context with record
IDs and availability times, and the decision (cause set, Bayes factor, re-localisation).

## 5. How the labelled data was used

`data/labels/train_episodes.json` is read by exactly one module, `tools/dev_check.py` (`--dev`), which prints per-class
precision/recall/F1, macro-F1, onset credit and the diagnostic score (`out/dev_report.md`). It is a held-out
verification tool: the labels were not used to build, tune or select anything, and the build did not run it.
Every threshold in `config.yaml` comes from physics, standards or the cited papers (each entry names its source).
The first dev run (13.3/40) was reviewed by the owner, who approved Fix Spec v1.1: structural fixes (event ends by
recovery, F11/F12, the 5-min link window, the local multipath reference) and physics-justified link changes, none of
them thresholds fitted to the labels. The dev check was then run once more and nothing changed afterwards
(`out/dev_findings.md` lists every deviation and open question).

## 6. Known failure modes

- Single-tractor jamming without oscillation, an RFI report or a second affected tractor stays below ALARM
  (by design: a lone C/N0 drop is ambiguous and surfaces as an unattributed WATCH).
- Wide-area jamming that drops all tractors equally can look like space weather without SDA context.
- Spoofing that drags the position along the row (not across it) is invisible in cross-track error; spoofing that
  also collapses C/N0 is attributed to the C/N0 cause (F8 is suppressed while signal health is degraded).
- Absolute fallback scales assume a typical receiver (≥ 40 dB-Hz nominal).
- Links and leaks are principled guesses, not fitted; confidence is a per-batch model posterior under a 1% prior,
  not a calibrated probability.
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
- The max-age rule for a frozen reference is paused while a finding is open on that channel; otherwise every event
  longer than an hour would switch to absolute scales mid-event.
- Alarms that span a parked night are split into one detection per working segment (a parked night separates two
  guidance episodes under the guide's scoring rules).
- Heading is derived from lat/lon; turns in this data are instantaneous row hops, so only the turn samples are
  masked rather than whole batches.
- F10 severity uses the report's own `confidence` field when present (else 1.0).
- Fix Spec v1.1 is applied in full except one item: the frozen pre-event reference stays valid while its event is
  open (`reference.pause_max_age_in_event`, switchable). The literal 60-min max-age would push any event longer
  than an hour onto absolute scales mid-event; Fix 1 removes the deadlock that motivated the revert.
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
