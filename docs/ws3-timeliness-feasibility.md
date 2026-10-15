# WS3 feasibility: timely MEXC alt-perp data (scan lag/duration, 1m, provisional bars)

Status: ANALYSIS — decision record. No venue calls made; all numbers are
from code constants and the corrected corpus audit (2026-10-15).

## 1. Scan lag/duration — DELIVERED (committed)

The daemon loop now measures each completed sweep's wall-clock duration
(`duration_s`, `proto/daemon.py`) and passes it to `on_sweep`; the
service tracks it plus observed cadence and data lag in the scanner
health state, all readable via `GET /scanner-health`:

- `sweep_count`, `last_sweep_ts` — completed sweeps (honest counts).
- `last_sweep_duration_s` — measured wall-clock seconds of the last
  sweep's work (scan OR collect, whichever completed the sweep).
- `observed_sweep_interval_s` — measured gap between consecutive
  completed sweeps (None until the second sweep).
- `data_lag_s` — `now - last_sweep_ts` at read time (0..N seconds).

All fields are None/0 until their first real observation — never
invented. `duration_s` is passed as a keyword with a TypeError
fallback, so pre-existing two-argument callbacks keep working.

Scan-cycle duration proper (time inside `app.scan_once()`) still needs
daemon-side instrumentation of the scan phase itself; the sweep-level
duration above already covers the loop's end-to-end cost per cycle and
is the operator-visible number. Deferred, not missing.

## 2. Live / one-minute feasibility — NOT FEASIBLE AS-IS (evidence)

Venue adapter (`proto/mexc.py:22-29`): measured `INTERVALS` starts at
**5m** (`Min5`); there is no `1m` (`Min1`) entry. Requesting it raises
`ValueError: unknown interval`. Whether MEXC contract klines serves 1m
at all is UNVERIFIED — no request was made; adding `"1m"` to INTERVALS
without a measured probe would be inventing capability.

Request budget (`proto/scan.py:29`): venue-wide cap `REQ_PER_S = 20`,
shared by scan and collector via one `LIMITER`. Full-universe collect =
576 coins (corrected audit) → 576 requests minimum ≈ **29s** at the
cap, one coin per request. A 1m cadence at the same universe needs
12x the requests of 5m → ~347s/sweep at 20 rps, i.e. it cannot keep a
1m schedule; and 5m collect at 300s cadence already uses ~10% of the
budget per sweep.

Conclusion: **stay on 5m closed bars.** 1m requires (a) a measured
venue probe of Min1 support, (b) a per-coin batching or websocket
design to fit the request budget, (c) an archive-format decision (the
current CSV archives are 300s grids; mixing 60s rows breaks every
consumer's grid assumption, including the audit's `tf_sec=300`
coverage math). That is a design stage, not a flag flip — tracked in
WS3, not started.

## 3. Provisional vs closed bars — DECIDED: closed-only, made explicit

Current behavior (already implemented, `proto/mexc.py:144-152`): the
still-forming bar is DROPPED at ingest (`ts + secs > now`), and
`/bars` serves closed-only bars with `closed_only: true` and per-bar
`closed: true` (`proto/service.py` `_handle_bars`). No provisional bar
ever enters an archive or a response.

Decision: **keep closed-only; do not add a provisional tier.** Rationale:
a forming candle's close/high/low/volume mutate mid-bar, so scoring it
makes signals unreproducible at close (the drop comment's original
reasoning). The explicit representation is the *absence* plus the
`closed_only` flag — consumers can distinguish "no data yet" (empty or
short series with `data_as_of` at the last closed bar) from "data
exists". Adding provisional bars would require a per-bar
`provisional: true` marker and re-validation of every scorer/resampler
against mutable rows — deferred until a research need justifies it.

What WOULD change for 1m: provisional handling becomes unavoidable at
1m (a 60s bar forms within one sweep), which is another reason 1m is a
design stage, not a flag.

## 4. Remaining WS3 items (visible, not started)

- Scan-phase duration instrumentation inside `app.scan_once()`
  (sweep-level duration is delivered).
- Live/1m: venue probe of Min1 + request-budget design (see §2).
- Provisional-vs-closed: decided closed-only (§3); revisit only with a
  research requirement.
