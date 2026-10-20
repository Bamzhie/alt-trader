# Task E report — Workstream E: champion/candidate tracking + immutable decision log

**Status: DONE_WITH_CONCERNS** (the work is complete and green; the concerns
are design decisions the reviewer should look at, listed in §5).

- **Worktree:** `/tmp/opencode/wt-e` (branch `feature/ws-e-champion`).
- **Base:** `280e41c` (merge(quant): WS-B competing outcomes & inference).
- **No promotion happened**, no decision was invented, nothing under `data/`
  was created, `docs/decisions/log.jsonl` is still 0 bytes and untouched, and
  `docs/decisions/chain.jsonl` does **not** exist in the repository.

---

## 1. What changed, file by file

### 1.1 `proto/quant/champion.py` (NEW, 807 lines after fix round 1)

The public API, verbatim:

```python
CHAMPION_VERSION = "champion-v1"
REGISTRY_SCHEMA_VERSION = "champion-registry-v1"
MIN_CLUSTERS = 3
BOOTSTRAP_B = 2000

@dataclass(frozen=True)
class ConfigVersion:
    id: str
    rule_id: str
    config_hash: str
    params: dict = field(default_factory=dict)
    geometry: dict = field(default_factory=dict)
    role: str = "candidate"
    status: str = "candidate"
    data_hash: str = ""
    code_hash: str = ""
    created_ts: int = 0
    notes: str = ""

class Registry:
    def __init__(self, path: str)
    def close(self)
    def __enter__(self) / __exit__(self, *exc)          # convenience only
    def add(self, version: ConfigVersion) -> None       # insert-only; a duplicate id is an error
    def get(self, id: str) -> ConfigVersion | None
    def list(self, status: str | None = None) -> list[ConfigVersion]
    def list_current(self, status: str | None = None) -> list[ConfigVersion]   # extra
    def set_champion(self, id: str, *, allow_promote: bool = False) -> None
        # appends; demotes the previous champion to "retired";
        # REFUSES without the explicit allow_promote=True opt-in
    def champion(self) -> ConfigVersion | None

def compare(champion: ConfigVersion, candidate: ConfigVersion, events, window, *,
            cfg=DEFAULT, funding_dir=None, settlements_dir=None,
            slip_mult=1.0, fee_mult=1.0, seed=1) -> dict
```

Behaviour:

- **The registry is a versioned history.**  `add()` only inserts (a duplicate
  id raises `ValueError`).  `set_champion(id)` **appends**: a new
  "champion" version of the promoted configuration and a new "retired"
  version of the champion it replaces (`role="retired"`, `status="retired"`).
  The superseded rows are never edited, so every earlier version stays
  retrievable through `get()` and visible in `list()` (the history).
  `list_current()` is the extra "live view" (the versions no later version
  supersedes).  `champion()` returns the most recent "champion" version.
- **The file is created only at the explicit path** the caller passes to
  `Registry(path)`.  There is no default location and **importing the module
  creates nothing** (asserted by a subprocess test that runs the import in an
  empty temp cwd).
- **`compare()`** evaluates both configurations through WS-B's outcome layer
  (`competing_outcomes.resolve_competing`) on the same events, in the same
  window, with matched costs:
  - *same events, verified*: the two sides' event-id sets are compared; if
    they differ the comparison is **refused with a reason** (the reason names
    the counts and the first ids that differ on each side).
  - *the applied cost model, verified*: both sides are evaluated with ONE
    applied cost model (the multipliers are folded into it); the descriptor
    is computed per side and compared, and the result reports it as
    `same_applied_cost_model` / `same_cost_model`.
  - *the provenance, reported separately*: `same_config_hash` says whether
    the two versions were registered under the same config hash.  When they
    were not, the comparison is **refused** — their evidence was produced
    under different cost models — and the reason says exactly that.
  - *the label slots are recorded*: two configurations with the same rule
    but different geometries select the same events and read DIFFERENT
    label slots of them; each side block carries its `label_slots` and
    `geometry`, and the result carries `same_label_slot`.
  - *matched costs*: `slip_mult`/`fee_mult` are folded into the **cost
    model itself** (`dataclasses.replace` on `CostModel`), so the outcome
    layer prices both sides identically instead of the multipliers being
    applied to the numbers afterwards.
  - *paired difference*: `difference` is the mean of the per-event paired
    differences (candidate − champion) over the events **both** sides have;
    the interval is `stats.day_cluster_mean(diffs, days, B=2000, seed=seed)`
    — the house day-cluster bootstrap — and `t` is
    `stats.day_cluster_t(diffs, days)`.  A **coin-cluster interval** is
    reported too (WS-B's `inference.uncertainty`: a coin-block bootstrap).
    An interval whose cluster count is below three is `None` with a reason
    (and the statistic is suppressed with it).
  - *no silent zeros*: when a side has no evaluable outcome the result is
    **refused with a reason** and carries no numbers.
  - *every number carries its denominator*: `n_events` (the paired,
    evaluable events), `n_champion`, `n_candidate`, per-side `n_selected` /
    `n_evaluable` / `causes` / `days` / `coins`, `clusters` and a
    `denominator` string that says what the numbers are over.
  - *funding is never zero-filled*: the metric is `"net_r"` when the venue
    funding cost can be attributed and `"friction_net_r"` (explicitly
    friction-only, never presented as a net number) when it cannot; both
    sides always use the same metric, and the reason says which one it is.
  - `disagreements` counts the paired events where the two sides disagree on
    the outcome **cause** (TP / SL / TIME / INVALIDATED / CENSORED).

  Returned shape (the keys the brief asks for, plus extras):
  `n_events, n_champion, n_candidate, same_events, same_cost_model,
  cost_model, champion, candidate, difference, ci_lo, ci_hi, t, clusters,
  disagreements, denominator` and — extra — `same_applied_cost_model,
  same_config_hash, same_label_slot, refused, reason, reasons, metric,
  ci_lo_coin, ci_hi_coin, t_coin, window, champion_id, candidate_id,
  version`.  A refusal reports the flags that were ACTUALLY verified
  (`True`/`False`) and `None` for the ones that were not established.

### 1.2 `proto/quant/decision_log.py` (NEW, 430 lines after fix round 1)

```python
DECISION_LOG_VERSION = "decision-chain-v1"
DECISION_KINDS = ("promote", "reject", "continue", "hold")
CHAIN_RELPATH = os.path.join("docs", "decisions", "chain.jsonl")
HUMAN_LOG_BASENAME = "log.jsonl"

class DecisionError(ValueError):        # every refusal raises this
    ...

def entry_hash(entry: dict) -> str
def next_id(path: str) -> str
def append(path: str, *, kind: str, subject: str, reasons: list[str],
           evidence: list, hypothesis_id: str | None = None,
           decided_by: str, ts: int | None = None) -> dict
def load(path: str) -> list[dict]
def validate_chain(path: str) -> list[str]
```

- The chain file is the **new** artifact `docs/decisions/chain.jsonl`.
  `docs/decisions/log.jsonl` is never written, never read and never
  migrated — `append()` even **refuses** a path whose file name is
  `log.jsonl` (a structural guard, not just a convention).
- `hash = sha256(canonical_json(entry_without_hash))`, canonical JSON being
  the sorted-key, compact UTF-8 encoding.  `prev_hash` is the previous
  entry's hash (`null` for the first).
- `append()` is **append-only and atomic**: the file is opened in append
  mode, one `\n`-terminated line is written, flushed and `os.fsync`-ed;
  nothing is ever rewritten or truncated.  It refuses to extend a chain
  that is already invalid (that is how an out-of-band edit is caught), it
  re-checks the tail under an exclusive `fcntl` lock immediately before the
  write (a concurrent writer is detected), and it verifies every cited
  evidence file by re-hashing it (a missing file or a changed file is a
  refusal with a reason).
- **The timestamp is checked at write time** (fix round 1, M6): a `ts` that
  is not strictly after the tail's `ts`, or an explicit `ts` in the future,
  is refused BEFORE it is written — otherwise it would poison the chain and
  make every later append impossible.  The default `ts` (now) is raised to
  `tail_ts + 1` when the clock has not moved past the previous decision, so
  two decisions in the same second still produce a strictly increasing
  chain.
- `validate_chain()` returns one string per problem and `[]` when the chain
  is valid.  It detects: a broken hash chain, an edited entry (the content
  no longer matches the recorded hash), a duplicate id, a bad id format, a
  non-monotonic timestamp, an unknown kind, empty reasons, an empty
  `decided_by`, an evidence entry without a 64-hex sha256 — plus malformed
  lines, a missing/!well-formed hash or prev_hash, a bad ts type and an
  empty subject.  The link check compares `prev_hash` with the
  **recomputed** hash of the previous entry, so an edit is reported twice:
  as a broken hash on the edited line *and* as a broken link on the line
  that follows it.
- The id rule is reused from `proto.decisions.DECISION_ID_RE`
  (`^DEC-[0-9]{4}$`, so `next_id` yields `DEC-0001`, `DEC-0002`, …) and the
  hypothesis-id vocabulary from `proto.hypothesis_registry.ID_RE`
  (`^HYD-[0-9]{4}$`).  **Neither module was modified.**

### 1.3 `proto/quant/__main__.py` (MODIFIED, +71 lines)

New subcommand `champions` with two sub-commands (there is **no**
`promote` sub-command and this branch never writes a decision or a
promotion record):

```
python3 -m proto.quant champions list    --db <path>
python3 -m proto.quant champions compare --db <path> --champion <id>
                                           --candidate <id> --window dev|val
                                           [--out <path>] [--data <bars dir>]
                                           [--coins ...] [--funding-dir ...]
                                           [--settlements-dir ...]
```

- `list` prints one JSON object per registry version (the history) and the
  current champion; it does **not** create the database when it is missing
  (it exits 1 with `no registry at …`).
- `compare` collects the events from the bars archive, maps `--window
  dev|val` onto `cfg.splits.bounds(n_days)`, runs `champion.compare()` and
  prints the comparison JSON (and writes it to `--out` when given).  A
  refused comparison is printed **and** exits 1 with `REFUSED: <reason>`.
- `--data`/`--coins`/`--funding-dir`/`--settlements-dir` are **extra,
  optional** flags: the brief's flags (`--db`, `--champion`, `--candidate`,
  `--window`, `--out`) are all present and behave as specified.  `--data` is
  needed because a comparison needs the events; the holdout is never spent.

### 1.4 `tests/test_ws_e_champion.py` (NEW, 895 lines, 138 checks after fix round 1)

Self-checking script in the house style (prints `PASS`/`FAIL` per check,
`sys.exit(1)` on failure).  Everything is synthetic: hand-built events, a
hand-built settlement archive and registries/chain files under `tempfile`
temp dirs.  It writes **nothing** into the repository.

Sections: (1) the registry is a versioned history, (2) importing the module
creates no file, (3) the matched comparison with an **independent
recomputation** of the paired difference and its day-cluster interval,
(4) the refusals, (5) the chain append/validate, (6) tamper detection,
(7) evidence verified at write time, (8) every defect `validate_chain` must
find, (9) the repository is untouched, (10) the CLI.

---

## 2. Tests — every command and its real final output

### 2.1 The tests were written first and failed first

```
$ python3 tests/test_ws_e_champion.py
Traceback (most recent call last):
  File "/tmp/opencode/wt-e/tests/test_ws_e_champion.py", line 47, in <module>
    from proto.quant import decision_log as DL
ImportError: cannot import name 'decision_log' from 'proto.quant' (/tmp/opencode/wt-e/proto/quant/__init__.py)
exit=1
```

(committed as `2d7ec8f` — the test file on its own, failing.)

### 2.2 Focused tests (the FINAL state, after fix round 1)

```
$ python3 tests/test_ws_e_champion.py
...
ALL PASS  tests/test_ws_e_champion.py  (138 checks)

$ python3 tests/test_decisions.py
  PASS  oversized JSON integer reported as bad-json with line (no raise)

ALL PASS

$ python3 tests/test_promotion.py
  PASS  outcome scope stated explicitly (WS5 unbuilt)

ALL PASS  tests/test_promotion.py  (16 checks)

$ python3 tests/test_quant.py
  PASS  registered ids are unique and include controls

ALL PASS
```

### 2.3 The full offline suite (the FINAL state, after fix round 1)

```
$ python3 tests/run_all.py
PASS  test_adapter.py  [ALL PASS]
... (84 files) ...
PASS  test_ws5_eval_harness.py  [ALL PASS]
PASS  test_ws_b_outcomes.py  [ALL PASS]
PASS  test_ws_e_champion.py  [ALL PASS  tests/test_ws_e_champion.py  (138 checks)]
PASS  test_ws_f_startup.py  [ALL PASS  tests/test_ws_f_startup.py  (91 checks)]

ALL SUITES PASS (84 files)
```

Exit status 0, no failures.

---

## 3. Evidence that nothing was promoted

After the full suite run (and after the CLI smoke test), in the worktree:

```
$ ls -la docs/decisions/
total 0
drwxr-xr-x 2 bamzhie bamzhie  60 Oct 19 23:08 .
drwxr-xr-x 6 bamzhie bamzhie 340 Oct 19 23:08 ..
-rw-r--r-- 1 bamzhie bamzhie   0 Oct 19 23:08 log.jsonl

$ stat -c '%n %s bytes' docs/decisions/log.jsonl
docs/decisions/log.jsonl 0 bytes

$ find . -name "chain.jsonl" -not -path "./.git/*"
(no output — the chain file does not exist in the repository)

$ ls -la data
ls: cannot access 'data': No such file or directory   # data/ was never created

$ git status --porcelain
 M proto/quant/__main__.py
 M tests/test_ws_e_champion.py
?? proto/quant/champion.py
?? proto/quant/decision_log.py
?? task-E-brief.md          # the orchestrator's brief, left untracked on purpose
```

- `docs/decisions/log.jsonl`: **0 bytes**, byte-identical to the checkout
  (the mtime is still the checkout time, and `git diff` shows no change).
- `docs/decisions/chain.jsonl`: **does not exist** — the chain is only ever
  created in a temp dir by a test.
- `data/promotions.json`: **does not exist** (the whole `data/` directory
  does not exist in this worktree).
- No registry database (no `*.db`) and no `chain.jsonl` anywhere in the
  repository — the registries only ever live in temp dirs.
- The suite asserts all of this itself (§9 of the test file), including
  "no registry database or chain file was created in the repository" and
  "the human-owned log is still 0 bytes".
- Untouched by design: `proto/quant/engine.py` (`promo = None`),
  `proto/quant/selector.py`, `proto/quant/gates.py`, the service's
  `"mode": "shadow"`, the holdout, `proto/decisions.py` and
  `proto/hypothesis_registry.py` (both are imported read-only).

---

## 4. Acceptance criteria → where they are met

| # | Criterion | Where |
|---|---|---|
| 1 | versioned history: both versions retrievable, champion discoverable, duplicate id refused | `Registry.add/get/list/set_champion/champion`; test §1 (16 checks) |
| 2 | matched comparison: same `n_events`, `same_events` true, difference = difference of the means, interval = the day-cluster interval of the paired differences (recomputed by the test) | `compare`; test §3 (25 checks, incl. the independent recomputation) |
| 3 | refusal on mismatched inputs (different event sets / different cost models) with a reason | `compare`; test §4 (11 checks) |
| 4 | no silent zeros: a side with no evaluable outcome → reason, no numbers | `compare`; test §4 |
| 5 | the chain detects tampering: broken hash **and** broken link; a 4th append after the edit is refused | `validate_chain`/`append`; test §6 (5 checks) |
| 6 | evidence verified at write time: a changed file is refused, a valid one accepted and the chain valid | `append`; test §7 (7 checks) |
| 7 | the existing log is untouched (0 bytes) and the chain file is not created in the repository | test §9 (6 checks) |
| 8 | no promotion happened: no champion entry and no decision entry anywhere in the repository | test §9 + §3 of this report |

---

## 5. Concerns / deviations (please look at these)

1. **A cross-`config_hash` comparison is REFUSED.**  The brief demands that
   the cost model be "verified the same way … the cost model hash … must
   match" and that a comparison across different cost models be refused.
   The only per-side cost-model identity a `ConfigVersion` carries is its
   `config_hash` (the `QuantConfig` hash the version's evidence was
   produced under).  Consequence: comparing a champion registered under
   config hash A with a candidate registered under config hash B is
   refused, even though `compare()` re-evaluates both under the *current*
   config.  **Revised in fix round 1 (I1):** the refusal is now driven by
   the PROVENANCE (`same_config_hash`), the applied cost model is
   reported separately as `same_applied_cost_model` / `same_cost_model`,
   and the reason says "registered under DIFFERENT config hashes" instead
   of pretending the applied cost models differed.  If the reviewer still
   prefers the lenient reading (compare anyway, report the provenance
   mismatch as a note), it is a two-line change in `compare()`.
2. **"Same events" is strict equality of the two selected event-id sets, not
   an intersection.**  A candidate with a different rule parameter (e.g.
   `rvol_min` 2.0 vs 4.0) therefore cannot be compared with the champion:
   the comparison is refused with the reason and the first differing ids.
   That is the literal reading of the brief ("the two evaluations must cover
   the same event ids … if they differ, the comparison is refused"), but it
   does mean the "compare two different rules" use case needs the
   intersection to be taken by the caller first.  (Unchanged by fix round 1;
   what changed is that the result now also records the LABEL SLOTS the two
   sides read — I2 — so a same-rule/different-geometry comparison is
   visible as `same_label_slot: false`.)
3. **The metric falls back to friction-only when funding is not
   attributable.**  `compare()` reports `metric: "friction_net_r"` (never
   presented as a net number) when the venue funding cost cannot be
   attributed, because the WS-B outcome layer leaves `net_r = None` in that
   case and a comparison with no numbers would be useless.  The reason is
   always in `reasons`, and both sides always use the same metric.
4. **The geometry must be in the pre-declared grid**
   (`config.grid_geometries()`), because the events carry the barrier
   labels in per-(geometry, side) slots that are addressed by that grid.
   A version with an off-grid geometry is **refused with a reason** (not a
   crash) — since fix round 1 (M5) `_resolve()` refuses it up front, and
   the outcome layer's own error is the backstop.
5. **The hypothesis-id check is a format check, not an existence check, so
   the chain's hypothesis linkage is ADVISORY.**  `append()` has no
   `known_ids` parameter (the brief fixes the signature), so a supplied
   `hypothesis_id` must match `^HYD-[0-9]{4}$` and may be `null`;
   whether the hypothesis exists is left to the caller (the human log's
   `proto.decisions.validate_log` is the existence check for the human
   log).  I did **not** add an optional `known_ids` parameter, to keep the
   published signature exactly as specified.  Stated in the module
   docstring and in §7.
6. **The registry mints ids from the same `cfg-NNNN` space as the
   caller.**  `set_champion()` mints the next free `cfg-NNNN`; a
   caller-supplied id that collides with a minted one is refused as a
   duplicate (loudly, never silently).  Callers should use a distinct
   range (the test uses `cfg-0001` and `cfg-0100`).
7. **`list()` returns the whole history, not the live view.**  The brief
   describes the registry as "a versioned history" and requires that
   superseded versions stay retrievable, so `list()` returns every version
   (filterable by `status`); `list_current()` (an extra method) returns the
   live view.
8. **The chain's tamper-evidence has the usual limit**, stated in the
   module docstring: an editor who rewrites an entry *and* recomputes every
   hash after it produces a self-consistent file again.  The anchor is the
   tail hash, which a reader must record out of band.
9. **`docs/decisions/chain.jsonl` was deliberately NOT created** in the
   repository (the brief requires it), so the chain artifact exists only as
   an implementation plus temp-dir tests.  The owner decides when to create
   the file and migrate the human log.
10. **`set_champion` is a conscious act** (fix round 1, I3): it refuses
    without the explicit `allow_promote=True` opt-in, so a future caller
    cannot promote anything by accident.  The capability stays, because the
    brief requires it.

---

## 7. Fix round 1

**Status after fix round 1: DONE_WITH_CONCERNS** (the same concerns as §5,
now with the reviewer's findings addressed; the two design decisions in §5
items 1 and 2 are unchanged in substance and are now worded exactly).

Commits: `74a0098` (the code + tests) and the report commit below.  The
hashes are in `git log --oneline feature/ws-e-champion`.

### B1 (blocking) — the "nothing was created in the repository" check failed in a real checkout

**What was wrong.**  The check walked the whole tree for `*.db`.  In the
worktree there is no `data/`, so it passed; in the main checkout
`data/quant.db` and `data/signals.db` belong to the running platform, not
to this task, and the check failed (the reviewer reproduced `1/104 failed`).

**What I changed.**

- `tests/test_ws_e_champion.py:762-790` — the walk was replaced by
  `repo_artifacts(root)`, which scans ONLY the artifacts this task could
  have created: every file in `docs/decisions/` except the human-owned
  `log.jsonl`, `data/promotions.json`, and a champion registry
  (`data/champions.db`, `data/champion_registry.db`,
  `data/quant_champions.db`, or any `data/*champion*`).  The repository
  check is now `repo_artifacts(ROOT) == []`.
- `tests/test_ws_e_champion.py:815-832` — the regression tests: a
  synthetic tree with an **unrelated** `data/quant.db` + `data/signals.db`
  must NOT trip the check, while a `docs/decisions/chain.jsonl`, a
  `data/champions.db` and a `data/promotions.json` in the same tree MUST
  trip it.

**Proof (the reviewer's own scenario, in a copy of the tree, not the
repository):**

```
$ cp -r proto tests docs /tmp/opencode/wsE-copy/ && mkdir -p /tmp/opencode/wsE-copy/data
$ : > /tmp/opencode/wsE-copy/data/quant.db && : > /tmp/opencode/wsE-copy/data/signals.db
$ cd /tmp/opencode/wsE-copy && python3 tests/test_ws_e_champion.py
...
ALL PASS  tests/test_ws_e_champion.py  (138 checks)
```

The old check, run on the same copy, reports the false positives that the
reviewer saw:

```
OLD check would report: ['/tmp/opencode/wsE-copy/data/signals.db',
                          '/tmp/opencode/wsE-copy/data/quant.db']
```

And the check still catches a would-be artifact (same copy, with a chain
file added):

```
$ : > /tmp/opencode/wsE-copy/docs/decisions/chain.jsonl
$ python3 tests/test_ws_e_champion.py
  FAIL  the chain file was NOT created in the repository
  FAIL  docs/decisions holds only the human-owned log   ['chain.jsonl', 'log.jsonl']
  FAIL  no decision/registry artifact was created in the repository   ['docs/decisions/chain.jsonl']
FAIL  tests/test_ws_e_champion.py  (3/138 failed: [...])
```

### I1 — `same_cost_model` did not test what it claimed

**What was wrong.**  `_cost_model()` hashed `version.config_hash` with the
fee/slip params, but the APPLIED cost model is
`_effective_cfg(cfg, slip_mult, fee_mult)` — one shared config for both
sides, identical by construction.  The check therefore tested PROVENANCE
equality while the refusal message called it a cost-model difference.

**What I changed.**

- `proto/quant/champion.py:378-404` — `_cost_model()` was replaced by
  `_applied_cost_model(cfg, slip_mult, fee_mult, funding_dir,
  settlements_dir)`: it describes and hashes the cost model the sides are
  actually evaluated with (the applied model, without any version data).
  It is still computed per side and compared, so the check stays a real
  check.
- `proto/quant/champion.py:520-550` — `_refusal()` now reports the
  verification state it was given (`same_applied_cost_model`,
  `same_config_hash`, `same_events`, `same_label_slot`).
- `proto/quant/champion.py:626-628, 779-806` — the result now exposes the two facts
  **separately**: `same_applied_cost_model` (the applied cost model is
  shared → True) and `same_config_hash` (the provenance).  The brief's
  `same_cost_model` key is kept and now means "the same APPLIED cost
  model" (it mirrors `same_applied_cost_model`).
- `proto/quant/champion.py:648-657` — the refusal reason is re-worded to
  say exactly what is wrong: *"the two configurations were registered under
  DIFFERENT config hashes (champion X, candidate Y), so their evidence was
  produced under different cost models; the comparison is refused."*  It no
  longer claims the applied cost models differed.

**Tests that cover it** (`tests/test_ws_e_champion.py`): "the applied cost
model is reported under its explicit name", "the provenance is reported
separately", "different config hashes are refused", "the refusal says
'config hashes', not 'priced with different cost models'", "the refusal
reports the applied cost model as shared", "the refusal reports the
provenance mismatch".

### I2 — the result never recorded that the two sides may read different label slots

**What was wrong.**  `same_events` compares the *selected* event sets.  Two
configurations with the same rule but different geometries select the same
events (so `same_events=True`) while reading **different outcome slots**
of each event — the intended comparison, but invisible in the result.

**What I changed.**

- `proto/quant/champion.py:470-475` — `_label_slots(geom)` returns the two
  label slots (long/short) a geometry reads per event.
- `proto/quant/champion.py:643-646` — `verified["same_label_slot"]` is
  computed for both sides.
- `proto/quant/champion.py:484-518` — every side block now carries
  `label_slots` and `geometry`; the result carries `same_label_slot`
  (True = the same slot, False = different slots, None = not established).
- The docstring of `compare()` and of the module documents it.

**Tests that cover it**: "the two sides read different label slots
(different geometries)", "each side records the label slots it reads",
"each side records its geometry".

### I3 — the "no promotion in production" guarantee was an unenforced convention

**What was wrong.**  `Registry.set_champion()` was fully functional; nothing
stopped a future caller from running it against a production DB.

**What I changed (per the orchestrator's ruling: keep the capability, make
it conscious).**

- `proto/quant/champion.py:237-262` — `set_champion(self, id: str, *,
  allow_promote: bool = False)`: without the explicit opt-in it **refuses**
  with a reason that names the risk ("set_champion records a PROMOTION …
  so it needs the explicit opt-in allow_promote=True. Promote only with
  the promotion gate's evidence recorded (proto.quant.gates); a measurement
  must never promote anything by accident.").
- `proto/quant/champion.py:54-61` (module docstring) and
  `proto/quant/champion.py:184-188` (the `Registry` docstring) state the
  rule: nothing in the production path calls it, the CLI has no `promote`
  sub-command, and no promotion happened while this module was written.

**Tests that cover it**: "set_champion refuses without the explicit
opt-in", "the refusal names the risk (a promotion)", "the refused promotion
wrote nothing".

### M4 — `set_champion` was not idempotent

**What was wrong.**  The guard compared the minted champion id with the
requested id, so asking twice appended four rows and the docstring
("Setting the current champion again is a no-op") was wrong.

**What I changed.**

- `proto/quant/champion.py:307-325` — `_lineage(id)` walks the `supersedes`
  chain, so "is this already the champion?" is a question about the
  LINEAGE, not about the raw ids.
- `proto/quant/champion.py:249-253` — the guard is now
  `target.id == current.id or target.id in self._lineage(current.id)`.

**Tests that cover it**: "promoting the current champion again is a no-op",
"promoting the champion version itself is a no-op", "the champion is
unchanged after the no-op promotions" (the history stays at 2 rows).

### M5 — `_resolve()` silently dropped unknown params and filled in defaults

**What was wrong.**  `params={}` resolved to `breakout_start/fade/
rvol_min=4.0` instead of being refused, and `"rvol_min": "4.0"` escaped as
an uncaught `TypeError` at comparison time.

**What I changed.**  `proto/quant/champion.py:407-467` — `_resolve()` now
refuses, with a reason: missing `params`/`geometry`, unknown keys, missing
keys, a non-string `family`/`policy`, a non-numeric `rvol_min`/`cusum_h`
or geometry field, and a geometry outside the pre-declared grid (the
events' label slots are addressed by that grid).

**Tests that cover it**: "an empty params dict is refused (not silently
defaulted)", "the empty-params refusal names the missing params", "an
unknown params key is refused", "the unknown-params refusal names the
key", "a wrong params type is refused (no uncaught TypeError)", "the
wrong-type refusal names the field", "an off-grid geometry is refused",
"the off-grid refusal names the grid".

### M6 — `append` accepted a non-monotonic / future `ts` and dead-ended the chain

**What was wrong.**  A `ts` that went backwards was written; `validate_chain`
then reported it AND every later append was refused — the chain was
dead-ended with no recovery path.

**What I changed.**  `proto/quant/decision_log.py:365-384` (the checks are
at `:370-384`) — `append()` refuses, before writing:

- a `ts` that is not STRICTLY AFTER the tail's `ts` ("the ts must be
  strictly after the previous decision's ts (…); a ts that goes backwards
  (or repeats it) would make the chain invalid and every later append
  impossible");
- an explicit `ts` in the future ("the ts is in the future: …"), as
  `proto/decisions.py` does.

The default `ts` (now) is raised to `tail_ts + 1` when the clock has not
moved past the previous decision, so two decisions in the same second still
produce a strictly increasing chain (documented in the docstring).

**Tests that cover it**: "a ts that goes backwards is refused", "a ts that
repeats the tail's ts is refused", "a ts in the future is refused", "the
refused timestamps did not extend the chain", "the chain is still valid
after the refused timestamps", "the chain still accepts a later decision".

### M7 — the report's own line counts were wrong

Fixed: `champion.py` is **807** lines (not 560), `decision_log.py` is
**430** (not 400), `tests/test_ws_e_champion.py` is **895** lines with
**138** checks (not 706/104).  The counts in §1.1-§1.4 are now the real
ones after fix round 1.

### M8 — every refusal claimed `same_events=False, same_cost_model=False`

**What was wrong.**  The refusal shape set the flags to `False` by
default, including refusals that happen *after* both were verified `True`.

**What I changed.**  `proto/quant/champion.py:520-550` — `_refusal()`
takes the verification state and reports it: a flag is `True`/`False` when
it was actually established and `None` when it was not established at all.
`compare()` threads the state through every refusal
(`proto/quant/champion.py:620-720`).

**Tests that cover it**: "the refusal reports what WAS verified (the
applied cost model)", "a refusal before the event check does not claim
same_events = False", "the no-outcome refusal reports the verified facts".

### Documented, no change needed (as instructed)

- **The hypothesis-id check is format-only and therefore ADVISORY.**
  `append` has no `known_ids` parameter (the brief fixes the signature),
  so a supplied `hypothesis_id` is checked for FORMAT only (`HYD-####`)
  and may be `null`; `HYD-9999` is accepted.  A chain entry therefore
  says "this decision claims to be about HYD-0007", not "this decision is
  provably about HYD-0007".  Stated in
  `proto/quant/decision_log.py:56-63` (module docstring) and here.
- **`CHAIN_RELPATH` is defined but unused by `append`** (which takes an
  explicit path).  It names the canonical location the owner chooses;
  nothing here creates the file until the first append, and the CLI has no
  command that creates it.  Stated in
  `proto/quant/decision_log.py:64-70` (module docstring) and here.

### Fix round 1 — test output (real)

```
$ python3 tests/test_ws_e_champion.py
...
ALL PASS  tests/test_ws_e_champion.py  (138 checks)

$ python3 tests/test_decisions.py
  PASS  oversized JSON integer reported as bad-json with line (no raise)

ALL PASS

$ python3 tests/test_promotion.py
  PASS  outcome scope stated explicitly (WS5 unbuilt)

ALL PASS  tests/test_promotion.py  (16 checks)

$ python3 tests/test_quant.py
  PASS  registered ids are unique and include controls

ALL PASS

$ python3 tests/run_all.py
PASS  test_adapter.py  [ALL PASS]
... (84 files) ...
PASS  test_ws_e_champion.py  [ALL PASS  tests/test_ws_e_champion.py  (138 checks)]
PASS  test_ws_f_startup.py  [ALL PASS  tests/test_ws_f_startup.py  (91 checks)]

ALL SUITES PASS (84 files)
```

### Fix round 1 — the repository is still untouched

```
$ ls -la docs/decisions/
-rw-r--r-- 1 bamzhie bamzhie   0 Oct 19 23:08 log.jsonl

$ stat -c '%n %s bytes' docs/decisions/log.jsonl
docs/decisions/log.jsonl 0 bytes

$ find . -name "chain.jsonl" -not -path "./.git/*"
(no output)

$ ls data
ls: cannot access 'data': No such file or directory
```

No chain file, no registry database and no `data/` directory exist in the
repository; the human-owned log is still 0 bytes and untouched.  The
reviewer's reproduction used a COPY of the tree in `/tmp/opencode/wsE-copy`,
never the repository.

---

## 6. Commits

| Hash | Message |
|---|---|
| `2d7ec8f` | `test(ws-e): champion/candidate + decision-chain suite (failing: modules not yet written)` |
| `5bef58d` | `feat(quant): WS-E champion/candidate registry, matched comparison and hash-chained decision log` |
| `e17d5cf` | `docs(ws-e): the Workstream E report (champion/candidate + decision chain)` |
| `74a0098` | `fix(ws-e): review round 1 -- scoped repo check, truthful cost-model flags, label slots, conscious promotion, timestamp rules` |
| `FIX1-REPORT` | `docs(ws-e): append the fix round 1 section to the Workstream E report` |

`git log --oneline feature/ws-e-champion` shows the commits above (the last
commit cannot name its own hash).
