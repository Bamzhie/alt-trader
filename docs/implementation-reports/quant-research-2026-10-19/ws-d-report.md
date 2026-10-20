# Task D report — Workstream D: Research UI expansion (+ its read-only API surface)

**Status: DONE_WITH_CONCERNS**

Everything the brief asks for is implemented and tested: the new read-only
`GET /quant-research` route, the expanded Research view, the dev-mock
routes and fixtures, and the tests. The concerns are *semantics decisions
the orchestrator may want to overrule* (the coverage denominator, the
calibration answer, the validation label mapping, the shadow-only read) and
two environment facts (the mock's own suite had to be run on a free port;
the worktree had no `node_modules`). No functional blocker, no banned
vocabulary, no promotion, no holdout spend.

- Worktree: `/tmp/opencode/wt-d` (branch `feature/ws-d-ui`)
- Base commit: `7ce9745` (docs: file the WS-A and WS-B workstream reports…)
- Implementation commit: **`e26c20a`** — `feat(research): read-only research surface route + expanded Research view (WS-D)`
- Report commit: see the last line of this file (HEAD)

---

## 1. What changed, file by file

| File | Δ | Status |
|---|---:|---|
| `proto/quant/research_surface.py` | +317 | new — the data assembly (stdlib only) |
| `proto/service.py` | +55 | the read-only route + handler |
| `app/src/api/types.ts` | +143 | the `QuantResearch` contract; `QuantHoldout` widened |
| `app/src/api/client.ts` | +36 | `research()` → `GET /quant-research` + shape guard |
| `app/src/pages/Research.tsx` | +539/−100 | the expanded Research view |
| `app/tests/test_research_surface.test.tsx` | +418 | new — 14 frontend tests |
| `tests/test_ws_d_research_surface.py` | +393 | new — 72 backend checks |
| `app/dev/mock_service.py` | +69 | `/quant-status` + `/quant-research` routes |
| `app/dev/fixtures.json` | +172 | the two fixture sections |
| `app/dev/test_mock_service.py` | +42 | 3 new mock-service tests |
| `app/README.md` | +2 | the two routes in the contract table |

Nothing outside those files was touched. `app/tests/test_research.test.tsx`
is **unchanged** (the acceptance criterion "the existing test still passes
unchanged"). No file from the do-not-touch list was modified
(`proto/quant/engine.py`, `registry.py`, `selector.py`, `research.py`,
`eval_harness.py`, `gates.py`, `proto/daemon.py`, `proto/collector.py`,
`app/src/pages/Dashboard.tsx`, `app/src/components/TopSignalsTable.tsx`,
`app/src/api/sse.ts`). The live systemd service was not restarted or
altered, and nothing was written to `data/`.

### 1.1 `proto/quant/research_surface.py` (new)

Assembles the additive, versioned payload from the quant store. Stdlib only
(`sqlite3`, `os`, `subprocess`, `time`, `importlib`, `functools`); no
pandas/numpy/scipy, nothing installed.

```python
SURFACE_VERSION = "research-surface-v1"

class ReadOnlyStore:      # the quant store, read-only and SHADOW-only
    def all_events(self, rule_id=None): ...
    def resolved_rows(self, rule_id=None): ...

def code_rev(): ...                 # cached: env ALT_RADAR_CODE_REV, else git rev-parse --short HEAD, else None
def feature_versions(): ...         # estimators' *_VERSION constants + feature_store + structure (guarded imports)
def _validation(rule_id, config_hash, records): ...
def _rule_payload(rep, config_hash, records): ...
def _sample(conn, n_rules): ...
def _coverage(conn, events, has_suppressed=True): ...
def build(conn, cfg=DEFAULT, promo_dir=".", now=None): ...
```

Design points:

- **The statistics are reused, never re-implemented.** The day-cluster
  bootstrap interval comes from `report.rule_report` — the *same* function
  the CLI research report uses, so the view and the report cannot disagree.
  The test asserts the endpoints are bit-identical to `rule_report`'s.
- **The mean net R is deliberately NOT in the payload.** `rule_report`
  computes it; `research_surface` drops it. There is no point reading of
  the result anywhere in the contract.
- **SHADOW-only read.** `ReadOnlyStore` filters `mode='SHADOW'`, so the
  counts, the denominators, the shares and the interval always describe
  the *same* rows and a legacy `ACTIONABLE` row can never enter a research
  denominator (the shadow-only house rule). The CLI report
  (`proto/quant/report.py`) is *not* filtered — this is a deliberate
  divergence, see §5.2.
- **`validation.status` is `validated` only for a PASSED gate record** for
  that exact rule id + config hash (`gates.load`, i.e. the same artefact
  `gates.promotion_for` reads). A failed record contributes the gate's own
  reasons; a *missing* record is its own reason
  (`"no promotion record for this rule id and config hash"`) — absent
  evidence fails, it is never read as a pass. The route writes nothing, so
  no promotion behaviour changes.
- **`feature_versions`** lists `estimators`' `*_VERSION` constants
  (`HURST_VERSION`, `MEASURE_VERSION`, `MFDFA_VERSION`) plus
  `feature_store.FEATURE_STORE_VERSION` and `structure.PIVOT_VERSION`.
  Every import is guarded (`importlib.import_module` in a `try`), so a
  module that is not in the tree is skipped, never imported blindly.
- **`code_rev`** is best-effort provenance: the `ALT_RADAR_CODE_REV` env
  var wins, then `git rev-parse --short HEAD` (2 s timeout, cached), else
  an explicit `None`.

### 1.2 `proto/service.py` — the route

Registered next to `/quant-status` in `_route()`, **behind the same auth
gate** (`self._gate()`), GET only:

```python
if self.command == "GET" and path == "/quant-research":
    self._handle_quant_research(gated, cfg_quant)
    return
```

`_handle_quant_research(origin, quant_db)` opens the store read-only
(`file:…?mode=ro`), answers **200** with the envelope when the store is
readable, **503 UNAVAILABLE** when it is not (missing `quant_event` table,
unreadable file, or any failure while assembling — the surface never
answers with a figure it could not read). It never writes, never evaluates
the holdout, and never touches a promotion record.

### 1.3 The exact response shape

```json
{
  "version": "research-surface-v1",
  "status": "ok" | "insufficient",
  "reasons": ["…"],
  "generated_ts": 1700000000,
  "provenance": {
    "config_hash": "…|null",
    "code_rev": "…|null",
    "rule_ids": ["…"],
    "feature_versions": [{"name": "…", "version": "…"}]
  },
  "sample": {
    "events": 0, "resolved": 0, "open": 0,
    "days": 0, "coins": 0, "rules": 0
  },
  "rules": [
    {
      "rule_id": "…",
      "role": "candidate|control|?",
      "events": 0, "resolved": 0, "open": 0,
      "days": 0, "coins": 0,
      "interval": {"lo": 0.0|null, "hi": 0.0|null} | null,
      "outcome_shares": {"tp": null, "sl": null, "time": null},
      "cost_note": "…",
      "suppression": [{"reason": "…", "n": 0}],
      "validation": {"status": "not_validated|validated", "reasons": ["…"]}
    }
  ],
  "calibration": {"available": false, "buckets": [], "reason": "…"},
  "coverage": {"attributed": 0, "eligible": 0, "ratio": null, "reason": "…"}
}
```

A real answer from the live backend (temp store, 3 resolved shadow events)
— this is the actual wire payload, not a mock:

```json
{"ok": true, "data": {
  "version": "research-surface-v1", "status": "ok", "reasons": [],
  "generated_ts": 1792452805,
  "provenance": {
    "config_hash": "d33cff513c30", "code_rev": "7ce9745",
    "rule_ids": ["quant-v1:breakout_start:rvol4:cont:T288_ks1.5_R1",
                 "quant-v1:breakout_start:rvol4:fade:T288_ks1.5_R1",
                 "quant-v1:residual_shock:h8:cont:T144_ks3_R1",
                 "quant-v1:residual_shock:h8:fade:T144_ks3_R1"],
    "feature_versions": [{"name": "hurst", "version": "hurst-v1"},
                          {"name": "measure", "version": "measures-v1"},
                          {"name": "mfdfa", "version": "mfdfa-v1"},
                          {"name": "feature_store", "version": "features-v1"},
                          {"name": "pivots", "version": "pivots-v1"}]},
  "sample": {"events": 3, "resolved": 3, "open": 0, "days": 3, "coins": 3, "rules": 1},
  "rules": [{"rule_id": "quant-v1:breakout_start:rvol4:fade:T288_ks1.5_R1",
             "role": "candidate", "events": 3, "resolved": 3, "open": 0,
             "days": 3, "coins": 3,
             "interval": {"lo": -0.01, "hi": 0.03},
             "outcome_shares": {"tp": 0.6666666666666666, "sl": 0.3333333333333333, "time": 0.0},
             "cost_note": "costs are assumed (fee + slippage); funding is not modeled; no fill model beyond next bar open",
             "suppression": [],
             "validation": {"status": "not_validated",
                            "reasons": ["no promotion record for this rule id and config hash"]}}],
  "calibration": {"available": false, "buckets": [],
                  "reason": "the shadow store records outcomes, not predicted outcomes - calibration is not computed"},
  "coverage": {"attributed": 3, "eligible": 3, "ratio": 1.0,
               "reason": "attributed events over recorded opportunities (emitted + recorded suppressions); opportunities the watcher never recorded are not in the denominator"}},
 "error": null}
```

### 1.4 `app/src/api/types.ts`

- `QuantHoldout` widened to the fields the backend already stores
  (`research.summarize`): `n, days, coins, mean_r, ci_lo, ci_hi, tp, sl,
  time, t`. The Research view reads **only** `ci_lo`/`ci_hi` from it.
- New contract types: `QuantResearch`, `QuantResearchSample`,
  `QuantResearchRule`, `QuantResearchInterval`, `QuantResearchOutcomeShares`,
  `QuantResearchValidation`, `QuantResearchSuppression`,
  `QuantResearchProvenance`, `QuantResearchFeatureVersion`,
  `QuantResearchCalibration`, `QuantResearchCalibrationBucket`,
  `QuantResearchCoverage` — with the hard rules written into the header
  comment (endpoints only, explicit nulls, `validated` only for a passed
  record).

### 1.5 `app/src/api/client.ts`

```ts
research(): Promise<QuantResearch>;   // in the ApiClient interface

async research(): Promise<QuantResearch> {
  const data = await get("/quant-research");
  if (!isQuantResearch(data)) {
    throw new ApiError("NETWORK", "response was not a research surface");
  }
  return data;
}
```

`isQuantResearch` is a shape guard: a body that is not a research surface
is reported as a contract error, never rendered as a partial surface. No
raw `fetch` — the client is the only caller.

### 1.6 `app/src/pages/Research.tsx` — the components added

The view now issues **two** GETs on mount (`client.quantStatus()` and
`client.research()`); every `quant.shadow` frame and every server resync
re-reads both. Four new components (all non-exported, all with their own
`// House rules:` header comment, all theme-token coloured, no `<table>`):

| Component | What it renders |
|---|---|
| `SampleCounts` | the sample with its denominators ("X of Y" + named denominator) |
| `StatusRows` | the calibration / coverage / validation status rows |
| `RuleRows` | one block per rule: counts, the measured range, the shares, the cost note, the validation status, the suppression rows |
| `ProvenanceRows` | config hash, code revision, generated timestamp, registered rule ids, feature versions |

Helpers (all module-private): `formatRange`, `hasEndpoints`, `formatUtc`,
`orUnavailable`, `formatShare`.

### 1.7 `app/dev/mock_service.py` + `app/dev/fixtures.json`

- `GET /quant-status` → the `quant_status` fixture (the route did not exist
  before, so the Research view showed `insufficient evidence` in dev).
- `GET /quant-research` → the `quant_research` fixture.
- Both fall back to explicit `EMPTY_QUANT_STATUS` /
  `EMPTY_QUANT_RESEARCH` constants (an `insufficient` surface with a
  reason and null/zero denominators) when a fixture is missing.
- The fixtures carry a populated, self-consistent surface (44 events, 17
  resolved, 3 rules — one of them with no resolved outcome, so the
  `measured range unavailable` / `unavailable` paths are visible in dev).
  The fixture text contains none of the mock suite's banned words and no
  `"1m"`.

---

## 2. Tests — every command and its real output

### 2.1 `python3 tests/test_ws_d_research_surface.py`

```
  PASS  payload version
  PASS  payload top-level keys
  PASS  status ok with events
  ...
  PASS  the quant store is untouched by the route
  PASS  the promotion records are untouched by the route
  PASS  the holdout was never evaluated
  PASS  an unreadable store -> 503 UNAVAILABLE
  PASS  the 503 carries the error envelope

ALL PASS  tests/test_ws_d_research_surface.py  (72 checks)
```

(exit code 0; 72 checks — the payload shape, the explicit denominators,
`null` + reason for everything uncomputable, endpoints-only uncertainty,
the interval being *bit-identical* to `report.rule_report`'s, no `net_r`/
`mean_r` in the payload, `validated` only for a passed record, the empty
store, the auth matrix, the read-only proof, and a banned-vocabulary scan
of the whole payload.)

### 2.2 `python3 tests/run_all.py`

```
PASS  test_ws_b_outcomes.py  [ALL PASS]
PASS  test_ws_d_research_surface.py  [ALL PASS  tests/test_ws_d_research_surface.py  (72 checks)]
PASS  test_ws_f_startup.py  [ALL PASS  tests/test_ws_f_startup.py  (91 checks)]

ALL SUITES PASS (85 files)
```

(exit code 0; 85 files, 0 failures.)

### 2.3 The dev-mock service's own suite

`app/dev/test_mock_service.py` hardcodes port 18741, which on this host is
owned by the **live** ALT RADAR service (pid 572048) — I did not touch it,
so the suite was run on a free port with the module's `BASE_PORT` patched
(`cd app/dev`):

```
Ran 51 tests in 1.062s

OK
```

(exit code 0; 48 tests before + 3 new ones: `/quant-status`, `/quant-research`
shape/endpoints-only, and the auth gate on both routes.)

### 2.4 `npm run test` (in `app/`)

```
 Test Files  14 passed (14)
      Tests  199 passed (199)
   Start at  00:41:20
   Duration  32.57s (transform 4.13s, setup 10.01s, collect 19.01s, tests 50.52s, environment 72.61s, prepare 16.76s)
```

(exit code 0; 177 before + 14 new in `tests/test_research_surface.test.tsx`
+ 8 unchanged in `tests/test_research.test.tsx`.)

### 2.5 `npm run typecheck`

```
> alt-radar-dashboard@0.1.0 typecheck
> tsc --noEmit
```

(exit code 0, no output.)

### 2.6 `npm run lint`

```
> alt-radar-dashboard@0.1.0 lint
> eslint . --max-warnings 0
```

(exit code 0, no warnings, no output.)

### 2.7 `npm run build`

```
> alt-radar-dashboard@0.1.0 build
> tsc --noEmit && vite build

vite v5.4.21 building for production...
transforming...
✓ 66 modules transformed.
rendering chunks...
dist/index.html                   0.44 kB │ gzip:  0.28 kB
dist/assets/index-DGKMbTBq.css    11.79 kB │ gzip:  3.22 kB
dist/assets/index-Buqb4waz.js    367.51 kB │ gzip: 114.48 kB
✓ built in 8.93s
```

(exit code 0.)

---

## 3. What the Research view now shows (exact user-visible strings)

Rendered from the dev fixtures — the whole `<section>` text, whitespace
collapsed and re-indented for readability (the *strings* are verbatim, the
indentation is not in the DOM). A human can check the house rules by
reading it:

```
Research
SHADOW — findings here are not actionable
watcher: healthy
Shadow events
  Emitted 44
  Resolved 17
  Shadow events 44
Suppression
  quant-v1:breakout_start:rvol4:fade:T288_ks1.5_R1 — cooldown from the previous event 9
  quant-v1:residual_shock:h8:fade:T144_ks3_R1 — quote volume below the cost model floor 4
Sample
  Resolved 17 of 44   resolved of emitted
  Open 27 of 44       open of emitted
  Days observed 12
  Coins observed 8
  Rules observed 3
  days and coins count the resolved rows the measured range is computed on
Status
  Calibration unavailable
    the shadow store records outcomes, not predicted outcomes - calibration is not computed
  Coverage 44 of 57
    attributed events over recorded opportunities (emitted + recorded suppressions);
    opportunities the watcher never recorded are not in the denominator
  Validation 0 of 3 rules with a passed gate record
Rule measurements
  quant-v1:breakout_start:rvol4:fade:T288_ks1.5_R1   candidate
    12 of 28 resolved · 9 days · 6 coins
    measured range (0.0042–0.0311)
    range of measured outcomes, not a prediction
    outcome shares of 12 resolved: tp 0.42 · sl 0.25 · time 0.33
    costs are assumed (fee + slippage); funding is not modeled; no fill model beyond next bar open
    validation: insufficient evidence
    holdout events 12 < 200
    holdout days 9 < 25
    suppression — cooldown from the previous event × 9
  quant-v1:breakout_start:rvol4:cont:T288_ks1.5_R1   control
    5 of 14 resolved · 5 days · 4 coins
    measured range (-0.0112–0.0094)
    range of measured outcomes, not a prediction
    outcome shares of 5 resolved: tp 0.40 · sl 0.40 · time 0.20
    costs are assumed (fee + slippage); funding is not modeled; no fill model beyond next bar open
    validation: insufficient evidence
    holdout events 5 < 200
    holdout days 5 < 25
  quant-v1:residual_shock:h8:fade:T144_ks3_R1   candidate
    0 of 2 resolved · 0 days · 0 coins
    measured range unavailable
    range of measured outcomes, not a prediction
    outcome shares: unavailable — no resolved events
    costs are assumed (fee + slippage); funding is not modeled; no fill model beyond next bar open
    validation: insufficient evidence
    no promotion record for this rule id and config hash
    suppression — quote volume below the cost model floor × 4
Provenance
  Config hash 9f2c1ab4de07
  Code revision 0f1e2d3
  Generated 2026-10-14 00:00:00 UTC
  registered rules
    quant-v1:breakout_start:rvol4:fade:T288_ks1.5_R1
    quant-v1:breakout_start:rvol4:cont:T288_ks1.5_R1
    quant-v1:residual_shock:h8:fade:T144_ks3_R1
  feature versions
    hurst hurst-v1
    measure measures-v1
    mfdfa mfdfa-v1
    feature_store features-v1
    pivots pivots-v1
```

The other states:

- **Empty store** — exactly one block:
  `insufficient evidence — no shadow events recorded yet`
  (the reason is the API's `reasons[0]`, with the established wording as
  the fallback when the research read did not answer).
- **Unreadable store (503) / unreadable research surface** —
  `insufficient evidence — <the error message>`; never blank.
- **A rule that passed its gate** renders the allowed label
  `validation: not actionable` plus `gate passed — research only`, and its
  gate reasons. It never renders `ACTIONABLE` (the string appears nowhere
  in the payload, in the view or in the tests).

### House-rule checklist (how each is met)

| Rule | How |
|---|---|
| Banned vocabulary (`confidence`, `probability`, `tier`, `TOTAL`, `1m`, `leverage`, `liquidation`, `scanned`) | never rendered; the payload is scanned by the backend test, the view by the existing + new frontend tests. `cost_note` deliberately says "fee + slippage" (not "tiered slippage"). |
| Allowed labels only | `SHADOW`, `not actionable`, `insufficient evidence`, `measured range (lo–hi)` + the static caption `range of measured outcomes, not a prediction`, and the house literal `unavailable` for a null. |
| Uncertainty is interval endpoints only | the payload has no mean (`net_r`/`mean_r` are absent and the test asserts it); the view renders `measured range (lo–hi)` or `measured range unavailable`. |
| No `<table>` in the Research view | the view uses `<ul>`/cards only; asserted by the existing test and the new one. |
| No raw `fetch` | the view uses `createClient` only. |
| Theme tokens only | `rounded-xl border border-border bg-card px-4 py-3`, `text-muted`, `text-fg`, `tabular-nums` — no hex. |
| Unavailable is an explicit `null` → `unavailable` | the interval's endpoints, the outcome shares, `config_hash` and `code_rev` are `null` in the payload and render the literal `unavailable` (never a zero). |
| No frontend math | every number is rendered verbatim from the API (`{x} of {y}` is concatenation, the house `StatCards` pattern). |
| Timestamps | `generated_ts` (UTC-seconds int) → `2026-10-14 00:00:00 UTC`. |
| Every component has a `// House rules:` header | the file header plus one per new component. |
| Read-only | the view issues two GETs; the four POST commands are never issued; the new route is a GET and writes nothing. |
| TypeScript strict / `import type` / ESLint 0 warnings | `npm run typecheck` and `npm run lint --max-warnings 0` are clean. |

---

## 4. Acceptance criteria → evidence

| # | Criterion | Evidence |
|---|---|---|
| 1 | The existing Research test still passes unchanged | `app/tests/test_research.test.tsx` is untouched (`git diff` empty) and passes (8 tests). |
| 2 | New frontend tests cover the counts/denominators, the interval form, `unavailable`, the insufficient block **with its reason**, the provenance rows, and the banned-word scan | `app/tests/test_research_surface.test.tsx` (14 tests) |
| 3 | `GET /quant-research` is read-only and auth-gated: no token → 401, bad token → 401, good token → 200 with the documented shape | `tests/test_ws_d_research_surface.py` (the three auth checks + the shape check) |
| 4 | Honest with an empty store: `status: "insufficient"` + a reason, null/0 denominators, and the UI renders the insufficient-evidence block | the Python "empty store" checks + the frontend "an empty store renders the insufficient-evidence block once" test |
| 5 | Never claims a validation that did not happen; the UI never renders `ACTIONABLE` | `validation.status` is `validated` only for a passed record (asserted both ways); the view renders `not actionable`; the frontend test asserts `queryByText("ACTIONABLE")` is null and the section text has no `ACTIONABLE`. |
| 6 | The dev mock serves the new route (and keeps serving `/quant-status`) | `app/dev/mock_service.py` + fixtures; 3 new mock tests; the suite is green. |
| 7 | `npm run typecheck`, `npm run lint` (0 warnings), `npm run test`, `npm run build` all pass | §2.4–2.7. |

---

## 5. Concerns / deviations (with reasons)

1. **The coverage denominator is my interpretation.** The brief names
   `coverage: {attributed, eligible, ratio, reason}` but not what
   "eligible" means. I defined: *attributed* = shadow events emitted
   (attributed to a rule); *eligible* = attributed + the recorded
   suppressions (`quant_suppressed.n`), i.e. the opportunities the watcher
   **recorded**; `ratio` = attributed / eligible, `null` when nothing was
   recorded. The `reason` states the limit: "opportunities the watcher
   never recorded are not in the denominator", so the ratio is a lower
   bound and says so. If the orchestrator means the archive-span coverage
   of `proto/quant/coverage.py` (full/partial/insufficient split support),
   that is a different question that needs the bars archive — it would also
   touch the holdout *span* (not its evaluation), and I did not go there.
2. **Calibration is honestly `unavailable`, not computed.** The shadow
   store records *outcomes*, not *predicted* outcomes, so no predicted-vs-
   realised table can be built from it without inventing the predicted
   side. The route therefore answers `available: false` with the reason
   "the shadow store records outcomes, not predicted outcomes - calibration
   is not computed", and the view renders `unavailable` + that reason. The
   `buckets` array stays empty (the type and the rendering exist for a
   future producer, e.g. Workstream B's `inference.chronological_
   probability`).
3. **The validation label mapping.** The allowed Research labels are
   `SHADOW` / `not actionable` / `insufficient evidence` /
   `measured range (lo–upper)`, and the brief forbids `ACTIONABLE` — so a
   rule that *passed* its gate cannot be labelled "validated". I render
   `validation: not actionable` + `gate passed — research only` for it, and
   `validation: insufficient evidence` + the gate's own reasons for a rule
   that did not. The status words therefore stay inside the allowed
   vocabulary while the gate outcome stays visible. If the orchestrator
   prefers the literal API word (`validated` / `not_validated`), that is a
   one-line change in `RuleRows` — but it would introduce a label outside
   the allowed set.
4. **The outcome shares are rendered as point proportions.** The payload
   carries `outcome_shares` (the brief's shape), and the view renders them
   as `outcome shares of 12 resolved: tp 0.42 · sl 0.25 · time 0.33` — a
   *composition* of the measured outcomes with its denominator named, not a
   point estimate of the result (the result's uncertainty is the interval,
   and the mean is not published at all). A stricter reading of
   "uncertainty is interval endpoints only" would drop the shares from the
   view; the payload would keep them.
5. **The research store read is SHADOW-only, the CLI report is not.** The
   view is the shadow research log and the house rule says shadow counts
   must never include `ACTIONABLE` rows, so `ReadOnlyStore` filters
   `mode='SHADOW'` and the counts, the denominators and the interval all
   describe the same rows. `proto/quant/report.py` (which I must not
   touch) does **not** filter, so on a store that still holds legacy
   `ACTIONABLE` rows the view and the CLI report will differ. That is
   intentional and is the same reason the two surfaces count differently
   today (`/quant-status` also separates `actionable_events`).
6. **`status: "insufficient"` means "no events", as the brief states.** A
   store with events but **no resolved** outcome is `status: "ok"`; the
   missing measurement shows up as `interval: null` → `measured range
   unavailable` and `outcome_shares: {null, null, null}` → `unavailable`,
   per rule. The brief says "status is insufficient when there are no
   events, with a reason", so I did not widen it; if the orchestrator
   wants "insufficient" also when nothing is resolved, that is one
   condition in `build()`.
7. **`days` and `coins` count the RESOLVED rows** (in the sample and per
   rule). They are the denominators the day-cluster interval is computed
   on, so the two always agree; the view says so in the sample footnote
   ("days and coins count the resolved rows the measured range is computed
   on"). The alternative (all events) would have made the sample's days
   disagree with the interval's clustering unit.
8. **`code_rev` is best-effort.** `ALT_RADAR_CODE_REV` wins, then
   `git rev-parse --short HEAD` in the repo root (2 s timeout, cached), else
   `null` → the view renders `unavailable`. A deployed service outside a
   git checkout will show `unavailable` rather than a guessed revision.
9. **`config_hash` is the CURRENT config's hash** (as in
   `report.rule_report` and `/quant-status`). Events carry their own
   `config_hash` column; the surface does not filter by it and does not
   warn about a mismatch, because the payload shape has no place for that
   warning and the brief does not ask for it. A store that mixes configs
   would therefore mix them in the figures — the same behaviour as the
   existing research report.
10. **Environment facts, not code problems.**
    - The worktree had **no `app/node_modules`**. I copied the
      orchestrator checkout's `node_modules` into the worktree (it is
      git-ignored; `git status` is clean apart from the untracked
      `task-D-brief.md`) so that `npm run test/typecheck/lint/build` could
      run. No dependency was added, upgraded or installed into the repo.
      (An `npx vitest` invocation before the copy pulled a transient
      vitest into the npx cache only — it never touched the project.)
    - `app/dev/test_mock_service.py` hardcodes port 18741, which the
      **live** service owns on this host. I ran the suite on port 18999
      with the module constant patched, and did not restart, reconfigure
      or otherwise touch the live service.

---

## 6. Commits

| Hash | Message |
|---|---|
| `e26c20a` | `feat(research): read-only research surface route + expanded Research view (WS-D)` |
| (this commit) | `docs(quant): file the WS-D workstream report` |

`task-D-brief.md` is left untracked on purpose (it is the orchestrator's
input document, not a deliverable — the WS-A branch does the same).

---

# Fix round 1

**Status: DONE** — all 12 findings are addressed; nothing was dropped
silently. The three items the reviewer marked "documented, no change
needed" are recorded in §7.1.

Round-1 commit: see the last line of this file (HEAD). The round-1
implementation is one commit on top of `5c63f6f`.

## 7. The findings, one by one

### 1 (Important) — the mock's event counts were impossible, and the test asserted the wrong invariant

**What was wrong.** The live route counts `emitted` as the rows that are
**still open** (`state='EMITTED'`, `proto/service.py:1464-1469`), so
`total = emitted + resolved` must hold. The dev fixture said
`{total: 44, emitted: 44, resolved: 17}` — impossible — and my mock test
asserted `total == emitted`, which pinned the impossible invariant.

**Changed.**
- `app/dev/fixtures.json:12543` — `quant_status.event_counts` is now
  `{"total": 44, "emitted": 27, "resolved": 17}` (27 + 17 = 44). The
  research fixture's sample already matched (`events 44 / open 27 /
  resolved 17`), so the two routes now agree.
- `app/dev/test_mock_service.py:559` — the test now asserts the real
  invariant: `counts["emitted"] + counts["resolved"] == counts["total"]`.
- `app/src/pages/Research.tsx:459` — the pre-existing "Shadow events" card
  now carries the clarification the reviewer was pointing at:
  *"emitted counts the shadow events that are still open; resolved counts
  the ones that finished"*. Without it the card's "Emitted 27" sat next to
  the sample's "Open 27 of 44" and the overloaded word "emitted" (still
  open, in `/quant-status`; "every event emitted", in the research sample)
  made the two footnotes ambiguous. The sample's denominators were renamed
  accordingly: **"resolved of all shadow events"** / **"open of all shadow
  events"** (the brief's example wording "resolved of emitted" is not used
  any more — in this contract "emitted" means still-open).

**Covered by.** `app/dev/test_mock_service.py::test_quant_status_route`
(the invariant) and `app/dev/test_mock_service.py::test_quant_research_route`
(the payload shape).

### 2 (Important) — a gate-passed rule read as "validation failed"

**What was wrong.** `validation: not actionable` inverts the meaning of a
validation status.

**Changed.** The payload keeps the API's vocabulary (`validated` /
`not_validated`, unchanged). The view now renders the status in its own
words — `app/src/pages/Research.tsx:239-240`:

```
validation: gate passed — research only
validation: gate not passed — research only
```

The standing "not actionable" statement stays where it belongs: in the
SHADOW badge at the top of the view ("SHADOW — findings here are not
actionable"). The file's header comment records the rule ("a status never
borrows another status' label").

**Covered by.** `app/tests/test_research_surface.test.tsx` — "a passed gate
record still renders as research, never as an action" now asserts
`/gate passed — research only/` **and** `/gate not passed — research only/`
are rendered, that `ACTIONABLE` never appears, and that the badge still
carries "not actionable".

### 3 (Minor, house rule) — two counts were computed in the frontend

**What was wrong.** `research.rules.filter(...).length` (the validated
count) and `research.calibration.buckets.length` (the bucket count) were
computed in the view.

**Changed.** Both counts now come from the API:
- `proto/quant/research_surface.py:356` — a new top-level
  `"validation": {"validated": <int>, "rules": <int>}` block.
- `proto/quant/research_surface.py:352` — `calibration.count` (the number
  of buckets).
- `app/src/pages/Research.tsx:155` and `:188` — the view renders
  `research.calibration.count` and `research.validation.validated` /
  `research.validation.rules`.
- `app/src/api/types.ts` — `QuantResearchValidationSummary` and
  `calibration.count`; `app/src/api/client.ts` — the shape guard now
  requires the `validation` block (a body without it is a contract error,
  never a partial surface).

The only remaining `.length` uses in the view are boolean guards
(`rule_ids.length > 0` → render the list or the literal "unavailable"),
never a rendered number.

**Covered by.** `app/tests/test_research_surface.test.tsx` — "the coverage
and validation status rows come from the API" asserts "11 of 15" and
"1 of 2 rules"; `tests/test_ws_d_research_surface.py` pins the two new
payload blocks.

### 4 (Minor, house rule) — the outcome shares were point proportions, rounded in the frontend

**What was wrong.** `outcome shares of 12 resolved: tp 0.42 · sl 0.25 ·
time 0.33` renders a proportion as a result, and `toFixed(2)` rounds it
in the frontend.

**Changed.** The shares are **not rendered any more** (the payload keeps
them for other consumers — the brief's shape). `formatShare` and the whole
shares line are gone from `app/src/pages/Research.tsx`; nothing in the view
rounds a number any more.

**Covered by.** `app/tests/test_research_surface.test.tsx` — "the outcome
shares are never rendered as a point proportion" asserts that no
`/outcome shares/` and no `tp 0.…` appears in the rendered text.

### 5 (Minor) — `toFixed(4)` could render a non-zero endpoint as `0.0000`

**Changed.** `app/src/pages/Research.tsx:50` — a new `formatEndpoint`:
four decimals as before, but an endpoint that would round away falls back
to its own digits (`String(value)`), so a non-zero endpoint is never shown
as a zero. The label form `measured range (lo–hi)` is unchanged.

**Covered by.** the existing `measured range (0.0123–0.0456)` assertion
(existing test file + the new one) and the new "the uncertainty renders as
interval endpoints only" test.

### 6 (Minor) — a rule with 0 resolved rows reported `days: 0, coins: 0`

**What was wrong.** The rule's interval is `null` (nothing to measure) but
its days/coins said `0` — a zero standing in for "not measured".

**Changed.** `proto/quant/research_surface.py:242` — a rule with no
resolved row now reports `days: null` and `coins: null` (the type in
`app/src/api/types.ts` is now `number | null`), and the view renders the
literal **"days unavailable · coins unavailable"**
(`app/src/pages/Research.tsx:227-230`).

**Covered by.** `tests/test_ws_d_research_surface.py` — "a rule with no
resolved row reports null days and coins" (and null interval / null
shares); `app/tests/test_research_surface.test.tsx` — "an unavailable value
renders the literal unavailable"; `app/dev/test_mock_service.py` — the
same for the dev fixture.

### 7 (Minor) — the blanket `except Exception` answered 503 with no trace

**Changed.** `proto/service.py:1571` — the handler now logs before
answering:

```python
LOG.warning("research surface unavailable: %s: %s",
            type(exc).__name__, exc)
```

The line carries the exception type and message only — no token, no
request body — and the service's existing redacting filter still applies.

**Covered by.** `tests/test_ws_d_research_surface.py` — a store with an
unreadable `quant_event` table: the route answers 503 **and** a
`WARNING` record is captured on the `proto.service` logger, and the
captured message contains neither the token nor anything else.

### 8 (Minor, house rule) — the frontend banned scan was weaker than the house rule

**What was wrong.** `/\btier\b/i` lets "tiered" through.

**Changed.** `app/tests/test_research_surface.test.tsx` — the scan is now
a plain-substring scan over the lower-cased text, exactly like the
backend's payload scan: `confidence, probability, tier, total, 1m,
leverage, liquidation, scanned` — each checked with `toContain`, so
"tiered" (or "Total") cannot slip through. (The pre-existing
`app/tests/test_research.test.tsx` is left untouched, as required.)

**Covered by.** the new test file's "the research surface carries none of
the banned words".

### 9 (Minor) — the sample footnote was wrong

**What was wrong.** The footnote claimed the sample's days/coins count
"the resolved rows the measured range is computed on" — which is the
*rule's* scope, not the sample's.

**Changed.** The backend now makes the two scopes explicit and the
footnote says which is which:
- `proto/quant/research_surface.py` `_sample()` — the sample's `days` and
  `coins` are the distinct days and coins of the **whole sample** (every
  shadow event).
- `app/src/pages/Research.tsx:125` — the footnote reads: *"sample days and
  coins count every shadow event in the sample; a rule's days and coins
  count its resolved rows — the measured range's denominators"*.

**Covered by.** `tests/test_ws_d_research_surface.py` — "sample days count
the distinct days of the whole sample" / "sample coins count the distinct
coins of the whole sample" (4 and 5 for the fixture store), next to the
rule-level "days are the measured range's denominators".

### 10 (Minor) — the loading paragraph had no `role="status"`

**Changed.** `app/src/pages/Research.tsx:405-410` — the "loading research…"
paragraph now carries `role="status"`, like its insufficient/error
siblings.

**Covered by.** the existing test (the loading state is rendered before
the fetch resolves) plus the unchanged existing Research test.

### 11 (Minor) — the read amplification of the per-frame re-read

**What was wrong.** Every `quant.shadow` frame triggers two GETs, and
each re-ran a 1500-sample bootstrap per rule (~300 ms for 2 500 events /
4 rules); `code_rev()` also shelled out to git inside the request.

**Changed.**
- `proto/quant/research_surface.py:72,381` — a new `build_cached()`: the
  assembled surface is reused for `CACHE_TTL_S = 15 s`. The cache key is
  the store's **content fingerprint** (`_content_key()`: the store's path
  + the event/outcome counts and maxima + a digest of the promotion
  records), so a change invalidates it immediately; the TTL is the upper
  bound on staleness. The cache is in memory and the surface stays
  **read-only** — nothing is written, the holdout is still never touched.
- `proto/quant/research_surface.py:75,167` — `code_rev()` is now cached
  for `CODE_REV_TTL_S = 300 s` (the git shell-out happens at most once per
  five minutes, not once per request).
- `proto/service.py:1563` — the route calls `build_cached()`.

**Covered by.** `tests/test_ws_d_research_surface.py` — "an unchanged store
reuses the assembled surface" (the same object is returned) and "a changed
store invalidates the cache" (a new event ⇒ a new surface that sees it).

### 12 (Minor, dev fidelity) — the mock's suppression reasons were invented

**What was wrong.** The fixture used "cooldown from the previous event" and
"quote volume below the cost model floor" — strings the watcher never
emits.

**Changed.** `app/dev/fixtures.json` now uses the reasons the real
watcher writes (`proto/quant/selector.py` and `proto/quant/engine.py`):
**`cooldown`** and **`no_vol`** — in `quant_status.suppression_top` and in
the research fixture's per-rule `suppression` rows.

**Covered by.** `app/dev/test_mock_service.py::test_quant_research_route`
(the fixture's shape) and the mock suite's banned-word scan.

## 7.1 Documented, no change needed (as the reviewer directed)

- **The live store may hold legacy `ACTIONABLE` rows.** `research_surface`
  is deliberately SHADOW-only while `proto/quant/report.py` is not, so the
  CLI report and this view can differ on such a store. Unchanged (still
  true, still deliberate — the view is the shadow research log and the
  shadow-only house rule says a shadow count never includes an actionable
  row).
- **`provenance.rule_ids` is the REGISTERED rules**, not the rules the
  surface reports. The view labels the list "registered rules" and the
  sample carries its own `rules` count. Unchanged.
- **The plan's §1.5 mentions a "data hash" in the provenance; the brief's
  payload shape supersedes it and `code_rev` fills that slot.** Noted
  here: there is no data hash in the payload, and `code_rev` (the short
  git revision, or `null` → "unavailable") stands in its place. If the
  orchestrator wants a real data hash (a digest of the bars archive), it
  is a separate field to add — it was not in the brief's shape and it is
  not computed here.

## 8. What the Research view now shows (round 1, exact strings)

Rendered from the dev fixtures (whitespace collapsed, re-indented for
readability — the strings are verbatim):

```
Research
SHADOW — findings here are not actionable
watcher: healthy
Shadow events
  Emitted 27
  Resolved 17
  Shadow events 44
  emitted counts the shadow events that are still open; resolved counts the ones that finished
Suppression
  quant-v1:breakout_start:rvol4:fade:T288_ks1.5_R1 — cooldown 9
  quant-v1:residual_shock:h8:fade:T144_ks3_R1 — no_vol 4
Sample
  Resolved 17 of 44   resolved of all shadow events
  Open 27 of 44       open of all shadow events
  Days observed 10
  Coins observed 8
  Rules observed 3
  sample days and coins count every shadow event in the sample; a rule's days
  and coins count its resolved rows — the measured range's denominators
Status
  Calibration unavailable
    the shadow store records outcomes, not predicted outcomes - calibration is not computed
  Coverage 44 of 57
    attributed events over recorded opportunities (emitted + recorded suppressions);
    opportunities the watcher never recorded are not in the denominator
  Validation 0 of 3 rules with a passed gate record
Rule measurements
  quant-v1:breakout_start:rvol4:fade:T288_ks1.5_R1   candidate
    12 of 28 resolved · days 9 · coins 6
    measured range (0.0042–0.0311)
    range of measured outcomes, not a prediction
    costs are assumed (fee + slippage); funding is not modeled; no fill model beyond next bar open
    validation: gate not passed — research only
    holdout events 12 < 200
    holdout days 9 < 25
    suppression — cooldown × 9
  quant-v1:breakout_start:rvol4:cont:T288_ks1.5_R1   control
    5 of 14 resolved · days 5 · coins 4
    measured range (-0.0112–0.0094)
    range of measured outcomes, not a prediction
    costs are assumed (fee + slippage); funding is not modeled; no fill model beyond next bar open
    validation: gate not passed — research only
    holdout events 5 < 200
    holdout days 5 < 25
  quant-v1:residual_shock:h8:fade:T144_ks3_R1   candidate
    0 of 2 resolved · days unavailable · coins unavailable
    measured range unavailable
    range of measured outcomes, not a prediction
    costs are assumed (fee + slippage); funding is not modeled; no fill model beyond next bar open
    validation: gate not passed — research only
    no promotion record for this rule id and config hash
    suppression — no_vol × 4
Provenance
  Config hash 9f2c1ab4de07
  Code revision 0f1e2d3
  Generated 2026-10-14 00:00:00 UTC
  registered rules
    quant-v1:breakout_start:rvol4:fade:T288_ks1.5_R1
    quant-v1:breakout_start:rvol4:cont:T288_ks1.5_R1
    quant-v1:residual_shock:h8:fade:T144_ks3_R1
  feature versions
    hurst hurst-v1
    measure measures-v1
    mfdfa mfdfa-v1
    feature_store features-v1
    pivots pivots-v1
```

What changed against round 0: the outcome-share line is gone, the
validation line reads `gate not passed — research only` (and
`gate passed — research only` for a rule that passed), the rule's
denominators read `days unavailable · coins unavailable` when they cannot
be measured, the sample's denominators are named "of all shadow events",
and the Shadow-events card explains what "Emitted" counts.

The payload shape after round 1 (additive, `version` unchanged — the
route has not shipped, so the shape was corrected before merge):

```json
{
  "version": "research-surface-v1",
  "status": "ok" | "insufficient",
  "reasons": ["…"],
  "generated_ts": 1700000000,
  "provenance": {"config_hash": "…|null", "code_rev": "…|null",
                  "rule_ids": ["…"],
                  "feature_versions": [{"name": "…", "version": "…"}]},
  "sample": {"events": 0, "resolved": 0, "open": 0,
             "days": 0, "coins": 0, "rules": 0},
  "rules": [{"rule_id": "…", "role": "candidate|control|?",
             "events": 0, "resolved": 0, "open": 0,
             "days": 0|null, "coins": 0|null,
             "interval": {"lo": 0.0|null, "hi": 0.0|null} | null,
             "outcome_shares": {"tp": null, "sl": null, "time": null},
             "cost_note": "…",
             "suppression": [{"reason": "…", "n": 0}],
             "validation": {"status": "not_validated|validated",
                            "reasons": ["…"]}}],
  "calibration": {"available": false, "buckets": [], "count": 0,
                  "reason": "…"},
  "coverage": {"attributed": 0, "eligible": 0, "ratio": null, "reason": "…"},
  "validation": {"validated": 0, "rules": 0}
}
```

## 9. Round-1 commands and their real output

### `python3 tests/test_ws_d_research_surface.py`

```
  PASS  a rule with no resolved row reports null days and coins
  PASS  the validation summary counts the passed rule
  PASS  an unchanged store reuses the assembled surface
  PASS  a changed store invalidates the cache
  ...
  PASS  an internal failure answers 503
  PASS  the internal failure is logged
  PASS  the log line names the failure and leaks no token

ALL PASS  tests/test_ws_d_research_surface.py  (86 checks)
```

(exit code 0; 72 → 86 checks.)

### `python3 tests/run_all.py`

```
PASS  test_ws_d_research_surface.py  [ALL PASS  tests/test_ws_d_research_surface.py  (86 checks)]
PASS  test_ws_f_startup.py  [ALL PASS  tests/test_ws_f_startup.py  (91 checks)]

ALL SUITES PASS (85 files)
```

(exit code 0; 85 files, 0 failures.)

### The dev-mock service's own suite (port 18999 — the live service owns 18741)

```
Ran 51 tests in 1.067s

OK
```

(exit code 0.)

### `npm run test` (in `app/`)

```
 Test Files  14 passed (14)
      Tests  200 passed (200)
   Start at  01:29:09
   Duration  26.34s (transform 3.84s, setup 7.85s, collect 19.15s, tests 41.51s, environment 64.05s, prepare 17.22s)
```

(exit code 0; 199 → 200 tests: the new file has 15.)

### `npm run typecheck`

```
> alt-radar-dashboard@0.1.0 typecheck
> tsc --noEmit
```

(exit code 0, no output.)

### `npm run lint`

```
> alt-radar-dashboard@0.1.0 lint
> eslint . --max-warnings 0
```

(exit code 0, no warnings, no output.)

### `npm run build`

```
> alt-radar-dashboard@0.1.0 build
> tsc --noEmit && vite build

vite v5.4.21 building for production...
transforming...
✓ 66 modules transformed.
rendering chunks...
dist/index.html                   0.44 kB │ gzip:  0.28 kB
dist/assets/index-2Pm9sww9.css    11.81 kB │ gzip:  3.22 kB
dist/assets/index-BxMF15B1.js    367.35 kB │ gzip: 114.47 kB
✓ built in 9.13s
```

(exit code 0.)

**One flake to report honestly.** On the first round-1 `npm run test` run,
`tests/test_sse.test.ts > useEvents — reconnect > reconnect after close
sends Last-Event-ID, delayed by backoff` failed with
`expected 2 to be 1` — a timing-sensitive assertion about the SSE
reconnect backoff in a file this workstream does not touch
(`app/src/api/sse.ts` is on the do-not-touch list). It passed on the
isolated re-run and on the full re-run quoted above (200/200). It is a
pre-existing flake under load, not a regression from round 1.

## 10. Round-1 concerns / notes

1. **The payload grew two fields** (`calibration.count` and the top-level
   `validation` block) and two rule fields became nullable
   (`days`/`coins`). The version tag stays `research-surface-v1`: the
   route has not shipped, so the shape was corrected before merge rather
   than after. If the orchestrator prefers a `v2` tag for the changed
   shape, that is a one-line change in `research_surface.SURFACE_VERSION`
   plus the two test assertions.
2. **The sample's days/coins now cover the whole sample** (every shadow
   event) while a rule's days/coins stay the measured range's denominators
   (its resolved rows). The two scopes are deliberately different and the
   view's footnote says so; a reviewer reading only the payload should
   note that the sample's `days`/`coins` are NOT the interval's
   denominators.
3. **The "Emitted" label of the pre-existing Shadow-events card is
   pre-existing app wording** for a count the route defines as still-open.
   I did not rename it (the existing test is unchanged and the label comes
   from the `/quant-status` contract); I added the clarifying line
   instead, and renamed the sample's denominators to "of all shadow
   events" so the two senses of "emitted" cannot be confused.
4. **The 15 s surface cache** trades a bounded staleness window for the
   read amplification of the per-frame re-read. The content fingerprint
   invalidates it the moment the store changes, so in practice the window
   is much shorter than 15 s; a stale figure can never survive a content
   change.
