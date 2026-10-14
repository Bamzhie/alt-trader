# Real Service + Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the real headless service, rewire the React frontend to it, and ship a Tauri desktop shell with a `.desktop` launcher.

**Architecture:** Resume the `headless-service` branch through its existing plan (Tasks 2–5) and merge; switch the React client to the real service with a conformance run; add `app/src-tauri/` whose sidecar spawns `python3 -m proto.service` and exposes one `get_service_config` invoke command (port + token) so the token never touches disk, bundles, or web storage.

**Tech Stack:** Python 3.10+ stdlib; React+TS+Vite (existing `app/`); Tauri 2 + `@tauri-apps/plugin-shell`; self-checking script tests via `tests/run_all.py`; vitest for frontend.

**Spec:** `docs/superpowers/specs/2026-10-14-real-service-shell-design.md` (commit `9a1bcef`; executors read both) plus `docs/superpowers/plans/2026-10-14-headless-service.md` for service Tasks 2–5 (briefs already written; branch `headless-service` at `b9d355f`).

## Global Constraints

- Read-only stays read-only — no orders, no keys, no execution code; loopback bind only.
- NO ML/probability outputs; no invented data (explicit `null`, honest unavailable states).
- Closed bars only; rate-limit/backoff unchanged; no profitability/edge claims.
- Token in memory only — never in logs, bundles, localStorage, or git.
- `python3 tests/run_all.py` must stay green; frontend `tsc` + lint + build clean; UI tests zero-network (mocked service + tripwire).
- Old Qt/TUI frontends untouched; PyInstaller + release installer deferred.

## Review Focus

- A second scanner running beside the service — expect lockfile refusal with no duplicate writes.
- The token reaching disk, bundle, or logs — expect grep tests over `app/dist`, logs, and config writes.
- A reconnect duplicating or dropping a setup transition — expect the reconcile test with a mid-recovery injection.
- App close leaving the spawned backend alive — expect process-table assertions after close.
- A mocked-service import outside `app/dev` and tests — expect the import-boundary test to fail on it.

---

### Task 1: Finish the real service (Tasks 2–5)

**Files:**
- Work on branch: `headless-service` (resume at `b9d355f`)
- Per `docs/superpowers/plans/2026-10-14-headless-service.md` Tasks 2–5 (routes, SSE, commands, daemon embedding)

**Interfaces:**
- Consumes: Task 1 interfaces (`serve`, `load_or_create_token`, envelope helpers) — already reviewed.
- Produces: merged-to-`main` service implementing the full §1 contract, `run_all.py` green.

- [ ] **Step 1: Resume the existing plan's Task 2 on the `headless-service` branch**

Run the plan's task loop (briefs via `task-brief` script, per-task review, fix loop, final branch review) exactly as that plan specifies.

- [ ] **Step 2: Verify the full service contract against the spec §1 checklist**

Run: `python3 tests/run_all.py` plus a live smoke (`/healthz`, `/readyz`, authed snapshot, SSE event, command round-trip).
Expected: ALL SUITES PASS; smoke shows envelope shapes with explicit nulls.

- [ ] **Step 3: Merge to `main`**

```bash
git checkout main && git merge headless-service --no-edit
```

Only after the final branch review is clean.

### Task 2: Rewire frontend + conformance

**Files:**
- Modify: `app/src/api/client.ts` (baseUrl/token source), `app/dev/*` (mock role docs only)
- Test: `app/tests/test_conformance.test.ts` (new; runs fixture suite against real service)

**Interfaces:**
- Consumes: Task 1 (real service on loopback, temp DB seedable via real writers).
- Produces:
  - `createClient({ baseUrl, token })` reading `VITE_SERVICE_URL` + untracked `app/.env.local` (`VITE_SERVICE_TOKEN`) in dev; no mock imports outside `app/dev` and tests
  - Conformance run: identical fixture assertions against mock AND real service

- [ ] **Step 1: Write the failing test**

```typescript
test("real service conforms", async () => {
  const c = createClient({ baseUrl: REAL_URL, token: REAL_TOKEN });
  expect((await c.snapshot()).setups).toEqual(fixtureSnapshot().setups);
});
```

plus: `grep -r "dev/mock_service" app/src --include=*.ts*` empty (import-boundary assertion in the test file header comment, enforced by eslint no-restricted-imports).

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- test_conformance` in `app/`
Expected: FAIL (client points at mock).

- [ ] **Step 3: Implement the client switch + mock-role docs**

Token from env only; document `app/dev/` as offline-fixture duty.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test`, `npm run typecheck`, `npm run lint`, `npm run build`
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add app/src app/dev app/tests
git commit -m "feat(app): rewire client to real service with conformance run"
```

### Task 3: Tauri shell + sidecar

**Files:**
- Create: `app/src-tauri/` (`Cargo.toml`, `tauri.conf.json`, `src/main.rs`, capabilities)
- Modify: `app/package.json` (tauri scripts + `@tauri-apps/cli`, `@tauri-apps/plugin-shell` — only new deps)
- Test: shell-level verification script (not vitest): `scripts/verify_shell.py` launching debug bundle against temp DB

**Interfaces:**
- Consumes: Tasks 1–2 (real service spawnable as `python3 -m proto.service --port <free>`).
- Produces:
  - `get_service_config` invoke command → `{port: u16, token: String}` (sole token path; frontend bootstraps via invoke before first fetch)
  - Attach-first: probe running service before spawn; lockfile single scanner; UI badge shows attached backend
  - `/readyz`-gated connected state; crash → honest disconnected + 3 bounded restarts; close → SIGTERM + orphan check

- [ ] **Step 1: Scaffold and write the failing verification**

Run: `npm --prefix app create tauri-app --template react-ts` into place (or hand-scaffold if the generator needs network beyond npm); write `scripts/verify_shell.py` asserting launch→connected→kill→disconnected→close→no-orphans.
Expected: FAIL (no shell yet).

- [ ] **Step 2: Implement shell + sidecar + invoke command**

`@tauri-apps/plugin-shell` scoped to `python3` + `proto.service` args only (no unrestricted shell). Window 1536×1024, bundled `app/dist`.

- [ ] **Step 3: Verify**

Run: `scripts/verify_shell.py` + `cargo tauri build --debug`
Expected: PASS; bundle produced; token absent from `app/dist` (grep).

- [ ] **Step 4: Commit**

```bash
git add app/src-tauri app/package.json app/src scripts/verify_shell.py
git commit -m "feat(app): Tauri shell with supervised service sidecar"
```

### Task 4: Launcher + install docs + matrix

**Files:**
- Create: `app/alt-radar.desktop`, icon asset (redrawn from Qt logo, no binary blobs from the mockup), `docs/TAURI_INSTALL.md`
- Test: clean-session launch test (new user env with `HOME` override): `.desktop` Exec launches, duplicate launch attaches, close leaves zero orphans

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces: installed launcher (`~/.local/share/applications/`), install/uninstall docs (service consent, pre-existing unit adoption, data preservation).

- [ ] **Step 1: Write the failing verification**

Extend `scripts/verify_shell.py`: clean-`HOME` launch via the `.desktop` Exec line; assert window-connected, second-launch-attaches, close-clean.
Expected: FAIL (no `.desktop`).

- [ ] **Step 2: Implement `.desktop` + icon + docs**

`Exec` points at the built bundle; icon installed beside it.

- [ ] **Step 3: Run full verification**

Run: `scripts/verify_shell.py`, `python3 tests/run_all.py`, `npm test`, `tsc`, build — ALL green.

- [ ] **Step 4: Commit**

```bash
git add app/alt-radar.desktop docs/TAURI_INSTALL.md scripts/verify_shell.py
git commit -m "feat(app): desktop launcher with clean-session verification"
```

## Self-Review

- Spec coverage: §1 service → Task 1 (via existing plan); §2 rewire → Task 2; §3 shell/launcher → Tasks 3–4; §4 verification → per-task gates + Task 4 matrix. Open decisions carried (port provisional, debug default).
- Step scan: test steps pin names/assertions; code steps pin signatures/files; no TBD.
- Type consistency: `serve/load_or_create_token`, envelope keys, `get_service_config`, `VITE_SERVICE_URL/VITE_SERVICE_TOKEN`, `snapshot_event_id` shared verbatim.
- Review Focus: all five lines owned (lockfile→T1; token→T3 grep; reconcile→T1 plan; orphans→T3/T4 scripts; mock boundary→T2).
- Proportion: signatures + assertions only; no bodies.
