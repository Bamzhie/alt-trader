# Quant System Overhaul Plan

**Status:** owner-directed design plan; not implemented. This plan supersedes
HYD-0003 and the current rule-family program as the intended product direction.
It does not erase their reports, evidence, code, or stored data. No market data,
bar archives, or existing database contents may be modified or deleted without
the owner's explicit approval.

## Intended system

ALT Radar continuously scans the full available coin universe. Candidate
generation is deferred during the current development phase: **no rules are
present, run, or allowed to emit candidate signals until the quant engine is
ready.** The engine is first built and evaluated from historical bars. Once
ready, candidate guidance may be introduced as an input to quant analysis; it
does not decide that a coin is profitable. The quant engine runs historical
backtests and measures candidates using versioned calculations, assumptions,
and data snapshots. It ranks and presents candidate evidence to the frontend
for human review. The system remains research and read-only: a positive result
does not authorize or place a trade.

The first planned candidate guidance, **ACC-POC long**, is deferred until the
quant engine is ready. It then provides the
accumulation → POC breakout → retest concepts described in
[the ACC-POC plan](strategy-acc-poc-plan.md) to help identify potential long
candidates. It is represented inside the quant analysis engine, but is not a
fixed recipe the engine must follow exactly. The engine may adapt, combine,
reweight, or replace its calculations as it tests what works. ACC-POC remains
long-oriented guidance in this phase; the engine owns candidate analysis and
outcome evaluation. Each distinct calculation/configuration must be versioned
so its results remain attributable and comparable.

## Benchmark direction

- A successful outcome reaches **+100% return on margin (ROE)** before the
  specified stop or outcome horizon. The target applies to long and short
  candidates; ACC-POC, if introduced after readiness, is long-oriented.
- The loss threshold is **−250% ROE**. The owner specifies cross margin. The
  backtest must model realized returns and costs consistently; account-level
  cross-margin exposure and portfolio drawdown are separate risk outputs.
- A **backtest cycle contains one selected signal per coin**: a 20-coin cycle
  therefore evaluates 20 signal outcomes, one per coin. The engine adjusts
  its calculation formulas using designated development data. The 75% target
  is then measured against the precommitted sample (15/20; for a 50-coin
  sample, 38/50). The owner reviews the result and may approve a new,
  predeclared sample schedule for a later cycle; coins are not manually swapped
  or resampled in response to observed outcomes. This replaces a requirement
  for many signals per coin in one cycle; the cycle-level denominator is
  coins/signals, not repeated events from the same coin.
- The exact selection rule for the one signal per coin (for example, first
  eligible candidate or highest-ranked candidate), how a coin outcome is
  scored, and whether the 20 → 50 → >100 cohorts are fresh or cumulative still
  need to be frozen. Every formula iteration and selected coin set must be
  recorded. Repeated tuning on the same cohort is development, not independent
  confirmation; after review, the next selected cohort should provide the
  next generalization check.
- With outcomes exactly +100% and −250% ROE, a 75% win rate gives +12.5%
  gross mean ROE per signal before costs. That arithmetic does not include
  losses beyond the stop threshold, fees, slippage, funding, overlapping
  positions, or portfolio-level cross-margin effects. The benchmark must
  report net results and its stated assumptions.

## Data architecture and update cadence

1. **Keep `data/bars/` as the canonical collector output.** Its current fetch
   process continues unchanged. The new design must not rewrite, truncate,
   reformat, delete, or backfill existing bar files or any other collected
   archive without explicit owner approval.
2. **Plan a separate quant research database** as a materialized, queryable
   mirror of `data/bars/`. It is populated by reading the bar archive; it does
   not fetch independently from the exchange and never writes back to the
   archive. It is a derivative copy, not a replacement source of truth. Its
   name, engine, schema, retention, and recovery procedure are implementation
   decisions still to be specified.
3. **Synchronize every eight hours** (three scheduled runs per day). Each sync
   imports newly available bars and records its source watermark and snapshot
   identity. Repeated syncs must be idempotent. Reconciliation errors must
   stop or quarantine the mirror update rather than “repair” source files.
4. **Run many backtests between syncs.** The engine uses the latest complete
   mirror snapshot for a batch of versioned tests during each eight-hour
   interval. Each run records its data snapshot/watermark, code revision,
   candidate/hypothesis version, parameters, cost assumptions, and outputs.
   Run scheduling, concurrency/resource limits, and progress visibility are
   not yet implemented or specified in detail.
5. Preserve the existing databases and their rows. The new mirror/backtest
   store is additive. Do not migrate destructively or drop legacy tables as
   part of this plan.

### Training-data boundary

**Only historical market data from `data/bars/` may be used to build, tune,
or evaluate the new quant engine.** The new mirror database is a derived copy
of those bars and must preserve source timestamps and provenance. Do not use
`signals.db` or `quant.db` rows as training examples, labels, features,
candidate-selection inputs, or benchmark outcomes. Their existing records
belong to legacy signal/rule definitions and outcome protocols. They may be
retained for separate historical audit or comparison, but must not feed the
new engine's calculations or pass rates. Other archives (funding, settlement,
price, or future sources) are also excluded from the initial engine unless the
owner explicitly changes this data boundary in a later plan revision.

The engine must create its own candidates and labels by replaying versioned
calculations over `data/bars/`, then resolving each selected candidate against
subsequent bars using the new benchmark's frozen entry, target, stop, horizon,
and cost rules. This keeps the training population and outcomes reproducible
and prevents legacy outcomes from silently defining the new task.

### Sampling and anti-overfitting protocol

Define the eligible coin universe and its eligibility date/rules before
sampling. Describe its representativeness across liquidity, volatility, market
capitalization (where available from approved inputs), and historical market
conditions. Draw reproducible random coin samples from that universe using
recorded seeds and a stated sampling/stratification method. The sample schedule
and seeds for a benchmark cycle must be fixed before inspecting its results.

Randomization must not become repeated resampling until a sample reaches 75%.
Do not change seeds, redraw coins, exclude inconvenient results, or keep
re-running a nominally equivalent test after seeing outcomes to obtain a pass.
Any additional exploratory sample is a separate, permanently recorded run and
cannot replace the preregistered result. If the fixed sample misses the target,
report that result; any engine revision is tuned on development data and then
gets a new, separately frozen validation sample.

Tune formulas only on designated development dates/coins. After tuning, freeze
the engine version and evaluate it on later dates and coins not used for
tuning. Record overlap checks and maintain chronological order so labels do
not leak across the split. The reported 75% validation result must come from
this frozen, unseen evaluation, not the tuning sample. Repeated development
experiments remain useful for improvement but are not independent validation.

Repeatedly tuning against the same historical bars is development evidence,
not independent confirmation. The overhaul needs a versioned chronological
backtest protocol, leakage/overlap controls, and a clearly identified
out-of-sample evaluation policy before any “75% success” claim. This plan does
not reuse HYD-0003's defective 14-day validation minimum or spend its sealed
holdout by default. Historical data can and should be used to develop and
backtest the engine; the reporting must distinguish tuning results from
out-of-sample evidence.

## What will be affected

- **Research authority:** HYD-0003 and current registered rule hypotheses
  cease to govern the product's candidate/pass decision. Their reports and
  outcomes remain archived as historical evidence; they are not retroactively
  rewritten.
- **Candidate pipeline:** no rule-based candidate generation is used during
  engine development. Only after the engine meets its readiness criteria may
  ACC-POC or another guidance source be introduced as a candidate-intake input.
  Current rule scores must not be treated as proof of edge.
- **Quant processing:** the existing shadow watcher is incremental and
  event-oriented; the requested repeated, snapshot-based backtest batches
  require a new or substantially expanded research runner and run registry.
- **Database and API:** the new mirror/backtest database, quant result
  contracts, routes, and frontend views will affect quant persistence and
  result presentation. `data/quant.db` and `signals.db` may have consumers that
  need compatibility work; preserve both databases and their contents.
- **Frontend:** the Research view will need to show candidate source/direction,
  analysis status, backtest progress, matured sample counts, per-coin pass
  status, net results, and assumptions. Existing pages that depend on legacy
  scanner or `signals.db` records need an explicit compatibility or retirement
  decision.
- **Evaluation protocol:** the existing dev/validation/holdout and promotion
  gates do not automatically define the new staged coin benchmark. A new
  protocol must pin entry, exit, stop, horizon, costs, eligibility, one-signal
  selection per coin, cohort semantics, and out-of-sample accounting before
  benchmark claims.

## What is lost or retired in the new design

- The old hypothesis families lose their role as the system's governing
  strategy and as the required path to a coin candidate. Their historical
  records and conclusions are retained.
- Any behavior whose only purpose is to run rule analysis on every API fetch
  stays discontinued; the new cadence is archive collection plus scheduled
  mirror sync and repeated quant backtests.
- Existing rule-specific verdicts, labels, promotion statuses, and dashboards
  cannot be assumed to describe the new benchmark. They may be retained for
  historical inspection, but must not be relabeled as results of the new
  engine.
- A single rule's direct “pass/fail” authority is removed. Candidate
  generators nominate; the quant engine measures. A candidate may still fail
  analysis, lack enough matured outcomes, or be withheld from the frontend
  until evidence is ready.

“Lost” here means retired as current product behavior, not deleted from the
repository or database. The owner's data-protection rule takes precedence:
only code and documentation may be modified/deleted without additional
approval; no collected data or existing database contents may be touched.

## Proposed implementation sequence

1. Freeze the benchmark contract and define “coin passes,” how one signal is
   selected per coin, eligible universe and precommitted randomized sample
   schedule, horizon, entry timing, stop semantics, costs, and portfolio
   treatment.
2. Inventory code and UI dependencies; mark each reusable, adapted, or
   retired while preserving all data stores. Enforce the training-data
   boundary: `data/bars/` only.
3. Design and review the read-only-source mirror, schema, idempotent eight-hour
   sync, snapshot identity, and recovery behavior. Do not create it until
   implementation is explicitly authorized.
4. Build the batch backtest/run registry around immutable bar snapshots and
   versioned candidates/calculations; generate labels from subsequent bars.
5. Build one-signal-per-coin cycle evaluation (20 → 50 → >100 coins), formula
   iteration/version tracking, owner review checkpoints, and chronological
   out-of-sample evidence, using bars only and with all rule candidate
   generation disabled.
6. Define and review quant-engine readiness against this backtest protocol;
   no rule-based candidate generation is enabled during this phase.
7. After readiness, integrate ACC-POC as optional long-oriented guidance
   inside the quant engine; allow versioned engine calculations to adapt it.
8. Compare the new behavior against retained historical reports only as a
   separate audit, review manually, and only then decide on any live
   deployment or data-store retirement.

## Requirements for the new plan

These requirements are the implementation acceptance checklist. “Planned” does
not mean implemented or authorized for a data-changing operation.

### Data protection and provenance

- Use only `data/bars/` as the new engine's historical training and evaluation
  source. Do not use legacy `signals.db` or `quant.db` rows in the new engine.
- Keep the existing collector and bar archive unchanged. The mirror reads from
  `data/bars/`; it never fetches independently or writes back to source files.
- Create a separate mirror/backtest database only after implementation is
  authorized. Preserve existing database contents; no destructive migration.
- Make the eight-hour sync idempotent, auditable, and recoverable. Record
  source watermark, snapshot identity, sync time, and any rejected/incomplete
  imports. Do not silently repair source data.
- Every backtest run records its bar snapshot, code revision, formula/config
  version, ACC-POC guidance version (if used), selected coins, entry/exit
  assumptions, cost assumptions, and outputs.
- Keep a durable, append-only record of **every backtest attempt**: successful,
  failed, interrupted, exploratory, tuning, and rerun. Never overwrite a prior
  run when a formula or parameter changes; create a new run linked to its
  parent/baseline where relevant.
- Each run record includes a unique run ID; start/end time and status; source
  snapshot ID and bar watermark; code revision; formula/config version and
  parameter values; selected coin set; candidate/signal selection result;
  benchmark, entry/exit, target/stop/horizon, and cost assumptions; per-coin
  outcomes and aggregate metrics; warnings/errors; and a reference to detailed
  logs or artifacts. Store enough inputs to reproduce the run.
- Record runs before execution begins and update their terminal status and
  results without changing their identity or inputs. Partial/interrupted runs
  must remain visible and must not be counted as completed benchmark passes.
- Backtest history must be queryable by run, cycle, formula version, coin,
  source snapshot, and status so the owner can review how each calculation
  changed and what evidence it produced.

### Candidate and backtest behavior

- Keep all rule-based candidate generation disabled while the quant engine is
  under development. ACC-POC is not active during this phase.
- Once readiness is established, ACC-POC may be introduced as adaptable,
  long-oriented guidance in the quant engine; the engine may adjust or replace
  its calculations. Do not hard-code it as a mandatory sequence or
  profitability verdict.
- Evaluate at most one selected signal per coin in each cycle. A 20-coin cycle
  therefore has 20 coin-level outcomes, not multiple counted signals from a
  coin.
- Record the rule for selecting that one signal and the coin set for each
  cycle. Keep every formula adjustment as a distinct, attributable version.
- Define eligible coins and a representative sampling/stratification method
  before the run. Random samples use recorded seeds and a precommitted sample
  schedule; do not redraw after seeing outcomes to try to reach 75%.
- Run numerous backtests on each complete mirror snapshot between the planned
  eight-hour syncs, with resource limits and run progress visible.
- Persist a durable record for every run and iteration, including unsuccessful
  and interrupted attempts; benchmark summaries must link back to those run
  records.
- Resolve outcomes from future bars without lookahead, with consistent entry,
  target (+100% ROE), stop (−250% ROE), horizon, tie/gap handling, and net-cost
  accounting.

### Benchmark and review

- Support the owner-directed stages of 20, 50, and more than 100 coins. At
  each stage, the target is at least 75% passing coins (15/20; 38/50).
- Define what makes the single signal/coin pass, including target-before-stop
  semantics, timeouts, costs, and data gaps. Report both coin-level pass count
  and the underlying per-coin outcome.
- After a cycle reaches its target, present results for owner review; the
  owner chooses the next coin values/cohort. Do not automatically treat a
  tuned cohort as independent confirmation.
- Distinguish formula-tuning results from generalization results. Record which
  historical coin/time data were used for each iteration. Tune on designated
  development data, then freeze the engine and validate on unseen coins and
  later dates; do not use validation results to keep tuning the same version.
- Keep the sample seeds, universe definition, strata, split dates, and frozen
  engine version with the run records so the sample and validation can be
  reproduced.
- During engine development, show backtest/run status and results only; there
  are no rule-generated candidate signals.
- After readiness, keep the frontend research-only: show candidate,
  source/guidance version, formula version, cycle progress, outcome, pass
  status, and assumptions. No order execution.

## Decisions still needed

- How the engine selects the single signal for each coin in a cycle, and the
  precise pass criterion for that signal/coin.
- Outcome horizon and whether a time exit counts as failure, neutral, or a
  separate category.
- Whether −250% is the hard modeled stop outcome or a trigger whose realized
  loss can exceed the threshold under gaps/cross-margin account effects.
- Exact fees, slippage, position sizing, overlap policy, and portfolio-level
  cross-margin assumptions. Funding data is excluded from the initial engine
  under the bars-only boundary unless that boundary is later revised.
- The holdout/out-of-sample design that prevents repeated historical tuning
  from being reported as independent success.
- The coin-universe eligibility rules, representativeness strata, random
  sampling schedule/seeds, and exact separation of development and unseen
  validation coins/dates.
- Mirror database technology/name, physical layout, resource budget, failure
  recovery, and the desired frontend update/progress behavior.

## Current implementation status

Implemented foundations include the `data/bars/` collector/archive, archive-
backed quant research and replay utilities, outcome/cost/funding analysis,
and a shadow quant event path with read-only research presentation. These do
not yet constitute the requested system. During development, existing rule
paths are not accepted as the new engine's candidate layer and no rules should
emit new candidates. There is no separate eight-hour
mirror database, no scheduled repeated backtest batch runner against its
snapshots, no staged one-signal-per-coin 20/50/>100 benchmark, and the new
engine has not yet met readiness to receive ACC-POC guidance. This document
records the target only; it authorizes no code changes, database creation,
data migration, deployment, or holdout evaluation.
