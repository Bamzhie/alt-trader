# Tauri Desktop Design — React frontend + headless Python service (24/7)

**Date:** 2026-10-14
**Status:** Spec (Sections 1–4 approved in chat 2026-10-14)
**Sources:** operator migration prompt (13 sections, Tauri 2 + React/TS/Vite/Tailwind/shadcn); dashboard mockup; `docs/superpowers/specs/2026-10-13-trader-dashboard-design.md` (2089540+a7aa724 — binding overrides: NO confidence/tiers/probability, no 1m, no TOTAL row, no leverage headroom, hypothetical-only Trades); backend inventory: `proto/scan.py`, `mexc.py`, `bybit.py`, `indicators.py`, `scorer.py`, `watcher.py`, `planner.py`, `store.py`, `collector.py`, `outcomes.py`, `daemon.py` (no HTTP/API/IPC layer exists — stdlib-only core, no FastAPI/uvicorn/PyInstaller installed).
**Scope:** New `app/` folder (Tauri shell + React frontend); new headless `proto/service.py` (stdlib HTTP+SSE); always-on collection via persistent user service with attach-first/spawn-fallback; old frontends (`gui/`, `qtgui/`, TUI) runnable until acceptance. README rules apply (read-only, no orders/keys, no invented data, no edge claims).

## 1. Headless service contract

- `proto/service.py`, stdlib only (`http.server` + SSE). Bind `127.0.0.1`; `--port` default 18741 with fallback scan; per-launch bearer token (stdout for Tauri capture, never stored) except `/healthz`; service mode uses config-file token (`0600`).
- Routes (JSON, explicit `null`, `ts` + ids everywhere): `GET /healthz` (ready only after first sweep), `GET /snapshot`, `GET /setups?state=`, `GET /setup/:id`, `GET /bars?coin=&tf=` (5m/15m/1H/4H/1D closed only), `GET /metrics?coin=`, `GET /tradeplan/:id` (100U reference, on demand), `POST /commands/{pause,resume,disable-trigger,enable-trigger}` (idempotent, reload-safe).
- Events (`GET /events`, SSE, `Last-Event-ID`): `setup.emitted/invalidated/expired/resolved`, `sweep.completed` — transitions only, never per refresh; client recovers snapshot-then-subscribe.
- Section-level `unavailable: reason` on exchange failure; rate-limit/backoff unchanged.

## 2. Frontend structure

- `app/src-tauri/` (Tauri 2, 1536×1024 default; spawns/supervises service only in fallback mode; readiness-gated connected state; crash reporting + bounded restarts; graceful shutdown; `.AppImage`/`.deb`).
- `app/src/` (React+TS+Vite; Tailwind tokens = 12 spec hexes as CSS vars; shadcn components; Lightweight Charts only — no Recharts; Zustand selection/filters/tabs; TanStack Query snapshot/bars + SSE hook). Pages mirror the six dock pages. EvidenceBar (criteria chips) instead of ConfidenceBar. Row click = selection only, never backend state. Frontend computes no signals/indicators beyond rendering.

## 3. Always-on collection (revised — overrides prompt's no-daemon default per operator justification)

- Systemd user service (existing pattern, `Restart=always` + linger) is the persistent collector/API host. App attaches first (lockfile + `/healthz` probe); spawns its own backend only when none answers; shuts down only what it spawned. `fcntl` lockfile guarantees one scanner; UI shows attached mode ("via 24/7 service" vs "local session backend").
- Installer enables linger + service by default (opt-out documented). Shared `data/` path/DB/schema untouched — no migration, no new DB. UI prefs (stars, display) in XDG config dir. Env/config names preserved.

## 4. Verification

- Backend: `tests/run_all.py` green unchanged + service tests (typed shapes, nulls, SSE order, idempotent commands, reconnect no-duplicates, rate-limit/error paths, clean shutdown).
- Frontend: render/selection/filters/tabs/states/null-safety, mocked service, no network.
- Integration matrix: attach-no-spawn; spawn-scan-clean-shutdown-zero-orphans; duplicate-launch refusal; crash → honest disconnected; close-during-scan DB consistency.
- Build/install: `tsc` + lint, frontend build, Tauri bundle, unit enablement in fresh session, packaged-app test, system-lib docs.

## Open decisions

- Exact default port (18741 provisional; conflict scan specified).
- Snapshot spacing/retention for rolling comparisons.
- `EV_hurdle`/K/tiers stay deferred per 2089540 (no probability source).
