"""Registered rules.  A rule is (candidate family, side policy, outcome
geometry) with a stable id.  Registration does NOT mean promotion: every rule
starts in status 'research' and is only ever shown as actionable after
proto.quant.gates records a passing decision for that exact id + config hash.

The 'cont' (continuation) variants are CONTROLS: they are logged and measured
beside their 'fade' twins so a result has to beat its own mirror image.
"""
from dataclasses import dataclass

from .config import Geometry, RuleParams, RULE_VERSION


@dataclass(frozen=True)
class RuleSpec:
    params: RuleParams
    geometry: Geometry
    role: str = "candidate"            # "candidate" | "control"

    @property
    def rule_id(self):
        p = self.params
        arg = (f"rvol{p.rvol_min:g}" if p.family == "breakout_start"
               else f"h{p.cusum_h:g}")
        return f"{RULE_VERSION}:{p.family}:{arg}:{p.policy}:{self.geometry.key()}"


def _pair(family, geometry, **kw):
    return (RuleSpec(RuleParams(family, "fade", **kw), geometry, "candidate"),
            RuleSpec(RuleParams(family, "cont", **kw), geometry, "control"))


# Geometry here only defines how a shadow event's outcome is tracked and what
# plan levels are displayed; the research harness evaluates the whole grid.
REGISTERED = (
    _pair("breakout_start", Geometry(288, 1.5, 1.0), rvol_min=4.0)
    + _pair("residual_shock", Geometry(144, 3.0, 1.0), cusum_h=8.0)
)


def by_id():
    return {r.rule_id: r for r in REGISTERED}
