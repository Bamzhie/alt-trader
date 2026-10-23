# ALT Trader (alt-radar)

A read-only crypto perpetual-futures data collection and archive system. It
collects 5-minute MEXC USDT perpetual bars into `data/bars/` plus the
funding, settlement, and price archives, and serves a neutral loopback API.
It never places orders, never holds API keys, and never touches funds.

**It never places orders, never holds API keys, never touches funds.** There is
no order code path anywhere in this repository.

## What it does

- **Collects** 5-minute MEXC USDT perpetual bars into `data/bars/`, plus the
  funding, settlement, and mark/index price archives that support research.
  Collection runs continuously and unconditionally under systemd — it is not
  gated on a scan.
- **Serves** a neutral, read-only loopback API (`/healthz`, `/readyz`,
  `/collector-health`, `/bars`, `/funding`, `/settlements`, `/prices`) for the
  native dashboard.
- **No scanning, scoring, planning, outcome resolution, or candidate
  emission.** The legacy rule/scanner pipeline has been removed; the quant
  engine is in development and does not yet generate or surface trading
  candidates. This README describes the collection backend and the neutral
  status dashboard.

    not yet an implemented quant engine.

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

Search for **ALT RADAR** in the application menu. The dashboard reads the
local bar/archive data served by the collection-only service and shows a
single Status page. The status page states explicitly that the quant engine
is in development and does not emit candidates; no legacy signal surfaces are
displayed.

To troubleshoot the backend:

```bash
systemctl --user status altradar.service
journalctl --user -u altradar.service -n 100 --no-pager
```

### Removed legacy interfaces

The scanner, signal journal, outcome resolution, and legacy UIs were retired
as part of the quant-engine architecture cleanup. The following interfaces no
longer exist:

```bash
# Removed: terminal scanner UI (proto.app), GUI/tkinter (gui), Qt GUI (qtgui)
# Removed: headless scan + outcome resolver (proto.daemon), signal reports (proto.report)
```

### Tests

```bash
python3 tests/run_all.py     # offline suites (no network needed)
```

## How it works (30-second version)

`systemd → proto.service (bar/funding/settlement/price collector + neutral API) → bar archive + app/README.md`

- `proto/service.py` runs as the systemd-owned backend. It collects MEXC
  bar/funding/settlement/price archives on a fixed cadence and serves the
  loopback read API. It no longer scans, scores, resolves outcomes, or
  emits candidates.
- `proto/collection.py` and `proto/collector.py` perform bar collection;
  `proto/funding.py`, `proto/settlements.py`, and `proto/prices.py`
  maintain the archive feeds.
- `proto/quant/` is an offline historical research toolkit (not wired into
  the runtime); it has no live watch/emit command.
- `app/` is the supported React frontend; `app/src-tauri/` packages it as a
  native desktop window. It reads the collection service and does not own its
  lifecycle.

## Project layout

```
proto/      collection service, bar/funding/settlement/price collectors, universe
app/        React frontend and Tauri native desktop shell
tests/      offline test suites
docs/       quant-engine overhaul plan and design references
data/       bar archive + SQLite DBs (created at runtime, git-ignored)
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
