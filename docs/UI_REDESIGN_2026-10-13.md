# ALT RADAR UI redesign notes (2026-10-13)

## Changes in this refactor

- Removed the legacy `VETOED — excluded from ranking` table from the visible Qt scanner layout.
  The hidden table widget remains instantiated only to keep existing legacy selection
  helpers and compatibility tests safe; it is no longer an operator-facing feed.
- Expanded the primary Signals table to use the space previously occupied by veto rows.
- Renamed the dashboard dock to `Active Setups — persistent signals` to distinguish
  watcher-managed setup lifecycle from the legacy scanner's per-scan candidates.
- Renamed the multi-page dock to make the dashboard/watchlist/analysis workspace clearer.

## Important behavior distinction

The persistent watcher dashboard is the intended view for setups that are emitted and
then monitored through invalidation/expiry/resolution. The legacy scanner view remains
available for historical/diagnostic compatibility. This UI-only refactor does not change
signal calculation, watcher rules, persistence, or order behavior. The application remains
read-only and does not place orders.

## Verification

- `python3 -m py_compile qtgui/app.py` passes.
- Qt-specific tests were cleanly skipped in the available interpreter because PySide6 is
  not installed.
- `python3 tests/run_all.py` did not complete within the execution time limit; full suite
  status is therefore unverified.
