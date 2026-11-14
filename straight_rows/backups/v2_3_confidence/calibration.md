# Calibration on the labelled dev episodes (Decision v2.1 §9)

Everything below was computed on the development period (2026-05-04/05) only; the evaluation window was never read. Physics values stay in `config.yaml`; the trained values are in `config_trained.yaml`.

## Data split

- Labelled time: each of the 12 episodes' span +- 10 min. Background: WORKING dev batches outside all labelled time: T1 2028 batches (16.9 h), T2 2028 batches (16.9 h), T3 2028 batches (16.9 h).
- Episode-level leak window: the median labelled episode, 84 batches (42 min), slid one batch at a time over contiguous background.
- Leave-one-episode-out (LOEO): links are re-learned without the held-out episode; the thresholds are chosen per fold on the other 11 episodes plus background extras, then the held-out episode (match, onset) and the extras within its +-10 min are scored. Background extras are averaged over the folds' chosen settings. Leaks come from background only, which never contains labelled time, so every fold sees the same leaks.
- Threshold search: `fusion.bf_alarm` x `episodes.phi_present` on cached episodes (offline re-classification of each replay; with physics values it reproduces the replay's score exactly: cb10 29.5 vs 29.5, cb10-fb0.3 27.5 vs 27.4, cb10-fb0.5 29.5 vs 29.5); `episodes.close_batches`, `position.float_band_m`, `findings.turn_heading_deg` by coordinate descent on real replays (in that order). The descent path was picked on all 12 episodes; the LOEO choice inside it is per fold.

## Leaks: learned vs physics

(k + 1) / (n + 2), clamped to [0.005, 0.2]; k = background batches (or windows) with the finding present, n = those with information. F12 has no per-batch value since Update v2, so it keeps the physics leak.

| finding | physics | learned per batch | k / n (batches) | learned per episode | k / n (windows) |
|---|---|---|---|---|---|
| F1 | 0.025 | 0.005 | 7 / 6084 | 0.005 | 0 / 4170 |
| F2 | 0.025 | 0.005 | 10 / 4113 | 0.005 | 0 / 4170 |
| F3 | 0.025 | 0.005 | 0 / 6084 | 0.005 | 0 / 4170 |
| F4 | 0.025 | 0.005 | 0 / 6084 | 0.005 | 0 / 4170 |
| F5 | 0.025 | 0.005 | 0 / 4591 | 0.005 | 0 / 4170 |
| F6 | 0.025 | 0.005 | 0 / 4430 | 0.005 | 0 / 4170 |
| F7 | 0.025 | 0.005 | 0 / 6084 | 0.005 | 0 / 4170 |
| F8 | 0.025 | 0.005 | 8 / 6030 | 0.005 | 0 / 4170 |
| F9 | 0.2 | 0.200 | 1584 / 6084 | 0.200 | 1086 / 4170 |
| F10 | 0.025 | 0.005 | 0 / 6084 | 0.005 | 0 / 4170 |
| F12 | 0.025 |  | 0 / 0 |  | 0 / 0 |

## Links: learned vs physics

(k + 2 q) / (n + 2) with q the physics value (blank = 0.025). k = labelled episodes of the class with the episode-level finding present (per-tractor findings count the fraction of the label's tractors). Cells show physics -> learned (k / n); only cells that moved by >= 0.05 are listed.

| class | F1 | F2 | F3 | F4 | F5 | F6 | F7 | F8 | F9 | F10 | F12 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| SPACE_WEATHER | 0.8 -> **0.90** (2/2) | 0.50 | 0.8 -> **0.90** (2/2) | 0.01 | 0.01 | 0.5 -> **0.67** (1/1) | 0.01 | 0.025 -> **0.35** (1/1) | 0.8 -> **0.90** (2/2) | 0.025 -> **0.26** (1/2) | 0.01 |
| JAMMING | 0.99 | 0.53 | 0.2 -> **0.28** (1/3) | 0.8 -> **0.32** (0/3) | 0.5 -> **0.33** (0.67/3) |  | 0.01 | 0.02 | 0.025 -> **0.21** (1/3) | 0.5 -> **0.60** (2/3) | 0.2 -> **0.08** (0/3) |
| SPOOFING | 0.025 -> **0.41** (2/3) | 0.025 -> **0.41** (2/3) | 0.025 -> **0.21** (1/3) | 0.01 | 0.01 | 0.025 -> **0.21** (1/3) | 0.01 | 0.99 | 0.025 -> **0.21** (1/3) | 0.01 | 0.8 -> **0.92** (3/3) |
| EQUIPMENT_FAULT | 0.2 -> **0.35** (1/2) | 0.2 -> **0.35** (1/2) | 0.01 | 0.01 | 0.2 -> **0.10** (0/2) | 0.8 -> **0.65** (1/2) | 0.8 -> **0.65** (1/2) | 0.025 -> **0.35** (1/1) | 0.01 | 0.01 | 0.8 -> **0.90** (2/2) |
| MULTIPATH | 0.025 -> **0.26** (1/2) |  | 0.025 -> **0.26** (1/2) | 0.01 | 0.985 -> **0.49** (0/2) |  | 0.01 | 0.02 | 0.025 -> **0.26** (1/2) | 0.01 | 0.8 -> **0.65** (1/2) |

Per-label episode-level findings that drove the links (fraction of the label's tractors with the finding present):

- TRN-01 SPACE_WEATHER: F1 1, F3 1, F6 1, F9 1, F10 1
- TRN-02 SPACE_WEATHER: F1 1, F2 1, F3 1, F8 1, F9 1
- TRN-03 JAMMING: F1 1, F2 0.67, F5 0.67, F10 1
- TRN-04 JAMMING: F1 1, F10 1
- TRN-05 JAMMING: F1 1, F2 1, F3 1, F9 1
- TRN-06 SPOOFING: F1 1, F2 1, F6 1, F8 1, F12 1
- TRN-07 SPOOFING: F8 1, F12 1
- TRN-08 SPOOFING: F1 1, F2 1, F3 1, F8 1, F9 1, F12 1
- TRN-09 EQUIPMENT_FAULT: F1 1, F2 1, F6 1, F8 1, F12 1
- TRN-10 EQUIPMENT_FAULT: F7 1, F12 1
- TRN-11 MULTIPATH: F12 1
- TRN-12 MULTIPATH: F1 1, F3 1, F9 1

## Thresholds

| knob | physics | best on all 12 | used in config_trained.yaml |
|---|---|---|---|
| bf_alarm | 20.0 | 20.0 | 20.0 |
| phi_present | 0.333 | 0.333 | 0.333 |
| close_batches | 10 | 10 | 10 |
| float_band_m | 0.5 | 0.5 | 0.5 |
| turn_heading_deg | 30.0 | 30.0 | 30.0 |

Per-fold choice (inner selection on the other 11):

- TRN-01: physics values
- TRN-02: physics values
- TRN-03: physics values
- TRN-04: close_batches 20
- TRN-05: physics values
- TRN-06: physics values
- TRN-07: physics values
- TRN-08: physics values
- TRN-09: physics values
- TRN-10: physics values
- TRN-11: physics values
- TRN-12: physics values

Coordinate-descent replays (best offline score on all 12 per structural setting):

| setting | replay, physics links | best (phi, bf) with trained links | score |
|---|---|---|---|
| cb10 {'close_batches': 10} | 29.5 | [0.2, 10.0] | 31.8 |
| cb10-fb0.3 {'close_batches': 10, 'float_band_m': 0.3} | 27.5 | [0.2, 10.0] | 29.6 |
| cb10-fb0.5 {'close_batches': 10} | 29.5 | [0.2, 10.0] | 31.8 |
| cb10-fb0.5-t20 {'close_batches': 10, 'float_band_m': 0.5, 'turn_heading_deg': 20.0} | 29.5 | [0.2, 10.0] | 31.8 |
| cb10-fb0.5-t30 {'close_batches': 10} | 29.5 | [0.2, 10.0] | 31.8 |
| cb10-fb0.5-t45 {'close_batches': 10, 'float_band_m': 0.5, 'turn_heading_deg': 45.0} | 29.5 | [0.2, 10.0] | 31.8 |
| cb10-fb0.8 {'close_batches': 10, 'float_band_m': 0.8} | 29.5 | [0.2, 10.0] | 31.7 |
| cb20 {'close_batches': 20} | 25.4 | [0.2, 10.0] | 25.4 |
| cb6 {'close_batches': 6} | 26.6 | [0.2, 10.0] | 28.9 |

## Scores (dev, diagnostic score /40 = 25 x macro-F1 + 15 x onset credit)

- **In-sample 31.8**, **LOEO 23.0** (gap 8.8; guardrail at 8 points: **tripped: thresholds reverted to physics** (the search had already picked the physics values, so nothing moved; the gap comes from the links, see the variants below)).
- Learned links/leaks with physics thresholds: in-sample 31.8, LOEO 23.0.

| class | in-sample P | R | F1 | LOEO P | R | F1 |
|---|---|---|---|---|---|---|
| SPACE_WEATHER | 1.00 | 1.00 | 1.00 | 1.00 | 0.50 | 0.67 |
| JAMMING | 1.00 | 1.00 | 1.00 | 0.67 | 0.67 | 0.67 |
| SPOOFING | 0.75 | 1.00 | 0.86 | 0.75 | 1.00 | 0.86 |
| EQUIPMENT_FAULT | 1.00 | 1.00 | 1.00 | 1.00 | 0.50 | 0.67 |
| MULTIPATH | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 | 0.00 |

Held-out results per fold:

- TRN-01 SPACE_WEATHER: matched, onset error 0 s
- TRN-02 SPACE_WEATHER: missed; extras nearby: JAMMING
- TRN-03 JAMMING: matched, onset error 210 s
- TRN-04 JAMMING: matched, onset error 30 s; extras nearby: SPOOFING
- TRN-05 JAMMING: missed
- TRN-06 SPOOFING: matched, onset error 30 s
- TRN-07 SPOOFING: matched, onset error 0 s
- TRN-08 SPOOFING: matched, onset error 270 s
- TRN-09 EQUIPMENT_FAULT: missed
- TRN-10 EQUIPMENT_FAULT: matched, onset error 1 s
- TRN-11 MULTIPATH: missed
- TRN-12 MULTIPATH: missed

Real replays (not offline):

- physics replay (config.yaml, dev check): 29.5 (nothing learned, so LOEO = in-sample)
- trained replay (config_trained.yaml, dev check): 31.8, the same as the offline in-sample 31.8: the offline re-classifier reproduces both replays exactly, so the LOEO above was computed offline
- variant: physics links + learned leaks: in-sample 29.5, LOEO 29.5, missed when held out: TRN-05, TRN-11, TRN-12
- variant: learned links + physics leaks: in-sample 31.8, LOEO 18.5, missed when held out: TRN-02, TRN-05, TRN-09, TRN-10, TRN-11, TRN-12
- variant: learned both: in-sample 31.8, LOEO 23.0, missed when held out: TRN-02, TRN-05, TRN-09, TRN-11, TRN-12
- variant: physics both: in-sample 29.5, LOEO 29.5, missed when held out: TRN-05, TRN-11, TRN-12

## Multipath (§9.4)

TRN-11 (T3 05-04 14:04:18) and TRN-12 (T1 05-05 19:00:35) both fall 7, 8 s before a north-headland turn, on a min-C/N0 dip that the other two tractors show in the same second. Compared against the 1066 dev turn dips (min-C/N0 dip > 3 dB around a turn):

| feature | TRN-11 | TRN-12 | turn dips: median (p5..p95) | turn dips outside the labelled range |
|---|---|---|---|---|
| depth_min | 10.9 | 13 | 11.2 (3.85..13.4) | 43% |
| depth_mean | -0.127 | 1.2 | 0 (-0.675..0.514) | 29% |
| dsats | 0 | 0 | 0 (-1..1) | 20% |
| dur | 17 | 17 | 17 (14..19) | 41% |
| lat | 46.90717 | 46.90718 | 46.90718 (46.90002..46.90719) | 79% |
| simul | 2 | 2 | 2 (2..2) | 3% |

Best pair: dur + lat separates 89% of the turn dips, below the 95% bar, and the 'range' is a min-max box around two points (the latitude box is ~1 m wide). **No F5 gate is added and no MULTIPATH is exported**: MULTIPATH F1 is 0 either way, and every extra costs precision.

## Confidence reliability (v2.2)

**v2.3 (owner allowed tuning on dev):** v2.2's prior was P(class c | an episode opened), split six ways, which ignores that the classifier has already chosen c, so P sat near 0.5 while 9 of 10 dev detections were right. v2.3 starts from how often a dev *detection* was right, shrunk: (hits + 1) / (n + 2) = (9 + 1) / (10 + 2) = 0.83, and counts the group LRs with weight w = 0.5 (chosen by leave-one-out log-loss over [0.0, 0.25, 0.5, 1.0]: w 0.0: 0.419, w 0.25: 0.389, w 0.5: 0.386, w 1.0: 0.396). Everything below is leave-one-out (the detection's own event is out of both the group table and the detection prior). The weight itself was picked on the same 10 detections.

Table `confidence_table.yaml` (run b2a8fabf00a85c7f): N = 12 opened dev episodes of the v2.1-physics run, labelled by the scorer's matching rule; leak target l = 0.2, clip [0.02, 0.97]. Dev score of that run: 29.5 (confidence is not scored).

| class | prior | group | q | k1/n1 | p1 | k0/n0 | p0 | LR present | LR absent |
|---|---|---|---|---|---|---|---|---|---|
| SPACE_WEATHER (n=2) | 0.167 | G1 C/N0 [F1,F2,F3] | 0.8 | 2/2 | 0.900 | 7/10 | 0.617 | 1.46 | 0.26 |
| SPACE_WEATHER (n=2) | 0.167 | G2 solution [F6] | 0.5 | 1/2 | 0.500 | 2/9 | 0.218 | 2.29 | 0.64 |
| SPACE_WEATHER (n=2) | 0.167 | G4 context [F9] | 0.8 | 2/2 | 0.900 | 2/10 | 0.200 | 4.50 | 0.12 |
| JAMMING (n=4) | 0.278 | G1 C/N0 [F1,F2,F4,F5,F11] | 0.985 | 4/4 | 0.995 | 5/8 | 0.540 | 1.84 | 0.01 |
| JAMMING (n=4) | 0.278 | G4 context [F10] | 0.5 | 3/4 | 0.667 | 1/8 | 0.140 | 4.76 | 0.39 |
| SPOOFING (n=3) | 0.222 | G3 position [F8] | 0.985 | 3/3 | 0.994 | 3/6 | 0.425 | 2.34 | 0.01 |
| SPOOFING (n=3) | 0.222 | G5 spatial [F12] | 0.8 | 3/3 | 0.920 | 4/4 | 0.733 | 1.25 | 0.30 |
| EQUIPMENT_FAULT (n=2) | 0.167 | G2 solution [F6,F7] | 0.8 | 2/2 | 0.900 | 2/10 | 0.200 | 4.50 | 0.12 |
| EQUIPMENT_FAULT (n=2) | 0.167 | G5 spatial [F12] | 0.8 | 2/2 | 0.900 | 3/10 | 0.283 | 3.18 | 0.14 |
| MULTIPATH (n=0) | 0.056 | G1 C/N0 [F5] | 0.985 | 0/0 | 0.985 | 1/12 | 0.100 | 9.85 | 0.02 |
| MULTIPATH (n=0) | 0.056 | G5 spatial [F12] | 0.8 | 0/0 | 0.800 | 3/12 | 0.243 | 3.29 | 0.26 |

Leave-one-event-out P for the 10 exported dev detections (each detection's table is rebuilt without the opened episodes of its labelled event; an unmatched detection drops the episodes it overlaps):

| bin | count | mean P | hit rate |
|---|---|---|---|
| [0.02, 0.5) | 0 | | |
| [0.5, 0.7) | 0 | | |
| [0.7, 0.85) | 0 | | |
| [0.85, 0.97] | 10 | 0.91 | 0.90 |

- Brier score: **v2.3 0.098** vs old BF/(1+BF) 0.099 vs v2.2 0.272 on the same 10 detections; log-loss v2.3 0.386, old 0.584, v2.2 0.741.
- Pass criteria (new Brier < old; no bin with count >= 3 off by > 0.20): **PASS**

| detection | class | tractors | start | hit | old | v2.2 | P (LOEO) | P (all dev) | groups (state, weighted LR) |
|---|---|---|---|---|---|---|---|---|---|
| A-OTHER-1 | EQUIPMENT_FAULT | T2 | 05-04T11:59 | yes | 0.995 | 0.64 | 0.94 | 0.95 | G2 present ×2.08; G5 present ×1.75 |
| A-SPOOFING-1 | SPOOFING | T2 | 05-04T13:12 | yes | 0.998 | 0.38 | 0.88 | 0.90 | G3 present ×1.53; G5 present ×1.11 |
| A-JAMMING-1 | JAMMING | T1,T2,T3 | 05-04T16:30 | yes | 1.000 | 0.71 | 0.93 | 0.94 | G1 present ×1.36; G4 present ×2.07 |
| A-JAMMING-2 | JAMMING | T1,T2,T3 | 05-04T21:00 | yes | 1.000 | 0.60 | 0.92 | 0.94 | G1 present ×1.36; G4 present ×1.89 |
| A-SPOOFING-2 | SPOOFING | T3 | 05-04T20:58 | no | 0.997 | 0.56 | 0.95 | 0.90 | G3 present ×1.58; G5 present ×1.24 |
| A-SPOOFING-3 | SPOOFING | T1 | 05-05T00:30 | yes | 0.998 | 0.38 | 0.88 | 0.90 | G3 present ×1.53; G5 present ×1.11 |
| A-EQUIPMENT_FAULT-1 | EQUIPMENT_FAULT | T1 | 05-05T02:00 | yes | 0.986 | 0.64 | 0.94 | 0.95 | G2 present ×2.08; G5 present ×1.75 |
| A-EQUIPMENT_FAULT-2 | SPACE_WEATHER | T1,T2,T3 | 05-05T11:00 | yes | 1.000 | 0.55 | 0.93 | 0.95 | G1 present ×1.19; G2 present ×1.24; G4 present ×2.08 |
| A-SPACE_WEATHER-1 | SPACE_WEATHER | T1,T2,T3 | 05-05T18:55 | yes | 0.999 | 0.26 | 0.88 | 0.91 | G1 present ×1.19; G2 absent ×0.65; G4 present ×2.08 |
| A-SPOOFING-4 | SPOOFING | T3 | 05-05T21:31 | yes | 0.998 | 0.38 | 0.88 | 0.90 | G3 present ×1.53; G5 present ×1.11 |
