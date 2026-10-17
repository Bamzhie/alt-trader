# Quant Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Vendor the ZIP's `proto/quant` research-and-shadow engine into the local app stream (daemon hook, service status route + event, Research page) with unpromoted rules permanently in shadow mode.

**Architecture:** Purely additive vendoring (no local file is replaced) plus three narrow local adaptations: a guarded daemon hook that can never fail a sweep, a read-only `GET /quant-status` route with a count-only `quant.shadow` SSE kind, and an Analysis-page Research view that labels everything SHADOW / insufficient-evidence. Quant keeps its own `quant.db`; archives stay append-only; costs never flow into episodes.

**Tech Stack:** Python 3.10+ stdlib only; React + TypeScript + Tailwind + Tauri 2 (existing app); self-checking backend tests; vitest frontend tests.

**Spec:** ZIP docs `docs/QUANT_MODULE.md` + `docs/quant-research-2026-10-12.md` (engine authority; verdict: no rule has passed) and local `docs/roadmap-status.md` WS5. On brief-vs-either-spec conflict, the local roadmap's honesty rules win.

## Global Constraints

- Python 3.10+; stdlib only for the core — zero pip dependencies.
- Read-only stays read-only — no order placement, no key storage, no execution code, ever.
- No invented data: unavailable is explicit `None`, never a zero and never a guess.
- Shadow output is never actionable, validated, predictive, or profitable in code, copy, or display.
- App copy bans: no `confidence` / `probability` / `tier` / `TOTAL` / `1m` / `leverage` / `liquidation` / `scanned` wording anywhere in Research UI. Uncertainty renders ONLY as interval endpoints labeled exactly `measured range (lower–upper)` with the static caption `range of measured outcomes, not a prediction` — defined here as the single approved uncertainty display; no other interval/probability-adjacent wording is permitted.
- App copy bans: no `confidence` / `probability` / `tier` / `TOTAL` / `1m` / `leverage` / `liquidation` / `scanned` wording anywhere in Research UI (uncertainty renders as `measured range (lower–upper)`).
- Tests are self-checking `tests/test_*.py` (loopback-only) and vitest with mocked fetch (loopback tripwire); `python3 tests/run_all.py`, `npm run test`, `typecheck`, `lint` stay green.
- Never sort or rewrite the raw archives; quant reads bars through its own order-independent loader.

## Availability states and watcher-to-SSE path (binding for Tasks 2–4)

Watcher states (single source of truth, from `QuantStore` meta + watermark):
`unseen` (watcher never ran — no watermark), `healthy`, `paused`
(manual or auto-pause flag set — including bars-missing/empty, which
the watcher itself reports with an explicit `auto_pause_reason` such as
"no BTC bars"), `unavailable` (quant.db itself unreadable).
Bars-health is intentionally NOT plumbed separately: the watcher's own
pause reason names the cause, and duplicating it would add surface for
no display gain. `GET /quant-status` returns 200 whenever the db is
readable (with a `status` field carrying one of the four states plus
an `insufficient-evidence` reason when counts are zero) and 503 only
when quant.db itself is unreadable.

Watcher-to-SSE path (exact): the service embeds daemon loop and HTTP
server in ONE process (`run_service`), so the daemon hook calls the
existing in-process `emit_event("quant.shadow", {emitted, resolved})`
directly at the hook site when `watcher.step()` reports new emissions;
`quant.shadow` is registered in `EVENT_KINDS` and flows through the
unchanged ring/`Last-Event-ID`/`resync` machinery. No polling, no new
threads, no per-bar emission (counts only, on new emissions).

## Integration cadence (binding for all tasks)

After each task's review comes back clean: push that task's commit(s)
with `git push origin main` and verify via `git ls-remote origin main`
matching local HEAD — per task, not batched at the end. Never amend
the checkpoint commit `3b14afe`; every task commit is separate.
WS4e deploy stays pending throughout: no service restart in this
stream under any circumstance.

## Review Focus

- Watcher pointed at a missing/empty bars dir: must report unavailable with zero events, never raise — pinned by Task 1's e2e and Task 2's guard test.
- Corrupt bar rows mid-file: coin drops to unavailable until rebuilt, other coins unaffected — pinned by Task 1 (vendored engine tests) — verify they cover it, add a case if absent.
- `quant.db` unwritable (permissions/full disk): watcher step and status route degrade to explicit unavailable, daemon loop and API unaffected — pinned by Task 2 and Task 3 tests.
- SSE reconnect with a below-floor `Last-Event-ID` after shadow events flowed: `resync` frame still emitted, new `quant.shadow` kind included — pinned by Task 3's stream test.
- `/quant-status` 503 (watcher never ran): Research page renders insufficient-evidence, never blank or stale — pinned by Task 4's frontend test.

---

### Task 1: Vendor quant module + tests + docs (additive only)

**Files:**
- Create (byte-identical copies from ZIP, then adapt only if tests demand): `proto/quant/*.py` (16 files), `tests/test_quant.py`, `tests/test_quant_engine.py`, `docs/QUANT_MODULE.md`, `docs/quant-research-2026-10-12.md`
- Test: vendored `tests/test_quant*.py` + `python3 tests/run_all.py`

**Interfaces:**
- Consumes: `data/bars` archives (same `ts,o,h,l,c,vol,amount` gzip layout both sides); nothing else.
- Produces: `python3 -m proto.quant {research,watch,report,status}` working offline; own `data/quant.db` (git-ignored, never `signals.db`); `REGISTERED` rule ids + `GATE` thresholds as vendored.

- [ ] **Step 1: Copy the files.** Copy `proto/quant/`, the two test files, and the two docs verbatim from the ZIP. No local file is modified or deleted in this task — verify with `git status --short` (only `??` additions).
- [ ] **Step 2: Run the vendored tests.** Run: `python3 tests/test_quant.py && python3 tests/test_quant_engine.py`. Expected: both `ALL PASS`. If a vendored test fails for a local-environment reason (not a real defect), adapt minimally and record why in the report; never weaken gates.
- [ ] **Step 3: Run the full suite.** Run: `python3 tests/run_all.py`. Expected: `ALL SUITES PASS` (previous count + 2).
- [ ] **Step 4: Bounded research run on fixture bars.** Run: `python3 -m proto.quant research` against a tmp dir of synthetic bars (never production `data/`), asserting: (a) dev and val event counts > 0, (b) the rendered report carries explicit denominator tokens (implementer cites the exact tokens from vendored `report.py`), (c) no holdout artifact is created and the report marks holdout unevaluated. Expected: exit 0 with all three assertions holding.
- [ ] **Step 5: Commit.** `git add proto/quant tests/test_quant.py tests/test_quant_engine.py docs/QUANT_MODULE.md docs/quant-research-2026-10-12.md && git commit -m "feat(WS5): vendor quant research-and-shadow engine (additive)"`

### Task 2: Daemon `--quant-shadow` hook (guarded, sweep-neutral)

**Files:**
- Modify: `proto/daemon.py` (argparse flags + lazy watcher init before the loop + guarded step after the measure block, before sweep accounting ~line 374)
- Test: `tests/test_quant_shadow_hook.py` (new; loop iterations with a failing watcher and a counting watcher against tmp bars/quant.db)

**Interfaces:**
- Consumes: `proto.quant.engine.Watcher(bars_dir, QuantStore(db))`, `.step()`; Task 1's module.
- Produces: `--quant-shadow/--quant-db/--quant-bars/--quant-equity` flags (same names/defaults as ZIP); per-iteration guarded step that never touches `sweep_ok/collect_ok/sweep_counts/last_*` and never calls `on_sweep`.

- [ ] **Step 1: Write the failing test.** Cover: watcher raising every iteration → loop still completes sweeps and `on_sweep` still fires with correct counts; counting watcher → called once per iteration, exceptions logged never raised; flags default off (no watcher without `--quant-shadow`).
- [ ] **Step 2: Run test to verify it fails.** Run: `python3 tests/test_quant_shadow_hook.py`. Expected: FAIL (no flags/hook yet).
- [ ] **Step 3: Implement the hook in `proto/daemon.py`.** Lazy imports only under the flag; init once pre-loop; step post-measure pre-accounting; `try/except Exception → print … quant FAILED`, no raise, no sweep-state contact.
- [ ] **Step 4: Run tests.** Run: `python3 tests/test_quant_shadow_hook.py` then `python3 tests/run_all.py`. Expected: `ALL PASS` then `ALL SUITES PASS`.
- [ ] **Step 5: Commit.** `git add proto/daemon.py tests/test_quant_shadow_hook.py && git commit -m "feat(WS5): guarded quant-shadow daemon hook"`

### Task 3: Service `GET /quant-status` + `quant.shadow` SSE kind

**Files:**
- Modify: `proto/service.py` (route dispatch + `_handle_quant_status`; `EVENT_KINDS` + emit on watcher emissions; quant.db path plumbing at `serve()` time like `funding_dir`)
- Test: `tests/test_quant_api.py` (new; loopback service, seeded tmp quant.db: auth, shape, empty-db, unwritable-db 503, SSE ordering/resync incl. the new kind)

**Interfaces:**
- Consumes: `QuantStore` reads (event counts, suppression top reasons, meta pause flags, `promotions.json` via `gates.promotion_for`); existing `_deny`/`envelope`/bearer gate/SSE ring.
- Produces: `GET /quant-status` → `{mode: "shadow", paused, config_hash, event_counts, suppression_top, promotions}` (empty-db → explicit zeros + `insufficient-evidence` reason, never 404); `quant.shadow` SSE frames carrying counts only (no levels, no advice).

- [ ] **Step 1: Write the failing test.** Cover: 401 without token; 200 shape on seeded db; empty db → zeros + insufficient-evidence; unwritable db path → 503 explicit; SSE emits `quant.shadow` in order with the ring, reconnect with below-floor id → `resync`.
- [ ] **Step 2: Run test to verify it fails.** Run: `python3 tests/test_quant_api.py`. Expected: FAIL (404 no such route).
- [ ] **Step 3: Implement route + event in `proto/service.py`.** Read-only handlers; quant.db path resolved once at `serve()`; emit call site in the daemon-facing path the implementer finds (watcher emissions surface via status polling — emit when counts change, never per-bar spam).
- [ ] **Step 4: Run tests.** Run: `python3 tests/test_quant_api.py` then `python3 tests/run_all.py`. Expected: `ALL PASS` then `ALL SUITES PASS`.
- [ ] **Step 5: Commit.** `git add proto/service.py tests/test_quant_api.py && git commit -m "feat(WS5): quant status route and shadow event"`

### Task 4: Research view + client + frontend tests

**Files:**
- Modify: `app/src/api/types.ts` (QuantStatus/QuantEvent types), `app/src/api/client.ts` (quantStatus() via get()+unwrap()), `app/src/pages/` (implement the Analysis stub as the Research view; no nav changes), `app/src/components/Sidebar.tsx` only if labels demand it (prefer untouched)
- Test: `app/tests/test_research.test.tsx` (new; mocked fetch, loopback tripwire)

**Interfaces:**
- Consumes: Task 3's `GET /quant-status` contract + `quant.shadow` SSE via the existing `useEvents` hook pattern.
- Produces: Research view showing SHADOW banner, event counts, suppression reasons with counts, promotion records, measured-range numbers, and insufficient-evidence states; banned-word scan clean; research output never rendered as setups and never mixed into Dashboard tables.

- [ ] **Step 1: Write the failing test.** Mocked fetch: populated status renders counts/reasons/promotions with SHADOW labeling; 503/empty renders insufficient-evidence (never blank); copy contains none of the banned words; no setup-table components render research rows.
- [ ] **Step 2: Run test to verify it fails.** Run: `cd app && npx vitest run tests/test_research.test.tsx`. Expected: FAIL (no view/client yet).
- [ ] **Step 3: Implement types + client + view.** Follow `snapshot()`/`bars()` patterns; reuse `useEvents`; allowed labels only: `SHADOW`, `not actionable`, `insufficient evidence`, `measured range (lower–upper)`.
- [ ] **Step 4: Run checks.** Run: `cd app && npm run test && npm run typecheck && npm run lint`. Expected: all green (test count = previous + new).
- [ ] **Step 5: Commit.** `git add app/src app/tests && git commit -m "feat(WS5): research view for shadow findings"`

### Task 5: Registry cross-ref note + integration doc + e2e + build + review

**Files:**
- Create: `docs/quant-integration.md`
- Modify: `docs/roadmap-status.md` (WS5 section: vendored engine + shadow path delivered; remaining WS5 becomes live-data evaluation evidence, calibration record, and broader WS6 display)
- Test: bounded e2e (tmp quant.db + fixture bars: research CLI → watch iterations → report → /quant-status read; never production data)

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: documented architecture/commands/data-flow/limitations; registry relationship note (`proto/quant/registry.py` = rule code, `docs/hypotheses/` = preregistration process, linked by rule_id+HYD id pairs recorded in future decision-log entries — no code merge); green everything.

- [ ] **Step 1: E2E run (bars → db → API → SSE, setups provably separate).** With tmp dirs only: seed fixture bars → `research` (assert Step-4-style split/denominator/holdout assertions) → `watch --once` iterations → assert SHADOW rows persisted in the tmp quant.db with `mode='shadow'` → start a test service on tmp db/quant.db → read `/quant-status` (counts match db) → open `/events`, assert a `quant.shadow` frame arrives with a matching watermark → reconnect with a below-floor id, assert `resync`. Separation proof: query the test signals db asserting zero quant rows/ids, and assert production `data/` mtimes/contents unchanged. Expected: exit 0 throughout; Dashboard/setup endpoints behaviorally unchanged (existing suite green covers the contract).
- [ ] **Step 2: Frontend build.** Run: `cd app && npm run build` (and `tauri build` only if the environment supports it; otherwise record as unverified-with-reason). Expected: build clean.
- [ ] **Step 3: Final diff review.** Review `git log` + full diff for accidental changes, secret/runtime inclusion, regressions, misleading claims, rule violations. Fix or record.
- [ ] **Step 4: Commit docs + status.** `git add docs/quant-integration.md docs/roadmap-status.md && git commit -m "docs(WS5): quant integration architecture and status"`
