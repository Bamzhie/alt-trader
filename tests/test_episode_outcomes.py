"""Episode plan resolver tests: pure helpers over synthetic 5m bars.

Contract (plan Task 4 / design spec sections 8, 9 and 14):
  A closed 5m slot is complete only when exactly one valid bar exists for it.
  Only fully closed bars are eligible: an open bar (open_ts >= T0 + 3600, or
  open_ts + 300 > T0 + 3600) never decides anything.
  Entry: open_ts > T0 and bar close <= T0 + 3600.
    LONG fills at entry_high when the bar LOW crosses it.
    SHORT fills at entry_low when the bar HIGH crosses it.
    A gap through the whole band still fills at the ADVERSE edge.
    The fill timestamp is the fill bar's CLOSE.
  Fill bar: a stop touch is terminal STOPPED and target touches are ignored.
  Later bars: stop-first; TP1 alone is never terminal; TP2 is terminal;
  expiry is the bar closing at fill_ts + 288*300 (EXPIRED at its close).
  Missing slots stay recoverable for six days, then UNAVAILABLE.
    Never zero-filled, never declared UNFILLED on incomplete bars.
  Fixed horizons 1h/4h/24h/7d use the MODELED FILL price and complete bars,
  and are independent of stops and targets.

Offline: pure functions over dict bars. No network, no database.

Run: python3 tests/test_episode_outcomes.py
"""

import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import outcomes as outmod
from proto.store import Store
from proto.measurement import (
    DEFAULT_ENTRY_VALIDITY_MINUTES,
    DEFAULT_HOLD_HORIZON_BARS,
    OUTCOME_RULE_VERSION,
)

FAILURES = []

BAR_S = 300
RECOVERABLE_S = 6 * 24 * 3600


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def bar(open_ts, o, h, l, c):
    """One synthetic 5m bar. close_ts is derived: open_ts + 300."""
    return {"open_ts": open_ts, "o": o, "h": h, "l": l, "c": c,
            "vol": 1.0, "amount": 1.0}


def series(open_ts, prices):
    """A run of bars whose high/low/close all sit at `prices[i]`.

    Good enough for gaps and for "price traded here" tests: the low of a bar
    equals the close, so `low <= entry_high` is exactly "price came down to
    entry_high or below".
    """
    return [bar(open_ts + BAR_S * i, p, p, p, p) for i, p in enumerate(prices)]


def check_close(name, bars, expect_ts):
    """close_ts of a bar is always open_ts + 300."""
    got = outmod.bar_close_ts(bars[0])
    check(name, got == expect_ts, f"got {got!r}, want {expect_ts!r}")


def check_valid(name, b, now, expect):
    """A bar is usable only when it closed before `now`."""
    got = outmod.is_valid_bar(b, now=now)
    check(name, got is expect, f"got {got!r}, want {expect!r}")


def check_recoverable(name, slot_ts, now, expect):
    got = outmod.slot_recoverable(slot_ts, now=now)
    check(name, got is expect, f"got {got!r}, want {expect!r}")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_bar_completeness_helpers():
    print("--- bar completeness and validity helpers")

    check_close("bar_close_ts is open_ts + 300", [bar(1000, 1, 1, 1, 1)], 1300)

    b = bar(1000, 1, 1, 1, 1)
    check_valid("a bar closing before now is closed", b, now=1300, expect=True)
    check_valid("a bar closing exactly at now is closed", b, now=1300,
                expect=True)
    check_valid("a bar still open is NOT closed", b, now=1250, expect=False)

    # An "open" bar the venue may still revise is identified by its close
    # lying in the future relative to now.
    b2 = bar(1000, 1, 1, 1, 1)
    check_valid("a future bar is not closed", b2, now=500, expect=False)

    check_recoverable("a just-missing slot is recoverable",
                      1000, now=1000 + RECOVERABLE_S - 1, expect=True)
    check_recoverable("a slot missing exactly six days is still recoverable",
                      1000, now=1000 + RECOVERABLE_S, expect=True)
    check_recoverable("a slot missing beyond six days is NOT recoverable",
                      1000, now=1000 + RECOVERABLE_S + 1, expect=False)


def test_entry_window_slots():
    print("--- entry window: eligibility and completeness")

    T0 = 1_700_000_100
    slots = outmod.entry_slots(T0, validity_s=DEFAULT_ENTRY_VALIDITY_MINUTES * 60)
    # open_ts > T0 and open_ts + 300 <= T0 + 3600: a 60-minute window holds
    # exactly eleven 5-minute bars.
    check("entry window has 11 five-minute slots",
          len(slots) == 11, str(len(slots)))
    check("first eligible slot is strictly after T0", slots[0] == T0 + 300,
          str(slots[:2]))
    check("last eligible slot closes exactly at T0+3600",
          slots[-1] + 300 == T0 + 3600, str(slots[-1:]))

    # A slot is complete only with exactly one valid bar.
    ts = slots[0]
    complete = [bar(ts, 1, 1, 1, 1)]
    check("one bar for a slot is complete",
          outmod.complete_slots(complete, slots[:1]) == {ts},
          str(outmod.complete_slots(complete, slots[:1])))
    # Two bars for one slot: ambiguous, NOT complete.
    dup = [bar(ts, 1, 1, 1, 1), bar(ts, 2, 2, 2, 2)]
    check("two bars for one slot is NOT complete (ambiguous)",
          outmod.complete_slots(dup, slots[:1]) == set(),
          str(outmod.complete_slots(dup, slots[:1])))
    check("a slot with no bar is not complete",
          outmod.complete_slots([], slots[:1]) == set())
    # Bars outside the window are ignored.
    check("bars outside the window do not complete a slot",
          outmod.complete_slots([bar(T0, 1, 1, 1, 1)], slots[:1]) == set())


def test_fill_adverse_edge():
    print("--- fill: adverse edge of the entry band")

    T0 = 1_700_000_100
    slots = outmod.entry_slots(T0)
    entry_high, entry_low = 100.1, 99.9
    plan = {"direction": "LONG", "entry_low": entry_low,
            "entry_high": entry_high, "stop": 95.0, "tp1": 105.0,
            "tp2": 110.0}

    # Bar 1 trades down to exactly the adverse edge: fills at entry_high.
    bars = [bar(slots[0], 100.2, 100.2, entry_high, 100.15)]
    r = outmod.resolve_entry(bars, T0, plan, now=slots[0] + 300)
    check("LONG touching the adverse edge fills", r["status"] == "FILLED",
          str(r))
    check("LONG fill price is the adverse edge (entry_high)",
          r["fill_price"] == entry_high, str(r))
    check("LONG fill ts is the fill bar's CLOSE",
          r["fill_ts"] == slots[0] + 300, str(r))
    check("LONG fill bar open_ts is the fill bar", r["fill_bar_open_ts"] ==
          slots[0], str(r))

    # Price never reaches the band: no fill in this bar.
    bars = [bar(slots[0], 100.2, 100.2, 100.15, 100.2)]
    r = outmod.resolve_entry(bars, T0, plan, now=slots[0] + 300)
    check("no touch means no fill", r["status"] != "FILLED", str(r))

    # Gap straight through the whole band: still fills at the adverse edge.
    bars = [bar(slots[0], 100.2, 100.2, 90.0, 95.0)]
    r = outmod.resolve_entry(bars, T0, plan, now=slots[0] + 300)
    check("a gap through the band still FILLS", r["status"] == "FILLED",
          str(r))
    check("a gap-through fill is priced at the adverse edge",
          r["fill_price"] == entry_high, str(r))

    # SHORT mirrors: fills when the HIGH reaches entry_low.
    splan = {"direction": "SHORT", "entry_low": entry_low,
             "entry_high": entry_high, "stop": 105.0, "tp1": 95.0,
             "tp2": 90.0}
    bars = [bar(slots[0], 99.8, entry_low, 99.8, 99.85)]
    r = outmod.resolve_entry(bars, T0, splan, now=slots[0] + 300)
    check("SHORT touching the adverse edge fills", r["status"] == "FILLED",
          str(r))
    check("SHORT fill price is entry_low", r["fill_price"] == entry_low,
          str(r))
    bars = [bar(slots[0], 99.8, 110.0, 99.0, 99.8)]
    r = outmod.resolve_entry(bars, T0, splan, now=slots[0] + 300)
    check("a SHORT gap through the band fills at entry_low",
          r["status"] == "FILLED" and r["fill_price"] == entry_low, str(r))
    bars = [bar(slots[0], 99.8, 99.85, 99.8, 99.8)]
    r = outmod.resolve_entry(bars, T0, splan, now=slots[0] + 300)
    check("SHORT does not fill without touching entry_low",
          r["status"] != "FILLED", str(r))


def test_entry_open_and_stale_bars_are_ignored():
    print("--- open bars and stale bars never fill an entry")

    T0 = 1_700_000_100
    slots = outmod.entry_slots(T0)
    plan = {"direction": "LONG", "entry_low": 99.9, "entry_high": 100.1,
            "stop": 95.0, "tp1": 105.0, "tp2": 110.0}

    # A bar whose close is at/after T0+3600 is not fully closed: ineligible.
    late = bar(T0 + 3300, 100.0, 100.0, 90.0, 95.0)  # closes at T0+3600 -> ok
    check("the slot closing exactly at T0+3600 is eligible",
          outmod.entry_slot_ok(late["open_ts"], T0), str(late["open_ts"]))
    toolate = bar(T0 + 3600, 100.0, 100.0, 90.0, 95.0)  # closes at T0+3900
    check("a slot closing after T0+3600 is NOT eligible",
          not outmod.entry_slot_ok(toolate["open_ts"], T0),
          str(toolate["open_ts"]))

    bars = [bar(T0, 100.0, 100.0, 90.0, 95.0)]
    r = outmod.resolve_entry(bars, T0, plan, now=T0 + 300)
    check("a bar at T0 (open_ts > T0 fails) is not an entry bar",
          r["status"] != "FILLED", str(r))

    bars = [toolate]
    r = outmod.resolve_entry(bars, T0, plan, now=T0 + 3900)
    check("a bar past the validity window is not an entry bar",
          r["status"] != "FILLED", str(r))

    # An unclosed (still open) bar is not usable even inside the window.
    bars = [bar(slots[0], 100.0, 100.0, 90.0, 95.0)]
    r = outmod.resolve_entry(bars, T0, plan, now=slots[0] + 299)
    check("an open bar cannot fill an entry", r["status"] != "FILLED", str(r))


def test_entry_status_pending_and_unfilled():
    print("--- entry status: pending, unfilled, unavailable")

    T0 = 1_700_000_100
    slots = outmod.entry_slots(T0)
    plan = {"direction": "LONG", "entry_low": 99.9, "entry_high": 100.1,
            "stop": 95.0, "tp1": 105.0, "tp2": 110.0}

    # Full window, price never dips to the band: UNFILLED.
    bars = series(slots[0], [100.5] * 11)
    r = outmod.resolve_entry(bars, T0, plan, now=slots[-1] + 300)
    check("a complete window with no touch is UNFILLED",
          r["status"] == "UNFILLED", str(r))

    # Window not yet elapsed at all: PENDING_ENTRY.
    r = outmod.resolve_entry(bars[:1], T0, plan, now=slots[0] + 300)
    check("a window that has not elapsed is PENDING_ENTRY",
          r["status"] == "PENDING_ENTRY", str(r))

    # A missing slot BEFORE any fill: pending while recoverable.
    partial = [bar(slots[i], 100.5, 100.5, 100.5, 100.5)
               for i in range(11) if i != 3]
    r = outmod.resolve_entry(partial, T0, plan,
                             now=slots[-1] + 300)
    check("a missing slot before any fill is PENDING_ENTRY",
          r["status"] == "PENDING_ENTRY", str(r))
    check("pending entry names the missing slot",
          slots[3] in r["missing_slots"], str(r))

    # Same gap, but now no longer recoverable: UNAVAILABLE, never UNFILLED.
    r = outmod.resolve_entry(partial, T0, plan,
                             now=slots[3] + RECOVERABLE_S + 1)
    check("an unrecoverable missing slot is UNAVAILABLE",
          r["status"] == "UNAVAILABLE", str(r))
    check("an incomplete window is never UNFILLED",
          r["status"] != "UNFILLED", str(r))

    # A fill found while a LATER slot is missing: still FILLED.
    with_fill = [bar(slots[0], 100.0, 100.0, 90.0, 95.0)]
    with_fill += [bar(slots[i], 100.5, 100.5, 100.5, 100.5)
                  for i in range(1, 11) if i != 6]
    r = outmod.resolve_entry(with_fill, T0, plan, now=slots[-1] + 300)
    check("a fill before a later gap is FILLED", r["status"] == "FILLED",
          str(r))


def test_trade_resolution_rules():
    print("--- trade resolution: fill bar, stop-first, TP1, TP2, expiry")

    T0 = 1_700_000_100
    entry_high, entry_low = 100.1, 99.9
    plan = {"direction": "LONG", "entry_low": entry_low,
            "entry_high": entry_high, "stop": 95.0, "tp1": 105.0,
            "tp2": 110.0}
    FILL_TS = T0 + 600            # bar closing at T0+600 fills the LONG
    FILL_OPEN = FILL_TS - 300

    def trade(bars, now=None):
        now = now if now is not None else bars[-1]["open_ts"] + 300
        return outmod.resolve_trade(bars, plan, fill_bar_open_ts=FILL_OPEN,
                                    fill_price=entry_high, now=now)

    # Fill bar also hits the stop: terminal STOPPED, target touches ignored.
    bars = [bar(FILL_OPEN, 100.0, 112.0, 94.0, 96.0)]
    r = trade(bars)
    check("a stop touch on the fill bar is STOPPED",
          r["status"] == "STOPPED", str(r))
    check("fill-bar target touches are ignored (no TP1)",
          r["tp1"] is None and r["tp2"] is None, str(r))
    check("fill-bar stop records the stop touch index",
          r["stop"] == 0, str(r))

    # Later bar touches BOTH stop and TP2: stop-first wins.
    bars = [bar(FILL_OPEN, 100.0, 100.0, 99.9, 100.0),
            bar(FILL_OPEN + 300, 100.0, 112.0, 94.0, 95.0)]
    r = trade(bars)
    check("same-bar stop and target resolves stop-first",
          r["status"] == "STOPPED", str(r))
    check("stop-first records no targets on that bar",
          r["tp1"] is None and r["tp2"] is None, str(r))
    check("stop index is the later bar", r["stop"] == 1, str(r))

    # TP1 before a later stop: recorded, and tp1_before_stop is set.
    bars = [bar(FILL_OPEN, 100.0, 100.0, 99.9, 100.0),
            bar(FILL_OPEN + 300, 100.0, 106.0, 99.0, 105.0),
            bar(FILL_OPEN + 600, 100.0, 100.0, 94.0, 95.0)]
    r = trade(bars)
    check("TP1 before a later stop stays recorded",
          r["tp1"] == 1 and r["tp2"] is None, str(r))
    check("tp1_before_stop is 1", r["tp1_before_stop"] == 1, str(r))
    check("TP1 alone is not terminal", r["status"] == "STOPPED", str(r))

    # TP1 alone, no stop, run ends: still OPEN, TP1 is progress only.
    bars = [bar(FILL_OPEN, 100.0, 100.0, 99.9, 100.0),
            bar(FILL_OPEN + 300, 100.0, 106.0, 99.0, 105.0)]
    r = trade(bars)
    check("TP1 alone leaves the trade OPEN", r["status"] == "OPEN", str(r))

    # TP2 is terminal.
    bars = [bar(FILL_OPEN, 100.0, 100.0, 99.9, 100.0),
            bar(FILL_OPEN + 300, 100.0, 106.0, 99.0, 105.0),
            bar(FILL_OPEN + 600, 100.0, 111.0, 100.0, 110.0)]
    r = trade(bars)
    check("TP2 is terminal", r["status"] == "TP2", str(r))
    check("TP2 index recorded", r["tp2"] == 2, str(r))

    # Expiry: 288 bars after the fill bar (indices 0..288), no stop or TP2.
    n = DEFAULT_HOLD_HORIZON_BARS
    bars = [bar(FILL_OPEN, 100.0, 100.0, 99.9, 100.0)]
    bars += [bar(FILL_OPEN + 300 * (i + 1), 100.0, 101.0, 99.0, 100.5)
             for i in range(n)]
    r = trade(bars)
    check("no stop or TP2 through the hold horizon is EXPIRED",
          r["status"] == "EXPIRED", str(r))
    check("expiry exits at the bar closing at fill_ts + 288 bars",
          r["exit_ts"] == FILL_TS + 288 * 300, str(r))
    check("expiry exit price is that bar's close", r["exit_price"] == 100.5,
          str(r))

    # Before the horizon completes the trade stays OPEN.
    r = trade(bars[:10])
    check("an unfinished horizon leaves the trade OPEN",
          r["status"] == "OPEN", str(r))

    # SHORT direction mirrors.
    splan = {"direction": "SHORT", "entry_low": entry_low,
             "entry_high": entry_high, "stop": 105.0, "tp1": 95.0,
             "tp2": 90.0}
    sbars = [bar(FILL_OPEN, 100.0, 100.0, 99.9, 99.9),
             bar(FILL_OPEN + 300, 100.0, 106.0, 99.0, 105.0)]
    r = outmod.resolve_trade(sbars, splan, fill_bar_open_ts=FILL_OPEN,
                             fill_price=entry_low, now=sbars[-1]["open_ts"] + 300)
    check("SHORT stop is above and counts", r["status"] == "STOPPED", str(r))
    sbars2 = [bar(FILL_OPEN, 100.0, 100.0, 99.9, 99.9),
              bar(FILL_OPEN + 300, 100.0, 100.0, 89.0, 90.0)]
    r = outmod.resolve_trade(sbars2, splan, fill_bar_open_ts=FILL_OPEN,
                             fill_price=entry_low,
                             now=sbars2[-1]["open_ts"] + 300)
    check("SHORT TP2 is terminal", r["status"] == "TP2", str(r))


def test_trade_gap_recovery():
    print("--- trade gaps: recoverable then unavailable, never zero-filled")

    T0 = 1_700_000_100
    plan = {"direction": "LONG", "entry_low": 99.9, "entry_high": 100.1,
            "stop": 95.0, "tp1": 105.0, "tp2": 110.0}
    FILL_OPEN = T0 + 300

    # A gap in the middle of the span the result depends on.
    bars = [bar(FILL_OPEN, 100.0, 100.0, 99.9, 100.0),
            bar(FILL_OPEN + 600, 100.0, 101.0, 99.0, 100.5)]  # slot 1 missing

    r = outmod.resolve_trade(bars, plan, fill_bar_open_ts=FILL_OPEN,
                             fill_price=100.1, now=FILL_OPEN + 900)
    check("a gap while the trade runs leaves it OPEN",
          r["status"] == "OPEN", str(r))

    r = outmod.resolve_trade(bars, plan, fill_bar_open_ts=FILL_OPEN,
                             fill_price=100.1,
                             now=FILL_OPEN + 300 + RECOVERABLE_S + 1)
    check("an unrecoverable gap makes the trade UNAVAILABLE",
          r["status"] == "UNAVAILABLE", str(r))

    # A gap AFTER a terminal result never rewrites that result.
    full = [bar(FILL_OPEN, 100.0, 100.0, 99.9, 100.0),
            bar(FILL_OPEN + 300, 100.0, 100.0, 94.0, 95.0),
            bar(FILL_OPEN + 900, 100.0, 101.0, 99.0, 100.5)]
    r = outmod.resolve_trade(full, plan, fill_bar_open_ts=FILL_OPEN,
                             fill_price=100.1, now=FILL_OPEN + 1200)
    check("a terminal result is not disturbed by a later gap",
          r["status"] == "STOPPED" and r["stop"] == 1, str(r))


def test_fixed_horizons():
    print("--- fixed horizons from the modeled fill")

    T0 = 1_700_000_100
    plan = {"direction": "LONG", "entry_low": 99.9, "entry_high": 100.1,
            "stop": 95.0, "tp1": 105.0, "tp2": 110.0}
    FILL_TS = T0 + 600
    FILL_OPEN = FILL_TS - 300
    FILL_PRICE = 100.1

    # 500 quiet bars after the fill bar, trending +0.5/bar.
    bars = [bar(FILL_OPEN, 100.0, 100.0, 99.9, 100.0)]
    for i in range(1, 500):
        c = 100.0 + 0.5 * i
        bars.append(bar(FILL_OPEN + 300 * i, c - 0.25, c + 0.25, c - 0.5, c))

    r = outmod.resolve_horizons(bars, plan, fill_bar_open_ts=FILL_OPEN,
                                fill_price=FILL_PRICE, now=bars[-1]["open_ts"] + 300)
    h = {row["horizon"]: row for row in r}
    check("1h/4h/24h matured; 7d still pending",
          h["1h"]["status"] == "MATURED" and h["4h"]["status"] == "MATURED"
          and h["24h"]["status"] == "MATURED" and h["7d"]["status"] == "PENDING",
          str({k: v["status"] for k, v in h.items()}))

    # 1h = 12 bars from the fill bar; return measured from the modeled fill.
    want = (bars[11]["c"] - FILL_PRICE) / FILL_PRICE * 100.0
    check("1h return is from the modeled fill to the closing bar's close",
          abs(h["1h"]["return_pct"] - want) < 1e-9, str(h["1h"]["return_pct"]))
    # MFE/MAE come from intrabar highs/lows over the same span.
    highs = [b["h"] for b in bars[:12]]
    lows = [b["l"] for b in bars[:12]]
    want_mfe = (max(highs) - FILL_PRICE) / FILL_PRICE * 100.0
    want_mae = (min(lows) - FILL_PRICE) / FILL_PRICE * 100.0
    check("1h MFE is the best intrabar high from the fill",
          abs(h["1h"]["mfe_pct"] - want_mfe) < 1e-9, str(h["1h"]["mfe_pct"]))
    check("1h MAE is the worst intrabar low from the fill",
          abs(h["1h"]["mae_pct"] - want_mae) < 1e-9, str(h["1h"]["mae_pct"]))

    # SHORT sign flips.
    splan = dict(plan, direction="SHORT")
    rs = outmod.resolve_horizons(bars, splan, fill_bar_open_ts=FILL_OPEN,
                                 fill_price=FILL_PRICE,
                                 now=bars[-1]["open_ts"] + 300)
    hs = {row["horizon"]: row for row in rs}
    check("SHORT 1h return is the sign-flipped return",
          abs(hs["1h"]["return_pct"] + h["1h"]["return_pct"]) < 1e-9,
          str(hs["1h"]["return_pct"]))

    # A horizon is UNAVAILABLE, never zero-filled, on an unrecoverable gap.
    # The gap sits at index 20 (inside the 48-bar 4h span) and `now` is past
    # that slot's six-day recovery window, so the 4h row cannot mature.
    gap_slot = FILL_OPEN + 300 * 20
    gapped = [b for b in bars if b["open_ts"] != gap_slot]
    r = outmod.resolve_horizons(gapped, plan, fill_bar_open_ts=FILL_OPEN,
                                fill_price=FILL_PRICE,
                                now=gap_slot + RECOVERABLE_S + 1)
    h = {row["horizon"]: row for row in r}
    check("an unrecoverable gap makes a horizon UNAVAILABLE",
          h["4h"]["status"] == "UNAVAILABLE", str({k: v["status"] for k, v in h.items()}))
    check("an unavailable horizon has no return value",
          h["4h"]["return_pct"] is None, str(h["4h"]))

    # Horizon span is independent of the stop: a stopped trade still has
    # a matured 24h value.
    stopped = [bar(FILL_OPEN, 100.0, 100.0, 99.9, 100.0),
               bar(FILL_OPEN + 300, 100.0, 100.0, 94.0, 95.0)]
    stopped += [bar(FILL_OPEN + 300 * i, 100.0, 101.0, 99.0, 100.5)
                for i in range(2, 300)]
    r = outmod.resolve_horizons(stopped, plan, fill_bar_open_ts=FILL_OPEN,
                                fill_price=FILL_PRICE,
                                now=stopped[-1]["open_ts"] + 300)
    h = {row["horizon"]: row for row in r}
    check("a stopped trade still has a matured 24h horizon",
          h["24h"]["status"] == "MATURED", str(h["24h"]))

    # Bars ending before a horizon completes: PENDING, never zero-filled.
    short = bars[:6]
    r = outmod.resolve_horizons(short, plan, fill_bar_open_ts=FILL_OPEN,
                                fill_price=FILL_PRICE,
                                now=short[-1]["open_ts"] + 300)
    h = {row["horizon"]: row for row in r}
    check("a horizon with too few bars is PENDING",
          all(row["status"] == "PENDING" for row in r), str(r))


# ---------------------------------------------------------------------------
# Store / resolver plumbing
# ---------------------------------------------------------------------------

def _make_store():
    import tempfile
    return Store(os.path.join(tempfile.mkdtemp(), "ep.db"))


def _plan_row(store, direction="LONG", signal_id=1):
    """A frozen episode plan row, mirroring episode_plan columns."""
    entry_high, entry_low = 100.1, 99.9
    if direction == "LONG":
        stop, tp1, tp2 = 95.0, 105.0, 110.0
    else:
        stop, tp1, tp2 = 105.0, 95.0, 90.0
    store.conn.execute(
        """INSERT INTO episode_plan
           (episode_id, signal_id, direction, entry_low, entry_high,
            stop, tp1, tp2, leverage, notional, max_loss, warnings,
            frozen_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (1, signal_id, direction, entry_low, entry_high, stop, tp1, tp2,
         10, 1.0, 0.05, None, 1_700_000_100))
    store.conn.commit()
    return {"direction": direction, "entry_low": entry_low,
            "entry_high": entry_high, "stop": stop, "tp1": tp1, "tp2": tp2}


def test_episode_tables_exist_and_resolver_signature():
    print("--- episode tables and resolver signature")

    store = _make_store()
    tables = {r[0] for r in store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("episode_plan", "episode_outcome", "episode_horizon"):
        check(f"table {t} exists", t in tables, str(sorted(tables)))
    store.close()

    sig = inspect.signature(outmod.resolve_episode_plans)
    params = list(sig.parameters)
    check("resolve_episode_plans exists",
          "resolve_episode_plans" in dir(outmod))
    check("signature takes store, symbol_map, now, bar_loader, data_dir",
          params[:2] == ["store", "symbol_map"] and "now" in params
          and "bar_loader" in params and "data_dir" in params,
          str(params))
    # `now`, `bar_loader` and `data_dir` are keyword-only, so their defaults
    # live in __kwdefaults__ rather than __defaults__.
    kwdefaults = outmod.resolve_episode_plans.__kwdefaults__ or {}
    check("data_dir defaults to data/bars",
          kwdefaults.get("data_dir") == "data/bars", str(kwdefaults))


def test_episode_plan_upsert_is_immutable_and_recomputable():
    print("--- episode plan rows are immutable and recomputable")

    store = _make_store()
    plan = _plan_row(store)

    store.insert_episode_plan(episode_id=1, signal_id=1, **plan)
    rows = store.conn.execute(
        "SELECT direction, entry_low, entry_high, stop, tp1, tp2"
        " FROM episode_plan").fetchall()
    check("insert_episode_plan writes one row", len(rows) == 1, str(rows))
    check("the row carries the frozen levels", rows[0] ==
          ("LONG", 99.9, 100.1, 95.0, 105.0, 110.0), str(rows))

    # A second insert for the same episode must not change the frozen plan.
    other = dict(plan, entry_high=101.0)
    store.insert_episode_plan(episode_id=1, signal_id=1, **other)
    rows = store.conn.execute(
        "SELECT entry_high FROM episode_plan WHERE episode_id=1").fetchall()
    check("the frozen plan is never rewritten", len(rows) == 1
          and rows[0][0] == 100.1, str(rows))

    # A different episode keeps its own row.
    store.insert_episode_plan(episode_id=2, signal_id=2, **other)
    check("each episode has its own plan row",
          store.conn.execute(
              "SELECT COUNT(*) FROM episode_plan").fetchone()[0] == 2)
    store.close()


def test_episode_outcome_upsert_pending_and_terminal():
    print("--- episode outcome upsert: pending then terminal")

    store = _make_store()
    _plan_row(store)

    store.upsert_episode_outcome(1, entry_status="FILLED", fill_bar_open_ts=100,
                                 fill_price=100.1, fill_ts=400,
                                 trade_status="OPEN")
    row = store.conn.execute(
        "SELECT entry_status, fill_price, trade_status FROM episode_outcome"
        " WHERE episode_id=1").fetchone()
    check("an outcome row is written", row is not None, str(row))
    check("pending entry and open trade are stored",
          row == ("FILLED", 100.1, "OPEN"), str(row))

    # Terminal rows are immutable: a later pass must not rewrite them.
    store.upsert_episode_outcome(1, entry_status="FILLED", fill_bar_open_ts=100,
                                 fill_price=100.1, fill_ts=400,
                                 trade_status="STOPPED", stop_index=1,
                                 exit_ts=700, exit_price=95.0)
    store.upsert_episode_outcome(1, entry_status="FILLED", fill_bar_open_ts=100,
                                 fill_price=100.1, fill_ts=400,
                                 trade_status="OPEN")
    row = store.conn.execute(
        "SELECT trade_status, stop_index FROM episode_outcome"
        " WHERE episode_id=1").fetchone()
    check("a terminal outcome is never rewritten", row == ("STOPPED", 1),
          str(row))
    store.close()


def test_episode_horizon_upsert():
    print("--- episode horizon upsert")

    store = _make_store()
    _plan_row(store)

    store.upsert_episode_horizon(1, "1h", status="PENDING")
    row = store.conn.execute(
        "SELECT status, return_pct FROM episode_horizon WHERE episode_id=1"
        " AND horizon='1h'").fetchone()
    check("a pending horizon row is written", row == ("PENDING", None),
          str(row))

    store.upsert_episode_horizon(1, "1h", status="MATURED", return_pct=1.25,
                                 mfe_pct=2.0, mae_pct=-0.5)
    row = store.conn.execute(
        "SELECT status, return_pct FROM episode_horizon WHERE episode_id=1"
        " AND horizon='1h'").fetchone()
    check("a matured horizon overwrites the pending row",
          row == ("MATURED", 1.25), str(row))

    # Terminal statuses are immutable once written.
    store.upsert_episode_horizon(1, "1h", status="PENDING")
    row = store.conn.execute(
        "SELECT status FROM episode_horizon WHERE episode_id=1"
        " AND horizon='1h'").fetchone()
    check("a matured horizon is never downgraded to pending", row[0] ==
          "MATURED", str(row))
    store.close()


def test_planned_episodes_query():
    print("--- planned episodes query")

    store = _make_store()
    store.conn.executescript(
        """INSERT INTO signal_log (id, ts, coin, flagged, direction, price)
           VALUES (1, 1_700_000_100, 'BTC', 1, 'LONG', 100.0);
           INSERT INTO signal_episode
             (id, coin, venue, direction, first_signal_id, start_ts, state,
              plan_status, config_hash)
           VALUES (1, 'BTC', 'MEXC', 'LONG', 1, 1_700_000_100, 'CLOSED',
                   'PLANNED', 'h1'),
                  (2, 'ETH', 'MEXC', 'LONG', 1, 1_700_000_100, 'CLOSED',
                   'NO_PLAN', 'h1'),
                  (3, 'SOL', 'MEXC', 'LONG', 1, 1_700_000_100, 'CLOSED',
                   'PLANNED', 'h1');""")
    _plan_row(store, signal_id=1)
    store.conn.execute(
        "INSERT INTO episode_plan (episode_id, signal_id, direction, entry_low,"
        " entry_high, stop, tp1, tp2, frozen_at) VALUES (3, 1, 'LONG', 99.9,"
        " 100.1, 95.0, 105.0, 110.0, 1_700_000_100)")
    store.conn.commit()

    rows = store.planned_episodes()
    ids = [r["episode_id"] for r in rows]
    check("planned_episodes lists planned episodes only",
          ids == [1, 3], str(ids))
    check("a NO_PLAN episode is not resolved", 2 not in ids, str(ids))
    check("each row carries coin, direction and the frozen plan",
          rows[0]["coin"] == "BTC" and rows[0]["direction"] == "LONG"
          and rows[0]["entry_high"] == 100.1, str(rows[0]))
    check("each row carries the episode start (T0)",
          rows[0]["start_ts"] == 1_700_000_100, str(rows[0]))
    store.close()


def test_resolve_episode_plans_end_to_end_offline():
    print("--- resolve_episode_plans end to end (injected bar_loader)")

    store = _make_store()
    store.conn.executescript(
        """INSERT INTO signal_log (id, ts, coin, flagged, direction, price)
           VALUES (1, 1_700_000_100, 'BTC', 1, 'LONG', 100.0);
           INSERT INTO signal_episode
             (id, coin, venue, direction, first_signal_id, start_ts, state,
              plan_status, config_hash)
           VALUES (1, 'BTC', 'MEXC', 'LONG', 1, 1_700_000_100, 'CLOSED',
                   'PLANNED', 'h1');""")
    store.conn.execute(
        """INSERT INTO episode_plan
           (episode_id, signal_id, direction, entry_low, entry_high,
            stop, tp1, tp2, leverage, notional, max_loss, warnings, frozen_at)
           VALUES (1, 1, 'LONG', 99.9, 100.1, 95.0, 105.0, 110.0,
                   10, 1.0, 0.05, NULL, 1_700_000_100)""")
    store.conn.commit()

    T0 = 1_700_000_100
    # Slot 1 (T0+300) holds price above the band: no touch. Slot 2 (T0+600)
    # trades down to 100.0, filling the LONG at the adverse edge 100.1, and
    # its bar closes at T0+900. Later slots drift quietly with no stop/TP.
    bars = [bar(T0 + 300, 100.2, 100.2, 100.15, 100.2),
            bar(T0 + 600, 100.2, 100.2, 100.0, 100.05)]
    for i in range(2, 40):
        bars.append(bar(T0 + 300 * i, 100.0, 101.0, 99.0, 100.5))
    # One bar per slot: a duplicated slot is ambiguous and must not decide
    # anything, so keep only the first copy of each open_ts.
    seen, unique = set(), []
    for b in bars:
        if b["open_ts"] in seen:
            continue
        seen.add(b["open_ts"])
        unique.append(b)
    bars = unique

    calls = []

    def bar_loader(coin, data_dir):
        calls.append((coin, data_dir))
        return list(bars)

    now = T0 + 300 * 60
    result = outmod.resolve_episode_plans(
        store, {"BTC": "BTC_USDT"}, now=now, bar_loader=bar_loader,
        data_dir="data/bars")

    check("the resolver used the injected bar loader", calls == [("BTC",
          "data/bars")], str(calls))
    check("the resolver returns a summary dict", isinstance(result, dict),
          str(type(result)))
    check("the summary counts the resolved episode",
          result.get("resolved") == 1 or result.get("filled") == 1
          or result.get("plans") == 1, str(result))

    out = store.conn.execute(
        "SELECT entry_status, fill_price, fill_ts, trade_status,"
        " resolved_through_ts FROM episode_outcome WHERE episode_id=1"
    ).fetchone()
    check("the entry resolved as FILLED", out[0] == "FILLED", str(out))
    check("the fill price is the adverse edge", out[1] == 100.1, str(out))
    # Bars: slot 1 (T0+300) holds 100.2/100.15 (no touch), slot 2 (T0+600)
    # trades down to 100.0, so the fill bar is slot 2 and it closes at T0+900.
    check("the fill ts is the fill bar's close", out[2] == T0 + 900, str(out))
    check("the trade is still OPEN at this point", out[3] == "OPEN", str(out))
    check("resolved_through_ts is recorded", out[4] is not None, str(out))

    # Idempotent: a second pass does not change a resolved row.
    outmod.resolve_episode_plans(store, {"BTC": "BTC_USDT"}, now=now,
                                 bar_loader=bar_loader, data_dir="data/bars")
    out2 = store.conn.execute(
        "SELECT entry_status, fill_price, fill_ts, trade_status,"
        " resolved_through_ts FROM episode_outcome WHERE episode_id=1"
    ).fetchone()
    check("re-running the resolver is idempotent", out2 == out, str(out2))
    store.close()


def test_resolve_episode_plans_unfilled_and_no_symbol():
    print("--- resolver skips coins with no venue symbol")

    store = _make_store()
    store.conn.executescript(
        """INSERT INTO signal_log (id, ts, coin, flagged, direction, price)
           VALUES (1, 1_700_000_100, 'BTC', 1, 'LONG', 100.0);
           INSERT INTO signal_episode
             (id, coin, venue, direction, first_signal_id, start_ts, state,
              plan_status, config_hash)
           VALUES (1, 'BTC', 'MEXC', 'LONG', 1, 1_700_000_100, 'CLOSED',
                   'PLANNED', 'h1');""")
    store.conn.execute(
        """INSERT INTO episode_plan
           (episode_id, signal_id, direction, entry_low, entry_high,
            stop, tp1, tp2, leverage, notional, max_loss, warnings, frozen_at)
           VALUES (1, 1, 'LONG', 99.9, 100.1, 95.0, 105.0, 110.0,
                   10, 1.0, 0.05, NULL, 1_700_000_100)""")
    store.conn.commit()

    def boom(coin, data_dir):
        raise AssertionError("bar_loader must not run for an unknown symbol")

    T0 = 1_700_000_100
    now = T0 + 3600 + 300
    outmod.resolve_episode_plans(store, {}, now=now, bar_loader=boom)
    row = store.conn.execute(
        "SELECT entry_status FROM episode_outcome WHERE episode_id=1").fetchone()
    check("an episode with no venue symbol is left unresolved",
          row is None or row[0] == "PENDING_ENTRY", str(row))
    store.close()


def test_legacy_resolver_untouched():
    print("--- legacy outcome resolver is untouched by Task 4")

    src = inspect.getsource(outmod)
    for fn in ("plan_touches", "resolve_plans", "signed_return",
               "resolve_pending"):
        check(f"legacy {fn} still exported", fn in dir(outmod))
    check("legacy HORIZON_BARS constant intact",
          outmod.HORIZON_BARS == {"1h": 12, "4h": 48, "24h": 288, "7d": 2016},
          str(outmod.HORIZON_BARS))
    sig = inspect.signature(outmod.resolve_pending)
    check("legacy resolve_pending signature unchanged",
          str(sig) == "(store, symbol_map, horizons=('1h', '4h', '24h',"
          " '7d'), bar_source='rest+collector', data_dir='data/bars')",
          str(sig))


# ---------------------------------------------------------------------------

def run(fn):
    print(f"\n=== {fn.__name__} ===")
    try:
        fn()
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"  FAIL  {fn.__name__} raised {type(e).__name__}: {e}")
        FAILURES.append(fn.__name__)


for t in (test_bar_completeness_helpers,
          test_entry_window_slots,
          test_fill_adverse_edge,
          test_entry_open_and_stale_bars_are_ignored,
          test_entry_status_pending_and_unfilled,
          test_trade_resolution_rules,
          test_trade_gap_recovery,
          test_fixed_horizons,
          test_episode_tables_exist_and_resolver_signature,
          test_episode_plan_upsert_is_immutable_and_recomputable,
          test_episode_outcome_upsert_pending_and_terminal,
          test_episode_horizon_upsert,
          test_planned_episodes_query,
          test_resolve_episode_plans_end_to_end_offline,
          test_resolve_episode_plans_unfilled_and_no_symbol,
          test_legacy_resolver_untouched):
    run(t)

print("\n" + ("ALL PASS" if not FAILURES
              else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
