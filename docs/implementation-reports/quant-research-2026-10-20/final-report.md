# ALT RADAR — quant research effort, 2026-10-20: final report

**Folder:** `docs/implementation-reports/quant-research-2026-10-20/`
**Base:** `d5097cb` · **Tip at the time of writing:** `94bdf49` (results) — the
final documentation commit follows.
**Scope:** the pivot-validation fix, a read-only data audit, a preregistered
analysis (HYD-0003), a manual-review signal surface, and the deployment of the
merged code to the local user service.

> **What this report does and does not claim.** Deploying code is not
> evidence that a signal has an edge. The preregistered analysis found
> **no positive edge** and the one-shot holdout was **not spent**. The
> Research view now displays rule-generated candidates **for manual review
> only**; the app does not execute them and never will without a separately
> approved stage.

---

## 1. The pivot validation fix (Part 1)

**Finding.** `feature_store.pivot_config_id()` rejected `left=0` / `right=0`
while `structure.confirmed_pivots()` accepts them (it rejects only
negatives and `frame_bars < 1`). The feature-identity helper was therefore
stricter than the detector whose output it identifies: a legitimate
zero-setting pivot could not be given an identity.

**Fix** (`proto/quant/feature_store.py`): the helper now accepts exactly the
detector's contract — `left`/`right` are non-negative integers,
`frame_bars` is a positive integer — and says so in its docstring.

**Tests** (`tests/test_ws_a_measurement.py`): the zero settings are accepted
and produce the identity `L0R0F1`; a detector-produced zero-setting pivot
round-trips through `from_pivots()` with `available_ts == confirmed_ts`; a
negative setting and `frame_bars < 1` are still refused; the identity and
the provenance hash differ from the default configuration; identical inputs
stay deterministic and idempotent.

**Commits:** `522647b` (the fix), `bb20a08` (the mean-R placebo and the
comparison label), `c124c1f` (the review fixes), `e2f555a` (the re-review
fixes), `94bdf49` (the results). Remote state: `origin/main` is pushed and
identical to the local tip at the time of writing (`git rev-parse
origin/main` == `git rev-parse main`).

## 2. The read-only data inventory

Full detail: `data-audit.md` (247 KB) + `data-audit.json` (1.36 MB,
sha256 `03c268bfb60fa223` / `02a82315521fe608`). Read-only throughout: no
archive was written, no backfill was performed, `data/quant_research/` was
not created, the holdout was not touched and the service was not restarted
during the audit.

| Quantity | Measured |
|---|---|
| Bar archives / coins / rows | 580 / 580 / 15,525,436 |
| Duplicates / malformed rows / gaps ≠ 300 s | **0 / 0 / 0** (largest gap 0 s) |
| Archive span | 2026-07-13T02:35Z → 2026-10-20T11:30Z = **99.37 d** |
| Coins with **< 95 days** | **65** (worst: GTC 0.33 d, PAPER 0.79 d, DOPE 4.06 d) |
| Coins ≥ 95 days | 515 |
| Newest bar age at run time | 394 s |
| Coins the watcher would call stale | 32 / 579 (5.5 %; the auto-pause threshold is 30 %) |
| Funding observations | 565 coins, 98,177 rows, median span **4.56 d** |
| Funding settlements | 552 coins, 1,355,039 rows, median span 540.7 d; **175 coins changed cycle** |
| Attributable windows (600 sampled) | 583 complete / 17 incomplete / 0 missing |
| Configured splits (day numbers) | dev 6–61, embargo 2 d, val 63–77, embargo 2 d, holdout 79–99.3715 |

Two asymmetries matter and are not visible from a summary: the **funding
observation** archive covers only the last ~4.6 % of the bar window on the
median coin, and the **coverage is not uniform** — 65 of 580 coins have
fewer than 95 days, so "95 days for every coin" would be false. 566 of the
580 bar files are **not time-ordered on disk** (appended), so any coverage
rule must be applied to the **sorted** series.

## 3. The preregistration (HYD-0003) and what was frozen

`docs/hypotheses/HYD-0003-manual-review-signal-family.md` (registry-valid,
0 errors) + the machine-readable sidecar `docs/hypotheses/HYD-0003.json`
(sha256 `5328878c5f8883d9`) + the per-archive digests
`HYD-0003-digests.json` (`a443188b6210c9b5`).

Frozen **before** any result was examined: the universe rule (≥ 60 days of
bars, no malformed rows, no gap in the sorted series), the point-in-time
membership rule, the five declared family members, the direction, the
entry/exit geometry and the tie-break convention, the horizon, the cost
model, the chronological splits and embargo, the metrics and their
denominators, the minimum sample sizes (200 events / 15 coins / 20 days),
the uncertainty method (day- **and** coin-cluster), the three baselines, the
family-level correction, and the **seven decision criteria** (dev and
validation means and intervals above zero, sample sizes, family p ≤ 0.10, a
matched placebo p ≤ 0.05, the funding delta ≥ −0.002 R, and beating the
member's own mirror-image comparison).

Identities recorded in the sidecar: `config_hash d33cff513c30`, the aggregate
data hashes (bars `4241a32a7119d623`, funding `be737c24583df299`,
settlements `99978e6123324052`), and the code hash `60057e50a28fb6ab…`
pinned to the revision whose code ran.

**Amendments, all made in the open and before any member number existed:**
the universe rule's wording (it had required 60 days *covering a 55-day dev
window*, which no coin could satisfy); the placebo criterion (it now
compares the **mean** net R and is mandatory — a missing comparison refuses
the freeze); criterion 7's applicability; and the holdout end in the
sidecar (a numeric bound, so the registry validator accepts it).

**The holdout wall is enforced, not documented.** `analysis.evaluate_member()`
refuses (raises) any window that reaches into the holdout, using the
boundary derived from the configured splits, and the wall is **on by
default** — a caller cannot forget to pass it. The legacy WS5 `--holdout`
path is refused while an open preregistration declares a holdout window:
that preregistration owns the one-shot spend, and the legacy sweep does
not carry its criteria.

## 4. The analysis (dev + validation only)

Commands: `python3 /tmp/opencode/hyd0003_run.py` (the runner: inventory →
`research.collect_events` over the 530-coin universe → the point-in-time
pivot states → the five members on dev and validation → the family
correction → the freeze criteria). The events (86,307) and the pivot states
(78,254) are cached under `/tmp/opencode/hyd0003/`.

**Results (dev, friction-only and funding-adjusted reported separately; the
unit is R per trade — no leverage applied, the equity-denominated return is
not computable without a position-sizing and liquidation model):**

| member | rule | dev n / coins / days | friction-only mean R | 95 % day-cluster | funding-adjusted mean R (n, coverage) | matched placebo p |
|---|---|---|---|---|---|---|
| m1 | breakout rvol≥4 fade | 24,737 / 372 / 56 | **−0.0400** | [−0.0835, +0.0053] | −0.0388 (24,732, 0.9998) | 0.199 |
| m2 | breakout rvol≥4 cont | 24,737 / 372 / 56 | **−0.0384** | [−0.0799, +0.0045] | −0.0396 (24,732, 0.9998) | 0.920 |
| m3 | residual_shock h=8 fade | 3,421 / 295 / 56 | **−0.0280** | [−0.0579, −0.0017] | −0.0280 (3,420, 0.9997) | 0.269 |
| m4 | residual_shock h=8 cont | 3,421 / 295 / 56 | **−0.0477** | [−0.0742, −0.0189] | −0.0477 (3,420, 0.9997) | 0.955 |
| m5 | breakout rvol≥4 fade **+ pivot_state=low** (new) | 12,445 / 365 / 56 | **−0.0393** | [−0.1074, +0.0271] | −0.0394 (12,440, 0.9996) | **0.0050** |

Family-level correction (SPA max-t over the five members, B=1000): **p = 1.0**
(best member m5). The per-member means were re-verified against the run's
own results before the correction was computed.

**Validation: every member is `insufficient`.** The validation window is 14
days and the preregistered minimum is 20 distinct days, so no member can be
evaluated on validation at all. This is a **protocol defect in the minimum
sample sizes** (they were not checked against the window lengths before the
freeze) — it is reported, not silently amended, because the criteria were
frozen and amending them after seeing the dev numbers would be
criterion-shopping.

**Consequence: no member met the seven criteria → no configuration was
frozen → THE ONE-SHOT HOLDOUT WAS NOT SPENT.** `data/quant_research/` does
not exist; no holdout lock was written; the holdout window was never
evaluated. The evidence artifact
(`hyd0003-evidence.json`, sha256 `1259e00182bf052b`) records every member
as `insufficient` with its reason.

**What the data does and does not support.** The data supports: the
registered rules, with and without a point-in-time structure filter, have
**negative** expected net R per trade on the dev window, with funding
adjusted within ~0.001 R of the friction-only figures. It does not
support any claim of an edge, and nothing here speaks to the holdout.

## 5. The manual-review signal surface

`proto/quant/signals.py` + `GET /quant-signals` (read-only, `mode=ro`,
behind the existing bearer gate) + the Research view's candidate block.

**The three concepts are separate and stay separate:**
1. **A candidate** — a rule fired on a closed bar: coin, direction, rule +
   version, signal timestamp, data freshness, entry (and its basis), stop /
   target / horizon **with the assumptions behind them**, the cost basis
   (labelled as an assumption), the evidence status, and the execution note.
2. **The evidence status of the rule** — `exploratory` / `insufficient` /
   `validated`, carried as data with its denominators, its interval
   endpoints and its reason. It comes from the preregistered artifact,
   which is checked against the **preregistration record** it names (id and
   config hash) and is keyed by **(rule, feature identity)**, so a
   hand-edited or foreign artifact cannot render a rule as validated, and
   two members of the same rule cannot inherit one another's verdict.
3. **Execution** — never. There is no order path, no credential, no
   account or broker endpoint in the diff; `actionable` is always `false`;
   the store's own `SHADOW` mode is reported unchanged; only `SHADOW` rows
   are surfaced (a legacy `ACTIONABLE` row is skipped); and the view says
   "manual review only — the app does not execute these". A promotion or a
   validation result never authorises an order.

The live route (after the restart) returns **24 candidates (12 long, 12
short)**, each with the evidence status `insufficient` and the reason, the
dev denominators (3,421 events / 295 coins / 56 days for the shock rule),
the measured range endpoints, and the family p = 1.0.

## 6. The deployment

Owner-authorized. The unit file and the operator's drop-in are
**byte-identical** before and after (md5 `68589d7a…` and `41873be1…`):
`--quant-shadow` and `TimeoutStopSec=600s` are preserved, and no systemd
file was written.

| Item | Value |
|---|---|
| Pre-restart | MainPID 732289, started 2026-10-19 15:46:39 WAT, `NRestarts=0` |
| Restart requested / returned | 13:51:46Z / **13:51:48Z** → **2 s** (the previous drain took 601 s and ended in a SIGKILL) |
| New process | MainPID **759227**, started 2026-10-20 14:51:48 WAT |
| Running revision | `code_rev 94bdf49` (reported by `/quant-research` — the live git HEAD) |
| Routes | `/healthz` 200 · `/readyz` 200 · `/scanner-health` 200 · `/quant-status` 200 · `/quant-research` 200 · **`/quant-signals` 200** |
| Auth gate | 401 without a token on `/readyz`, `/quant-research`, `/quant-signals` |
| First sweep | `state: in_progress`, `completed_ts: null` — **API readiness does not mean the sweep has completed** |

The drain is 2 s because the new code observes the stop flag **between
per-coin units**: the interrupted iteration is recorded honestly
(`resolve skipped: the iteration is interrupted`,
`stages: scan=381.9(partial) collect=55.5(failed) funding=0.0(skipped) …`).

**Safety, verified after the restart:** `docs/decisions/log.jsonl` is still
**0 bytes**; no `docs/decisions/chain.jsonl`; no `data/promotions.json`;
**no `data/quant_research/`** (the holdout was never evaluated); the quant
store holds 24 events, **0 `ACTIONABLE`** rows and 0 outcomes; **no archive
was rewritten**; no pause guard was weakened or bypassed; and no order was
placed because no order path exists.

## 7. Tests actually run (and what they show)

| Check | Result |
|---|---|
| `python3 tests/test_quant_analysis.py` | ALL PASS (the holdout wall on the evaluation path, the funding-label alignment, the criteria, the mean-R placebo) |
| `python3 tests/test_quant_signals.py` | ALL PASS (the read-only route, the member-keyed evidence, the artifact trust, SHADOW-only) |
| `python3 tests/test_ws_a_measurement.py` | ALL PASS (the pivot-identity fix) |
| `python3 tests/test_ws_c_prereg.py`, `tests/test_hypotheses.py` | ALL PASS (the preregistration validates clean) |
| `python3 tests/run_all.py` (full offline backend suite) | **ALL SUITES PASS (87 files)** on the merged tip |
| `app/`: `npm run test` | 200 passed / 14 files (one pre-existing SSE reconnect timing flake under parallel load, passing 3/3 in isolation) |
| `app/`: `npm run typecheck` / `lint` / `build` | clean / 0 warnings / built |

Two review rounds (a fresh task review and a fresh scoped re-review) were
run on the integrated work; the findings they raised — the unenforced
holdout wall, the mis-sliced funding labels, the non-read-only route, the
unsatisfiable universe rule, the unimplemented criteria, the artifact trust
and several test-quality problems — are all closed, and the fix rounds are
in the history.

## 8. Unresolved limitations

1. **The preregistered minimum sample sizes are not satisfiable on the
   validation window** (14 days < 20). Every member is `insufficient`
   there. A future preregistration must check the minimums against the
   window lengths **before** it is frozen.
2. **Criterion 7 has no registered control for the filtered member**, so it
   cannot freeze either; the code compares it with the unfiltered version of
   itself, which is not what the frozen text says. Reported, not amended.
3. **The funding observation archive covers only ~4.6 % of the bar window**
   on the median coin; the funding-adjusted figures therefore rest on the
   settlement archive, and the observation fallback contributed little.
4. **The cost model is an assumption**: the 5 bp/side fee is the planner's
   unconfirmed constant and the slippage is tiered by trailing-24h quote
   volume, not measured. The +100 % aspiration is a research objective with
   **no** supporting evidence here, and the equity-denominated return is
   not computable without a position-sizing and liquidation model.
5. **The 24 live candidates are exploratory**: their rules have negative dev
   means, their evidence status is `insufficient`, and one of them (the
   shock rule) had a dev mean whose 95 % interval excludes zero **on the
   negative side** (−0.0280 R, [−0.0579, −0.0017]). Showing them is
   showing a rule that loses money on the measured window.
6. The quant watcher was **paused** for part of the deployed window
   ("BTC bars stale"): the venue's klines lag the collect, and the guard
   (20 min) then refuses to emit from stale data. The guard was **not**
   weakened; the watcher resumed by itself when the lag fell back.
7. The first sweep's per-stage timings for the **new** deployment were not
   captured before this report was written (the first sweep was still
   running); the stage breakdown that exists in the 2026-10-19 deployment
   report belongs to the previous deployment.

## 9. The candid conclusion

**What the data supports:** over the 2026-07-13 → 2026-10-20 archive, the
registered breakout and shock rules, with and without a point-in-time
structure filter, had **negative** expected net R per trade on the dev
window, and the family-level correction gives **p = 1.0**. The point-in-time
filter roughly halved the event count and left the mean unchanged
(−0.0393 vs −0.0400). The funding-adjusted figures are within ~0.001 R of
the friction-only ones, because funding attribution covers > 99.9 % of the
evaluated events.

**What the data does not support:** any claim that these rules have an
edge, any claim about the holdout (it was never evaluated), and any claim
that a large return — +100 % or otherwise — is attainable. No position was
sized, no liquidation distance was modelled, and the costs are assumptions.

**What changed in the product:** the app can now *show* rule-generated
candidates with their evidence, so the owner can review them by hand; the
app still cannot and will not trade them.

**Still awaiting the owner:** whether to spend the one-shot holdout (not
recommended on this evidence: the protocol's own criteria were not met, and
the validation window is too short to evaluate them), whether to adopt the
new hash-chained decision log in place of the human-owned one, and the
deployment of the remaining WS3 items (1 m design, provisional tier,
confirmed fee tier).
