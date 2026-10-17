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

## Funding cost attribution

Costs attach venue-reported funding rates to each event's holding window:

1. **Settlement archive** (`data/funding_settlements/`) is consulted first --
   these are venue-reported settled rates at exact `settle_ts` boundaries.
2. **Observation archive** (`data/funding/`) is the fallback -- these are
   point-in-time observed rates (the rate at `ts`, not a settled fact).
3. The cost is the sum of rates at each settle boundary (observation
   timestamp) in the position's holding window `[open_ts, exit_ts)`,
   multiplied by the trade side.
4. **Position sign**: positive funding rates are costs for long positions
   (side=+1) and credits for short positions (side=-1). Negative rates
   reverse the effect. The actual trade side from the barrier labeler is
   used.
5. **Actual exit timestamp**: funding is charged only through the event's
   actual exit, not the maximum horizon. TP/SL events exit early (the
   barrier labeler's `exit_offset` is retained on the `Event` struct and
   used to compute the real exit timestamp). Only TIME exits go to the
   full horizon.
6. **Missing data is missing** -- if a coin has no funding archive at all,
   `cost=None, missing=True`. The harness does NOT estimate, interpolate,
   or zero-fill. Coins with missing cost data are listed under
   `missing_cost_coins` in the report.
7. **Incomplete boundary coverage**: events whose funding data does not
   cover the full holding window are excluded from funding-adjusted metrics.
   The report carries `incomplete_boundary_events` (count excluded) and
   `incomplete_boundary_denominator` (total events with cost data available).

The cost is additive to the round-trip friction in `barrier.net_r`:

```
net_r = (gross_log_return - round_trip_cost - funding_cost) / stop_distance
```

## Reproducibility

Every report carries:

- `config_hash` -- SHA16 of the frozen quant config (version, cost tiers,
  feature params, grid, splits, selector, risk).
- `cost_hash` -- SHA16 of the config hash + funding_dir + settlements_dir
  + settle-seconds. Any change to cost inputs changes this hash.
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
  "leakage_violations": [],
  "missing_cost_coins": ["AAA", "BBB", ...],
  "git_rev": "4e644cc...",
  "bar_digests": {"BTC": "sha256...", ...},
  "funding_digests": {"BTC": "sha256...", ...},
  "settlement_digests": {"BTC": "sha256...", ...},
  "incomplete_boundary_events": 0,
  "incomplete_boundary_denominator": 89,
  "produced_at": 1792262400
}
```

## Limitations

- The default Splits assume >=91 days of bars for full dev/val/holdout.
  With shorter archives (e.g. the current ~16-day production set), the
  dev window may be truncated and no holdout is spent. This is honest,
  not a defect.
- Funding is charged at the observation/settlement timestamps directly,
  not a synthetic 8h grid. The venue-reported rate at each boundary
  applies piecewise-constant until the next observation.
- The harness does NOT model funding on unrealized positions, partial
  fills, or mark-price divergence. It is a research tool, not a trading
  calculator.
- No rule has passed promotion. All outputs are SHADOW / not actionable.
