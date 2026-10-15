"""WS7 change control: candidate -> validated promotion gate.

Pure evaluation, no writes: given the measurement cadence stats
(``proto.measurement.cadence_stats`` shape) plus the cohort identity of
a candidate config, decide promote vs hold with explicit reasons. The
decision is a value, not a side effect — callers append it to their own
decision log (the log itself is a later WS7 increment).

Honesty rules (same as the rest of the system):
- Every check cites the signal it used; a missing signal is a HOLD
  reason, never a silent pass.
- Outcome-based comparison (competing outcomes, calibrated
  probabilities — proposal §3–§5) is NOT implemented here: WS5 builds
  those inputs. Until then the gate promotes on CADENCE evidence only,
  and says so.
- No confidence/probability language: decisions are promote/hold with
  reasons, never "likely" or "probably".
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Gate thresholds (proposal §5.7 placeholders, deliberately explicit).
MIN_COINS = 20          # cohort breadth: fewer coins -> not representative
MIN_ATTEMPTS = 200      # sample size for cadence evidence


@dataclass(frozen=True)
class GateResult:
    """One promotion decision. ``checks`` is the auditable evidence."""

    decision: str                      # "promote" | "hold"
    reasons: List[str] = field(default_factory=list)
    checks: Dict[str, Any] = field(default_factory=dict)
    candidate: Optional[str] = None    # config_hash or code rev

    def as_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "reasons": list(self.reasons),
            "checks": dict(self.checks),
            "candidate": self.candidate,
        }


def evaluate_promotion(stats: Optional[Dict[str, Any]],
                       candidate: Optional[str] = None,
                       *, min_coins: int = MIN_COINS,
                       min_attempts: int = MIN_ATTEMPTS) -> GateResult:
    """Evaluate one candidate config for promotion to validated.

    ``stats`` is a ``cadence_stats``-shaped dict (calibration, coins,
    attempts, failures, degraded, gaps). Returns a GateResult; promote
    requires every cadence check to pass. Outcome evidence is out of
    scope until WS5 lands — the result says so explicitly.
    """
    reasons: List[str] = []
    checks: Dict[str, Any] = {}

    if not isinstance(stats, dict):
        return GateResult(
            decision="hold",
            reasons=["no cadence stats available"],
            checks={"stats_available": False},
            candidate=candidate)

    checks["stats_available"] = True

    # 1. Calibration (the only quality signal that exists today).
    cal = stats.get("calibration") or {}
    cal_pass = bool(cal.get("pass"))
    checks["calibration_pass"] = cal_pass
    checks["calibration_detail"] = {
        "p95_ok": bool(cal.get("p95_ok")),
        "p99_ok": bool(cal.get("p99_ok")),
        "p95_limit_s": cal.get("p95_limit_s"),
        "p99_limit_s": cal.get("p99_limit_s"),
    }
    if not cal_pass:
        reasons.append(
            "cadence calibration fails (p95>10min or p99>15min or "
            "unestablished)")

    # 2. Sample size.
    coins = int(stats.get("coins") or 0)
    attempts = int(stats.get("attempts") or 0)
    checks["coins"] = coins
    checks["attempts"] = attempts
    checks["min_coins"] = min_coins
    checks["min_attempts"] = min_attempts
    if coins < min_coins:
        reasons.append(
            f"cohort breadth {coins} coins < required {min_coins}")
    if attempts < min_attempts:
        reasons.append(
            f"sample size {attempts} attempts < required {min_attempts}")

    # 3. Failure honesty: a cohort whose attempts are mostly
    # failures/degraded has no cadence evidence to promote on.
    failures = int(stats.get("failures") or 0)
    degraded = int(stats.get("degraded") or 0)
    checks["failures"] = failures
    checks["degraded"] = degraded
    if attempts and (failures + degraded) > attempts // 2:
        reasons.append(
            f"more than half of attempts failed/degraded "
            f"({failures}+{degraded} of {attempts})")

    # 4. Scope honesty: outcome-based promotion awaits WS5.
    checks["outcome_evidence"] = "not evaluated (WS5 inputs unbuilt)"

    decision = "promote" if not reasons else "hold"
    return GateResult(decision=decision, reasons=reasons, checks=checks,
                      candidate=candidate)
