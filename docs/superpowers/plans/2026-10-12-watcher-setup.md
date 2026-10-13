# Watcher Setup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the sidecar watcher (stable criteria-triggered setups) beside the scanner per the frozen spec.

**Architecture:** New stdlib-only `proto/watcher.py` (quant pure + state machine + persistence) + `proto/barrier_math.py` (verified formulas) + `proto/watcher_report.py` (diagnostics); additive `watcher_setup`/`watcher_eval` tables; scanner untouched; closed 5m bars only.

**Tech Stack:** Python 3.10+ stdlib only, SQLite (existing `Store` pattern), self-checking script tests via `tests/run_all.py`.

**Spec:** `docs/superpowers/specs/2026-10-12-watcher-setup-design.md` (commits `fe30250` + `09e0efe`; read both spec and this plan).

## Global Constraints

- Read-only stays read-only — no order placement, no key storage, no execution code, ever.
- Scoring/watcher changes are additive and versioned (`t1-break-v1`, `cusum-raw-v1`); never change logged `signal_log` semantics mid-collection.
- Every displayed number traces to a venue response or a logged row; estimates labeled estimates.
- Closed bars only: `bar.ts + 300 <= now`, else `unavailable`; no intrabar evaluation.
- `python3 tests/run_all.py` must stay green.
- No profitability/edge claim without out-of-sample evidence; unit-test success ≠ edge.

## Review Focus

- Forming-bar leak (bar with `ts + 300 > now` scored anyway) — expect `unavailable`, never a candidate.
- Missing-bucket TOD profile silently defaulting to 1.0 — expect `unavailable` + counted, never a default.
- Restart recreating an active setup as a second row — expect one row per `rule_id:coin:src_bar_ts`, terminal immutable.
- Same-bar stop+target counted as target win — expect stop wins (conservative).
- Funding snapshot age >8h silently treated as fresh — expect `funding=unavailable`, net EV unavailable, selector silent.

---

### Task 1: barrier math port

**Files:**
- Create: `proto/barrier_math.py`
- Test: `tests/test_barrier_math.py`

**Interfaces:**
- Consumes: `~/Downloads/barrier_math.py` reference formulas (verified by Monte Carlo).
- Produces:
  - `p_tp_first(a: float, b: float, mu: float, sigma: float) -> float`
  - `noise_touch_prob(x_ratio: float, n_bars: int) -> float` (`2Φ(−x/√n)`)
  - `breakeven_p(a_R: float, c_R: float) -> float` (`(1+c_R)/(1+a_R)`)
  - `friction_R(cost_pct: float, stop_pct: float) -> float`
  - `cluster_z(p: float, p0: float, n: int, frac: float) -> float`

- [ ] **Step 1: Write the failing test** — `tests/test_barrier_math.py` self-checking script (`check()` + `FAILURES` + `ALL PASS` tail, following `test_score_v3.py`): null `p_tp_first(5,1,0,1)==1/6` within 1e-9; `noise_touch_prob(2,5)` matches `2Φ(−2/√5)` within 1e-9; `breakeven_p(4.5,0.0)==1/5.5`; `friction_R(0.10,0.5)==0.20`; `cluster_z` shrinks with frac (z(1.0) > z(0.1)).
- [ ] **Step 2: Run it to verify it fails** — Run: `python3 tests/test_barrier_math.py` Expected: FAIL (module/import error).
- [ ] **Step 3: Implement the five functions in `proto/barrier_math.py`** — stdlib `math.erf` for Φ; drift formula `(1−e^(−2μb/σ²))/(1−e^(−2μ(a+b)/σ²))`, `|μ|<1e-14` → `b/(a+b)`; no numpy.
- [ ] **Step 4: Run test to verify it passes** — Run: `python3 tests/test_barrier_math.py` Expected: `ALL PASS`; then `python3 tests/run_all.py` Expected: all green.
- [ ] **Step 5: Commit** — Run: `git add proto/barrier_math.py tests/test_barrier_math.py && git commit -m "feat(watcher): port verified barrier math"`

### Task 2: watcher quant pure (vol, TOD, CUSUM, beta, RVOL)

**Files:**
- Create: `proto/watcher.py` (quant section only)
- Test: `tests/test_watcher_quant.py`

**Interfaces:**
- Consumes: bars as `[{ts,o,h,l,c,vol,amount}]`; `proto.scorer.score_v3`, `flow_state_v3` (reuse, not reimplement).
- Produces:
  - `gk_vol(bars) -> list[float|None]` (per-bar GK floored 0)
  - `ewma(vals, span=24) -> list[float|None]`
  - `tod_profile(keys, vals, lookback_days=14) -> dict[int,float]` (prior-only; `<30` samples → key absent)
  - `sigma_hat(bars, now) -> float|None` (adjusted EWMA sqrt; warm-up 60 else None; forming bar excluded)
  - `resid_vol(e, ...) -> float|None` (own profile on `e²`, never raw `s_vol`)
  - `cusum(zs, k=0.5, H=4.0) -> {long_idx, short_idx}` (symmetric S⁺/S⁻, fire-side reset, 6-bar refractory)
  - `beta_ols(r, r_btc) -> float|None` (no intercept, winsorize ±5 MAD, current bar excluded, zero-var → None); `beta_shrunk=0.5*ols+0.5`
  - `rvol_adj(bars, now) -> float|None`
  - `btc_drift_7d(btc_closes_5m) -> float|None` (`(c[-1]-c[-2016])/c[-2016]`, `<2016` → None)

- [ ] **Step 1: Write the failing test** — hand-calc GK on 3 fixed bars; EWMA seed = mean first 24; TOD bucket key `(ts//1800)%48` prior-only (today's bars never in profile; missing bucket → None not 1.0); CUSUM symmetry (mirror `z→−z` swaps long/short indices); beta zero-var → None; `btc_drift_7d` with 2015 bars → None, 2016+ → exact ratio; forming bar (`ts+300>now`) excluded from sigma.
- [ ] **Step 2: Run it to verify it fails** — Run: `python3 tests/test_watcher_quant.py` Expected: FAIL.
- [ ] **Step 3: Implement quant functions in `proto/watcher.py`** — pure, no I/O, no network; `L5`/initiation via existing `scorer.score_v3` (call, don't copy).
- [ ] **Step 4: Run test to verify it passes** — Run: `python3 tests/test_watcher_quant.py` then `python3 tests/run_all.py` Expected: green.
- [ ] **Step 5: Commit** — `git add proto/watcher.py tests/test_watcher_quant.py && git commit -m "feat(watcher): alt-perp quant pure functions"`

### Task 3: geometry + costs + funding + trail

**Files:**
- Modify: `proto/watcher.py` (append geometry section)
- Test: `tests/test_watcher_geometry.py`

**Interfaces:**
- Consumes: Task 1 (`breakeven_p`, `friction_R`), Task 2 (`sigma_hat`).
- Produces:
  - `fund_cost(direction, rates: list[float]) -> float` (`sign(dir)*Σrates`; long `+Σ`, short `−Σ` floored at 0 in `c_total`, signed leg in net column)
  - `fund_stress(fund_now: float, n_settlements: int) -> float` (`3*fund_now*n`)
  - `geometry(sigma, N, c_total, k_s, k_t) -> (b_stop, a_tgt)` (`b=max(k_s*σ√N,10*c_total)`, `a=k_t*σ√N`)
  - `levels(entry, direction, b_stop, a_tgt) -> (stop_px, tgt_px)`
  - `apply_breakeven(state, close) -> state` (trigger close ≥ entry+1·b_stop; stop→entry at next bar open; post-move stop-first)

- [ ] **Step 1: Write the failing test** — long positive funding raises `c_total`, short same rate rebates (floored 0); `fund_cost` sign check both dirs; `b_stop` floor binds at tiny sigma (`10*c_total` wins); trail: pre-trigger touches use original stop, post-move same-bar stop+tgt → stop at moved level; levels LONG/SHORT mirror.
- [ ] **Step 2: Run it to verify it fails** — Run: `python3 tests/test_watcher_geometry.py` Expected: FAIL.
- [ ] **Step 3: Implement in `proto/watcher.py`** — return fractions throughout; no price rounding (tick rules unverified).
- [ ] **Step 4: Run test to verify it passes** — plus `run_all.py` green.
- [ ] **Step 5: Commit** — `git commit -m "feat(watcher): volatility geometry, funding cost, breakeven trail"`

### Task 4: event study + N selection + kill gate

**Files:**
- Modify: `proto/watcher.py` (append `event_study`, `select_N`, `kill_check`)
- Test: `tests/test_watcher_eventstudy.py`

**Interfaces:**
- Consumes: Tasks 2–3; bar windows as closes lists.
- Produces:
  - `cumret(c0, ch, direction) -> float` (`dir*ln(ch/c0)`)
  - `event_score(cand: list[float], plac: list[float]) -> float` (`(mean_c−mean_p)/SD_c`, `SD_c==0` → 0.0)
  - `select_N(curves: dict[int,score]) -> int` (argmax over {12,48,96,288})
  - `kill_check(gross: dict, net: dict) -> bool` (True=kill when gross≤0 at all {12,48,288} AND net≤0 at all three)

- [ ] **Step 1: Write the failing test** — synthetic curves: peak at 48 picked; zero-variance → 0.0 not NaN; kill True only when all six ≤0; mixed (one horizon positive) → False; placebo-subtraction unit check `(mean_c−mean_p)/SD_c` hand value.
- [ ] **Step 2: Run it to verify it fails** — Expected: FAIL.
- [ ] **Step 3: Implement** — no I/O; horizons fixed {12,48,96,288} (+report table at 15m/1h/4h/24h).
- [ ] **Step 4: Run tests green** — plus `run_all.py`.
- [ ] **Step 5: Commit** — `git commit -m "feat(watcher): gross event study N selection and kill gate"`

### Task 5: triggers (T1-break + CUSUM-raw) + H freeze

**Files:**
- Modify: `proto/watcher.py` (append `t1_candidate`, `cusum_candidate`, `freeze_H`)
- Test: `tests/test_watcher_triggers.py`

**Interfaces:**
- Consumes: Tasks 2–4; `scorer.score_v3` for L5/initiation.
- Produces:
  - `t1_candidate(ctx) -> dict|None` (age≤6, L5>0.15 LONG, drift>+5%, vetoes; returns setup fields or None with reason)
  - `cusum_candidate(ctx) -> dict|None` (raw alt-perp z, H_cusum, vetoes)
  - `freeze_H(dev_windows) -> float` (grid {3,4,5,6}, smallest with ≤2500/day, frozen)
  - veto helper `vetoes_ok(vol, spread, m1h, m24h) -> list[str]`

- [ ] **Step 1: Write the failing test** — T1: age 6 fires, age 7 doesn't; drift +4.9% blocks, +5.1% passes; short L5 never T1; CUSUM long/short mirror; veto breach (spread 3.1%, vol 249k, 1h 12.0%, 24h 35.0%) blocks with named reason; stale/funding-missing still emits setup with `funding=unavailable` (selector silence tested in Task 6).
- [ ] **Step 2: Run it to verify it fails** — Expected: FAIL.
- [ ] **Step 3: Implement** — `rule_id` `t1-break-v1` / `cusum-raw-v1`; id `rule:coin:src_bar_ts`; closed-bar gate inside.
- [ ] **Step 4: Run tests green**.
- [ ] **Step 5: Commit** — `git commit -m "feat(watcher): T1 and CUSUM triggers with frozen H"`

### Task 6: lifecycle state machine + persistence + recovery

**Files:**
- Modify: `proto/watcher.py` (append store fns), `proto/store.py` (additive: create tables if missing + `Store` passthrough or standalone `watcher_store` — prefer new functions in `watcher.py` taking a sqlite3 conn to avoid touching legacy paths)
- Test: `tests/test_watcher_lifecycle.py`

**Interfaces:**
- Consumes: Tasks 1–5; DDL from spec §4.
- Produces:
  - `ensure_watcher_tables(conn)`
  - `emit_setup(conn, setup) -> id` (dedupe: repeated bars → same id, no dup)
  - `eval_bar(conn, setup_id, bar, ctx) -> (state, monitoring)` (order: gap→barriers stop-first→close-invalidation; invalidation never masks touch; 48-gap-bars → `expired_gap` censored)
  - `checkpoint(conn, coin, rule, watermark)` / `replay_since(conn, ...)` (idempotent; restart no dup; terminal immutable)

- [ ] **Step 1: Write the failing test** — repeated qualifying bars → one row; cooldown suppresses second emit; same-bar stop+tgt → `resolved_stop`; barrier-day invalidation attempt after touch ignored; 48 `unavailable` bars → `expired_gap`; kill process + reopen DB → no duplicate active; terminal row never rewritten; full-recompute mismatch (1e-9) → `unavailable`.
- [ ] **Step 2: Run it to verify it fails** — Expected: FAIL.
- [ ] **Step 3: Implement** — sqlite3 stdlib; watermark per (coin,rule); hourly checkpoint; `monitoring=unavailable` overlay preserves `active`.
- [ ] **Step 4: Run tests green** (incl. restart simulation with temp DB).
- [ ] **Step 5: Commit** — `git commit -m "feat(watcher): setup lifecycle, dedupe, checkpoint recovery"`

### Task 7: shadow selector + report/UI wiring

**Files:**
- Create: `proto/watcher_report.py`
- Modify: `gui/model.py`, `qtgui/` minimal watcher panel (display only; sorting/flags untouched) — if GUI touch proves noisy, report-only + GUI follow-up noted.
- Test: `tests/test_watcher_selector.py`

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces:
  - `ev_r(p_lb, a, b, c) -> float`
  - `shadow_select(setups, K=15, ev_hurdle=0.1) -> set[id]` (funding-unavailable excluded; ≤2/cluster `|ρ|>0.7`; never suppresses setups)
  - `watcher_report` CLI: states, coverage input×coin×date, gross/net columns, correlated opens, assumptions labeled.

- [ ] **Step 1: Write the failing test** — funding-unavailable setup never selected; K ceiling enforced; 3 mutually-correlated → ≤2 selected; below-hurdle never selected; unselected setup still `active` in store (non-suppression).
- [ ] **Step 2: Run it to verify it fails** — Expected: FAIL.
- [ ] **Step 3: Implement selector + report** — research-labeled; global pause + per-trigger disable flags.
- [ ] **Step 4: Run full suite** — Run: `python3 tests/run_all.py` Expected: ALL SUITES PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat(watcher): shadow selector and report"`

## Self-Review

- Spec coverage: §1 sidecar/no-ranking/silence → Tasks 5–7; §2 GK/EWMA/TOD/beta/CUSUM/RVOL/OI → Task 2, funding/costs/trail → Task 3, event study/kill → Task 4, scorer calibration (logistic/Beta/`p̂_LB`) → deferred note: Task 7 uses frozen `p̂_LB` interface with hand-set scorer v0 (logistic training on triple-barrier labels is follow-up work — spec §2 scorer training is the one item this plan time-boxes; flagging here so executor doesn't invent an ML fit); §3 lifecycle/persistence → Task 6; §4 DDL/modules/order → Tasks 1/6; UI/API → Task 7. OI 1h flow reuses `scorer.flow_state_v3` (no new OI math).
- Step scan: each step names one test/code/run/commit artifact; no TBD.
- Type consistency: `b_stop`/`a_tgt` return fractions everywhere; `fund_cost` positive-cost; `event_score` dimensionless; ids `rule:coin:src_bar_ts`.
- Review Focus: each of the 5 lines has an owning task test (forming-bar→T2/T5, TOD default→T2, restart dup→T6, same-bar→T3/T6, stale funding→T5/T7).
- Proportion: plan shorter than spec; bodies only where signature+test leave choice (trail timing, H freeze).
