// Data-flow blocks for the judge view. Every number here is the v2.3 engine's (config.yaml / README §4);
// the live tables (links, leaks, thresholds) are read from /webui/meta where the UI shows them.
export interface Block {
  id: string
  title: string
  sub: string
  lane: 'in' | 'detect' | 'decide' | 'out'
  what: string
  why: string
  how: string[]
  refs: string[]
}

export const BLOCKS: Block[] = [
  {
    id: 'ingest', title: 'Telemetry + SDA feeds', sub: '1 Hz × 3 tractors · Kp, Dst, S4, advisories, RFI', lane: 'in',
    what: 'Replays tractor GNSS telemetry (mean/min C/N0, satellites, fix state, base-link flag, cross-track error, speed, lat/lon) alongside the Space Domain Awareness feeds.',
    why: 'The five causes leave different fingerprints across signal, solution, position and context. No single channel separates them.',
    how: [
      'Columns are discovered from the CSV header, and units come from the column suffixes.',
      'A feed counts as missing after 2× its cadence without a record (Kp 3 h, Dst 1 h, S4 15 min).',
      'An RFI report has no publish field, so it becomes available 30 min after its event time and is never used earlier.',
      'Causal throughout: nothing reads data after the current data time.',
    ],
    refs: ['R11', 'R12', 'R13', 'R14'],
  },
  {
    id: 'grid', title: '30 s grid + working gate', sub: 'WORKING / PARKED per tractor', lane: 'in',
    what: 'Everything runs on one global 30 s batch grid aligned to UTC. A gate marks each tractor WORKING or PARKED.',
    why: 'Raw 1 Hz data is autocorrelated and over-alarms. Parked machines are not a mission risk.',
    how: [
      'PARKED means speed < 0.5 m/s for ≥ 5 min. WORKING means > 0.5 m/s for ≥ 30 s.',
      'The gate holds its last state while the fix is AUTONOMOUS/NO_FIX or cross-track drift is active, because a spoofed position can fake motion.',
      'Headland turn batches (heading change > 30°, plus 12 s after the turn) carry no information for F2, F5 or the satellite half of F6.',
    ],
    refs: ['R5', 'R6', 'R7'],
  },
  {
    id: 'cusum', title: 'Reference + CUSUM', sub: 'k = 0.5 · ARL₀ 24 h → h ≈ 6.80', lane: 'detect',
    what: 'Tabular CUSUMs on batch means against a trailing 60 min reference. The onset is backtracked to the last reset.',
    why: 'This gives a designed false-alarm rate (one per 24 h per stream) instead of a tuned threshold.',
    how: [
      'h is solved from Siegmund\'s approximation at startup. As a check, h = 5 gives ARL₀ ≈ 469, matching published tables.',
      'σ is inflated for lag-1 autocorrelation (the AR(1) factor), capped at 3σ.',
      'The references freeze at a farm episode\'s onset and are rebuilt after it closes.',
      'An in-event reference drives recovery and stacked-step CUSUMs.',
    ],
    refs: ['R1', 'R2', 'R3', 'R8', 'R9'],
  },
  {
    id: 'findings', title: 'Findings F1–F12', sub: 'present / absent / no information + severity', lane: 'detect',
    what: 'Twelve findings per tractor per batch, each with a severity in [0, 1] from a 5-level ladder (L0..L4, s = L/4).',
    why: 'Discrete, explainable signs are what the diagnosis network and the evidence strings are built from.',
    how: [
      'F1 C/N0 level drop. F2 C/N0 oscillation. F3 equal drop on all tractors. F4 unequal drop on ≥ 2 tractors. F5 short dip (5 s FMA test).',
      'F6 fix/satellites degraded while C/N0 is normal. F7 correction link down. F8 cross-track drift. F9 space weather active.',
      'F10 an available RFI report covers the tractor. F11 stacked step (opens a sub-episode). F12 machine-specific.',
      '"Missing" (no information) is different from "absent". A missing finding is skipped, never counted against a cause.',
    ],
    refs: ['R4', 'R10', 'R15', 'R16', 'R18', 'R19', 'R20', 'R21'],
  },
  {
    id: 'episodes', title: 'Episodes first', sub: 'signal · sub · position · short dip', lane: 'decide',
    what: 'The scorer counts episodes, so the episode is the unit of decision: first find when something is wrong, then decide what it is, once per episode.',
    why: 'Per-batch class decisions fragment one event into many detections and flip classes mid-event.',
    how: [
      'Signal track: A = F1 ∨ F6_raw ∨ F7 on WORKING non-turn batches. An episode opens on 2 of the last 3 batches and closes after 10 A-false batches.',
      'Tractor episodes with onsets within 5 min of each other form one farm episode.',
      'Stacked step: the in-event CUSUM firing on ≥ 2 tractors opens a sub-episode, which is a separate detection if its class differs.',
      'Position track (spoofing): the F8 drift CUSUM plus a position-domain F12, opening on SPOOF BF ≥ 20 in 2 of 3 batches.',
      'Short dip (generic multipath): an F5 dip with no other episode on that tractor within ±60 s.',
      'Safety valve: a tractor episode with no class at BF ≥ 20 is force-closed as unattributed after 6 h.',
    ],
    refs: ['R28', 'R31'],
  },
  {
    id: 'network', title: 'Noisy-OR diagnosis', sub: '6 causes · 2⁶ sets exact · BF 3 / 20', lane: 'decide',
    what: 'A two-layer leaky noisy-OR network in the style of QMR-DT. Causes are the five classes plus OTHER (prior 0.01 each), and all 64 cause sets are enumerated exactly.',
    why: 'Explaining-away and Occam pruning fall out of the model, with no hand-written vetoes. The links are coarse on purpose (QMR five-level scale).',
    how: [
      'A present finding\'s link is scaled by floor + (1 − floor)·s.',
      'Decisions use Kass–Raftery Bayes factors: BF ≥ 3 is WATCH, and BF ≥ 20 is ALARM provided the cause is in the most probable non-empty set.',
      'For SPACE_WEATHER and JAMMING there is one decision per farm episode: per-tractor log-likelihoods are summed and shared findings count once.',
      'OTHER, or a group below BF 20, is logged as unattributed and never exported.',
    ],
    refs: ['R22', 'R23', 'R24', 'R25', 'R26', 'R27'],
  },
  {
    id: 'v23', title: 'v2.3 accuracy rules', sub: 'site multipath · spoof-under-jam · storm-jam', lane: 'decide',
    what: 'Four targeted rules added after the dev check. They are the most dev-specific part of the system.',
    why: 'Each one fixes a labelled dev episode the general pipeline missed or mis-scored. See Limits for what that costs.',
    how: [
      'P1 site multipath: a map of recurring min-C/N0 dip cells learned on the dev days (189 sites, mostly the north headland). Each dip there becomes a one-tractor MULTIPATH report, as a suppressed alert.',
      'P2: a position episode starting inside a JAMMING span (±5 min) with F1 present is explained away, not exported as spoofing.',
      'P3 storm-jam: a ≥ 6 dB flat step of 1 min mean C/N0 on a ≤ 40 dB-Hz floor, on ≥ 2 tractors for ≥ 3 min, is its own JAMMING. It is rule-based, so its logged BF is 0.',
      'P4: SPACE_WEATHER needs F1 on ≥ 2 tractors, and an advisory alone does not make F9.',
    ],
    refs: ['R29'],
  },
  {
    id: 'lifecycle', title: 'Live alarms', sub: 'WATCH → ALARM, reasons logged', lane: 'decide',
    what: 'Each open episode is one alarm per class group: WATCH while no class has BF ≥ 20, ALARM once one does.',
    why: 'Operators get the earliest defensible warning, and every state change carries a reason code.',
    how: [
      'Reason codes: RAISED, ESCALATED, WEAKENED, RECLASSIFIED, JOINED, RELOCALISED, CONFIRMED, MERGED, NOT_SUSTAINED, PARKED, UNATTRIBUTED, CLOSED.',
      'Alarm IDs keep the class they were raised with. For example, A-EQUIPMENT_FAULT-2 ended as SPACE_WEATHER.',
      'Every event goes to out/alarm_log.jsonl, the /feed and /stream (SSE) endpoints, and the console.',
    ],
    refs: ['R29'],
  },
  {
    id: 'watchdog', title: 'Watchdog (every 5 min)', sub: 'hindsight, data ≤ now', lane: 'decide',
    what: 'Every 5 min of data time, and once at the end, the watchdog re-examines recent alarms using only data up to now.',
    why: 'Live decisions are made early. Hindsight fixes onsets, fragments and late evidence without ever peeking at the future.',
    how: [
      'Step 1: re-classify closed episodes with late evidence, such as RFI reports that have since become available.',
      'Step 2: re-localise boundaries within ±10 min by an exact single-change fit on 1 Hz data (a step fit, or a hinge fit for SPOOFING and SPACE_WEATHER onsets).',
      'Step 3: merge SPACE_WEATHER alarms ≤ 30 min apart.',
      'Step 4: retract an alarm shorter than 2 batches (NOT_SUSTAINED) or entirely parked (PARKED). Otherwise clip it to working time and CONFIRM it.',
    ],
    refs: ['R30', 'R31'],
  },
  {
    id: 'confidence', title: 'Confidence', sub: 'reporting only, never gates', lane: 'out',
    what: 'P per detection from a frozen table (confidence_table.yaml): a class prior times likelihood ratios for the sign groups seen. Site multipath uses the site\'s dev dip rate instead.',
    why: 'Judges and operators need a number. It is kept out of the decision path so it cannot change what is detected.',
    how: [
      'The detection prior is (10 + 1)/(10 + 2) = 0.92, from 10 of 10 dev detections being hits. P is clipped to [0.02, 0.97].',
      'Site multipath: P = (site dips + 1)/(site passes + 2), capped at 0.95.',
      'Fitted on the dev run; the leave-one-out Brier score was 0.098.',
    ],
    refs: ['R27'],
  },
  {
    id: 'outputs', title: 'API + submission', sub: '/status /alarms /watchdog /feed /stream', lane: 'out',
    what: 'A FastAPI read side over a thread-safe engine snapshot, plus out/submission.json (validated) at the end of the replay.',
    why: 'This UI, a judge, or a farm system can all read the same state the engine acts on.',
    how: [
      'Only CONFIRMED detections in the evaluation window are exported, with the fixed class → action table.',
      'At most 500 exported detections. Above the cap, site-multipath reports inside a JAMMING span are dropped first, then the shallowest.',
      '/webui/meta (a webui hook) serves the window, field outline, RFI reports and the live fusion tables to this UI.',
    ],
    refs: ['R32'],
  },
]

export const REFS: Record<string, [string, string]> = {
  R1: ['Page, "Continuous inspection schemes," Biometrika 41 (1954)', ''],
  R2: ['MetricGate, CUSUM ARL tables and Siegmund approximation', 'https://metricgate.com/blogs/cusum-small-shift-detection-r/'],
  R3: ['CUSUM with backtracking onset', 'https://arxiv.org/pdf/2605.19966'],
  R4: ['Egea-Roca et al., finite moving average test for transient change detection in GNSS', 'https://collaborate.princeton.edu/en/publications/a-finite-moving-average-test-for-transient-change-detection-in-gn/'],
  R5: ['CUSUM / residual charts for autocorrelated data', 'https://koreascience.kr/article/JAKO200810103470528.pdf'],
  R6: ['Kim et al., distribution-free tabular CUSUM for autocorrelated data (IIE Trans. 2007)', ''],
  R7: ['Runger & Willemain, model-based and model-free control of autocorrelated processes, JQT 1995', ''],
  R8: ['UBM minimum batch size for AR(1)', 'https://koreascience.kr/article/JAKO202003440879153.pdf'],
  R9: ['Effective sample size n(1−ρ)/(1+ρ)', 'https://kls2177.github.io/Climate-and-Geophysical-Data-Analysis/chapters/Week5/n_eff.html'],
  R10: ['Kujur, Khanafseh & Pervan, optimal INS monitor for spoofer tracking error', 'https://rosap.ntl.bts.gov/view/dot/79801/dot_79801_DS1.pdf'],
  R11: ['Gonzalez et al., Dst storm classes', 'https://arxiv.org/pdf/2404.09234'],
  R12: ['Septentrio CALIBRA, ION GNSS 2011', 'https://www.septentrio.com/sites/default/files/2019-06/Paper_2011_CALIBRA_Ionospheric_scintillation_Brazil_ION_GNSS.pdf'],
  R13: ['Australian BoM S4 guidance', 'https://www.sws.bom.gov.au/Satellite/6/3'],
  R14: ['NOAA SWPC Space Weather Scales', 'https://www.swpc.noaa.gov/sites/default/files/images/NOAAscales.pdf'],
  R15: ['Inside GNSS, measuring GNSS signal strength', 'https://insidegnss.com/measuring-gnss-signal-strength/'],
  R16: ['US DOT Volpe GPS loss-of-lock analysis', 'https://rosap.ntl.bts.gov/view/dot/32102/dot_32102_DS1.pdf'],
  R18: ['John Deere StarFire accuracy; GPS SPS performance', 'https://gps.gov/systems/gps/performance'],
  R19: ['Textbook RAIM (4 to fix, 5 to detect, 6 to exclude)', ''],
  R20: ['Swift Navigation RTK/NTRIP troubleshooting', 'https://www.swiftnav.com/resource/blog/troubleshooting-the-most-common-rtk-ntrip-issues-a-step-by-step-guide'],
  R21: ['Trimble R750 RTK propagation limit', 'https://help.fieldsystems.trimble.com/r750/help-receiver-config-position.htm'],
  R22: ['Heckerman, a tractable inference algorithm for diagnosing multiple diseases (UAI 1989)', 'https://www.microsoft.com/en-us/research/uploads/prod/2016/11/A-Tractable-Algorithm-for-Diagnosing-Multiple-Diseases.pdf'],
  R23: ['Heckerman & Shwe, diagnosis of multiple faults', 'https://arxiv.org/pdf/1303.1463.pdf'],
  R24: ['Shwe, Middleton et al., QMR frequency → probability mapping', 'https://arxiv.org/pdf/1304.1141'],
  R25: ['Pradhan, Henrion et al., sensitivity of belief networks to imprecise probabilities, AI 85 (1996)', ''],
  R26: ['Wellman & Henrion, explaining "explaining away", IEEE TPAMI 1993', 'https://arxiv.org/pdf/1304.1082'],
  R27: ['Kass & Raftery, "Bayes factors," JASA 90 (1995)', 'https://search.r-project.org/CRAN/refmans/pcal/html/bfactor_interpret_kr.html'],
  R28: ['Abraham, sliding M-of-N detector (APL-UW TR 2504)', 'https://apl.uw.edu/project/project.php?id=tr_2504'],
  R29: ['ISA-18.2 alarm vs alert', 'https://www.exida.com/Blog/to_be_an_alarmor_not_to_be'],
  R30: ['Fearnhead, Maidstone & Letchford, detecting changes in slope with an L0 penalty (JCGS 2019)', 'https://eprints.lancs.ac.uk/id/eprint/84114'],
  R31: ['Ebbers et al., Sound Event Bounding Boxes (Interspeech 2024)', 'https://merl.com/publications/TR2024-118'],
  R32: ['Rich console docs', 'https://rich.readthedocs.io/en/stable/console.html'],
}
