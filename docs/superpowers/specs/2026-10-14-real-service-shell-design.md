# Real-Service + Standalone Shell Design

**Date:** 2026-10-14
**Status:** Spec (Sections 1–4 approved in chat 2026-10-14)
**Sources:** Tauri desktop spec (`2026-10-14-tauri-desktop-design.md`); headless-service plan (`2026-10-14-headless-service.md`, Task 1 done at `b9d355f`); frontend app (`app/`, mock `app/dev/mock_service.py`); operator approval of staged Tauri (service → rewire → shell + launcher; PyInstaller + release installer deferred).
**Scope:** Finish `proto/service.py` Tasks 2–5; rewire React client to the real service; Tauri 2 shell with external sidecar + `.desktop` launcher. No engine changes; no ML/probability outputs; read-only; no orders/keys.

## 1. Finish the real service

- Resume `headless-service` branch at `b9d355f` under the existing plan: Task 2 routes/envelope, Task 3 SSE/reconcile, Task 4 commands, Task 5 daemon embedding + lockfile. Briefs, tests, and gates stand as written. Mock untouched until rewire. Exit: final branch review, merge to `main`.

## 2. Rewire to the real service

- Config-only client switch: `baseUrl` → real service origin, token from local dev config (never bundled); SSE hook unchanged. Conformance run of the fixture suite against the real service (loopback, temp DB, real writers). Mock retires to offline-fixture duty, documented; no dual maintenance.

## 3. Tauri shell + launcher

- `app/src-tauri/` (Tauri 2): 1536×1024 window, bundled `app/dist`; external sidecar `python3 -m proto.service --port <free>` (no PyInstaller this stage); stdout token → memory; `/readyz`-gated connected state; crash → honest disconnected + 3 bounded restarts; close → SIGTERM + orphan check.
- Attach-first (probe before spawn; lockfile single scanner); UI badge shows attached backend. Debug bundle first. `.desktop` entry (icon, Exec to bundle) + install docs (user applications dir). PyInstaller + release installer deferred.

## 4. Verification + rollout

- Service gates + branch review + merge; mock-vs-real differential before mock retires; frontend suite green against real service; `tsc`/lint/build clean; no `dev/mock` imports outside `app/dev` + tests.
- Shell matrix: cold launch connected; kill → disconnected; reload → no respawn; close → zero orphans; duplicate → attach, no second scanner; `.desktop` clean-session launch.
- Rollout: service merge → rewire → shell → launcher; Qt/TUI untouched; packaging later.

## Open decisions

- Default port 18741 provisional (conflict scan specified).
- Debug vs release bundle for the first launcher (default: debug).
