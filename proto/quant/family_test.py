"""WS-B: family-level multiple testing.

A research program that tried many things must pay for it.  This module
corrects a **DECLARED FAMILY** of hypotheses -- the list of things the
program said up front it was going to test -- and nothing else:

  * ``method="holm"``       Holm-Bonferroni step-down: the adjusted
                            p-values are monotone in the raw p-values and
                            never smaller than the raw p-value.
  * ``method="bootstrap"``  a max-t (reality-check style) family
                            correction: whole DAYS are resampled jointly
                            across the family and the largest studentized
                            statistic of the family is compared with its
                            own null distribution.  This reuses
                            ``stats.spa_max_t`` -- the same machinery the
                            288-configuration grid evaluation uses.

Guarantees (the family is declared BEFORE evaluation, so these hold for
any input):

  * ``p_adj >= p`` for every member;
  * for a FIXED member, its adjusted p-value never decreases when the
    family grows (Holm);
  * a family of size one returns that member's own p-value;
  * the bootstrap family p-value does not decrease when the family grows
    by members that do not displace the current best (adding a better
    member legitimately lowers it -- that is the point of the test);
  * an empty family is REFUSED with a reason, and so is a family with an
    invalid p-value.

Stdlib only.  This module corrects p-values; it never promotes a rule
and never touches the holdout.
"""
import math

from . import stats as S

FAMILY_TEST_VERSION = "family-v1"

#: Bootstrap resamples for the max-t family correction.
DEFAULT_BOOTSTRAP = 1000

#: Default seed (the correction must be reproducible).
DEFAULT_SEED = 7


def _refuse(reason, method, alpha, family_size, members=None):
    return {
        "version": FAMILY_TEST_VERSION,
        "ok": False,
        "method": method,
        "alpha": alpha,
        "family_size": family_size,
        "n_rejected": 0,
        "family_p": None,
        "members": members or [],
        "reason": reason,
    }


def correct(hypotheses, *, method="holm", alpha=0.05, B=DEFAULT_BOOTSTRAP,
            seed=DEFAULT_SEED) -> dict:
    """Correct a declared family of hypotheses for multiplicity.

    ``hypotheses``  a list of dicts with at least ``id`` and ``p``; ``n``
                    and ``metric`` are carried through for the report.
                    The bootstrap method additionally needs ``values`` and
                    ``days`` per member (the per-event numbers and their
                    day labels).
    ``method``      "holm" or "bootstrap".
    ``alpha``       the rejection level.

    Returns a dict with the family size, the per-member adjusted p-values
    and rejection flags, the family-level p-value and a reason when the
    family was refused.  Members are returned sorted by their raw p-value
    (so the adjusted p-values are visibly monotone in the raw ones).
    """
    raw = list(hypotheses or [])
    if not raw:
        return _refuse("empty-family: a declared family must contain at "
                       "least one hypothesis", method, alpha, 0)
    if method not in ("holm", "bootstrap"):
        return _refuse(f"unknown-method: {method!r} (use 'holm' or "
                       "'bootstrap')", method, alpha, len(raw))
    for h in raw:
        p = h.get("p")
        if (p is None or isinstance(p, bool) or not isinstance(p, (int, float))
                or not math.isfinite(p) or p < 0.0 or p > 1.0):
            return _refuse(f"invalid-p: {h.get('id')!r} has p={p!r} "
                           "(a p-value must be a number in [0, 1])",
                           method, alpha, len(raw))

    members = sorted(raw, key=lambda h: (h["p"], str(h.get("id", ""))))
    m = len(members)

    if method == "holm":
        adjusted = _holm(members)
        family_p = adjusted[0]
        n_bootstrap = 0
        best_id = members[0].get("id")
    else:
        if m == 1:
            # A family of one IS that member: nothing was multiplied.
            adjusted = [members[0]["p"]]
            family_p = members[0]["p"]
            n_bootstrap = 0
            best_id = members[0].get("id")
        else:
            values = [h.get("values") for h in members]
            days = [h.get("days") for h in members]
            if any(v is None or d is None for v, d in zip(values, days)):
                return _refuse("bootstrap requires per-member 'values' and "
                               "'days' (the per-event numbers and their day "
                               "labels)", method, alpha, m)
            best, _best_t, family_p = S.spa_max_t(values, days, B=B, seed=seed)
            best_id = members[best].get("id")
            n_bootstrap = B
            # Every member is credited with at most its own p-value and at
            # least the family-level correction: no member is ever credited
            # with more evidence than the family correction allows.
            adjusted = [max(h["p"], family_p) for h in members]

    out_members = []
    n_rejected = 0
    for h, adj in zip(members, adjusted):
        rejected = bool(adj <= alpha)
        n_rejected += 1 if rejected else 0
        out_members.append({
            "id": h.get("id"),
            "p": h["p"],
            "p_adj": adj,
            "rejected": rejected,
            "n": h.get("n"),
            "metric": h.get("metric"),
        })
    return {
        "version": FAMILY_TEST_VERSION,
        "ok": True,
        "method": method,
        "alpha": alpha,
        "family_size": m,
        "n_rejected": n_rejected,
        "family_p": family_p,
        "best_id": best_id,
        "n_bootstrap": n_bootstrap,
        "members": out_members,
        "reason": None,
    }


def _holm(members):
    """Holm-Bonferroni step-down adjusted p-values.

    With the raw p-values sorted ascending, the adjusted value of the
    i-th (1-based) order statistic is

        p_adj(i) = max_{j <= i} min(1, (m - j + 1) * p(j))

    (the running maximum makes the adjusted values monotone, and every
    adjusted value is at least its own raw p-value).
    """
    m = len(members)
    out = []
    running = 0.0
    for rank, h in enumerate(members):
        val = min(1.0, (m - rank) * h["p"])
        running = max(running, val)
        out.append(running)
    return out
