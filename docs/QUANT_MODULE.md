# Quant module (`proto/quant/`)

Read-only decision-support engine. It places no orders, holds no keys, makes no
network calls, and never writes to `data/signals.db` (it has its own
`data/quant.db`). Stdlib only, like the rest of the app.

## What it is for

Answer one question honestly: **is there a rule whose net expected R, after
costs, is reliably above zero on data that was never used to choose it?**
If yes, emit a small number of budgeted, risk-sized events. If no, say so and
stay quiet. TP-hit rate is reported but is not the objective: with zero drift it
is fixed by stop/target geometry (`p0 = b/(a+b)`); only net R and the excess of
the TP-first rate over that null mean anything.

**Current verdict (see `docs/quant-research-2026-10-12.md`): no rule has passed.**
Everything the watcher emits today is labeled `SHADOW` / "not actionable".

## Pipeline

```
collector bars -> FeatureStream (per coin, per closed bar)
              -> candidate rules (breakout_start, residual_shock)  [registry.py]
              -> selector (liquidity, cost-in-R, cooldown, caps; budget/EV for promoted)
              -> quant_event (SHADOW or ACTIONABLE) -> triple-barrier outcome -> report
research.py : same FeatureStream over the archive -> dev / val / holdout-once
gates.py    : the only path from SHADOW to ACTIONABLE
```

| File | Role |
|---|---|
| `config.py` | Frozen, hashed config: cost tiers, feature params, grid, splits, selector, risk |
| `bars.py` | Order-independent bar loading (files are NOT time-ordered), gap-free runs |
| `stream.py` | Causal per-bar features: GK vol, BTC-residual return, breakout run age, time-of-day RVOL, liquidity |
| `barrier.py` | Triple-barrier labeling (stop-first, entry at next open) + closed-form barrier math |
| `stats.py` | Day-cluster and moving-block (consecutive-day) bootstraps, max-t over configs, Aalen-Johansen, e-process, calibration |
| `model.py` | Tiny ridge scorer (<= 8 features) judged on calibration, not fit |
| `research.py` | Event collection, chronological splits, grid search, one-shot holdout |
| `registry.py` | Registered rules (+ mirrored "cont" controls) |
| `selector.py` | Auditable gates; every suppression is counted with a reason |
| `risk.py` | Volatility-based plan levels, 5%-risk sizing, leverage cap, correlation clusters, exposure caps |
| `roe.py` | ROE feasibility: the price move a target ROE needs, the stop's cost on margin, the liquidation headroom, the measured base rates |
| `engine.py` | Watcher: per-coin state from bar files, gap/duplicate/invalid handling, global pause |
| `qstore.py` | `quant.db` schema (events, outcomes, suppression counters, watermarks, flags) |
| `gates.py` | Pre-declared promotion thresholds and the promotion record |
| `report.py` | Denominator-explicit measurement report |

## Commands

```bash
# Research on the archive (never touches the holdout unless validation kept the config)
python3 -m proto.quant research --data data/bars --out data/quant_research
python3 -m proto.quant research --holdout          # spends the holdout ONCE, only if val kept the config

# Live shadow watcher (reads data/bars; ~10 s to rebuild 80 coins, seconds per cycle after)
python3 -m proto.quant watch --data data/bars --db data/quant.db --every 300
python3 -m proto.daemon --no-scan --quant-shadow     # or run it inside the daemon

python3 -m proto.quant report --db data/quant.db
python3 -m proto.quant status --db data/quant.db
python3 -m proto.quant pause --db data/quant.db      # global pause; `resume` to clear
python3 -m proto.quant gates --research data/quant_research/research.json

# ROE feasibility (read-only calculator; measures the PRE-HOLDOUT window only)
python3 -m proto.quant roe --data data/bars --leverage 10,20,25 --stop-roe 0.5
```

## Rules that cannot be bypassed from the CLI

- **Holdout is one-shot.** `holdout_evaluated.json` locks it; a second look is refused.
  A new holdout means genuinely newer data, not a deleted lock.
- **Promotion needs all of:** >= 200 events, >= 25 days, >= 15 coins on the
  holdout; day-cluster 95% CI lower bound of net R > 0; still > 0 with slippage
  doubled; validation mean > 0; best-of-all-configs max-t p <= 0.10. Thresholds
  live in `gates.GateSpec` and are never relaxed after a miss.
- A promoted rule's displayed expected R is the **CI lower bound**, not the mean.
- Unregistered configs cannot be promoted: add a new rule id and collect fresh
  shadow evidence first.
- Any config change changes `config_hash()`; results are only comparable within one hash.

## ROE feasibility

`proto/quant/roe.py` answers a question the promotion gate cannot: **what
would a +100% on-margin target actually require, and how often has the archive
delivered it?** It is a CALCULATOR, not a recommendation.

- A target worth +R on margin at leverage L is a price move of `R/L` plus the
  round-trip cost. The stop costs `stop_roe / L` on margin at that leverage,
  and it has to sit inside the liquidation distance to be survivable at all.
- The measured base rates are **unconditional**: every stride-aligned bar of
  every contiguous run is an entry and BOTH sides are labeled with the same
  geometry. A label window never crosses a timestamp gap.
- The command measures the **pre-holdout window only** (dev + validation). The
  holdout window is **excluded from the measurement**: no entry is sampled
  there and no outcome is labelled there. The archive's *timestamps* are still
  read — they are what places the split boundaries — but no holdout outcome
  is computed, and the command refuses any window whose label horizon would
  reach into the holdout. The holdout is spent once, through the research
  harness, by a config that validation kept.
- The intervals are **moving-block** bootstraps over consecutive UTC days,
  not one-day clusters: the label window is 24 h, so the rows of adjacent days
  share their price paths. The block length is a declared assumption and the
  sensitivity across block lengths is reported next to every headline figure.
- Every SHADOW event carries `features.roe_100`: the leverage that event's own
  target would need, what its stop then costs on margin, and whether that stop
  sits inside the liquidation distance.
- Nothing is promoted by any of this and no event becomes actionable. The
  registered rules' own evaluation is recorded in
  `docs/implementation-reports/quant-research-2026-10-20/final-report.md`.

The measurement and its limits: `docs/quant-roe-feasibility-2026-10-22.md`.

## Safeguards

Every event shows its rule id, cost assumption, plan levels, size/leverage under
the operator's 5%-risk/cross-margin setup, and warnings. The watcher pauses
emission when BTC bars are stale, when > 30% of coins lag BTC by more than 3
bars, or when `pause` is set. Gaps rebuild the coin from its last contiguous run
and features stay unavailable until warm; duplicates/out-of-order bars are
ignored; invalid bars drop the coin until rebuilt. Candidates older than 3 bars
are history, not alerts.

## Assumptions you should confirm

- **Fees:** 0.05%/side taker is the planner's constant; your MEXC tier was not
  provided. Set `CostModel.fee_per_side` in `config.py`.
- **Slippage:** tiered by trailing-24h quote volume (0.015%-0.12%/side), an
  assumption for a small account, not a measurement. Below $1M/day a coin is
  untradeable here.
- **Not modeled:** funding, partial fills, limit-order entry, spread widening in
  stress. Entry is "next bar open, market".
- **Cross margin:** liquidation depends on the whole account; the per-trade
  leverage cap (10x) and the 20% aggregate open-risk cap are the protections.
  The isolated-margin liquidation distance shown is a floor check only.

## Tests

`tests/test_quant.py` (74 checks: causality, barrier math vs Monte Carlo, three
labelers agree, stats, gates, selector), `tests/test_quant_engine.py`
(37 checks: engine on synthetic markets, gaps, pause, restart, planted-edge vs
placebo research plumbing, CLI) and `tests/test_quant_roe.py` (86 checks: the
ROE identities, the side symmetry of the geometry, the deterministic
stop/target accounting, the gap handling, the coin reports, the watcher block,
the CLI and the champion module's Python 3.10-3.13 annotations).
