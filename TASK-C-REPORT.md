# TASK C report — Workstream C: the preregistered research workflow

**Status: DONE_WITH_CONCERNS** (everything in the brief is implemented and
green; the concerns are the honest ones listed in §5 — chiefly that the
288-config record had to be registered under a *new* hypothesis id, and
that it is a retrospective registration).

- Worktree: `/tmp/opencode/wt-c`, branch `feature/ws-c-prereg`
- Base: `ef438e6` (Workstream A, `feature/ws-a-measurement`)
- Commits: `40d805f` (implementation + data), `8517488` (code fingerprint
  pinned), and this report (the tip of `feature/ws-c-prereg`; a commit
  cannot contain its own hash, so its hash is in the hand-off).
- Nothing was pushed. `data/` was never written. The one-shot holdout was
  never evaluated, unlocked or tuned against. The 288-configuration
  evaluation was **not** re-run.

---

## 1. What changed, file by file

### 1.1 `proto/quant/prereg.py` (NEW, 699 lines)

The preregistration record and the append-only variant registry.

```python
PREREG_VERSION = "prereg-v1"

class PreregError(ValueError):        # carries .errors = [<violation strings>]
    ...

@dataclass(frozen=True)
class Variant:
    variant_id: str
    hypothesis_id: str
    description: str
    config_hash: str
    result: dict          # {"status": "pending"|"passed"|"failed"|"rejected",
                           #  "metric": str, "value": float|None,
                           #  "n": int, "ci_lo": float|None,
                           #  "ci_hi": float|None, "detail": dict}
    tested_ts: int
    evidence: list        # [{"path": str, "sha256": str}]

@dataclass(frozen=True)
class PreregRecord:
    id: str
    title: str
    status: str
    hypothesis: str
    feature_definitions: list      # [{"name","version","definition","params"}]
    universe: str
    point_in_time_membership: str
    horizons: list                 # e.g. ["1h","4h","24h","7d"]
    event_rules: list              # [{"rule_id","family","params","side"}]
    costs: dict                    # {"fee_per_side","slip_tiers","funding","source"}
    splits: dict                   # {"dev":[lo,hi],"val":[lo,hi],"holdout":[lo,hi]}
    embargo_days: float
    statistics: dict               # {"metric","threshold","denominator","test"}
    promotion_criteria: list       # [{"name","test","threshold"}]
    family: list                   # the declared experiment family (hypothesis ids)
    config_hash: str
    data_hash: str
    code_hash: str
    variants: list                 # [Variant]
    created_ts: int
    updated_ts: int

def file_sha256(path) -> str
def data_hash(paths: dict) -> str            # sha256 over sorted (name, sha256) pairs
def git_rev(root) -> str | None              # helper (best effort)
def code_hash(root: str, files: list) -> str # git rev (if available) + file digests
def new_record(id, title, status="registered", hypothesis="", *,
               feature_definitions=None, universe="",
               point_in_time_membership="", horizons=None, event_rules=None,
               costs=None, splits=None, embargo_days=1.0, statistics=None,
               promotion_criteria=None, family=None, config_hash="",
               data_hash="", code_hash="", variants=None,
               created_ts=None, updated_ts=None) -> PreregRecord
def save(record, directory) -> str           # <directory>/<ID>.json, atomic; returns the path
def load(directory, id) -> PreregRecord
def load_all(directory) -> list[PreregRecord]
def validate(record, *, known_hypothesis_ids=None) -> list[str]
def register_variant(record, variant, directory) -> PreregRecord
# serialisation helpers: record_to_dict / record_from_dict /
#                        variant_to_dict / variant_from_dict
```

Behaviour implemented:

- **Sidecar, not a rewrite.** `save()` writes `<directory>/<ID>.json`
  (temp file + `os.replace`, so a reader never sees a half-written file).
  The markdown front-matter is never touched; `docs/hypotheses/*.md` keeps
  validating clean under `proto/hypothesis_registry`.
- **Append-only registry.** `register_variant()` compares the incoming
  result against the variant in the record **and** against the stored file:
  a `variant_id` that already exists keeps its result forever; a changed
  result raises `PreregError("variant-result-changed: ...")` (a
  `ValueError` subclass whose `.errors` list carries the same violation
  string `validate()` uses) and nothing is written; an identical result is
  idempotent and the file is left byte-identical; a new `variant_id` is
  appended and persisted.
- **`validate()`** returns `[]` when valid, otherwise a list of
  `<rule>: <detail>` strings using the registry's vocabulary where the
  rule is the same rule: `required-field:`, `within-window:`, `ordering:`,
  `embargo:`, `unknown-status:`, `bad-id:`, `unknown-hypothesis:`; plus
  the machine-readable `bad-hash:`, `bad-family:`,
  `missing-denominator:`, `unknown-variant-status:`,
  `family-mismatch:`, `bad-evidence-hash:`, `evidence-missing:`,
  `evidence-hash-mismatch:`, `variant-result-changed:`.
  It checks: the status-gated required-field sets (mirroring
  `hypothesis_registry`, extended with the new fields), `dev_end <=
  val_start <= val_end <= holdout_start`, the embargo (`7` days for a
  `"7d"` horizon, at least `1` day otherwise) **and** that both window
  gaps are at least `embargo_days`, a non-empty family whose ids are known
  hypothesis ids, non-empty lowercase-hex `config_hash` / `data_hash` /
  `code_hash`, a denominator `n` on every non-`pending` variant result,
  and a 64-hex sha256 on every evidence entry — which is **re-hashed from
  disk**, so a modified evidence file is detected and a wrong digest is
  flagged.
- **`data_hash()` / `code_hash()`** read only; a missing input raises
  `FileNotFoundError` naming it (never a silent skip).

### 1.2 `proto/quant/research_report.py` (NEW, 531 lines)

```python
RESEARCH_REPORT_VERSION = "research-report-v1"

def slice_results(events, rule, geom, window, *, cfg=DEFAULT,
                   liquidity_groups=None, regime_groups=None,
                   min_cell=30, seed=1) -> dict
def attach_evidence(report, *, data_hash=None, code_hash=None) -> dict
```

- **Overall block**: `n`, the mean net R, the 95% day-cluster bootstrap
  interval, `t`, `n_days`, `n_coins` and the TP/SL/TIME shares. The
  friction model and the value formula mirror `research.eval_config`
  (`round_trip(qv24)` cost, `stop_distance`, `net_r`), so the report and
  the evaluation harness cannot drift apart.
- **Slices**: `coin` (top/bottom by mean, each with its `n`),
  `period` (the window cut into 5 equal chronological sub-periods),
  `regime` (trailing volatility terciles) and `liquidity` (the
  `CostModel.slip_tiers` boundaries, using each event's `qv24`).
- **Denominators everywhere**: every cell reports `n`, `n_days` and
  `n_coins`; a cell with `n < min_cell` is
  `{"status": "insufficient", "n": n, "n_days": .., "n_coins": ..,
  "min_cell": min_cell}` and reports **no mean**. This applies to the
  overall block too (the literal reading of "every cell").
- **Evidence**: the report carries `config_hash`, `code_hash` (the digest
  of the ten source files that produce it) and a `data_hash` slot.
- **The holdout is refused, never evaluated**: a window that reaches into
  the holdout range returns `{"status": "refused", "reason":
  "holdout-sealed: ...", "overall": None, "slices": {}}` — nothing is
  computed. Leakage is checked with the harness' own
  `eval_harness.check_no_leakage`; a leaky window is refused with the
  violations listed. The module contains **no `open()` call at all**
  (asserted by the test through `ast`), so it cannot write
  `holdout_evaluated.json` or anything else.

### 1.3 `proto/quant/__main__.py` (MODIFIED, +56 lines)

```
python3 -m proto.quant prereg validate [--dir docs/hypotheses]
python3 -m proto.quant prereg show <ID> [--dir ...]
```

`validate` prints every record, its validation errors and a summary, and
exits 0 when all are valid, 1 otherwise. `show` prints one record as JSON
(exit 1 when it is missing). Both are read-only and never write to
`data/`.

### 1.4 `docs/hypotheses/HYD-0002-breakout-continuation-net-r.md` (NEW, data)

The markdown hypothesis for the sweep that was actually run, with the
full front-matter the registry requires (`status: rejected`, the windows,
the metric, the threshold, the denominator, the costs, `comparisons: 288`,
`rule_version: quant-v1`, `config_hash: d33cff513c30`) and a body that
states the outcome and the honest scope.

### 1.5 `docs/hypotheses/HYD-0002.json` (NEW, data)

The preregistration sidecar for HYD-0002 — see §4.

### 1.6 `tests/test_ws_c_prereg.py` (NEW, 566 lines)

91 self-checking assertions covering: the hash helpers (including the
missing-file error), every required field of a registered record, the
split ordering and the embargo rules, the family rules, the variant
denominator, the evidence hash (wrong digest, non-64-hex digest, missing
file, **tampered file detected by re-hashing**), the append-only
registry (append / idempotent / refused / no overwrite on disk), the
save-load round trip, the sliced report (denominators, the five
sub-periods, the regime and liquidity groups, the `insufficient` label
with no mean), the **holdout refusal**, the shipped 288-config record
(re-read from the artifact, sha256 recomputed, numbers compared), and the
CLI.

### 1.7 Files deliberately NOT touched

`proto/hypothesis_registry.py`, `proto/quant/gates.py`,
`proto/quant/registry.py`, `proto/quant/selector.py`,
`proto/quant/estimators.py` (+ `measures.py`, `mfdfa.py`),
`proto/quant/config.py`, `proto/quant/eval_harness.py`,
`proto/quant/stats.py`, `proto/quant/research.py`,
`proto/quant/barrier.py`, `docs/hypotheses/HYD-0001-funding-quintile-drift.md`,
`docs/implementation-reports/**` (the preserved artifact is byte-identical)
and everything under `data/`. The watcher's `promo = None` pinning and
the SHADOW-only behaviour are untouched; no promotion, no ACTIONABLE
output, no order placement.

---

## 2. Tests — every command and its real output

### 2.1 Tests first (RED)

```
$ python3 tests/test_ws_c_prereg.py
Traceback (most recent call last):
  File "/tmp/opencode/wt-c/tests/test_ws_c_prereg.py", line 28, in <module>
    from proto.quant import prereg
ImportError: cannot import name 'prereg' from 'proto.quant' (/tmp/opencode/wt-c/proto/quant/__init__.py)
```

(Exit status 1 — the module did not exist yet.)

### 2.2 Focused tests (GREEN)

```
$ python3 tests/test_ws_c_prereg.py
...
ALL PASS
```
(91 `PASS` lines, 0 `FAIL` lines, exit 0.)

```
$ python3 tests/test_hypotheses.py
...
ALL PASS
```
(48 `PASS` lines, exit 0 — the shipped markdown registry still validates
clean and `HYD-0001` is still present with `status: testing`.)

```
$ python3 tests/test_quant.py
...
ALL PASS
```
(74 `PASS` lines, exit 0.)

### 2.3 Full offline suite

```
$ python3 tests/run_all.py
PASS  test_adapter.py  [ALL PASS]
PASS  test_app_watcher.py  [ALL PASS]
PASS  test_backfill.py  [ALL PASS]
PASS  test_bar_quality.py  [ALL PASS]
PASS  test_barrier_math.py  [ALL PASS]
PASS  test_bybit.py  [ALL PASS]
PASS  test_coin_state.py  [ALL PASS]
PASS  test_collector.py  [ALL PASS]
PASS  test_decisions.py  [ALL PASS]
PASS  test_episode_outcomes.py  [ALL PASS]
PASS  test_episode_regressions.py  [OK]
PASS  test_episode_reporting.py  [OK]
PASS  test_epoch_cut.py  [ALL PASS]
PASS  test_epoch_integration.py  [ALL PASS]
PASS  test_flagging.py  [ALL PASS]
PASS  test_funding.py  [ALL PASS]
PASS  test_funding_route.py  [ALL PASS  tests/test_funding_route.py  (39 checks)]
PASS  test_gui.py  [OK]
PASS  test_gui_watcher.py  [OK]
PASS  test_hypotheses.py  [ALL PASS]
PASS  test_indicators.py  [ALL PASS]
PASS  test_measurement.py  [OK]
PASS  test_mtf.py  [ALL PASS]
PASS  test_oi_wiring.py  [ALL PASS]
PASS  test_outcomes.py  [ALL PASS]
PASS  test_picks.py  [ALL PASS]
PASS  test_planner.py  [ALL PASS]
PASS  test_plans.py  [ALL PASS]
PASS  test_prices.py  [ALL PASS  tests/test_prices.py]
PASS  test_prices_api.py  [ALL PASS  tests/test_prices_api.py  (52 checks)]
PASS  test_promotion.py  [ALL PASS  tests/test_promotion.py  (16 checks)]
PASS  test_qtgui.py  [ALL PASS]
PASS  test_qtgui_bottom.py  [ALL PASS  tests/test_qtgui_bottom.py — Qt bottom row green]
PASS  test_qtgui_cards.py  [ALL PASS  tests/test_qtgui_cards.py — Qt card grid green]
PASS  test_qtgui_chrome.py  [ALL PASS  tests/test_qtgui_chrome.py — Qt chrome green]
PASS  test_qtgui_dashboard.py  [ALL PASS  tests/test_qtgui_dashboard.py — Qt dashboard green]
PASS  test_qtgui_detail.py  [ALL PASS  tests/test_qtgui_detail.py — Qt coin detail green]
PASS  test_qtgui_layout.py  [ALL PASS  tests/test_qtgui_layout.py — Qt layout green]
PASS  test_qtgui_overview.py  [ALL PASS  tests/test_qtgui_overview.py — Qt dashboard pages green]
PASS  test_qtgui_pages.py  [ALL PASS  tests/test_qtgui_pages.py — Qt dashboard pages green]
PASS  test_qtgui_sigtable.py  [ALL PASS  tests/test_qtgui_sigtable.py — Qt signals table green]
PASS  test_qtgui_theme.py  [ALL PASS (tokens 12/12 exact + live Qt checks). OK]
PASS  test_qtgui_watcher.py  [ALL PASS  tests/test_qtgui_watcher.py — Qt watcher dock green]
PASS  test_quant.py  [ALL PASS]
PASS  test_quant_api.py  [ALL PASS  tests/test_quant_api.py  (30 checks)]
PASS  test_quant_coverage.py  [ALL PASS]
PASS  test_quant_engine.py  [ALL PASS]
PASS  test_quant_funding_regressions.py  [ALL PASS (funding regressions)]
PASS  test_quant_pipeline_regressions.py  [ALL PASS (pipeline regressions)]
PASS  test_quant_shadow_hook.py  [ALL PASS (15 checks)]
PASS  test_quant_shadow_only.py  [ALL PASS  tests/test_quant_shadow_only.py]
PASS  test_replay.py  [ALL PASS]
PASS  test_report.py  [ALL PASS]
PASS  test_scan_phases.py  [ALL PASS]
PASS  test_score_v3.py  [ALL PASS]
PASS  test_scorer.py  [ALL PASS]
PASS  test_service_auth.py  [ALL PASS  tests/test_service_auth.py  (19 checks)]
PASS  test_service_commands.py  [ALL PASS  tests/test_service_commands.py  (19 checks)]
PASS  test_service_events.py  [ALL PASS  tests/test_service_events.py  (22 checks)]
PASS  test_service_lifecycle.py  [ALL PASS  tests/test_service_lifecycle.py  (76 checks)]
PASS  test_service_routes.py  [ALL PASS  tests/test_service_routes.py  (33 checks)]
PASS  test_settlement_audit.py  [ALL PASS]
PASS  test_settlements.py  [ALL PASS  tests/test_settlements.py]
PASS  test_settlements_api.py  [ALL PASS  tests/test_settlements_api.py  (43 checks)]
PASS  test_settlements_regression.py  [ALL PASS  tests/test_settlements_regression.py  (23 checks)]
PASS  test_setup_copy.py  [ALL PASS]
PASS  test_snapshot.py  [ALL PASS]
PASS  test_snapshot_file.py  [ALL PASS]
PASS  test_stakeless.py  [ALL PASS]
PASS  test_universe.py  [ALL PASS]
PASS  test_watcher_eventstudy.py  [ALL PASS]
PASS  test_watcher_feed.py  [ALL PASS]
PASS  test_watcher_geometry.py  [ALL PASS]
PASS  test_watcher_journal.py  [ALL PASS]
PASS  test_watcher_lifecycle.py  [ALL PASS]
PASS  test_watcher_quant.py  [ALL PASS]
PASS  test_watcher_selector.py  [ALL PASS]
PASS  test_watcher_triggers.py  [ALL PASS]
PASS  test_watcher_ui_parity.py  [ALL PASS  tests/test_watcher_ui_parity.py — cross-surface parity green]
PASS  test_watcher_view.py  [ALL PASS]
PASS  test_ws5_eval_harness.py  [ALL PASS]
PASS  test_ws_a_measurement.py  [ALL PASS]
PASS  test_ws_c_prereg.py  [ALL PASS]

ALL SUITES PASS (83 files)
```
(Exit status 0. The run above is the final one, after the last code
change; two earlier runs also ended `ALL SUITES PASS (83 files)`.)

### 2.4 CLI smoke test

```
$ python3 -m proto.quant prereg validate --dir docs/hypotheses
HYD-0002  status=rejected  variants=1  Breakout continuation edges have no positive net edge after friction
1 record(s), 0 with errors
$ echo $?
0

$ python3 -m proto.quant prereg show HYD-0002 --dir docs/hypotheses | head -4
{
  "prereg_version": "prereg-v1",
  "id": "HYD-0002",
  "title": "Breakout continuation edges have no positive net edge after friction",
```

(A record with a corrupted hash makes the CLI print the errors and exit 1 —
verified separately.)

---

## 3. The registered 288-config record

**Where it lives:** `docs/hypotheses/HYD-0002.json` (the sidecar) next to
`docs/hypotheses/HYD-0002-breakout-continuation-net-r.md` (the markdown
hypothesis). It validates clean:

```
$ python3 -m proto.quant prereg validate --dir docs/hypotheses
HYD-0002  status=rejected  variants=1  ...
1 record(s), 0 with errors
```

**Evidence reference and the sha256 I computed** (recomputed from the file
on disk, not copied from the report text):

```
docs/implementation-reports/quant-roadmap-2026-10-18/evaluation.json
sha256 = 81affa4ad5d1661f7a2b55f3e776f74bea65ef7ec136f7b1f70c1836f513242c
```

This matches the value quoted in the preserved progress report
(`81affa4ad5d1661f7a2b55f3e776f74bea65ef7ec136f7b1f70c1836f513242c`) and
was independently recomputed by the test
(`sha256(file) == record.variants[0].evidence[0].sha256`).

**The quoted result** (`variant_id: ws5-288-config-sweep`,
`result.status: "failed"`, `result.detail.reason: "no positive edge"`) —
quoted exactly from the artifact's `dev` block:

| field | registered value | artifact (`evaluation.json` → `dev`) |
|---|---|---|
| `metric` | `mean_r` | the artifact's key |
| `value` | `-0.015419825470978433` | `dev.mean_r` = `-0.015419825470978433` |
| `n` | `24749` | `dev.n` = `24749` |
| `ci_lo` | `-0.04171311066689822` | `dev.ci_lo` = `-0.04171311066689822` |
| `ci_hi` | `0.014238059376343185` | `dev.ci_hi` = `0.014238059376343185` |

Also carried in `result.detail` (all quoted from the artifact): the `val`
block (n 6495, mean_r `-0.016606089545525376`, CI
`[-0.04675817709982529, 0.017415955039167874]`), the SPA
(`best_t -1.0619704147237248`, `p_value 1.0`), the selected identity
(`breakout_start[rvol>=4]/cont`, `T288_ks3_R2`, grid index 34),
`dev_config_means` (`share_positive 0.0`), `n_configs 288`,
`leakage_violations []`, the missing-cost coins, the artifact's own
`git_rev` / `produced_at`, and `holdout_evaluated: false` / `holdout:
null` — the one-shot holdout was **not** spent.

**Hashes computed by the helpers:**

| field | value | how |
|---|---|---|
| `config_hash` | `d33cff513c30` | the run's own config hash, quoted from the artifact |
| `data_hash` | `fe2ae686a3c74e367e9bd9aa7798930314564b58bf2b1eb79ef35f559761e913` | `prereg.data_hash({"evaluation_artifact": <artifact>})` |
| `code_hash` | `a2ade572aa2b708a883323a06d385cd70b4e731cdfea97e4130bc3b51a483b49` | `prereg.code_hash(<repo>, <10 source files>)` |

The `code_hash` embeds the git revision `40d805fed340a7f3dfd8a24236ffee965269a194`
(the code commit). It is reproducible: recomputing
`sha256("git:<rev>" + the ten file digests)` at that revision yields the
stored value — verified:

```
code_hash stored    : a2ade572aa2b708a883323a06d385cd70b4e731cdfea97e4130bc3b51a483b49
code_hash recomputed: a2ade572aa2b708a883323a06d385cd70b4e731cdfea97e4130bc3b51a483b49 MATCH: True
files changed since that commit: (none)
```

The record also states the point-in-time membership rule, the seven
feature definitions with their parameters, the eight event rules, the
cost model (fee 0.05 %/side + the four slippage tiers, `funding: none`
because the selection metric is friction only), the splits quoted from
the artifact (`dev [6.0, 61.0]`, `val [63.0, 77.0]`,
`holdout [79.0, 98.31944444444444]`), `embargo_days 2.0`, the statistics
and the seven promotion criteria (mirroring the fixed thresholds in
`proto/quant/gates.py`: 200 events / 25 days / 15 coins / CI lower bound
> 0 / validation mean > 0 / SPA p ≤ 0.10 / positive mean at 2× slippage).

**The record was NOT produced by re-running the evaluation.** The artifact
was read, its sha256 recomputed and its numbers quoted; the artifact file
is byte-identical to the committed one (no modification).

---

## 4. Acceptance criteria → where each is satisfied

| # | criterion | where |
|---|---|---|
| 1 | every required field present and non-empty for a "registered" record; a missing one fails with a specific error string | `prereg.validate`; the test loops over every required field and asserts `required-field: <field> is required` |
| 2 | split ordering + embargo enforced, mirroring the front-matter rules | `prereg._validate_splits` (`ordering:`, `embargo:`, `within-window:`) |
| 3 | the variant registry is append-only | `prereg.register_variant` + the test (append / idempotent / refused-without-overwrite) |
| 4 | the evidence hashes are real | `prereg.validate` re-hashes the evidence file (`evidence-hash-mismatch`, `bad-evidence-hash`, `evidence-missing`); the test tampers with a file and sees it flagged |
| 5 | sliced results carry denominators; a cell below `min_cell` is `insufficient` with no mean | `research_report._cell`; the test |
| 6 | the holdout is never touched | `slice_results` refuses a holdout window with a reason; the module has no `open()` call at all (asserted via `ast`) |
| 7 | the markdown registry still validates clean | `tests/test_hypotheses.py` still passes; the test file also re-checks the shipped directory |
| 8 | the registered record quotes the artifact truthfully | the test re-reads `evaluation.json`, recomputes its sha256 and compares the hash, the value, `n` and the interval |

---

## 5. Concerns and deviations (with reasons)

1. **The 288-config record is registered under a NEW hypothesis id
   (`HYD-0002`), not under `HYD-0001`.** The sweep tested the
   breakout-start / residual-shock *continuation* family; `HYD-0001` is the
   funding-quintile drift hypothesis and was **never** evaluated by that
   run. Registering the sweep as a variant of `HYD-0001` would put a
   result under a hypothesis that was not tested — exactly the
   misrepresentation this workstream exists to prevent. I therefore added a
   markdown hypothesis (`HYD-0002-breakout-continuation-net-r.md`,
   `status: rejected`) whose sidecar carries the record; `HYD-0001` is
   untouched and still `testing`. If the orchestrator wants the record
   under `HYD-0001` instead, it is a one-line change (the variant's
   `hypothesis_id` and the record's `family`), but the wording of the
   hypothesis would then have to change too.
2. **The registration is retrospective and says so.** The record was
   written on 2026-10-19/20, *after* the run it documents (the artifact's
   `produced_at` is 1792409830 = 2026-10-19T12:37:10Z). The record's
   `hypothesis` field states this in capitals and the markdown has an
   "Honest scope" section. It transcribes the preserved artifacts; it is
   not a prospective pre-registration and must not be read as one.
3. **`data_hash` covers the preserved artifact, not the raw archives.**
   `data/` is gitignored and absent from the worktree, so the bar /
   funding / settlement archives cannot be hashed here. The per-coin
   digests of those archives are recorded *inside* the artifact
   (`bar_digests`, `funding_digests`, `settlement_digests` — 450 / 447 /
   443 coins), and the artifact's own sha256 is the evidence hash, so the
   chain is: evidence file → its sha256 → the record's `data_hash` → the
   record's sha256. The record says this in `detail.input_digests`.
4. **`slice_results` keeps the mandated signature exactly**, so it has no
   `data_hash` parameter. The report always carries a `data_hash` key
   (`None` when not given) and `attach_evidence(report, data_hash=...)`
   sets it. Adding an optional keyword to the mandated signature was the
   alternative; I did not do it, to avoid diverging from the contract the
   orchestrator integrates against.
5. **The volatility/regime slice uses an equivalent trailing-window
   classifier, not `estimators.regime` itself.** `estimators.regime`
   needs the market-factor return series, which the event stream does not
   carry. The report therefore uses the same construction (a tercile of
   the trailing distribution, calibrated on the *preceding* days only,
   never on the current one) applied to each event's own trailing sigma,
   and labels the groups `low-vol` / `mid-vol` / `high-vol` /
   `unclassified`. The trend half of the 6-state regime label is not
   reproduced. This is documented in the module docstring and in the
   function docstring.
6. **`register_variant` raises on a changed result** rather than
   returning an invalid record. The brief calls it "a validation error";
   raising `PreregError` (a `ValueError` subclass whose message contains
   the exact token `variant-result-changed` and whose `.errors` list
   mirrors `validate()`'s output) makes the refusal impossible to
   ignore, and nothing is written. The in-record duplicate case is also
   reported by `validate()` with the same token. The test covers both.
7. **`min_cell` also gates the overall block** (the literal reading of
   "every cell ... no mean is reported"). A report over a tiny window
   therefore shows the denominators but no mean.
8. **The coin slice ranks only coins with `n >= min_cell`** (a tiny
   coin's mean must not be shown); the counts of ranked and unranked coins
   are reported alongside.
9. **The prereg embargo rule follows the brief** (7 days for a `"7d"`
   label, at least 1 day otherwise) and not the quant `Splits`' 2-day
   embargo; the record declares `embargo_days: 2.0`, which satisfies both
   (and both window gaps are exactly 2 days).
10. **The code fingerprint needed a second commit.** `code_hash` mixes
    the git revision with the file digests, and a commit cannot contain
    its own hash, so the record is committed once and then re-generated
    (commit `8517488`) so that its `code_git_rev` names the commit that
    carries the code. Only the fingerprint and the timestamps changed;
    the variant's result is byte-identical — the append-only registry
    refused the first attempt, which had put the varying git revision
    inside the variant's result.
11. **Not done, deliberately:** no preregistration sidecar was created for
    `HYD-0001` (it has never been evaluated, so a sidecar would imply a
    declared test that never happened); no promotion, no ACTIONABLE
    output, no change to `gates.py` / `registry.py` / `selector.py` or to
    the watcher's `promo = None`; the systemd service was not touched;
    `--holdout` was never passed anywhere.

---

## 6. Commits (branch `feature/ws-c-prereg`, not pushed)

| commit | content |
|---|---|
| `40d805f` | `feat(quant): preregistration record + sliced research report (workstream C)` — the implementation, the test file, the HYD-0002 markdown + sidecar |
| `8517488` | `data(quant): pin the HYD-0002 preregistration code fingerprint to the code commit` — the record re-generated so its `code_git_rev` names `40d805f` |
| (tip) | `docs(quant): task C report (workstream C: preregistration workflow)` — this file; the hash is in the hand-off because a commit cannot contain its own hash |

Base commit: `ef438e6` (Workstream A). Working tree is clean apart from
the untracked `task-C-brief.md` (the orchestrator's brief, deliberately
not committed).
