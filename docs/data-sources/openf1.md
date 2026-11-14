# OpenF1: behaviour verified against real responses

All points below were checked with live requests on 2026-11-11 (historical data, no auth).
Recorded responses live in `tests/fixtures/openf1/` and are refreshed with `make fixtures`.

## Endpoints confirmed

`sessions`, `meetings`, `drivers`, `laps`, `stints`, `pit`, `race_control`, `weather`,
`intervals`, `position`, `car_data`, `location`, `team_radio`, `overtakes`, `session_result`,
`starting_grid`. Every one returns a JSON list of flat objects.

## Quirks that shaped the client

| Observation | How the client handles it |
| --- | --- |
| An empty result is **HTTP 404** `{"detail": "No results found."}` | Returned as `[]`, not an error |
| An unknown endpoint gives the **same** 404 | Endpoint names are validated against an enum before any request |
| Comparison filters live in the raw query: `date>=2026-03-08T04:10:00` | `Filter.as_query()` writes the operator literally |
| Encoding the operator into the key (`date%3E%3D=...`) gives **HTTP 500** | Never done; covered by a test |
| No rate-limit headers; a burst at 3 req/s drew an HTTP 429 | Client-side budget of 2/s and 30/min, 429s retried with backoff |
| An invalid filter value (e.g. `session_key=abc`) also returns the 404 "No results" | Callers pass typed values |

## 2026 data observations (relevant later)

- `car_data.drs` is present but **null** for 2026 cars, as the spec anticipated: there is no
  DRS in 2026, and there is no Overtake Mode / ERS channel either.
- 2026 `sessions` includes pre-season testing (`session_type` "Practice", names "Day 1" to
  "Day 3") and an `is_cancelled` flag; the Bahrain and Saudi Arabian rounds are cancelled.
- Sprint races have `session_type` "Race" and `session_name` "Sprint"; filter on
  `session_name` to separate them.
- `pit` has `lane_duration`, `stop_duration` and `pit_duration` (seconds).
- `laps` has `duration_sector_1..3`, `i1_speed`, `i2_speed`, `st_speed`, `is_pit_out_lap` and
  mini-sector `segments_sector_*` codes, but no pit-in flag or compound (compounds come from
  `stints`).
- `overtakes` exists as its own endpoint (useful for `fct_overtake` in Milestone 7).
- `team_radio` and `starting_grid` returned no results for the 2026 Australian GP race.
