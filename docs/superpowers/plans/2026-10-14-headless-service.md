# Headless Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the stdlib-only headless service (`proto/service.py`) exposing the typed HTTP+SSE contract, embedding the existing daemon loop, with the frontend and packaging following as separate plans.

**Architecture:** One process: `http.server` on threads (JSON routes + SSE stream) plus the untouched daemon scan/collect/resolve loop; per-launch or config-file bearer token; allowlisted origins; singular ownership via the existing single-scanner lockfile. No new dependencies.

**Tech Stack:** Python 3.10+ stdlib only (`http.server`, `sqlite3`, `threading`, `hmac.compare_digest`), self-checking script tests via `tests/run_all.py` (loopback HTTP only, with an egress tripwire blocking non-`127.0.0.1` connects).

**Spec:** `docs/superpowers/specs/2026-10-14-tauri-desktop-design.md` (commit `6c906b5`; §1–§4; executors read both).

## Global Constraints

- Read-only stays read-only — no orders, no keys, no execution code; loopback bind only, never public interfaces.
- NO ML/probability outputs anywhere; no invented data (explicit `null`, section-level `unavailable: reason`).
- Closed bars only; rate-limit/backoff behavior unchanged.
- No profitability/edge claims in any string the service emits.
- `python3 tests/run_all.py` must stay green; service tests talk to `127.0.0.1` only (tripwire fails any other connect).
- `proto/daemon.py` becomes a thin wrapper; engine/scan/watcher/store semantics unchanged.

## Review Focus

- A route reachable without a token (other than `/healthz`) — expect 401 tests on every route.
- An event emitted per refresh rather than per transition — expect append-ordered transition-only assertions with refresh traffic in between.
- A reconnect that duplicates or drops a transition between snapshot and subscribe — expect the reconcile test (transition injected mid-recovery) to pass.
- A second service instance starting a second scanner — expect lockfile refusal, never two collectors.
- `Authorization` appearing in any log line — expect the redaction test to fail on it.

---

### Task 1: Server skeleton, auth, origin policy

**Files:**
- Create: `proto/service.py`
- Test: `tests/test_service_auth.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `serve(port: int, token: str, ready: Callable[[], dict]) -> HTTPServer` (loopback-only; port fallback scan owned by Task 5)
  - `load_or_create_token(path: str) -> str` (`0600` file, generated if absent)
  - `GET /healthz` (no auth → `{"ok": true, "alive": true}`)
  - 401 on missing/wrong bearer for all other routes; 403 on mismatched `Origin`/`Host` for authed routes; CORS preflight answered only for allowlisted origins (Tauri asset origin + `http://localhost:*` dev)
  - No `Authorization` value in any log line

- [ ] **Step 1: Write the failing test**

```python
def test_auth_matrix():
    assert get("/healthz").status == 200
    assert get("/snapshot").status == 401
    assert get("/snapshot", bad_token).status == 401
    assert post("/commands/pause", no_origin_header_mismatch).status == 403
def test_no_token_in_logs():
    assert "bearer-secret" not in captured_server_logs()
```

plus: CORS preflight 200 for allowlisted origin, 403 otherwise.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_service_auth.py`
Expected: FAIL (no `proto/service.py`).

- [ ] **Step 3: Implement skeleton in `proto/service.py`**

`hmac.compare_digest` token compare; `Origin`/`Host` validation before routing; redacting log handler.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_service_auth.py`, then `python3 tests/run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add proto/service.py tests/test_service_auth.py
git commit -m "feat(service): loopback server with token auth and origin policy"
```

### Task 2: Snapshot + resource routes

**Files:**
- Modify: `proto/service.py` (append routes)
- Test: `tests/test_service_routes.py`

**Interfaces:**
- Consumes: Task 1 (`serve`, envelope helpers); existing `Store`, `detail_metrics`, `reference_plan`, watcher tables (read-only).
- Produces:
  - Envelope `{ok, data, error:{code,message}}`; timestamps UTC-seconds ints; codes `UNAUTH/BAD_REQUEST/NOT_FOUND/UNAVAILABLE/RATE_LIMITED`
  - `GET /readyz` (auth; ready = first sweep done AND DB writable, else 503 `UNAVAILABLE`)
  - `GET /snapshot`, `GET /setups?state=&limit=&cursor=` (default 100, max 500), `GET /setup/:id` (404 `NOT_FOUND`), `GET /bars?coin=&tf=` (5m/15m/1H/4H/1D closed only; 1m → 400 `BAD_REQUEST`), `GET /metrics?coin=` (server-side indicators; `null` when unavailable), `GET /tradeplan/:id` (100U reference, on demand; pre-cut rows read from `plan_log`)

- [ ] **Step 1: Write the failing test**

```python
def test_envelope_and_nulls():
    assert get("/metrics?coin=NOPE", auth) == 404
    assert get("/metrics?coin=THIN", auth)["data"]["rsi"] is None  # explicit null
def test_pagination_caps():
    assert len(get("/setups?limit=9999", auth)["data"]) <= 500
```

plus: bars 1m rejected; tradeplan has no leverage key; setup ids carry `rule:coin:src_bar_ts` shape.

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_service_routes.py`
Expected: FAIL (404 from missing routes).

- [ ] **Step 3: Implement routes in `proto/service.py`**

Reuse `Store` read paths, `detail_metrics`, `reference_plan`; fixture temp DB in tests via real writers.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_service_routes.py`, then `run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add proto/service.py tests/test_service_routes.py
git commit -m "feat(service): snapshot and resource routes with typed envelope"
```

### Task 3: SSE events with reconcile

**Files:**
- Modify: `proto/service.py` (append stream), `proto/watcher.py` (append transition hooks only)
- Test: `tests/test_service_events.py`

**Interfaces:**
- Consumes: Tasks 1–2; watcher lifecycle transition points.
- Produces:
  - `GET /events` (SSE, monotonic ids, ring buffer last 500 / 30 min)
  - Kinds: `setup.emitted/invalidated/expired/resolved`, `monitoring.unavailable/ok`, `sweep.completed`
  - `snapshot_event_id` in `GET /snapshot`; stale cursor → `resync` event

- [ ] **Step 1: Write the failing test**

```python
def test_transition_only():
    refresh_traffic(50); assert events_since() == []  # no per-refresh events
def test_mid_recovery_transition_not_lost():
    stream = open_events(last_id); inject_transition(); snap = get_snapshot()
    assert reconcile(stream, snap) contains injected_transition
def test_stale_cursor_resyncs():
    assert open_events(ancient_id).first.kind == "resync"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_service_events.py`
Expected: FAIL.

- [ ] **Step 3: Implement stream + hooks**

Hooks at existing transition points only (emit/invalidate/expire/resolve + monitoring flips); resolutions already journaled — reuse, don't duplicate.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_service_events.py`, then `run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add proto/service.py proto/watcher.py tests/test_service_events.py
git commit -m "feat(service): transition-only SSE with snapshot reconcile"
```

### Task 4: Commands

**Files:**
- Modify: `proto/service.py` (append commands)
- Test: `tests/test_service_commands.py`

**Interfaces:**
- Consumes: Tasks 1–3; existing `meta` pause/disable flags (same keys panels read).
- Produces: `POST /commands/{pause,resume,disable-trigger,enable-trigger}` (idempotent; repeated identical calls return same state; reload-safe via `meta` persistence)

- [ ] **Step 1: Write the failing test**

```python
def test_idempotent_commands():
    post("/commands/pause"); post("/commands/pause")
    assert flags() == {"paused": True}  # same state, no duplication
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_service_commands.py`
Expected: FAIL.

- [ ] **Step 3: Implement commands in `proto/service.py`**

Write the same `meta` keys the Qt panels read; JSON body `{rule}` for trigger commands with 400 on unknown rule.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_service_commands.py`, then `run_all.py` green.

- [ ] **Step 5: Commit**

```bash
git add proto/service.py tests/test_service_commands.py
git commit -m "feat(service): idempotent pause/resume/trigger commands"
```

### Task 5: Daemon embedding + single scanner

**Files:**
- Modify: `proto/service.py` (embed loop), `proto/daemon.py` (thin wrapper), `altradar.service.example` (ExecStart → service)
- Test: `tests/test_service_lifecycle.py`

**Interfaces:**
- Consumes: Tasks 1–4; existing daemon loop + lockfile.
- Produces:
  - `python3 -m proto.service` runs scan/collect/resolve loop + HTTP server; SIGTERM/SIGINT stops loop, server, sessions in order
  - Second instance refuses via lockfile (exit code + message, no duplicate scanning)
  - `proto.daemon` delegates to service headless (all flags preserved)

- [ ] **Step 1: Write the failing test**

```python
def test_single_scanner():
    start_service(); assert start_second_service().refused is True
def test_clean_shutdown():
    terminate(); assert port_free() and db_consistent()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_service_lifecycle.py`
Expected: FAIL.

- [ ] **Step 3: Implement embedding + wrapper + unit file**

Move, don't fork: import the daemon loop, don't copy it.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_service_lifecycle.py`, then full `run_all.py` — ALL SUITES PASS.

- [ ] **Step 5: Commit**

```bash
git add proto/service.py proto/daemon.py altradar.service.example tests/test_service_lifecycle.py
git commit -m "feat(service): embed daemon loop with single-scanner guarantee"
```

### Task 6: Dashboard read model (drop-in parity with the mock contract)

**Files:**
- Modify: `proto/service.py` (append read-model builders; reshape existing route data objects)
- Test: `tests/test_service_dashboard.py` (new; differential assertions runnable against mock AND real)

**Interfaces:**
- Consumes: Tasks 1–5 (envelope, auth, pagination, SSE, store/journal helpers); `app/dev/mock_service.py` shapes as the oracle (read-only reference, never imported).
- Produces (converged contract — the mock/dashboard shapes win; sparse shapes are replaced, with test updates justified by this reconciliation):
  - `GET /snapshot` data: `{ts, data_as_of, snapshot_event_id: str, eligible_count, evaluated_count, active_setups: [...], comparison_24h | null, market_overview: [...], movers, supported_timeframes, unavailable: []}` (replaces the counts-object `setups` key; counts remain derivable as `len(active_setups)`)
  - `GET /setups`: `{setups: [...], next_cursor: str | null}` with opaque string cursors (replaces integer-offset cursor)
  - `GET /setup/:id`: watcher row PLUS `inputs/criteria/analysis` (rule criteria from trigger definitions + availability) and `tradeplan_id`
  - `GET /bars`: `{bars: [{ts,open,high,low,close,volume,closed}], closed_only: true, count, data_as_of, unavailable}` (key rename from o/h/l/c/vol; old keys dropped)
  - `GET /metrics`: `{coin, ts, rsi, macd, ema50, ema200, volume_pct, unavailable, data_as_of}` (adds coin/ts; renames macd_hist→macd, vol_quote→volume_pct)
  - `GET /tradeplan/:id`: `{id: "tp-…", setup_id, entry_zone: {low, high}, reference_notional_usdt: 100.0, …}` (string tp id; no leverage key — unchanged rule)
  - Setup states keep `active/invalidated/expired/resolved` (no `emitted` state; emission is the create event, not a state)

- [ ] **Step 1: Write the failing test**

```python
def test_dashboard_shapes():
    snap = get("/snapshot")["data"]
    assert isinstance(snap["active_setups"], list) and "data_as_of" in snap
    page = get("/setups?limit=2")
    assert isinstance(page["data"]["next_cursor"], str)
    assert get("/bars?coin=X&tf=5m")["data"]["bars"][0].keys() >= {"open", "high", "low", "close", "volume", "closed"}
```

plus: differential run of the same assertions against the mock (documents parity, not run in `run_all.py` by default).

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 tests/test_service_dashboard.py`
Expected: FAIL (counts-object `setups`, missing keys).

- [ ] **Step 3: Implement the read model in `proto/service.py`**

Reuse existing helpers (store reads, `detail_metrics`, `reference_plan`, rule criteria from trigger definitions); update the Task-2 tests that pinned sparse shapes, with each change justified in the commit message as contract reconciliation (spec authority: frontend spec §3 line 48 drop-in promise).

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 tests/test_service_dashboard.py`, then full `run_all.py` — ALL SUITES PASS.

- [ ] **Step 5: Commit**

```bash
git add proto/service.py tests/test_service_dashboard.py tests/test_service_routes.py
git commit -m "feat(service): dashboard read model matching the mock contract"
```

## Self-Review

- Spec coverage: §1 server/auth/origin/tokens → T1; routes/envelope/pagination/codes/indicators → T2, reshaped to the dashboard contract in T6; events/reconcile/resync → T3; commands → T4; embedding/lockfile/shutdown → T5; ready split → T1+T2 (`/readyz`); frontend/packaging/integration-matrix items belong to follow-up plans (frontend plan, Tauri/packaging plan), each testable on its own.
- Step scan: test steps pin names/assertions; code steps pin signatures/files; no TBD.
- Type consistency: `serve/load_or_create_token`, envelope keys, `snapshot_event_id`, `JOURNAL_CAP` reuse, `meta` flag keys shared verbatim across tasks.
- Review Focus: all five lines owned (auth matrix→T1; transition-only→T3; reconcile→T3; lockfile→T5; redaction→T1).
- Proportion: signatures + assertions only; no bodies.
