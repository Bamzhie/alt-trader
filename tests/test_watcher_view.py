"""Shared watcher view-model (Task 1).

Self-checking script: `python3 tests/test_watcher_view.py`.
Covers id-stable selection under reorder, grouping per state, unknown
selection fallback (first active / None + silence-valid notice when empty),
all-unavailable stale notice with asof_ts, paused notice, research banner
always first, verbatim STATE_WORDS, and detail-dict shape.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.watcher_view import (
    RESEARCH_BANNER,
    STATE_WORDS,
    WatcherPanel,
    build_panel,
)

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def mkrow(rule, coin, ts, state="active", **kw):
    row = {
        "id": f"{rule}:{coin}:{ts}",
        "rule_id": rule,
        "coin": coin,
        "direction": "LONG",
        "src_bar_ts": ts,
        "entry": 100.0,
        "stop_px": 99.0,
        "tgt_px": 102.0,
        "state": state,
        "monitoring": "ok",
    }
    row.update(kw)
    return row


row_a = mkrow("r", "AAA", 1)
row_b = mkrow("r", "BBB", 2)


def detail(panel):
    return panel.by_id[panel.selected_id]


def test_selection_survives_reorder():
    p1 = build_panel([row_b, row_a], {}, selected_id="r:AAA:1")
    p2 = build_panel([row_a, row_b], {}, selected_id="r:AAA:1")
    assert p1.selected_id == p2.selected_id == "r:AAA:1"
    assert detail(p1) == detail(p2)


check("constants: research banner + verbatim state words",
      RESEARCH_BANNER == "WATCHER (research, shadow-only — scanner untouched; no orders)"
      and STATE_WORDS == {"active": "active", "invalidated": "invalidated",
                          "expired": "expired", "resolved": "resolved"},
      f"{RESEARCH_BANNER!r} {STATE_WORDS!r}")

try:
    test_selection_survives_reorder()
    check("selection survives reorder (id-stable, detail equal)", True)
except AssertionError as e:
    check("selection survives reorder (id-stable, detail equal)", False, str(e))

rows = [mkrow("r", "A", 1, "active"),
        mkrow("r", "B", 2, "invalidated"),
        mkrow("r", "C", 3, "expired"),
        mkrow("r", "D", 4, "resolved"),
        mkrow("r", "E", 5, "active", monitoring="unavailable")]
p = build_panel(rows, {})
check("grouping counts per state",
      p.groups["active"] == ["r:A:1"]
      and p.groups["invalidated"] == ["r:B:2"]
      and p.groups["expired"] == ["r:C:3"]
      and p.groups["resolved"] == ["r:D:4"]
      and p.groups["unavailable"] == ["r:E:5"],
      str(p.groups))

pu = build_panel(rows, {}, selected_id="nope")
check("unknown selected_id falls back to first active",
      pu.selected_id == "r:A:1", repr(pu.selected_id))

pe = build_panel([], {})
check("empty panel: no selection + silence-valid notice",
      pe.selected_id is None
      and any("no active setups" in n and "silence is valid" in n for n in pe.notices),
      f"{pe.selected_id!r} {pe.notices!r}")

urows = [mkrow("r", "A", 10, monitoring="unavailable"),
         mkrow("r", "B", 20, monitoring="unavailable")]
ua = build_panel(urows, {"r:A:10": {"bar_ts": 30}, "r:B:20": {"bar_ts": 40}})
check("all-unavailable adds stale notice with asof_ts",
      ua.groups["active"] == [] and ua.groups["unavailable"] == ["r:A:10", "r:B:20"]
      and any("unavailable" in n and str(ua.asof_ts) in n for n in ua.notices),
      f"{ua.groups!r} {ua.notices!r} asof={ua.asof_ts!r}")
check("last_bar_ts tracks latest eval bar",
      ua.last_bar_ts == 40, repr(ua.last_bar_ts))

pp = build_panel([row_a], {}, paused=True)
check("paused adds paused notice",
      pp.selected_id == "r:AAA:1"
      and any("paused" in n.lower() for n in pp.notices),
      f"{pp.selected_id!r} {pp.notices!r}")

for panel, name in ((p, "grouped"), (pe, "empty"), (ua, "unavailable"), (pp, "paused")):
    check(f"research banner first notice ({name})",
          panel.notices and panel.notices[0] == RESEARCH_BANNER,
          repr(panel.notices[:1]))

d = build_panel([mkrow("r", "AAA", 1, invalid_price=98.5, ev_r=0.4, p_lb=0.55,
                       cost_total=0.002, shadow_selected=1)], {}).by_id["r:AAA:1"]
check("detail dict shape + verbatim state",
      d["rule"] == "r" and d["coin"] == "AAA" and d["direction"] == "LONG"
      and d["src_bar_ts"] == 1 and d["entry"] == 100.0
      and d["stop_px"] == 99.0 and d["tgt_px"] == 102.0
      and d["invalid_price"] == 98.5 and d["state"] == "active"
      and d["monitoring"] == "ok" and isinstance(d["missing"], list)
      and isinstance(d["cost"], dict) and d["ev_r"] == 0.4
      and d["p_lb"] == 0.55 and d["shadow_selected"] is True,
      str(d))
check("detail state is STATE_WORDS verbatim",
      all(build_panel([mkrow("r", "C", 1, state=s)], {}).by_id["r:C:1"]["state"] == w
          for s, w in (("active", "active"), ("invalidated", "invalidated"),
                       ("expired", "expired"), ("resolved", "resolved"))))

pd = build_panel([row_a], {}, disabled=("r",))
check("disabled rule surfaces in notices",
      any("r" in n and "disabled" in n.lower() for n in pd.notices),
      repr(pd.notices))

check("panel is a WatcherPanel dataclass with required fields",
      isinstance(p, WatcherPanel)
      and isinstance(p.groups, dict) and isinstance(p.by_id, dict)
      and isinstance(p.notices, list) and isinstance(p.asof_ts, int)
      and (p.last_bar_ts is None or isinstance(p.last_bar_ts, int)),
      repr(type(p)))

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
