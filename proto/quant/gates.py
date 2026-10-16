"""Promotion gate: the ONLY way a rule becomes actionable.

A rule id + config hash is promoted only if ALL hold on data that was never
used for any choice (the holdout, evaluated once by research.holdout_once):
  * enough evidence: >= min_events events, >= min_days days, >= min_coins coins
  * the day-cluster 95% interval of mean net R has a lower bound > 0
  * the mean stays > 0 with slippage doubled (cost robustness)
  * validation mean net R > 0 (the dev-selected config held up)
  * the best-of-all-configs max-t test on dev had p <= spa_p_max
Thresholds are fixed here BEFORE any holdout is seen and are never relaxed
after a disappointing result - a miss means keep collecting or reject.
"""
import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GateSpec:
    min_events: int = 200
    min_days: int = 25
    min_coins: int = 15
    min_ci_lo: float = 0.0
    spa_p_max: float = 0.10
    require_slip2x_positive: bool = True


GATE = GateSpec()


def evaluate(holdout, val, spa_p, slip2x=None, gate=GATE):
    """Returns (passed: bool, reasons: list[str]).  Missing evidence fails."""
    why = []
    if holdout is None:
        return False, ["holdout not evaluated"]
    if holdout["n"] < gate.min_events:
        why.append(f"holdout events {holdout['n']} < {gate.min_events}")
    if holdout["days"] < gate.min_days:
        why.append(f"holdout days {holdout['days']} < {gate.min_days}")
    if holdout["coins"] < gate.min_coins:
        why.append(f"holdout coins {holdout['coins']} < {gate.min_coins}")
    if not (holdout["ci_lo"] > gate.min_ci_lo):
        why.append(f"holdout CI lower bound {holdout['ci_lo']:+.4f} not > "
                   f"{gate.min_ci_lo:g}")
    if val is None or not (val["mean_r"] > 0):
        why.append("validation mean net R not > 0")
    if spa_p is None or spa_p > gate.spa_p_max:
        why.append(f"best-of-configs max-t p={spa_p} > {gate.spa_p_max}")
    if gate.require_slip2x_positive:
        if slip2x is None or not (slip2x["mean_r"] > 0):
            why.append("mean net R not > 0 with slippage doubled")
    return (not why), why


def _path(data_dir):
    return os.path.join(data_dir, "promotions.json")


def load(data_dir):
    p = _path(data_dir)
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        return json.load(f)


def record(data_dir, rule_id, config_hash, passed, reasons, holdout):
    os.makedirs(data_dir, exist_ok=True)
    cur = load(data_dir)
    cur[f"{rule_id}|{config_hash}"] = {
        "rule_id": rule_id, "config_hash": config_hash, "passed": bool(passed),
        "reasons": reasons, "holdout": holdout,
        # EV shown for a promoted rule is the CI lower bound, never the mean
        "expected_net_r": (holdout["ci_lo"] if passed else 0.0)}
    with open(_path(data_dir), "w") as f:
        json.dump(cur, f, indent=2, default=float)
    return cur


def promotion_for(data_dir, rule_id, config_hash):
    rec = load(data_dir).get(f"{rule_id}|{config_hash}")
    return rec if rec and rec.get("passed") else None
