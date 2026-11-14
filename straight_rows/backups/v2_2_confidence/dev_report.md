# Dev check: pipeline vs development labels (diagnostic score)

Development period 2026-05-04T00:00:00+00:00 .. 2026-05-06T00:00:00+00:00: 12 labelled episodes, 10 confirmed detections.

## Accuracy rating

- **Diagnostic score 29.5 / 40** (25 x macro-F1 + 15 x mean onset credit)
- Macro-F1 0.731; episodes found 9/12 (recall 75%); extra detections 1 (precision 90%); mean onset credit 0.75

| version | score /40 | macro-F1 | found | extras | onset credit |
|---|---|---|---|---|---|
| v1 (2026-11-14, before Fix Spec v1.1) | 13.3 | 0.333 | 4/12 | 11 | 0.33 |
| v1.1 (first run, before Update v2) | 18.9 | 0.444 | 7/12 | 33 | 0.52 |
| v2 (before Decision v2.1) | 12.6 | 0.305 | 4/12 | 3 | 0.33 |
| v2.1-physics (config.yaml) | 29.5 | 0.731 | 9/12 | 1 | 0.75 |
| **current** | **29.5** | **0.731** | **9/12** | **1** | **0.75** |

| class | truth | found | extra | missed | precision | recall | F1 |
|---|---|---|---|---|---|---|---|
| SPACE_WEATHER | 2 | 2 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| JAMMING | 3 | 2 | 0 | 1 | 1.00 | 0.67 | 0.80 |
| SPOOFING | 3 | 3 | 1 | 0 | 0.75 | 1.00 | 0.86 |
| EQUIPMENT_FAULT | 2 | 2 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| MULTIPATH | 2 | 0 | 0 | 2 | 0.00 | 0.00 | 0.00 |

## Episode by episode

### TRN-01 SPACE_WEATHER 05-05T11:00:00..16:00:00 T1,T2,T3 — MATCHED by A-EQUIPMENT_FAULT-2 11:00:00..16:00:00 T1,T2,T3, onset error 0 s (credit 1.00), confidence 0.75

- RIGHT: the expected findings (F1, F3, F9) drove a confirmed SPACE_WEATHER alarm
  - T1: working 100%; findings present: F1 59%, F3 58%, F6 8%, F9 100%, F10 0%; no information: F6,F8,F11,F12; median posterior: SPACE_WEATHER 0.85
  - T2: working 100%; findings present: F1 59%, F3 58%, F6 12%, F9 100%, F10 0%; no information: F6,F8,F11,F12; median posterior: SPACE_WEATHER 0.85
  - T3: working 100%; findings present: F1 60%, F3 58%, F6 6%, F9 100%, F10 0%; no information: F6,F8,F11,F12; median posterior: SPACE_WEATHER 0.85
  - alarms overlapping: A-EQUIPMENT_FAULT-2 CONFIRMED 11:00:00..16:00:00 T1,T2,T3 conf 0.75

### TRN-02 SPACE_WEATHER 05-05T19:00:00..22:00:00 T1,T2,T3 — MATCHED by A-SPACE_WEATHER-1 18:55:12..22:00:00 T1,T2,T3, onset error 288 s (credit 1.00), confidence 0.46

- RIGHT: the expected findings (F1, F3, F9) drove a confirmed SPACE_WEATHER alarm
  - T1: working 100%; findings present: F1 100%, F2 76%, F3 99%, F9 100%, F11 20%; no information: F6,F8,F11,F12; median posterior: SPACE_WEATHER 0.99
  - T2: working 100%; findings present: F1 100%, F2 74%, F3 99%, F9 100%, F11 20%; no information: F6,F8,F11,F12; median posterior: SPACE_WEATHER 0.99
  - T3: working 100%; findings present: F1 99%, F2 76%, F3 99%, F8 14%, F9 100%, F11 8%; no information: F6,F8,F11,F12; median posterior: SPACE_WEATHER 0.99
  - alarms overlapping: A-SPACE_WEATHER-1 CONFIRMED 18:55:12..22:00:00 T1,T2,T3 conf 0.46; A-SPACE_WEATHER-2 RETRACTED(MERGED) 20:00:00..20:36:00 T1,T2 conf 0.57; A-SPOOFING-4 CONFIRMED 21:31:58..22:06:02 T3 conf 0.46; A-SPACE_WEATHER-3 RETRACTED(MERGED) 20:00:00..20:36:00 T1,T2 conf 0.17; A-SPACE_WEATHER-4 RETRACTED(MERGED) 20:00:00..20:36:00 T1,T2 conf 0.17; A-SPACE_WEATHER-5 RETRACTED(MERGED) 20:00:00..20:36:00 T1,T2 conf 0.17; A-SPACE_WEATHER-6 RETRACTED(MERGED) 20:00:00..20:36:00 T1,T2 conf 0.17; A-SPACE_WEATHER-7 RETRACTED(MERGED) 20:00:00..20:36:00 T1,T2 conf 0.17

### TRN-03 JAMMING 05-04T16:30:00..17:12:00 T1,T2,T3 — MATCHED by A-JAMMING-1 16:30:00..17:12:00 T1,T2,T3, onset error 0 s (credit 1.00), confidence 0.77

- RIGHT: the expected findings (F1, F4, F2) drove a confirmed JAMMING alarm
  - T1: working 100%; findings present: F1 99%, F2 39%, F3 1%, F4 75%, F5 8%; no information: F6,F8,F11,F12; median posterior: JAMMING 0.49, OTHER 0.03
  - T2: working 100%; findings present: F1 100%, F2 5%, F3 1%, F4 75%; no information: F6,F8,F11,F12; median posterior: JAMMING 0.49, OTHER 0.03
  - T3: working 100%; findings present: F1 100%, F2 60%, F3 1%, F4 75%, F5 4%; no information: F6,F8,F11,F12; median posterior: JAMMING 0.95, OTHER 0.02
  - alarms overlapping: A-JAMMING-1 CONFIRMED 16:30:00..17:12:00 T1,T2,T3 conf 0.77

### TRN-04 JAMMING 05-04T21:00:00..21:36:00 T1,T3 — MATCHED by A-JAMMING-2 21:00:00..21:36:00 T1,T2,T3, onset error 0 s (credit 1.00), confidence 0.77

- RIGHT: the expected findings (F1, F4, F2) drove a confirmed JAMMING alarm
  - T1: working 100%; findings present: F1 97%, F4 22%, F8 1%; no information: F6,F11,F12; median posterior: OTHER 0.03
  - T3: working 100%; findings present: F1 99%, F2 4%, F4 22%, F8 6%; no information: F6,F8,F11,F12; median posterior: OTHER 0.04
  - alarms overlapping: A-JAMMING-2 CONFIRMED 21:00:00..21:36:00 T1,T2,T3 conf 0.77; A-SPOOFING-2 CONFIRMED 20:58:00..21:16:30 T3 conf 0.46

### TRN-05 JAMMING 05-05T20:00:00..20:36:00 T1,T2,T3 — MISSED

- WRONG (attribution): findings fired but the network preferred another cause (A-SPACE_WEATHER-1)
  - T1: working 100%; findings present: F1 100%, F2 75%, F3 100%, F9 100%, F11 99%; no information: F6,F8,F12; median posterior: SPACE_WEATHER 0.99
  - T2: working 100%; findings present: F1 100%, F2 75%, F3 100%, F9 100%, F11 99%; no information: F6,F8,F12; median posterior: SPACE_WEATHER 0.99
  - T3: working 100%; findings present: F1 100%, F2 75%, F3 100%, F9 100%; no information: F6,F8,F11,F12; median posterior: SPACE_WEATHER 0.99
  - alarms overlapping: A-SPACE_WEATHER-1 CONFIRMED 18:55:12..22:00:00 T1,T2,T3 conf 0.46; A-SPACE_WEATHER-2 RETRACTED(MERGED) 20:00:00..20:36:00 T1,T2 conf 0.57; A-SPACE_WEATHER-3 RETRACTED(MERGED) 20:00:00..20:36:00 T1,T2 conf 0.17; A-SPACE_WEATHER-4 RETRACTED(MERGED) 20:00:00..20:36:00 T1,T2 conf 0.17; A-SPACE_WEATHER-5 RETRACTED(MERGED) 20:00:00..20:36:00 T1,T2 conf 0.17; A-SPACE_WEATHER-6 RETRACTED(MERGED) 20:00:00..20:36:00 T1,T2 conf 0.17; A-SPACE_WEATHER-7 RETRACTED(MERGED) 20:00:00..20:36:00 T1,T2 conf 0.17; A-SPACE_WEATHER-8 RETRACTED(MERGED) 20:00:00..20:36:00 T1,T2 conf 0.17

### TRN-06 SPOOFING 05-04T13:12:00..13:54:00 T2 — MATCHED by A-SPOOFING-1 13:12:10..13:54:02 T2, onset error 10 s (credit 1.00), confidence 0.46

- RIGHT: the expected findings (F8) drove a confirmed SPOOFING alarm
  - T2: working 100%; findings present: F1 95%, F2 75%, F8 99%; no information: F6,F11,F12; median posterior: OTHER 0.52, SPOOFING 0.19, JAMMING 0.04
  - alarms overlapping: A-OTHER-1 CONFIRMED 11:59:00..14:00:00 T2 conf 0.74; A-SPOOFING-1 CONFIRMED 13:12:10..13:54:02 T2 conf 0.46

### TRN-07 SPOOFING 05-05T00:30:00..01:18:00 T1 — MATCHED by A-SPOOFING-3 00:30:06..01:18:02 T1, onset error 6 s (credit 1.00), confidence 0.46

- RIGHT: the expected findings (F8) drove a confirmed SPOOFING alarm
  - T1: working 100%; findings present: F8 99%; no information: F11,F12; median posterior: SPOOFING 0.27, OTHER 0.03
  - alarms overlapping: A-SPOOFING-3 CONFIRMED 00:30:06..01:18:02 T1 conf 0.46

### TRN-08 SPOOFING 05-05T21:30:00..22:06:00 T3 — MATCHED by A-SPOOFING-4 21:31:58..22:06:02 T3, onset error 118 s (credit 1.00), confidence 0.46

- RIGHT: the expected findings (F8) drove a confirmed SPOOFING alarm
  - T3: working 100%; findings present: F1 83%, F2 67%, F3 83%, F8 88%, F9 100%; no information: F6,F11,F12; median posterior: SPACE_WEATHER 0.98, SPOOFING 0.27, OTHER 0.07
  - alarms overlapping: A-SPACE_WEATHER-1 CONFIRMED 18:55:12..22:00:00 T1,T2,T3 conf 0.46; A-SPOOFING-4 CONFIRMED 21:31:58..22:06:02 T3 conf 0.46

### TRN-09 EQUIPMENT_FAULT 05-04T12:00:00..14:00:00 T2 — MATCHED by A-OTHER-1 11:59:00..14:00:00 T2, onset error 60 s (credit 1.00), confidence 0.74

- RIGHT: the expected findings () drove a confirmed EQUIPMENT_FAULT alarm
  - T2: working 100%; findings present: F1 95%, F2 75%, F8 35%; no information: F6,F11,F12; median posterior: OTHER 0.15, JAMMING 0.04, SPACE_WEATHER 0.03
  - alarms overlapping: A-OTHER-1 CONFIRMED 11:59:00..14:00:00 T2 conf 0.74; A-SPOOFING-1 CONFIRMED 13:12:10..13:54:02 T2 conf 0.46

### TRN-10 EQUIPMENT_FAULT 05-05T02:00:00..02:48:00 T1 — MATCHED by A-EQUIPMENT_FAULT-1 02:00:01..02:51:30 T1, onset error 1 s (credit 1.00), confidence 0.74

- RIGHT: the expected findings (F7) drove a confirmed EQUIPMENT_FAULT alarm
  - T1: working 100%; findings present: F7 100%; no information: F8,F11,F12; median posterior: EQUIPMENT_FAULT 0.02
  - alarms overlapping: A-EQUIPMENT_FAULT-1 CONFIRMED 02:00:01..02:51:30 T1 conf 0.74

### TRN-11 MULTIPATH 05-04T14:04:18..14:04:35 T3 — MISSED

- WRONG (detector): none of the MULTIPATH findings (F5) fired during the episode
  - T3: working 100%; findings present: none; no information: F2,F5,F6,F11,F12; median posterior: nothing above 0.02
  - alarms overlapping: none

### TRN-12 MULTIPATH 05-05T19:00:35..19:00:52 T1 — MISSED

- WRONG (detector): none of the MULTIPATH findings (F5) fired during the episode
  - T1: working 100%; findings present: F1 100%, F3 100%, F9 100%; no information: F2,F5,F6,F8,F11,F12; median posterior: SPACE_WEATHER 0.93
  - alarms overlapping: A-SPACE_WEATHER-1 CONFIRMED 18:55:12..22:00:00 T1,T2,T3 conf 0.46

## Extra detections (1): confirmed but matching no label

### A-SPOOFING-2 SPOOFING 05-04T20:58:00..21:16:30 T3 conf 0.46

- WRONG (false alarm) unless the label set is incomplete; what the pipeline saw:
  - T3: working 100%; findings present: F1 89%, F2 3%, F4 38%, F8 11%; no information: F6,F8,F11,F12; median posterior: OTHER 0.03
  - alarms overlapping: A-JAMMING-2 CONFIRMED 21:00:00..21:36:00 T1,T2,T3 conf 0.77; A-SPOOFING-2 CONFIRMED 20:58:00..21:16:30 T3 conf 0.46

