# WS5 Evaluation Harness — Operator Reference

Research-only historical-data evaluation harness with funding cost
attribution. Runs the vendored quant research engine on production bar
archives with proper split discipline and venue-reported funding costs.

## Commands

```bash
# Full evaluation on production bars, with funding cost attribution:
python3 -m proto.quant evaluate --data data/bars \
    --funding-dir data/funding \
    --settlements-dir data/funding_settlements \
    --out data/quant_research

# Spend the one-shot holdout (irreversible -- lock file prevents re-evaluation):
python3 -m proto.quant evaluate --data data/bars \
    --funding-dir data/funding \
    --holdout \
    --out data/quant_research
```

Both commands are **research-only**: they never write to `data/`, never
create `data/quant.db`, never start the service, and never change emission
modes.

## Split discipline

Splits are inherited from `proto/quant/config.Splits` (same as the research
harness):

| Window  | Default (days from archive start) |
|---------|-----------------------------------|
| First event | day 6.0 (warmup) |
| Dev    | day 6.0 -> 61.0 |
| Embargo | 2.0 days (>= max horizon 288 bars + 1 bar) |
| Val    | day 63.0 -> 77.0 (14 days) |
| Embargo | 2.0 days |
| Holdout | day 79.0 -> end of archive |

The harness asserts no event's label window crosses a split boundary via
`check_no_leakage()`. Any violation is reported in `evaluation.json` under
`leakage_violations`.

**Selection is friction-only.** The dev -> val selection and the max-t
multiplicity control use the research harness's friction (fees + slippage)
net R. Funding-adjusted numbers are computed afterwards, for the selected
config, as reporting only — they never select a config, and nothing is
tuned on validation or holdout.

## Funding cost attribution

Costs attach venue funding to each event's holding window. Funding is paid
or received **only at the venue's settle boundaries** (the funding events);
an observation timestamp is not a funding event.

1. **Settlement archive** (`data/funding_settlements/`) first. Each row's
   `settle_ts` IS a funding event timestamp and `settled_rate` is what the
   venue actually paid there. When the archive holds a row for every
   boundary inside the window, those settled rates are the cost.
2. **Observation archive** (`data/funding/`) is the fallback for
   observation-only periods. Observation rows record the MUTATING rate at
   `ts`; they are **never summed per snapshot**. The documented mapping is:
   a funding event at boundary `b` takes the **last snapshot in
   `[b - cycle, b)`**, each boundary charged at most once. A snapshot after
   `b` belongs to the next cycle.
3. **Grid phase.** Boundaries are `phase + k * cycle`, the VENUE's own
   settle grid. When the coin has a settlement archive, **both** `cycle`
   (its `cycle_hours`) and `phase` (`settle_ts % cycle`) are inferred from
   that archive and used for the observation fallback too, so snapshots
   map to the boundaries the venue actually settles on — even when the
   phase is off-epoch. Only a coin with **no** settlement archive at all
   uses the epoch-aligned 8h UTC grid. If a settlement archive exists but
   carries no usable `cycle_hours`, the grid is **uninferable** and the
   window is incomplete (`reason="venue-grid-unknown"`); epoch alignment
   is never assumed in place of the venue grid. `BOUNDARY_TOL_S` (one 5m
   bar) absorbs whole-second rounding when matching a venue row to its
   boundary.
4. **Incomplete means excluded, never estimated.** If any funding event
   inside the holding window cannot be attributed — a missing venue
   settlement row, no snapshot covers a boundary's cycle, or an
   uninferable venue grid — the event's cost is `None` (`incomplete=True`)
   and the event is excluded from the funding-adjusted metric and counted.
   Snapshot rates are never summed repeatedly to fill a gap.
5. **Position side from the rule policy.** The position side is derived from
   the SELECTED rule (`trade_side`): `cont` trades with the break
   (`ev.side`), `fade` trades against it (`-ev.side`). That same side is
   used for the funding sign, for exit-offset slot selection, and for
   coverage accounting.
6. **Sign**: positive funding rates are costs for long positions (side=+1)
   and credits for short positions (side=-1). Negative rates reverse the
   effect.
7. **Actual exit timestamp**: funding is charged only through the event's
   actual exit, not the maximum horizon. TP/SL events exit early (the
   barrier labeler's `exit_offset`, keyed by the trade side, is retained on
   the `Event` struct). Only TIME exits go to the full horizon.
8. **Missing data is missing** — if a coin has no cost archive rows at all,
   `missing=True`, `cost=None`. The harness does NOT estimate, interpolate,
   or zero-fill. Such coins are listed under `missing_cost_coins`.

The cost is additive to the round-trip friction in `barrier.net_r`:

```
friction-only net R      = (gross_log_return - round_trip_cost) / stop_distance
funding-adjusted net R   = (gross_log_return - round_trip_cost - funding_cost) / stop_distance
```

## Funding-adjusted metrics and denominators

Every window reports two clearly separated views:

| Report field | Basis | Selection |
|--------------|-------|-----------|
| `dev`, `val` | friction-only (fees + slippage) | used for dev->val selection |
| `holdout`, `holdout_slip2x` | friction-only | one-shot holdout + 2x-slip sensitivity |
| `dev_funding_adjusted`, `val_funding_adjusted` | friction + funding | reporting only |
| `holdout_funding_adjusted`, `holdout_slip2x_funding_adjusted` | friction + funding | reporting only, computed in the same single holdout look |

Each `*_funding_adjusted` block makes its denominator explicit:

| Field | Meaning |
|-------|---------|
| `n_events` | selected, tradeable events in the window (the coverage denominator) |
| `n_funding_adjusted` | events with a fully attributed funding cost — **the metric's denominator** |
| `n_excluded_missing_coin` | events on coins with no cost archive rows |
| `n_excluded_incomplete` | events with unattributable funding events in their window |
| `coverage` | `n_funding_adjusted / n_events` |
| `missing_coins` | sorted coins counted as missing |

`n_funding_adjusted + n_excluded_missing_coin + n_excluded_incomplete ==
n_events` always holds. When `n_funding_adjusted` is 0, every metric field
is `null` — a funding-adjusted label never carries a friction-only number.
`dev_funding_adjusted` / `val_funding_adjusted` are `null` when no config
was selected (insufficient data), and the holdout funding-adjusted fields
are `null` unless the one-shot holdout was actually spent.

Top-level `incomplete_boundary_events` / `incomplete_boundary_denominator`
aggregate the excluded-and-counted totals across every window whose
funding-adjusted metric was computed (dev + val + holdout-if-spent; the
2x-slip variant is the same events and is not double-counted).

## Reproducibility

Every report carries:

- `config_hash` -- SHA12 of the frozen quant config (version, cost tiers,
  feature params, grid, splits, selector, risk).
- `cost_hash` -- SHA16 of the config hash + funding_dir + settlements_dir
  + settle-seconds + boundary tolerance. Any change to cost inputs or the
  attribution rule changes this hash.
- `git_rev` -- the git HEAD at run time (enables exact reproduction).
- `bar_digests` -- SHA256 of each coin's bar archive file (all coins, no cap).
- `funding_digests` -- SHA256 of each contributing coin's funding observation
  archive (all coins, no cap).
- `settlement_digests` -- SHA256 of each contributing coin's settlement
  archive (all coins, no cap).
- `splits` -- the exact split boundaries used.

Results are only comparable within the same `config_hash` + `cost_hash`.

## Report fields

`evaluation.json`:

```json
{
  "config_hash": "abc123def456",
  "cost_hash": "7890abcdef12",
  "splits": {"dev": [6.0, 61.0], "val": [63.0, 77.0], "holdout": [79.0, 91.4], ...},
  "dev": {"n": 234, "mean_r": 0.05, "ci_lo": -0.12, "ci_hi": 0.22, ...},
  "val": {"n": 89, "mean_r": 0.03, ...},
  "holdout": {"n": 45, "mean_r": -0.01, ...} | null,
  "holdout_slip2x": {...} | null,
  "dev_funding_adjusted": {
    "n_events": 234, "n_funding_adjusted": 210,
    "n_excluded_missing_coin": 12, "n_excluded_incomplete": 12,
    "coverage": 0.897, "missing_coins": ["AAA", "BBB"],
    "mean_r": 0.021, "ci_lo": -0.14, "ci_hi": 0.18, "days": 54, "coins": 40,
    "tp": 0.31, "sl": 0.28, "time": 0.41, "t": 1.8
  },
  "val_funding_adjusted": {...} | null,
  "holdout_funding_adjusted": {...} | null,
  "holdout_slip2x_funding_adjusted": {...} | null,
  "leakage_violations": [],
  "missing_cost_coins": ["AAA", "BBB", ...],
  "git_rev": "4e644cc...",
  "bar_digests": {"BTC": "sha256...", ...},
  "funding_digests": {"BTC": "sha256...", ...},
  "settlement_digests": {"BTC": "sha256...", ...},
  "incomplete_boundary_events": 12,
  "incomplete_boundary_denominator": 323,
  "produced_at": 1792262400
}
```

## Limitations

- **Coverage (corrected 2026-10-18).** The default Splits need about 91
  days of bars for a full dev/val/holdout run (holdout starts at day 79;
  the original study reserved a ~12-day holdout). The bar archives are
  batch-appended and NOT chronological, so coverage must be read from
  **sorted unique min/max**. Measured that way, 523 of 578 coins have ≥91
  days (BTC 28,067 unique bars / 97.45 days). The earlier "~16.41 days / no
  coin ≥91 days" claim was the unsorted first/last-line artifact and is
  superseded. Inspect per-coin coverage
  (`proto.quant.coverage.audit_spans`) before interpreting universe-wide
  results; the 52 coins below 79 days cannot populate a holdout window.
- **Funding costs.** `data/funding_settlements/` now holds 559 coin
  archives (~1.37M venue settle rows, spans up to ~540 days), so
  funding-adjusted metrics can represent full-window historical costs for
  coins whose settlement archive covers the window. The `data/funding/`
  observation archive (571 coins, ~2 days each) is a fallback for
  observation-only periods. Extend settlements before treating a coin's
  funding-adjusted numbers as historical evidence.
- The settlement archive must cover every funding event in a window for
  its rates to be used. Collections that started mid-archive leave earlier
  windows incomplete (excluded and counted), not back-filled.
- The observation mapping uses the venue grid (phase + cycle) inferred
  from the coin's settlement archive when present, so an off-epoch venue is
  mapped to its real boundaries. Observation-only coins (no settlement
  archive at all) assume the epoch-aligned 8h UTC grid. A settlement
  archive that carries no usable `cycle_hours` yields an uninferable grid
  and its windows are reported incomplete — never epoch-assumed.
- The harness does NOT model funding on unrealized positions, partial
  fills, or mark-price divergence. It is a research tool, not a trading
  calculator.
- No rule has passed promotion. All outputs are SHADOW / not actionable.
