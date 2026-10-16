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

Venue probe (verified live 2026-10-16, one read-only public request —
no adapter change, no kept code): `GET
/kline/BTC_USDT?interval=Min1` returns `success=true`, 11 rows in
~0.5s, with the same columnar shape as Min5 (`time` as 60s-spaced
epoch seconds plus `open/close/high/low/vol/amount`). Min1 IS served;
the adapter's `INTERVALS` (`proto/mexc.py:22-29`) merely lacks the
entry. Venue support is no longer the question.

Request budget, corrected with live timing: 1m steady state needs
576 coins × 1 request/min ≈ **9.6 rps** against the venue-wide 20 rps
cap (`proto/scan.py:29`) — feasible on paper. (The older ~347s/sweep
figure described full 200-bar history sweeps, not steady state, and
is superseded for cadence planning.) The live loop's measured sweep
is **~803s** (`GET /scanner-health`, 2026-10-16) — the scan phase
alone exceeds a 60s schedule by ~13×. So the schedule is blocked by
**consumption/sweep time, not request rate**: collectors could fetch
1m bars, but nothing downstream can act on them each minute.

Conclusion: **stay on 5m closed bars.** 1m requires a design stage:
(a) a measured loop fast enough to consume 1m data (current sweep
~803s), (b) a separate 1m archive namespace — the current CSV
archives are 300s grids and mixing 60s rows breaks every consumer's
grid assumption, including the audit's `tf_sec=300` coverage math,
(c) a provisional-bar policy with scorer re-validation (a 60s bar
necessarily forms mid-sweep). Tracked in WS3 as a design-stage item,
not started; do not add `"1m"` to `INTERVALS` without that design.

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
- Live/1m: Min1 venue support VERIFIED (2026-10-16 probe); remaining
  design (see §2): consumer loop speed, separate 1m archive namespace,
  provisional-bar policy.
- Provisional-vs-closed: decided closed-only (§3); revisit only with a
  research requirement.
