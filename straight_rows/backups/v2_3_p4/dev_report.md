# Dev check: pipeline vs development labels (diagnostic score)

Development period 2026-05-04T00:00:00+00:00 .. 2026-05-06T00:00:00+00:00: 12 labelled episodes, 961 confirmed detections.

## Accuracy rating

- **Diagnostic score 37.5 / 40** (25 x macro-F1 + 15 x mean onset credit)
- Lenient reading of the multipath duplicate rule (only same-tractor duplicates count): 40.0 / 40
- Macro-F1 0.900; episodes found 12/12 (recall 100%); extra detections 4 (precision 75%); mean onset credit 1.00

| version | score /40 | macro-F1 | found | extras | onset credit |
|---|---|---|---|---|---|
| v1 (2026-11-14, before Fix Spec v1.1) | 13.3 | 0.333 | 4/12 | 11 | 0.33 |
| v1.1 (first run, before Update v2) | 18.9 | 0.444 | 7/12 | 33 | 0.52 |
| v2 (before Decision v2.1) | 12.6 | 0.305 | 4/12 | 3 | 0.33 |
| v2.1-physics (config.yaml) | 29.5 | 0.731 | 9/12 | 1 | 0.75 |
| **current** | **37.5** | **0.900** | **12/12** | **4** | **1.00** |

| class | truth | found | extra | missed | precision | recall | F1 |
|---|---|---|---|---|---|---|---|
| SPACE_WEATHER | 2 | 2 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| JAMMING | 3 | 3 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| SPOOFING | 3 | 3 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| EQUIPMENT_FAULT | 2 | 2 | 0 | 0 | 1.00 | 1.00 | 1.00 |
| MULTIPATH | 2 | 2 | 4 | 0 | 0.33 | 1.00 | 0.50 |

## Episode by episode

### TRN-01 SPACE_WEATHER 05-05T11:00:00..16:00:00 T1,T2,T3 — MATCHED by A-EQUIPMENT_FAULT-2 11:00:00..16:00:00 T1,T2,T3, onset error 0 s (credit 1.00), confidence 0.97

- RIGHT: the expected findings (F1, F3, F9) drove a confirmed SPACE_WEATHER alarm
  - T1: working 100%; findings present: F1 59%, F3 58%, F6 8%, F9 100%, F10 0%; no information: F6,F8,F11,F12; median posterior: SPACE_WEATHER 0.85
  - T2: working 100%; findings present: F1 59%, F3 58%, F6 12%, F9 100%, F10 0%; no information: F6,F8,F11,F12; median posterior: SPACE_WEATHER 0.85
  - T3: working 100%; findings present: F1 60%, F3 58%, F6 6%, F9 100%, F10 0%; no information: F6,F8,F11,F12; median posterior: SPACE_WEATHER 0.85
  - alarms overlapping: A-MULTIPATH-582 CONFIRMED 11:00:38..11:00:46 T2 conf 0.94; A-MULTIPATH-583 CONFIRMED 11:00:38..11:00:51 T1 conf 0.95; A-MULTIPATH-584 CONFIRMED 11:00:38..11:00:52 T3 conf 0.94; A-EQUIPMENT_FAULT-2 CONFIRMED 11:00:00..16:00:00 T1,T2,T3 conf 0.97; A-MULTIPATH-585 CONFIRMED 11:06:32..11:06:48 T1 conf 0.95; A-MULTIPATH-586 CONFIRMED 11:06:32..11:06:48 T2 conf 0.95; A-MULTIPATH-587 CONFIRMED 11:06:33..11:06:48 T3 conf 0.95; A-MULTIPATH-588 CONFIRMED 11:12:28..11:12:44 T1 conf 0.91

### TRN-02 SPACE_WEATHER 05-05T19:00:00..22:00:00 T1,T2,T3 — MATCHED by A-SPACE_WEATHER-1 18:55:12..22:00:00 T1,T2,T3, onset error 288 s (credit 1.00), confidence 0.97

- RIGHT: the expected findings (F1, F3, F9) drove a confirmed SPACE_WEATHER alarm
  - T1: working 100%; findings present: F1 100%, F2 76%, F3 99%, F9 100%, F11 20%; no information: F6,F8,F11,F12; median posterior: SPACE_WEATHER 0.99
  - T2: working 100%; findings present: F1 100%, F2 74%, F3 99%, F9 100%, F11 20%; no information: F6,F8,F11,F12; median posterior: SPACE_WEATHER 0.99
  - T3: working 100%; findings present: F1 99%, F2 76%, F3 99%, F8 14%, F9 100%, F11 8%; no information: F6,F8,F11,F12; median posterior: SPACE_WEATHER 0.99
  - alarms overlapping: A-MULTIPATH-826 CONFIRMED 19:00:01..19:00:53 T1 conf 0.93; A-MULTIPATH-827 CONFIRMED 19:00:01..19:00:53 T2 conf 0.86; A-MULTIPATH-828 CONFIRMED 19:00:01..19:00:53 T3 conf 0.89; A-SPACE_WEATHER-1 CONFIRMED 18:55:12..22:00:00 T1,T2,T3 conf 0.97; A-MULTIPATH-829 CONFIRMED 19:06:32..19:06:48 T1 conf 0.94; A-MULTIPATH-830 CONFIRMED 19:06:33..19:06:49 T2 conf 0.94; A-MULTIPATH-831 CONFIRMED 19:06:32..19:06:49 T3 conf 0.91; A-MULTIPATH-832 CONFIRMED 19:12:28..19:12:43 T1 conf 0.95

### TRN-03 JAMMING 05-04T16:30:00..17:12:00 T1,T2,T3 — MATCHED by A-JAMMING-1 16:30:00..17:12:00 T1,T2,T3, onset error 0 s (credit 1.00), confidence 0.97

- RIGHT: the expected findings (F1, F4, F2) drove a confirmed JAMMING alarm
  - T1: working 100%; findings present: F1 99%, F2 39%, F3 1%, F4 75%, F5 8%; no information: F6,F8,F11,F12; median posterior: JAMMING 0.49, OTHER 0.03
  - T2: working 100%; findings present: F1 100%, F2 5%, F3 1%, F4 75%; no information: F6,F8,F11,F12; median posterior: JAMMING 0.49, OTHER 0.03
  - T3: working 100%; findings present: F1 100%, F2 60%, F3 1%, F4 75%, F5 4%; no information: F6,F8,F11,F12; median posterior: JAMMING 0.95, OTHER 0.02
  - alarms overlapping: A-JAMMING-1 CONFIRMED 16:30:00..17:12:00 T1,T2,T3 conf 0.97; A-MULTIPATH-279 CONFIRMED 16:32:31..16:32:44 T2 conf 0.94; A-MULTIPATH-280 CONFIRMED 16:38:24..16:38:39 T2 conf 0.94; A-MULTIPATH-281 CONFIRMED 16:44:23..16:44:35 T2 conf 0.94; A-MULTIPATH-282 CONFIRMED 16:50:15..16:50:30 T2 conf 0.95; A-MULTIPATH-283 CONFIRMED 16:56:11..16:56:22 T2 conf 0.91; A-MULTIPATH-284 CONFIRMED 17:02:07..17:02:12 T2 conf 0.94; A-MULTIPATH-285 CONFIRMED 17:02:06..17:02:21 T1 conf 0.95

### TRN-04 JAMMING 05-04T21:00:00..21:36:00 T1,T3 — MATCHED by A-JAMMING-2 21:00:00..21:36:00 T1,T2,T3, onset error 0 s (credit 1.00), confidence 0.97

- RIGHT: the expected findings (F1, F4, F2) drove a confirmed JAMMING alarm
  - T1: working 100%; findings present: F1 97%, F4 22%, F8 1%; no information: F6,F11,F12; median posterior: OTHER 0.03
  - T3: working 100%; findings present: F1 99%, F2 4%, F4 22%, F8 6%; no information: F6,F8,F11,F12; median posterior: OTHER 0.04
  - alarms overlapping: A-JAMMING-2 CONFIRMED 21:00:00..21:36:00 T1,T2,T3 conf 0.97; A-SPOOFING-2 RETRACTED(UNATTRIBUTED) 20:58:00..21:16:30 T3 conf 0.97; A-MULTIPATH-404 CONFIRMED 21:11:08..21:11:15 T1 conf 0.95; A-SPOOFING-3 RETRACTED(UNATTRIBUTED) 20:58:00..21:16:30 T3 conf 0.22; A-MULTIPATH-407 CONFIRMED 21:22:51..21:22:59 T1 conf 0.91; A-SPOOFING-4 RETRACTED(UNATTRIBUTED) 20:58:00..21:16:30 T3 conf 0.22; A-MULTIPATH-410 CONFIRMED 21:28:46..21:28:52 T3 conf 0.94; A-SPOOFING-5 RETRACTED(UNATTRIBUTED) 20:58:00..21:16:30 T3 conf 0.22

### TRN-05 JAMMING 05-05T20:00:00..20:36:00 T1,T2,T3 — MATCHED by A-JAMMING-3 20:00:00..20:36:00 T1,T2,T3, onset error 0 s (credit 1.00), confidence 0.89

- RIGHT: the expected findings (F1, F2) drove a confirmed JAMMING alarm
  - T1: working 100%; findings present: F1 100%, F2 75%, F3 100%, F9 100%, F11 99%; no information: F6,F8,F12; median posterior: SPACE_WEATHER 0.99
  - T2: working 100%; findings present: F1 100%, F2 75%, F3 100%, F9 100%, F11 99%; no information: F6,F8,F12; median posterior: SPACE_WEATHER 0.99
  - T3: working 100%; findings present: F1 100%, F2 75%, F3 100%, F9 100%; no information: F6,F8,F11,F12; median posterior: SPACE_WEATHER 0.99
  - alarms overlapping: A-SPACE_WEATHER-1 CONFIRMED 18:55:12..22:00:00 T1,T2,T3 conf 0.97; A-MULTIPATH-856 CONFIRMED 19:59:52..20:00:29 T3 conf 0.94; A-SPACE_WEATHER-2 RETRACTED(MERGED) 20:00:00..20:36:00 T1,T2 conf 0.97; A-MULTIPATH-857 CONFIRMED 19:59:52..20:00:31 T1 conf 0.95; A-MULTIPATH-858 CONFIRMED 19:59:52..20:00:33 T2 conf 0.94; A-JAMMING-3 CONFIRMED 20:00:00..20:36:00 T1,T2,T3 conf 0.89; A-SPACE_WEATHER-3 RETRACTED(MERGED) 20:00:00..20:36:00 T1,T2 conf 0.17; A-SPACE_WEATHER-4 RETRACTED(MERGED) 20:00:00..20:36:00 T1,T2 conf 0.17

### TRN-06 SPOOFING 05-04T13:12:00..13:54:00 T2 — MATCHED by A-SPOOFING-1 13:12:10..13:54:02 T2, onset error 10 s (credit 1.00), confidence 0.97

- RIGHT: the expected findings (F8) drove a confirmed SPOOFING alarm
  - T2: working 100%; findings present: F1 95%, F2 75%, F8 99%; no information: F6,F11,F12; median posterior: OTHER 0.52, SPOOFING 0.19, JAMMING 0.04
  - alarms overlapping: A-OTHER-1 CONFIRMED 11:59:00..14:00:00 T2 conf 0.97; A-SPOOFING-1 CONFIRMED 13:12:10..13:54:02 T2 conf 0.97; A-MULTIPATH-178 CONFIRMED 13:16:55..13:17:10 T2 conf 0.94; A-MULTIPATH-183 CONFIRMED 13:22:51..13:23:09 T2 conf 0.94; A-MULTIPATH-186 CONFIRMED 13:28:44..13:29:08 T2 conf 0.91; A-MULTIPATH-187 CONFIRMED 13:34:32..13:34:37 T2 conf 0.91; A-MULTIPATH-190 CONFIRMED 13:34:42..13:35:00 T2 conf 0.94; A-MULTIPATH-193 CONFIRMED 13:40:35..13:40:55 T2 conf 0.94

### TRN-07 SPOOFING 05-05T00:30:00..01:18:00 T1 — MATCHED by A-SPOOFING-15 00:30:06..01:18:02 T1, onset error 6 s (credit 1.00), confidence 0.97

- RIGHT: the expected findings (F8) drove a confirmed SPOOFING alarm
  - T1: working 100%; findings present: F8 99%; no information: F11,F12; median posterior: SPOOFING 0.27, OTHER 0.03
  - alarms overlapping: A-SPOOFING-15 CONFIRMED 00:30:06..01:18:02 T1 conf 0.97; A-MULTIPATH-506 CONFIRMED 00:32:29..00:32:43 T1 conf 0.94; A-MULTIPATH-509 CONFIRMED 00:38:24..00:38:40 T1 conf 0.91; A-MULTIPATH-513 CONFIRMED 00:44:19..00:44:36 T1 conf 0.94; A-MULTIPATH-515 CONFIRMED 00:50:15..00:50:30 T1 conf 0.94; A-MULTIPATH-518 CONFIRMED 00:56:10..00:56:27 T1 conf 0.94; A-MULTIPATH-522 CONFIRMED 01:02:05..01:02:22 T1 conf 0.91; A-MULTIPATH-525 CONFIRMED 01:08:01..01:08:18 T1 conf 0.95

### TRN-08 SPOOFING 05-05T21:30:00..22:06:00 T3 — MATCHED by A-SPOOFING-16 21:31:58..22:06:02 T3, onset error 118 s (credit 1.00), confidence 0.97

- RIGHT: the expected findings (F8) drove a confirmed SPOOFING alarm
  - T3: working 100%; findings present: F1 83%, F2 67%, F3 83%, F8 88%, F9 100%; no information: F6,F11,F12; median posterior: SPACE_WEATHER 0.98, SPOOFING 0.27, OTHER 0.07
  - alarms overlapping: A-SPACE_WEATHER-1 CONFIRMED 18:55:12..22:00:00 T1,T2,T3 conf 0.97; A-MULTIPATH-889 CONFIRMED 21:34:41..21:34:58 T3 conf 0.94; A-SPOOFING-16 CONFIRMED 21:31:58..22:06:02 T3 conf 0.97; A-MULTIPATH-891 CONFIRMED 21:40:37..21:40:52 T3 conf 0.91; A-MULTIPATH-895 CONFIRMED 21:46:32..21:46:49 T3 conf 0.94; A-MULTIPATH-898 CONFIRMED 21:52:29..21:52:44 T3 conf 0.94; A-MULTIPATH-901 CONFIRMED 21:58:23..21:58:39 T3 conf 0.89; A-MULTIPATH-905 CONFIRMED 22:04:20..22:04:36 T3 conf 0.92

### TRN-09 EQUIPMENT_FAULT 05-04T12:00:00..14:00:00 T2 — MATCHED by A-OTHER-1 11:59:00..14:00:00 T2, onset error 60 s (credit 1.00), confidence 0.97

- RIGHT: the expected findings () drove a confirmed EQUIPMENT_FAULT alarm
  - T2: working 100%; findings present: F1 95%, F2 75%, F8 35%; no information: F6,F11,F12; median posterior: OTHER 0.15, JAMMING 0.04, SPACE_WEATHER 0.03
  - alarms overlapping: A-MULTIPATH-123 CONFIRMED 11:59:53..12:00:09 T2 conf 0.94; A-OTHER-1 CONFIRMED 11:59:00..14:00:00 T2 conf 0.97; A-MULTIPATH-126 CONFIRMED 12:05:49..12:05:58 T2 conf 0.91; A-MULTIPATH-129 CONFIRMED 12:05:59..12:06:07 T2 conf 0.92; A-MULTIPATH-130 CONFIRMED 12:06:08..12:06:14 T2 conf 0.89; A-MULTIPATH-132 CONFIRMED 12:11:32..12:11:38 T2 conf 0.94; A-MULTIPATH-135 CONFIRMED 12:11:42..12:12:03 T2 conf 0.94; A-MULTIPATH-139 CONFIRMED 12:17:39..12:18:00 T2 conf 0.95

### TRN-10 EQUIPMENT_FAULT 05-05T02:00:00..02:48:00 T1 — MATCHED by A-EQUIPMENT_FAULT-1 02:00:01..02:51:30 T1, onset error 1 s (credit 1.00), confidence 0.97

- RIGHT: the expected findings (F7) drove a confirmed EQUIPMENT_FAULT alarm
  - T1: working 100%; findings present: F7 100%; no information: F8,F11,F12; median posterior: EQUIPMENT_FAULT 0.02
  - alarms overlapping: A-EQUIPMENT_FAULT-1 CONFIRMED 02:00:01..02:51:30 T1 conf 0.97; A-MULTIPATH-553 CONFIRMED 02:01:22..02:01:38 T1 conf 0.95; A-MULTIPATH-556 CONFIRMED 02:07:19..02:07:33 T1 conf 0.91; A-MULTIPATH-558 CONFIRMED 02:13:12..02:13:29 T1 conf 0.94; A-MULTIPATH-561 CONFIRMED 02:19:08..02:19:24 T1 conf 0.94; A-MULTIPATH-564 CONFIRMED 02:25:03..02:25:19 T1 conf 0.91; A-MULTIPATH-568 CONFIRMED 02:31:00..02:31:16 T1 conf 0.94; A-MULTIPATH-570 CONFIRMED 02:36:55..02:37:11 T1 conf 0.94

### TRN-11 MULTIPATH 05-04T14:04:18..14:04:35 T3 — MATCHED by A-MULTIPATH-205 14:04:19..14:04:35 T3, onset error 1 s (credit 1.00), confidence 0.94

- RIGHT: the expected findings () drove a confirmed MULTIPATH alarm
  - T3: working 100%; findings present: none; no information: F2,F5,F6,F11,F12; median posterior: nothing above 0.02
  - alarms overlapping: A-MULTIPATH-205 CONFIRMED 14:04:19..14:04:35 T3 conf 0.94

### TRN-12 MULTIPATH 05-05T19:00:35..19:00:52 T1 — MATCHED by A-MULTIPATH-826 19:00:01..19:00:53 T1, onset error 34 s (credit 1.00), confidence 0.93

- RIGHT: the expected findings () drove a confirmed MULTIPATH alarm
  - T1: working 100%; findings present: F1 100%, F3 100%, F9 100%; no information: F2,F5,F6,F8,F11,F12; median posterior: SPACE_WEATHER 0.93
  - alarms overlapping: A-MULTIPATH-826 CONFIRMED 19:00:01..19:00:53 T1 conf 0.93; A-SPACE_WEATHER-1 CONFIRMED 18:55:12..22:00:00 T1,T2,T3 conf 0.97

## Extra detections (4): confirmed but matching no label

### A-MULTIPATH-204 MULTIPATH 05-04T14:04:20..14:04:35 T1 conf 0.95

- WRONG (false alarm) unless the label set is incomplete; what the pipeline saw:
  - T1: working 100%; findings present: none; no information: F2,F5,F6,F11,F12; median posterior: nothing above 0.02
  - alarms overlapping: A-MULTIPATH-204 CONFIRMED 14:04:20..14:04:35 T1 conf 0.95

### A-MULTIPATH-206 MULTIPATH 05-04T14:04:19..14:04:36 T2 conf 0.95

- WRONG (false alarm) unless the label set is incomplete; what the pipeline saw:
  - T2: working 100%; findings present: none; no information: F2,F5,F6,F11,F12; median posterior: nothing above 0.02
  - alarms overlapping: A-MULTIPATH-206 CONFIRMED 14:04:19..14:04:36 T2 conf 0.95

### A-MULTIPATH-827 MULTIPATH 05-05T19:00:01..19:00:53 T2 conf 0.86

- WRONG (false alarm) unless the label set is incomplete; what the pipeline saw:
  - T2: working 100%; findings present: F1 100%, F3 100%, F9 100%; no information: F2,F6,F8,F11,F12; median posterior: SPACE_WEATHER 0.93
  - alarms overlapping: A-MULTIPATH-827 CONFIRMED 19:00:01..19:00:53 T2 conf 0.86; A-SPACE_WEATHER-1 CONFIRMED 18:55:12..22:00:00 T1,T2,T3 conf 0.97

### A-MULTIPATH-828 MULTIPATH 05-05T19:00:01..19:00:53 T3 conf 0.89

- WRONG (false alarm) unless the label set is incomplete; what the pipeline saw:
  - T3: working 100%; findings present: F1 100%, F3 100%, F9 100%; no information: F2,F6,F8,F11,F12; median posterior: SPACE_WEATHER 0.93
  - alarms overlapping: A-MULTIPATH-828 CONFIRMED 19:00:01..19:00:53 T3 conf 0.89; A-SPACE_WEATHER-1 CONFIRMED 18:55:12..22:00:00 T1,T2,T3 conf 0.97

