# Episode Measurement Implementation — Review Follow-up

**Date:** 2026-10-09
**Reviewed implementation:** `db0c3a1` (Tasks 1–6)
**Follow-up:** fixes are present in the working tree and remain uncommitted.

## Summary

The implementation established the episode schema, lifecycle, scheduler, resolver, reporting, and epoch controls. The review found several seams where the new pieces were implemented but not fully connected to the production path, plus data-integrity and reporting issues that could make the measurements incomplete or misleading. Those gaps have now been fixed and covered with regression tests.

The scoring rules were not changed. This work fixes measurement persistence, outcome resolution, data-quality classification, and cohort reporting so the frozen signal process can be measured consistently.

## What the implementation missed and what changed

| Review finding | Why it mattered | Fix |
|---|---|---|
| The first qualifying observation did not persist its plan into `episode_plan`. | Episodes could be counted as planned while the resolver had no frozen levels to evaluate. | The episode transaction now validates and freezes the first valid plan. A direction reversal freezes the new episode’s plan as well. Invalid or incomplete plans are recorded as `NO_PLAN`. See [`proto/measurement.py`](/home/bamzhie/alt-radar/proto/measurement.py). |
| Episode resolution existed as code but was not called by the daemon. | Production episodes would remain unresolved even while the legacy signal and plan resolvers ran. | The daemon now calls `resolve_episode_plans()` on its regular resolution cycle and logs the number of episode outcomes resolved. See [`proto/daemon.py`](/home/bamzhie/alt-radar/proto/daemon.py). |
| The resolver expected `open_ts`, while persisted collector candles use `ts`. | Production collector history could fail to map to resolver slots, leaving entries and outcomes pending. | Collector and REST bars are normalized to `open_ts`; duplicate timestamps are de-duplicated with collector bars taking precedence. See [`proto/outcomes.py`](/home/bamzhie/alt-radar/proto/outcomes.py). |
| The episode entry window advanced in 5-minute increments from the signal timestamp. | A signal timestamp not on an exchange candle boundary generated impossible candle slots. | Entry slots now begin at the first 5-minute boundary strictly after the signal and stay within the fully closed validity window. Tests now use an aligned fixture for tests that assert exact candle positions. |
| Terminal trade outcomes stopped all resolver work. | A stop or TP2 could prevent independently defined 1h/4h/24h/7d descriptive horizons from maturing. | Trade terminality now freezes the trade result while fixed-horizon observations continue to resolve independently. Terminal entry states still remain immutable. |
| Resolver input validation did not reject malformed OHLC data. | Invalid bars could create false fills, stops, targets, or horizon values; string-valued prices could also fail during comparisons. | Bar indexing now rejects missing, non-finite, or internally inconsistent OHLC values and normalizes accepted prices to floats. Missing timestamps are handled as invalid input rather than raising. |
| Transient 1H/4H and Bybit OI fetch errors were counted in venue health but did not mark the scorecard as degraded. | The measurement scheduler could treat a score based on failed inputs as an ordinary valid observation. | Scan adapters now attach a `DATA transient input failure` note for transport failures. The existing measurement degradation rule classifies those attempts as `UNKNOWN`. Thin history and structurally absent MTF data remain informational. See [`proto/scan.py`](/home/bamzhie/alt-radar/proto/scan.py). |
| Summary and exploratory breakdowns could pool different configuration or rule-version cohorts. | Mixing unlike measurement rules can produce rates with invalid denominators and conceal behavior changes. | Unfiltered reports now split multiple immutable cohorts into `by_cohort` results. Breakdown rows are cohort-filtered and tagged too. Nullable rule versions use `IS NULL`, so cohorts with a missing version are still selected correctly. See [`proto/episode_report.py`](/home/bamzhie/alt-radar/proto/episode_report.py). |
| The next episode after a coverage-loss closure did not reliably carry the `after_gap` marker. | A reappearance after unobserved time could be mistaken for a clean re-entry, hiding a coverage discontinuity. | New qualifying episodes inspect intervening observations after the last coverage-loss close and set `after_gap` when no valid non-qualifying observation showed the signal had quieted. |
| Store row conversion used the wrong SQLite cursor API. | `open_episode()` could return an empty mapping, breaking lifecycle/status checks. | Column names are now read from `cursor.description`. See [`proto/store.py`](/home/bamzhie/alt-radar/proto/store.py). |
| Qualifying observations did not carry the scheduler’s unique attempt ID into the episode journal. | Linking and deduplicating attempts was inconsistent between qualifying and other observation classes. | `ObservationEvent.qualifying()` accepts the attempt ID and the scheduler passes it through to the persisted episode observation. |
| The all-eligible daemon measurement loop scored coins sequentially and gave no progress until the full cycle ended. | A 582-coin cycle took too long to diagnose and could appear stalled; a bounded check accumulated attempts slowly. | Measurement scoring now uses a configurable worker pool (the daemon uses the scan’s 12-worker setting), while serializing journal writes in deterministic eligible order. The daemon logs scoring progress every 50 coins and reports feed/eligibility counts before scoring. |
| Full-universe collection and outcome resolution ran synchronously ahead of measurement. | Collection took about 9 minutes and the resolver walked thousands of outcomes; either could push measurement gaps past calibration limits. | Collection now uses eight workers under the shared MEXC request limiter. Outcome resolution runs in a single background worker with an isolated SQLite connection, so it cannot block the measurement loop or start duplicate resolver passes. |

## Regression coverage added or updated

- Added [`tests/test_episode_regressions.py`](/home/bamzhie/alt-radar/tests/test_episode_regressions.py) to cover frozen plans, invalid plans, candle-grid alignment, collector timestamp normalization, malformed OHLC rejection, the `after_gap` marker, and cohort separation.
- Updated [`tests/test_episode_outcomes.py`](/home/bamzhie/alt-radar/tests/test_episode_outcomes.py) with exchange-aligned timestamps and valid short-side OHLC fixtures. The stricter validator correctly rejected a previously malformed fixture.
- Updated [`tests/test_episode_reporting.py`](/home/bamzhie/alt-radar/tests/test_episode_reporting.py) to require separate results for mixed cohorts.
- Updated [`tests/test_mtf.py`](/home/bamzhie/alt-radar/tests/test_mtf.py) to verify transient MTF fetch failures mark measurement quality as degraded.

## Verification

- `python3 -m proto.daemon --db <fresh-temp-db> --measure-status` — confirmed it exits read-only, reports calibration `FAIL`, and leaves the epoch `UNSET`.
- `python3 -m proto.daemon --db <fresh-temp-db> --enable-measurement-epoch` — confirmed explicit activation is refused while calibration fails. A direct SQLite check confirmed `episode_epoch_ts` remains unset.
- `python3 tests/test_epoch_integration.py` — passed, including sparse-cadence refusal, clean-cadence activation, immutable epoch, and CLI-surface checks.
- `python3 tests/run_all.py` — the aggregator finds **24 files**: **23 pass**, and the Qt GUI file cleanly skips because PySide6 is unavailable. It intentionally excludes `tests/test_app.py`.
- `python3 tests/test_app.py` — **live MEXC scan passed** after retrying with network access. It loaded the 150-coin universe, produced scan cards and directional signals in under 90 seconds, wrote new signal rows, and generated seven valid plans. The smoke test used its temporary SQLite database, not the project database.
- Bounded `python3 -m proto.daemon --no-scan --iterations 1` probe using a temporary DB — refresh succeeded with **1,207 tickers, 1,199 detail rows, and 582 eligible coins**. The completed parallel measurement cycle recorded **582 attempts**: 45 qualifying, 521 non-qualifying, and 16 unknown (8 fetch failures, 8 degraded inputs). This confirms the current `--no-scan` path populates its measurement feed and attempts every eligible coin. The probe skipped collection and legacy resolution.
- Calibration correctly remained **FAIL** in that one-cycle probe: no coin had a repeated successful observation yet, so there were no gap quantiles. No epoch was enabled.
- The updated branch was loaded by the existing `altradar.service` (`--no-scan`). Production collection completed in 70 seconds, and two full measurement cycles each attempted 582 coins. The second cycle reported calibration **PASS** with p95 **363s** and p99 **365s**. Direct `--measure-status` reported 1,164 attempts across 571 observed coins, p50 361s, p95 363s, p99 365s, **calibration PASS**, and epoch **UNSET**. The service remains active; the epoch was not enabled.
- `git diff --check` — passed.
- The first sandboxed `tests/test_app.py` attempt failed at DNS resolution before the scan. The network-enabled retry passed. The bounded daemon probe verifies one current-code `--no-scan` measurement cycle against the venue; it does not verify repeated cadence on the long-running service.
- The live smoke test previously checked only that its fixture database contained rows, while logging was disabled during the scan. The test now enables logging for the live section and asserts that the scan increases the row count; the corrected test passed on its second network-enabled run.
- Qt GUI tests were cleanly skipped because PySide6 is not installed in the active interpreter. The changes reviewed here are in the measurement/resolver/reporting path; this does not establish Qt runtime behavior.

### Epoch control conclusion

The `--measure-status` and `--enable-measurement-epoch` options from `db0c3a1` remain present and functional in this working tree. Activation remains an explicit operator action and is refused until cadence calibration passes. A successful status check or the presence of the option does not itself enable the epoch.

## Remaining boundary

Episode summaries and cohort breakdowns are available through the reporting module, but this follow-up did not add an episode analytics view to the GUI. The implementation keeps the report layer read-only and does not feed measured outcomes back into live scoring. The CLI live-scan smoke test and one bounded no-scan measurement cycle passed; Qt GUI behavior and repeated cadence on the long-running service remain unverified. Any UI presentation or later scoring change should be reviewed as separate work, with the measurement rules versioned before comparing new results to this baseline.

## Working-tree state

The follow-up code and tests are in the working tree and are not committed or pushed. The checked-out `HEAD` remains `db0c3a1`.
