# ALT Trader (alt-radar)

A read-only crypto perpetual-futures scanner and measurement system. It watches
MEXC USDT perpetuals (with Bybit open-interest confirmation), surfaces early
directional signals with full trade plans, and — most importantly — logs every
signal, plan, and outcome so performance can be measured instead of guessed.

**It never places orders, never holds API keys, never touches funds.** There is
no order code path anywhere in this repository.

## What it does

- **Scans** ~580 MEXC USDT perpetuals on a configurable cadence, scoring each
  coin 0–100 on early abnormal buyer/seller activity (volume/price expansion,
  order-book imbalance, OI/funding), with LONG / SHORT / NEUTRAL direction.
- **Plans** every qualifying signal: direction, limit entry band, structural
  stop, 2R/5R targets, computed leverage, size, costs, and warnings — for
  human review only.
- **Measures** everything it claims: signal journal, frozen plans, forward
  outcomes (1h/4h/24h/7d), stop/TP1/TP2 first touches, and episode-based
  cohorts with explicit denominators. No net-P&L is ever reported — the
  system reports touch rates and hit rates, honestly labeled.
- **Displays** it all in a tkinter desktop GUI, a PySide6/Qt desktop GUI, or a
  terminal UI.

Current status: the detector is **unvalidated** — scores measure abnormal
activity, not edge. The outcome log being collected is what will calibrate
thresholds. See `docs/quantitative-audit-2026-10-09.md` for an independent
audit of the pipeline, limitations included.

## Requirements

- **Python 3.10+** (stdlib only for the core — zero pip dependencies)
- **Linux** recommended (the 24/7 collector uses a systemd user service;
  anything POSIX works for interactive use)
- **tkinter** for the default GUI — on Debian/Ubuntu: `sudo apt install python3-tk`
- **Internet access** to MEXC and Bybit public REST endpoints (no accounts, no keys)
- ~100 MB disk to start; data grows ~5 MB/day

## Install

```bash
git clone https://github.com/Bamzhie/alt-trader.git
cd alt-trader
python3 --version        # need 3.10+
python3 -c "import tkinter; print('gui ok')"   # or: sudo apt install python3-tk
```

That's it for the core app. For the Qt interface (optional):

```bash
python3 -m venv .venv-qt
.venv-qt/bin/pip install PySide6-Essentials
```

## Usage

```bash
# Desktop GUI (tkinter, no extra installs)
python3 -m gui

# Desktop GUI (Qt, modern dark terminal look)
.venv-qt/bin/python -m qtgui

# Terminal UI (curses)
python3 -m proto.app --stake 0.10 --coins 150 --interval 60

# One-shot headless scan (cron/systemd/pipes)
python3 -m proto.app --headless --iterations 1

# 24h hit-rate report (flagged signals: won/lost/%won/avg + plan touches)
python3 -m proto.report --hours 24
python3 -m proto.report --top20 --days 7     # daily top-20 review

# Unattended data collection (scan + 5m bars + hourly outcome resolution)
python3 -m proto.daemon
python3 -m proto.daemon --no-scan            # collector + resolver only
```

### 24/7 collection (systemd)

A user service file is included (`altradar.service.example` — copy it to
`~/.config/systemd/user/altradar.service` and edit the two paths), then:

```bash
cp altradar.service.example ~/.config/systemd/user/altradar.service
# edit WorkingDirectory= and ExecStart= in that file to match your checkout
systemctl --user daemon-reload
systemctl --user enable --now altradar.service
systemctl --user status altradar.service
journalctl --user -u altradar.service -f
```

With `enable-linger` the service survives logout and starts at boot. Run it in
`--no-scan` mode alongside an open GUI to avoid double-logged signals, or in
full mode when the GUI is closed.

### Tests

```bash
python3 tests/run_all.py     # all 24 offline suites (no network needed)
python3 tests/test_app.py    # live venue smoke test (needs internet)
```

## How it works (30-second version)

`feeds → universe → scorer → planner → interfaces + SQLite`

- `proto/mexc.py`, `proto/bybit.py` — public REST adapters with envelope,
  shape, timestamp-order, and OHLC validation. MEXC supplies price/candles/
  book; Bybit supplies open-interest history for shared symbols.
- `proto/scan.py` — stake-aware universe rotation (tradeable-first + degen
  tail + sweep), per-coin fetch/score with counted (never silent) failures.
- `proto/scorer.py` — signal-quality score + symmetric LONG/SHORT lean.
- `proto/planner.py` — computed (never defaulted) leverage, risk-capped size,
  funding-aware costs, surfaced warnings.
- `proto/measurement.py` — episode lifecycle (signal episodes as the unit of
  performance), gated behind an explicit calibration epoch that is **off by
  default** and refuses to enable until cadence calibration passes.
- `proto/outcomes.py`, `proto/collector.py`, `proto/report.py`,
  `proto/episode_report.py` — forward outcomes, 5m bar archive, hit-rate and
  cohort reports.
- `gui/`, `qtgui/` — tkinter and Qt front ends over the same engine.

## Project layout

```
proto/      scanner engine, planner, measurement, daemon, reports
gui/        tkinter desktop app (stdlib only)
qtgui/      PySide6 desktop app (needs .venv-qt)
tests/      24 self-checking suites (run_all.py aggregates them)
docs/       manual (GUI_MANUAL.md), independent audit, design specs
data/       SQLite DB + bar archive (created at runtime, git-ignored)
```

## Contributing

Issues and pull requests welcome. Ground rules, enforced by review:

1. Read-only stays read-only — no order placement, no key storage, no
   execution code, ever.
2. Scoring changes must come with offline tests and must not silently change
   logged semantics mid-collection (version the rules instead).
3. No invented data: every displayed number traces to a venue response or a
   logged row; estimates are labeled as estimates.
4. `python3 tests/run_all.py` must stay green.

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, trade at your own risk: this
software has no demonstrated edge and its authors offer no financial advice.
