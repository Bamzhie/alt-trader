# Tauri Desktop Design — React frontend + headless Python service (24/7)

**Date:** 2026-10-14
**Status:** Spec (Sections 1–4 approved in chat 2026-10-14)
**Sources:** operator migration prompt (13 sections, Tauri 2 + React/TS/Vite/Tailwind/shadcn); dashboard mockup; `docs/superpowers/specs/2026-10-13-trader-dashboard-design.md` (2089540+a7aa724 — binding overrides: NO confidence/tiers/probability, no 1m, no TOTAL row, no leverage headroom, hypothetical-only Trades); backend inventory: `proto/scan.py`, `mexc.py`, `bybit.py`, `indicators.py`, `scorer.py`, `watcher.py`, `planner.py`, `store.py`, `collector.py`, `outcomes.py`, `daemon.py` (no HTTP/API/IPC layer exists — stdlib-only core, no FastAPI/uvicorn/PyInstaller installed).
**Scope:** New `app/` folder (Tauri shell + React frontend); new headless `proto/service.py` (stdlib HTTP+SSE); always-on collection via persistent user service with attach-first/spawn-fallback; old frontends (`gui/`, `qtgui/`, TUI) runnable until acceptance. README rules apply (read-only, no orders/keys, no invented data, no edge claims).

## 1. Headless service contract

- `proto/service.py`, stdlib only (`http.server` + SSE). **One process does both jobs:** the service embeds the existing daemon loop (scan/collect/resolve, same code paths as `proto.daemon`) plus the HTTP/SSE server on threads — the systemd unit's `ExecStart` points at `python3 -m proto.service`, and `proto.daemon` remains only as a thin legacy entrypoint that execs the service headless. Ownership is singular: the service process owns scanning (existing single-scanner `fcntl` lockfile), DB access (same `Store`), restart (systemd), and shutdown (signal handler stops loop, server, exchange sessions in order). A UI-spawned fallback runs the identical binary, so attach probing works uniformly — no configuration runs two collectors.
- Bind `127.0.0.1`; `--port` default 18741 with fallback scan. **Tokens:** app-spawned backends use a per-launch bearer printed once to stdout for Tauri to capture; the persistent service uses a config-file token (`~/.config/altradar/service.token`, `0600`, auto-generated on first start if absent) read directly by the same-user Tauri app. Tokens live in memory only in the frontend — never in logs (server redacts `Authorization` from request logging), localStorage, or bundled assets. **Origin policy:** allowlisted origins only (Tauri asset origin + localhost dev port); CORS preflight answered only for those; mismatched `Origin`/`Host` on command routes → 403. All routes except `/healthz` require the bearer token.
- Liveness vs readiness: `GET /healthz` (no auth — process alive) vs `GET /readyz` (auth — first sweep complete AND DB writable). UI shows "starting — first sweep in progress" on alive-but-not-ready, "connected" only on ready.
- Routes (envelope `{ok, data, error:{code,message}}`; explicit `null`; timestamps UTC-seconds ints; ids everywhere): `GET /snapshot`, `GET /setups?state=&limit=&cursor=` (default 100, max 500), `GET /setup/:id`, `GET /bars?coin=&tf=` (5m/15m/1H/4H/1D closed only), `GET /metrics?coin=` (RSI/MACD/EMA50/EMA200/volume computed server-side from closed bars via existing `detail_metrics` — frontend never computes indicators), `GET /tradeplan/:id` (100U reference, on demand), `POST /commands/{pause,resume,disable-trigger,enable-trigger}` (idempotent, reload-safe). Error codes: `UNAUTH, BAD_REQUEST, NOT_FOUND, UNAVAILABLE, RATE_LIMITED`.
- Events (`GET /events`, SSE with monotonic ids, ring buffer last 500 or 30 min): `setup.emitted/invalidated/expired/resolved`, `monitoring.unavailable/ok`, `sweep.completed` — transitions only. Recovery is subscribe-first-then-reconcile: client opens the stream with `Last-Event-ID`, then fetches `GET /snapshot` carrying `snapshot_event_id`; events ≤ snapshot id are dropped, newer applied. Cursor below buffer floor → server sends `resync` → client re-snapshots. A transition landing between subscribe and snapshot is therefore never lost.
- Section-level `unavailable: reason` on exchange failure; rate-limit/backoff unchanged.

## 2. Frontend structure

- `app/src-tauri/` (Tauri 2, 1536×1024 default; spawns/supervises service only in fallback mode; readiness-gated connected state; crash reporting + bounded restarts; graceful shutdown; `.AppImage`/`.deb`).
- `app/src/` (React+TS+Vite; Tailwind tokens = 12 spec hexes as CSS vars; shadcn components; Lightweight Charts only — no Recharts; Zustand selection/filters/tabs; TanStack Query snapshot/bars + SSE hook). Pages mirror the six dock pages. EvidenceBar (criteria chips) instead of ConfidenceBar. Row click = selection only, never backend state. Frontend computes no signals/indicators beyond rendering.

## 3. Always-on collection (revised — overrides prompt's no-daemon default per operator justification)

- Systemd user service (existing pattern, `Restart=always` + linger) is the persistent collector/API host. App attaches first (lockfile + `/healthz` probe); spawns its own backend only when none answers; shuts down only what it spawned. `fcntl` lockfile guarantees one scanner; UI shows attached mode ("via 24/7 service" vs "local session backend").
- Installer shows an explicit persistent-service consent step (plain language: runs at login, collects 24/7, restarts on failure; default: enabled, one click to decline) and handles a pre-existing `altradar.service` (adopts it by pointing the unit at the service binary with a backup of the old unit; uninstall stops/disables only what the installer enabled and offers data removal). Shared `data/` path/DB/schema untouched — no migration, no new DB. UI prefs (stars, display) in XDG config dir. Env/config names preserved.

## 4. Verification

- Backend: `tests/run_all.py` green unchanged + service tests (typed shapes, nulls, SSE order, idempotent commands, reconnect no-duplicates, rate-limit/error paths, clean shutdown; token handoff + wrong/missing-token rejection + log redaction; CORS/Origin/Host rejection incl. preflight; SSE reconnect/replay, stale-cursor `resync`, transition-between-subscribe-and-snapshot not lost; liveness-true/ready-false during first sweep).
- Frontend: render/selection/filters/tabs/states/null-safety, mocked service, no network.
- Integration matrix: attach-no-spawn; spawn-scan-clean-shutdown-zero-orphans; duplicate-launch refusal; crash → honest disconnected; close-during-scan DB consistency; upgrade with pre-existing `altradar.service` (adopt + backup); uninstall removes only installer-enabled pieces.
- Frontend: render/selection/filters/tabs/states/null-safety, mocked service, no network.
- Integration matrix: attach-no-spawn; spawn-scan-clean-shutdown-zero-orphans; duplicate-launch refusal; crash → honest disconnected; close-during-scan DB consistency.
- Build/install: `tsc` + lint, frontend build, Tauri bundle, unit enablement in fresh session, packaged-app test, system-lib docs.

## Open decisions

- Exact default port (18741 provisional; conflict scan specified).
- Snapshot spacing/retention for rolling comparisons.
- `EV_hurdle`/K/tiers stay deferred per 2089540 (no probability source).
