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
- **Displays** live service data in a React dashboard packaged as a native
  Tauri desktop app. The scanner and API run independently under systemd;
  closing the window does not stop collection.

Current status: the legacy detector is **unvalidated** — scores measure
abnormal activity, not edge. Quant research capabilities exist, but the new
owner-directed engine overhaul is not implemented. Its current requirements
are in [`docs/quant-system-overhaul-plan.md`](docs/quant-system-overhaul-plan.md)
and status in [`docs/roadmap-status.md`](docs/roadmap-status.md). During this
development phase, no rules may emit candidates; only `data/bars/` is the new
engine's historical input.

## Current project context

- This README describes the implemented backend and desktop architecture.
- [`app/README.md`](app/README.md) covers the React/Tauri frontend.
- [`docs/superpowers/specs/2026-10-14-react-backend-launch-design.md`](docs/superpowers/specs/2026-10-14-react-backend-launch-design.md) records the systemd-owned backend and native desktop boundary.
- [`docs/quant-system-overhaul-plan.md`](docs/quant-system-overhaul-plan.md) is the authoritative target for the new bars-only quant engine.
- [`docs/quant-analytics-system-proposal.md`](docs/quant-analytics-system-proposal.md) is an older proposal retained for history; it is superseded for current requirements.

Historical implementation reports, hypotheses, and design records are retained
as evidence; use the overhaul plan and current roadmap for active requirements.

## Requirements

- **Python 3.10+** (stdlib only for the core — zero pip dependencies)
- **Linux** recommended (the 24/7 collector uses a systemd user service;
  anything POSIX works for interactive use)
- **systemd user services** for the always-on backend on Linux
- **Rust, Node.js, and Linux WebKitGTK development libraries** only when
  building the desktop app from source; end users can install its package
- **Internet access** to MEXC and Bybit public REST endpoints (no accounts, no keys)
- ~100 MB disk to start; data grows ~5 MB/day

## Install

```bash
git clone https://github.com/Bamzhie/alt-trader.git
cd alt-trader
python3 --version        # need 3.10+
```

## Usage

### Native desktop app and always-on backend (normal workflow)

Install and start the user service once. Edit `WorkingDirectory` and
`ExecStart` in the example unit to match your checkout and Python path.

```bash
mkdir -p ~/.config/systemd/user
cp altradar.service.example ~/.config/systemd/user/altradar.service
systemctl --user daemon-reload
systemctl --user enable --now altradar.service
sudo loginctl enable-linger "$USER"
systemctl --user status altradar.service
journalctl --user -u altradar.service -f
```

Build the native app from source (requires Tauri's Linux build dependencies):

```bash
cd app
npm install
npm run tauri -- build --bundles deb
```

Install the generated `.deb` from `app/src-tauri/target/release/bundle/deb/`
to add ALT RADAR to the desktop application menu. Launching the app opens a
native window and connects to the systemd service; it does not open a browser
or start another backend. AppImage packaging can be requested on machines
with `linuxdeploy` installed using `npm run tauri -- build --bundles appimage`.
On other Linux distributions, run the installer after the build to add a
native launcher to the application menu and desktop:

```bash
bash app/install-user-app.sh
```

Search for **ALT RADAR** or **ALT Trader** in the application menu. The
dashboard reads the existing local database and bar files. Its connection
indicator reflects API reachability and the event stream; scanner first-sweep
progress is separate, so a new scan does not hide data already on disk.

To troubleshoot the backend:

```bash
systemctl --user status altradar.service
journalctl --user -u altradar.service -n 100 --no-pager
```

### Legacy interfaces

The Python interfaces remain in the repository for existing users, but are
no longer the supported desktop launch path:

```bash
# Desktop GUI (tkinter)
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

### Tests

```bash
python3 tests/run_all.py     # all 24 offline suites (no network needed)
python3 tests/test_app.py    # live venue smoke test (needs internet)
```

## How it works (30-second version)

`systemd → proto.service (scanner + collector + resolver + API) → SQLite and bar archive → Tauri/React dashboard`

- `proto/service.py` runs as the systemd-owned backend and serves the local
  authenticated HTTP/SSE API. It embeds the scanner, collector, and resolver.
- `proto/scan.py`, `proto/scorer.py`, and `proto/planner.py` are the current
  legacy signal pipeline. Its scores are not validated estimates of edge.
- `proto/collector.py`, `proto/outcomes.py`, and `proto/measurement.py`
  maintain bar history and outcome measurements used for research.
- `app/` is the supported React frontend; `app/src-tauri/` packages it as a
  native desktop window. It reads the existing service and does not own its
  lifecycle.
- The intended quant-first direction and staged validation requirements are
  in `docs/quant-analytics-system-proposal.md`. That proposal is not yet an
  implemented quant engine.

## Project layout

```
proto/      scanner service, collectors, measurement, reports
app/        React frontend and Tauri native desktop shell
tests/      offline test suites
docs/       current quant-analysis proposal and systemd/Tauri design reference
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
