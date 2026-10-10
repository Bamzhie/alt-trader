"""Functional tests for the ALT RADAR tkinter GUI (task G2).

Two layers in one file:

  MODEL  — ``gui.model`` is pure Python (no tkinter import): price/volume
           formatting, flags, direction filters, sorts, veto splits, plan
           and detail text, outcome summaries and operator-input
           validation. This layer must run with NO display — one test
           re-imports the module in a subprocess with DISPLAY stripped to
           prove it.

  WIDGET — real ``RadarGUI`` windows are built only when Tk can open a
           display. When it cannot (DISPLAY unset / X unavailable / tkinter
           missing), every widget test is SKIPPED cleanly, never failed —
           see HAS_DISPLAY.

Network policy: ZERO network in this file. Cards are fake
``proto.scorer.Scorecard`` objects, stores are temp-dir SQLite files, and
each window's worker thread is retired immediately after construction
(None sentinel + join — the ctor's offline ``stats`` job is queued before
the sentinel, so the real queue → poll → render path is still exercised).
No scan / plan / collect / resolve job can ever run, hence no network.

Event synthesis notes (verified empirically on this Tk 8.6 build):
  * virtual events (``<<TreeviewSelect>>``, ``<<ComboboxSelected>>``) and
    real non-key events (``<FocusOut>``) dispatch via ``event_generate``;
  * generated key events (``<Return>``) do NOT reach bindings here, so the
    stake/threshold Apply paths are driven through the real ttk buttons'
    ``invoke()`` (the command the button runs when clicked), and the
    spinbox commit path through its bound ``<FocusOut>`` handler.

Run:  python3 tests/test_gui.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from gui import model
from proto import app as appmod
from proto import planner as pl
from proto.scorer import Scorecard, Veto
from proto.store import Store


# --------------------------------------------------------------------------
# display probe (import-safe: no Tk() unless the platform uses $DISPLAY)
# --------------------------------------------------------------------------

def _tk_available():
    try:
        import tkinter  # noqa: F401
        return True
    except Exception:
        return False


TK_AVAILABLE = _tk_available()


def _has_display():
    if not TK_AVAILABLE:
        return False
    if sys.platform.startswith(("linux", "freebsd")) and not os.environ.get("DISPLAY"):
        return False
    try:
        import tkinter as tk
        root = tk.Tk()
        root.destroy()
        return True
    except Exception:
        return False


HAS_DISPLAY = _has_display()

if TK_AVAILABLE:
    from tkinter import ttk
    from gui.app import COLOR_ERROR, DIR_CHOICES, RadarGUI, SIGNAL_COLUMNS, SIGNAL_HEADINGS

    class _TrackedRadarGUI(RadarGUI):
        """RadarGUI that records every after() timer id.

        Tk timer events are thread-wide: a destroyed window's pending
        after-callbacks would fire during a LATER test's update() and print
        ``invalid command name ...`` to stderr. Tests cancel the recorded ids
        in tearDown before destroying the window.
        """

        def after(self, ms, func=None, *args):
            tid = super().after(ms, func, *args)
            ids = getattr(self, "_timer_ids", None)
            if ids is None:
                ids = self._timer_ids = []
            ids.append(tid)
            return tid
else:  # pragma: no cover - only on tkinter-less interpreters
    RadarGUI = None
    _TrackedRadarGUI = None


# --------------------------------------------------------------------------
# shared fixtures
# --------------------------------------------------------------------------

def mkcard(coin, score=50.0, direction="LONG", venue="BYBIT", vetoes=(),
           min_notional=0.01, notes=(), lean=0.5, earlyness=0.5, price=1.0,
           change_24h_pct=0.0, quote_vol_24h=1e6, funding_rate=0.0001,
           oi_change_pct=None, spread_pct=0.1):
    """A fake scorecard — no network, no scanner, plain data."""
    sc = Scorecard(coin=coin, venue=venue, score=score, direction=direction,
                   lean=lean, earlyness=earlyness, price=price,
                   change_24h_pct=change_24h_pct, quote_vol_24h=quote_vol_24h,
                   funding_rate=funding_rate, oi_change_pct=oi_change_pct,
                   spread_pct=spread_pct, min_notional=min_notional,
                   notes=list(notes))
    sc.vetoes = [Veto(v, "test reason") for v in vetoes]
    sc.magnitude_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
    sc.lean_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
    return sc


def zero_outcome():
    return model.outcome_summary(None)


# ==========================================================================
# MODEL LAYER — no display required
# ==========================================================================

class TestFormatPrice(unittest.TestCase):
    def test_invalid_inputs_render_na(self):
        # venue fields are untrusted text; a bad price must never crash a render
        for bad in (None, "garbage", "", float("nan"), float("inf"),
                    float("-inf"), True, False, [1]):
            self.assertEqual(model.format_price(bad), "n/a", repr(bad))

    def test_zero(self):
        self.assertEqual(model.format_price(0), "0")
        self.assertEqual(model.format_price(0.0), "0")

    def test_tiny_prices_never_use_scientific_notation(self):
        self.assertEqual(model.format_price(1e-05), "0.00001")
        self.assertEqual(model.format_price(1.2e-07), "0.00000012")
        self.assertEqual(model.format_price(-1e-05), "-0.00001")
        # just under the 1e-4 threshold still goes through the fixed-decimals path
        self.assertEqual(model.format_price(9.9999e-05), "0.000099999")

    def test_threshold_boundary_uses_significant_digits(self):
        # abs(price) >= 1e-4 -> %.8g branch (no trailing-dot artefacts)
        self.assertEqual(model.format_price(0.0001), "0.0001")
        self.assertEqual(model.format_price(0.123456), "0.123456")
        self.assertEqual(model.format_price(1234.5), "1234.5")
        self.assertEqual(model.format_price(123456789), "1.2345679e+08")

    def test_numeric_strings_are_accepted(self):
        self.assertEqual(model.format_price("1234.5"), "1234.5")


class TestFormatVol(unittest.TestCase):
    def test_millions_and_thousands(self):
        self.assertEqual(model.format_vol(4_300_000), "$4.3M")
        self.assertEqual(model.format_vol(1_000_000), "$1.0M")
        self.assertEqual(model.format_vol(600_000), "$600K")
        self.assertEqual(model.format_vol(1_300), "$1K")
        self.assertEqual(model.format_vol(999), "$999")
        self.assertEqual(model.format_vol(0), "$0")

    def test_sub_million_rounding_boundary(self):
        # 999,999 is still the K branch (>= 1e3, < 1e6) — documented rounding
        self.assertEqual(model.format_vol(999_999), "$1000K")

    def test_invalid_inputs_render_na(self):
        for bad in (None, "junk", float("nan"), float("inf"), True):
            self.assertEqual(model.format_vol(bad), "n/a", repr(bad))
        self.assertEqual(model.format_vol("2500000"), "$2.5M")


class TestFormatOi(unittest.TestCase):
    def test_percent_with_notional_base(self):
        self.assertEqual(model.format_oi(68.3, 12_400), "+68.3% (~$12K)")
        self.assertEqual(model.format_oi(-9.1, 5_300_000), "-9.1% (~$5.3M)")

    def test_percent_without_base(self):
        self.assertEqual(model.format_oi(4.2), "+4.2%")
        self.assertEqual(model.format_oi(4.2, None), "+4.2%")

    def test_missing_percent_is_na_even_with_base(self):
        self.assertEqual(model.format_oi(None), "n/a")
        self.assertEqual(model.format_oi(None, 99_000), "n/a")
        self.assertEqual(model.format_oi("junk", 100), "n/a")


class TestFormatFlags(unittest.TestCase):
    def test_watch_rule_and_boundary(self):
        # min margin ($1 at 50x) > stake -> WATCH
        self.assertEqual(model.format_flags(mkcard("A", min_notional=50.0), 0.1), "WATCH")
        # $2.48 notional needs ~$0.05 margin: fits a $0.10 stake (leveraged reality)
        self.assertEqual(model.format_flags(mkcard("A", min_notional=2.48), 0.1), "")
        # boundary: margin exactly stake is NOT a watch
        self.assertEqual(model.format_flags(mkcard("A", min_notional=5.0), 0.1), "")
        # below stake -> fine
        self.assertEqual(model.format_flags(mkcard("A", min_notional=0.05), 0.1), "")

    def test_none_or_garbage_min_notional_never_flags(self):
        self.assertEqual(model.format_flags(mkcard("A", min_notional=None), 0.1), "")
        self.assertEqual(model.format_flags(mkcard("A", min_notional="junk"), 0.1), "")
        # an unvalidated stake must not produce a spurious WATCH either
        self.assertEqual(model.format_flags(mkcard("A", min_notional=50.0), None), "")
        self.assertEqual(model.format_flags(mkcard("A", min_notional=50.0), "junk"), "")

    def test_unvalidated_rule(self):
        # MEXC rows are Tier-2 by venue
        self.assertEqual(model.format_flags(mkcard("A", venue="MEXC"), 0.1),
                         "UNVALIDATED")
        # non-MEXC venue with no note is clean
        self.assertEqual(model.format_flags(mkcard("A", venue="BYBIT"), 0.1), "")
        # a Tier-2 note marks even a non-MEXC row
        flagged = mkcard("A", venue="BYBIT", notes=["UNVALIDATED — no history"])
        self.assertEqual(model.format_flags(flagged, 0.1), "UNVALIDATED")
        # unrelated / non-string notes are ignored
        self.assertEqual(
            model.format_flags(mkcard("A", venue="BYBIT", notes=["plain", 42]), 0.1), "")

    def test_combined_flags_space_separated(self):
        both = mkcard("A", venue="MEXC", min_notional=50.0)
        self.assertEqual(model.format_flags(both, 0.1), "WATCH UNVALIDATED")


class TestFilterCards(unittest.TestCase):
    def setUp(self):
        self.cards = [mkcard("L1", direction="LONG"),
                      mkcard("S1", direction="SHORT"),
                      mkcard("L2", direction="LONG"),
                      mkcard("N1", direction="NEUTRAL")]

    def test_both_keeps_everything_and_returns_a_copy(self):
        out = model.filter_cards(self.cards, "both")
        self.assertEqual([c.coin for c in out], ["L1", "S1", "L2", "N1"])
        self.assertIsNot(out, self.cards)

    def test_long_short_selection_is_case_insensitive(self):
        self.assertEqual([c.coin for c in model.filter_cards(self.cards, "long")],
                         ["L1", "L2"])
        self.assertEqual([c.coin for c in model.filter_cards(self.cards, "Short")],
                         ["S1"])
        self.assertEqual([c.coin for c in model.filter_cards(self.cards, "  LONG  ")],
                         ["L1", "L2"])
        # NEUTRAL rows appear in neither directional view
        self.assertEqual(model.filter_cards(self.cards, "long"),
                         [c for c in self.cards if c.direction == "LONG"])

    def test_unknown_filter_raises(self):
        with self.assertRaises(ValueError):
            model.filter_cards(self.cards, "sideways")

    def test_empty_cards(self):
        self.assertEqual(model.filter_cards([], "both"), [])
        self.assertEqual(model.filter_cards([], "long"), [])


class TestRowTags(unittest.TestCase):
    def test_direction_band_watch_combinations(self):
        self.assertEqual(model.row_tags("LONG", 1), ("long",))
        self.assertEqual(model.row_tags("LONG", 2), ("long_alt",))
        self.assertEqual(model.row_tags("SHORT", 3), ("short",))
        self.assertEqual(model.row_tags("SHORT", 4), ("short_alt",))
        self.assertEqual(model.row_tags("NEUTRAL", 1), ("plain",))
        self.assertEqual(model.row_tags("NEUTRAL", 2), ("plain_alt",))
        self.assertEqual(model.row_tags("LONG", 1, watch=True),
                         ("long", "watch"))
        self.assertEqual(model.row_tags("LONG", 1, vetoed=True), ("vetoed",))
        self.assertEqual(model.row_tags("SHORT", 2, vetoed=True),
                         ("vetoed_alt",))

    def test_tags_resolve_to_backgrounds(self):
        from gui import theme
        for tags in (model.row_tags("LONG", 1), model.row_tags("SHORT", 2),
                     model.row_tags("NEUTRAL", 1), model.row_tags("X", 2),
                     model.row_tags("LONG", 1, vetoed=True)):
            self.assertIn(tags[0], theme.TAG_BACKGROUNDS, tags)


class TestNoHardcodedColors(unittest.TestCase):
    def test_app_and_model_use_tokens_only(self):
        import re
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in ("gui/app.py", "gui/model.py"):
            with open(os.path.join(here, name)) as f:
                src = f.read()
            hits = re.findall(r"#[0-9a-fA-F]{6}\b", src)
            self.assertEqual(hits, [], f"{name}: {hits}")


class TestFilterSearch(unittest.TestCase):
    def setUp(self):
        self.cards = [mkcard("QNT"), mkcard("QUANTA"), mkcard("BTC")]

    def test_substring_case_insensitive(self):
        self.assertEqual([c.coin for c in model.filter_search(self.cards, "qnt")],
                         ["QNT"])
        self.assertEqual([c.coin for c in model.filter_search(self.cards, "quan")],
                         ["QUANTA"])

    def test_blank_keeps_everything(self):
        for blank in ("", "   ", None):
            out = model.filter_search(self.cards, blank)
            self.assertEqual([c.coin for c in out], ["QNT", "QUANTA", "BTC"])
            self.assertIsNot(out, self.cards)

    def test_no_match_is_empty_not_an_error(self):
        self.assertEqual(model.filter_search(self.cards, "zzz"), [])
        self.assertEqual(model.filter_search([], "qnt"), [])


class TestSortCards(unittest.TestCase):
    def setUp(self):
        # distinct values per sort key so every ordering is unambiguous
        self.cards = [
            mkcard("A", score=10, earlyness=0.1, lean=-0.5,
                   change_24h_pct=3.0, quote_vol_24h=100),
            mkcard("B", score=90, earlyness=0.9, lean=0.8,
                   change_24h_pct=-12.0, quote_vol_24h=5000),
            mkcard("C", score=50, earlyness=0.5, lean=-0.1,
                   change_24h_pct=5.0, quote_vol_24h=1000),
        ]

    def test_sort_keys_delegate_to_proto_app(self):
        # same object — never a fork of the TUI's sorting semantics
        self.assertIs(model.SORT_KEYS, appmod.SORT_KEYS)

    def test_every_key_matches_proto_order(self):
        for key in appmod.SORT_KEYS:
            got = model.sort_cards(self.cards, key)
            want = sorted(self.cards, key=appmod.SORT_KEYS[key])
            self.assertEqual([c.coin for c in got], [c.coin for c in want], key)
        # pinned concretely for two keys so a shared regression is caught
        self.assertEqual([c.coin for c in model.sort_cards(self.cards, "score")],
                         ["B", "C", "A"])
        self.assertEqual([c.coin for c in model.sort_cards(self.cards, "lean")],
                         ["B", "A", "C"])
        # input order untouched (sorted() is pure)
        self.assertEqual([c.coin for c in self.cards], ["A", "B", "C"])

    def test_unknown_key_raises(self):
        with self.assertRaises(ValueError):
            model.sort_cards(self.cards, "bogus")
        self.assertEqual(model.sort_cards([], "score"), [])


class TestSplitVetoed(unittest.TestCase):
    def test_mixed_preserves_input_order(self):
        cards = [mkcard("A"), mkcard("B", vetoes=("late_move",)), mkcard("C")]
        ranked, vetoed = model.split_vetoed(cards)
        self.assertEqual([c.coin for c in ranked], ["A", "C"])
        self.assertEqual([c.coin for c in vetoed], ["B"])

    def test_vetoed_only_list(self):
        cards = [mkcard("B", vetoes=("late_move",)), mkcard("D", vetoes=("stale",))]
        ranked, vetoed = model.split_vetoed(cards)
        self.assertEqual(ranked, [])
        self.assertEqual([c.coin for c in vetoed], ["B", "D"])

    def test_empty_and_no_vetoes(self):
        self.assertEqual(model.split_vetoed([]), ([], []))
        ranked, vetoed = model.split_vetoed([mkcard("A"), mkcard("B")])
        self.assertEqual([c.coin for c in ranked], ["A", "B"])
        self.assertEqual(vetoed, [])


class TestPlanText(unittest.TestCase):
    def test_none_plan(self):
        self.assertEqual(model.plan_text(mkcard("A"), None), "no plan available")

    def test_plan_delegates_to_proto_planner(self):
        card = mkcard("A", direction="LONG", price=2.0)
        plan = pl.Plan(coin="A", direction="LONG", entry_low=1.998, entry_high=2.002,
                       stop=1.9, tp1=2.2, tp2=2.5, leverage=5, notional=10,
                       margin=0.5, max_loss=0.1, costs=0.01, break_even_pct=0.1,
                       reward_risk=2.5)
        got = model.plan_text(card, plan)
        self.assertEqual(got, pl.format_plan(plan, card))
        self.assertIn("▲ LONG", got)
        self.assertIn("Entry", got)
        self.assertIn("TP1", got)


class TestOutcomeSummary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="alt-radar-outcomes-")
        self.store = Store(os.path.join(self.tmp, "o.db"))

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_none_store_zero_shape(self):
        zero = model.outcome_summary(None)
        self.assertEqual(zero, {
            "counts": {"1h": 0, "4h": 0, "24h": 0, "7d": 0},
            "total": 0,
            "direction": {"LONG": {"count": 0, "avg_return": 0.0, "hit_rate": 0.0},
                          "SHORT": {"count": 0, "avg_return": 0.0, "hit_rate": 0.0}},
        })

    def test_empty_store_is_zero(self):
        self.assertEqual(model.outcome_summary(self.store), model.outcome_summary(None))

    def test_signals_without_outcomes_stay_zero(self):
        self.store.log_signal(mkcard("AAA"), flagged=True)
        s = model.outcome_summary(self.store)
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["direction"]["LONG"]["count"], 0)

    def test_counts_avg_return_and_hit_rate(self):
        sig_long = self.store.log_signal(mkcard("AAA", direction="LONG"), flagged=True)
        sig_short = self.store.log_signal(mkcard("BBB", direction="SHORT"), flagged=False)
        sig_long2 = self.store.log_signal(mkcard("CCC", direction="LONG"), flagged=True)
        self.store.log_outcome(sig_long, "1h", 10.0, 12.0, -3.0)
        self.store.log_outcome(sig_long, "4h", -5.0, 12.0, -8.0)
        self.store.log_outcome(sig_short, "1h", 4.0, 5.0, -1.0)
        # NULL return: counted in totals but excluded from direction statistics
        self.store.log_outcome(sig_long2, "4h", None, None, None)

        s = model.outcome_summary(self.store)
        self.assertEqual(s["counts"], {"1h": 2, "4h": 2, "24h": 0, "7d": 0})
        self.assertEqual(s["total"], 4)
        lng = s["direction"]["LONG"]
        self.assertEqual(lng["count"], 2)              # NULL return excluded
        self.assertAlmostEqual(lng["avg_return"], 2.5)  # (10 - 5) / 2
        self.assertAlmostEqual(lng["hit_rate"], 0.5)    # 1 of 2 positive
        sht = s["direction"]["SHORT"]
        self.assertEqual(sht["count"], 1)
        self.assertAlmostEqual(sht["avg_return"], 4.0)
        self.assertAlmostEqual(sht["hit_rate"], 1.0)

    def test_unknown_horizon_counts_total_only(self):
        sig = self.store.log_signal(mkcard("AAA", direction="SHORT"), flagged=True)
        self.store.log_outcome(sig, "1h", 4.0, 5.0, -1.0)
        self.store.log_outcome(sig, "9h", 1.0, 2.0, -1.0)   # not a known horizon
        s = model.outcome_summary(self.store)
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["counts"]["1h"], 1)
        self.assertEqual(s["counts"]["4h"], 0)
        # the 9h row still feeds the direction stats
        self.assertEqual(s["direction"]["SHORT"]["count"], 2)
        self.assertAlmostEqual(s["direction"]["SHORT"]["avg_return"], 2.5)


class TestDetailText(unittest.TestCase):
    def test_base_layout(self):
        card = mkcard("AAA", score=70.0, direction="LONG", venue="MEXC",
                      lean=0.5, earlyness=0.5, change_24h_pct=3.2,
                      quote_vol_24h=4_300_000, funding_rate=0.0001,
                      spread_pct=0.1, oi_change_pct=None, min_notional=None)
        text = model.detail_text(card, stake=0.1)
        self.assertIn("AAA  ▲ LONG   score 70.0   lean +0.50   earlyness 0.50", text)
        self.assertIn("venue MEXC · tier 2 · READ-ONLY — this app places no orders", text)
        self.assertIn("SCORE COMPONENTS", text)
        self.assertIn("MAG      LEAN", text)
        self.assertIn("24h +3.2%", text)
        self.assertIn("vol24 $4.3M", text)
        self.assertIn("funding +0.0100%", text)
        self.assertIn("⚠ OI unavailable on MEXC — OI/FUNDING signal is running on "
                      "funding alone", text)
        self.assertIn("TRADE PLAN (review only — this app places no orders)", text)

    def test_oi_value_rendered_without_the_unavailable_warning(self):
        card = mkcard("AAA", oi_change_pct=12.5)
        text = model.detail_text(card, stake=0.1)
        self.assertIn("OIΔ +12.5%", text)
        self.assertNotIn("OI unavailable", text)

    def test_min_notional_branches(self):
        # unknown -> fails closed
        t = model.detail_text(mkcard("A", min_notional=None), stake=0.1)
        self.assertIn("⚠ minimum notional unknown — fails closed, this coin never "
                      "flags at any stake", t)
        # margin above stake -> WATCH wording with the margin math shown
        t = model.detail_text(mkcard("A", min_notional=50.0), stake=0.1)
        self.assertIn("WATCH only until stake grows", t)
        self.assertIn("margin at 50x", t)
        # at/below stake -> fits
        t = model.detail_text(mkcard("A", min_notional=0.05), stake=0.1)
        self.assertIn("(~$0.0010 margin at 50x", t)
        self.assertIn("fits stake", t)
        t = model.detail_text(mkcard("A", min_notional=2.48), stake=0.1)
        self.assertIn("fits stake", t)
        # no stake in hand -> no notional line at all
        t = model.detail_text(mkcard("A", min_notional=5.0), stake=None)
        self.assertNotIn("min notional", t)

    def test_vetoes_warnings_and_last_error(self):
        card = mkcard("A", vetoes=("late_move",),
                      notes=["counter-trend: 5m LONG vs 4H SHORT — elevated risk"])
        text = model.detail_text(card, stake=0.1, last_error="fetch blew up")
        self.assertIn("VETOES", text)
        self.assertIn("⨯ late_move: test reason", text)
        self.assertIn("WARNINGS", text)
        self.assertIn("⚠ counter-trend: 5m LONG vs 4H SHORT — elevated risk", text)
        self.assertIn("⚠ last error: fetch blew up", text)

    def test_tier2_warning_variants(self):
        # MEXC row without a note -> venue-based wording
        t = model.detail_text(mkcard("A", venue="MEXC"), stake=0.1)
        self.assertIn("⚠ TIER 2 · UNVALIDATED — MEXC row, no outcome history for "
                      "this score yet; treat as experimental", t)
        # note-based wording wins when the note exists (no duplication of the MEXC one)
        t = model.detail_text(mkcard("A", venue="MEXC",
                                     notes=["UNVALIDATED — no history"]), stake=0.1)
        self.assertIn("no outcome history exists for this score yet", t)
        self.assertNotIn("MEXC row, no outcome history", t)
        # clean non-MEXC row -> no warning
        t = model.detail_text(mkcard("A", venue="BYBIT"), stake=0.1)
        self.assertNotIn("UNVALIDATED", t)

    def test_plan_states(self):
        card = mkcard("A", direction="LONG", price=2.0)
        plan = pl.Plan(coin="A", direction="LONG", entry_low=1.998, entry_high=2.002,
                       stop=1.9, tp1=2.2, tp2=2.5, leverage=5, notional=10,
                       margin=0.5, max_loss=0.1, costs=0.01, break_even_pct=0.1,
                       reward_risk=2.5)
        self.assertIn(model.plan_text(card, plan),
                      model.detail_text(card, plan=plan, stake=0.1))
        err_text = model.detail_text(card, plan_err="klines down", stake=0.1)
        self.assertIn("no plan: klines down", err_text)
        pending = model.detail_text(card, stake=0.1)
        self.assertIn("no plan available (fetching…)", pending)


class TestValidators(unittest.TestCase):
    def test_validate_stake(self):
        self.assertEqual(model.validate_stake("0.10"), 0.1)
        self.assertEqual(model.validate_stake(1e-3), 0.001)
        for bad in (0, -1, "abc", None, True, "0"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                model.validate_stake(bad)

    def test_validate_threshold_boundaries(self):
        self.assertEqual(model.validate_threshold(0), 0.0)
        self.assertEqual(model.validate_threshold(100), 100.0)
        self.assertEqual(model.validate_threshold("24.5"), 24.5)
        for bad in (-0.001, 100.001, 101, -1, "abc", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                model.validate_threshold(bad)

    def test_validate_coins_boundaries(self):
        self.assertEqual(model.validate_coins(10), 10)
        self.assertEqual(model.validate_coins(581), 581)
        self.assertEqual(model.validate_coins("150"), 150)
        for bad in (9, 582, 10.5, 150.5, "abc", None, -10):
            with self.assertRaises(ValueError, msg=repr(bad)):
                model.validate_coins(bad)

    def test_validate_interval_boundaries(self):
        self.assertEqual(model.validate_interval(15), 15)
        self.assertEqual(model.validate_interval("60"), 60)
        for bad in (14, 0, -1, 10.5, "abc", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                model.validate_interval(bad)

    def test_direction_arrow(self):
        self.assertEqual(model.direction_arrow("LONG"), "▲")
        self.assertEqual(model.direction_arrow("SHORT"), "▼")
        self.assertEqual(model.direction_arrow("NEUTRAL"), "•")
        self.assertEqual(model.direction_arrow("anything"), "•")

    def test_constants(self):
        self.assertEqual(model.DIR_FILTERS, ("both", "long", "short"))
        self.assertEqual(model.HORIZONS, ("1h", "4h", "24h", "7d"))
        self.assertEqual((model.MIN_COINS, model.MAX_COINS), (10, 581))
        self.assertEqual(model.MIN_INTERVAL, 15)


class TestModelNeedsNoDisplay(unittest.TestCase):
    """gui.model must import and work with DISPLAY removed."""

    def _run(self, code, strip_display=True):
        env = dict(os.environ)
        if strip_display:
            env.pop("DISPLAY", None)
        return subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                              capture_output=True, text=True, timeout=60)

    def test_model_imports_without_display_and_never_imports_tkinter(self):
        code = ("import sys, gui.model; "
                "bad = [m for m in sys.modules if m == 'tkinter' or m.startswith('tkinter.')]; "
                "sys.exit(1 if bad else 0)")
        proc = self._run(code)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, "")

    @unittest.skipUnless(TK_AVAILABLE, "tkinter is not installed")
    def test_gui_app_imports_without_a_display(self):
        # import-safe: defining RadarGUI must not open a window (or need $DISPLAY)
        proc = self._run("import gui.app, gui.__main__; gui.__main__.build_parser()")
        self.assertEqual(proc.returncode, 0, proc.stderr)


# ==========================================================================
# WIDGET LAYER — skipped cleanly when no display is available
# ==========================================================================

@unittest.skipUnless(HAS_DISPLAY,
                     "no usable display: DISPLAY missing or Tk cannot open one")
class TestWidgetLayer(unittest.TestCase):
    """Real RadarGUI windows, withdrawn for the whole test, zero network."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="alt-radar-gui-")
        self.gui = None

    def tearDown(self):
        gui = self.gui
        if gui is not None and not gui._closing:
            # Cancel pending after-timers BEFORE destroying: Tcl timers are
            # thread-wide and would otherwise fire (dead command name) during
            # a later test's update().
            for tid in getattr(gui, "_timer_ids", ()):
                try:
                    gui.after_cancel(tid)
                except Exception:
                    pass
            # Drop StringVars while the interpreter is still alive so their
            # __del__ never races interpreter teardown (stderr noise).
            for name in ("var_header", "var_stake", "var_threshold", "var_coins",
                         "var_interval", "var_dir", "var_sort", "var_search",
                         "var_outcomes",
                         "var_activity", "var_failed", "var_statusline"):
                setattr(gui, name, None)
            gui._on_close()          # sets the stop flag, destroys the window
        self.gui = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- fixture -----------------------------------------------------------
    def make_gui(self, **kw):
        kw.setdefault("auto_scan", False)         # no timer-driven scans in tests
        kw.setdefault("db", os.path.join(self.tmp, "gui.db"))
        gui = _TrackedRadarGUI(**kw)
        self.gui = gui
        gui.withdraw()                            # never mapped
        # Retire the worker: the ctor's offline "stats" job sits in front of
        # the sentinel, so the real queue→poll→render path still runs, but no
        # scan/plan/collect/resolve job can ever execute (=> no network).
        gui._jobs.put(None)
        gui._worker.join(timeout=10)
        if gui._worker.is_alive():          # diagnostic: show where it is stuck
            import traceback
            frames = sys._current_frames()
            stack = traceback.format_stack(frames.get(gui._worker.ident))
            self.fail("worker thread did not retire; stack:\n" + "".join(stack))
        # Retire the plan lane too: plan jobs queued by these tests must never
        # execute (=> no network), only accumulate for assertion.
        gui._plan_jobs.put(None)
        gui._plan_worker.join(timeout=10)
        if gui._plan_worker.is_alive():
            self.fail("plan worker thread did not retire")
        return gui

    def pump_until(self, cond, timeout=10.0, step=0.02):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if cond():
                return True
            self.gui.update()
            time.sleep(step)
        return cond()

    # ---- widget lookups (creation order == winfo_children order) -----------
    def widgets(self, cls, root=None):
        root = root if root is not None else self.gui
        out = []
        stack = [root]
        while stack:
            w = stack.pop(0)
            if isinstance(w, cls):
                out.append(w)
            stack[0:0] = list(w.winfo_children())
        return out

    def labels(self):
        return [str(w.cget("text")) for w in self.widgets(ttk.Label)]

    def buttons(self, text):
        return [w for w in self.widgets(ttk.Button) if str(w.cget("text")) == text]

    def toolbar(self):
        for b in self.buttons("Scan now"):
            return b.master.master
        raise AssertionError("toolbar not found")

    def toolbar_of_type(self, cls):
        return [w for w in self.widgets(cls)
                if isinstance(w.master, ttk.LabelFrame)]

    # ---- 1. construction ---------------------------------------------------
    def test_build_withdrawn_window_layout(self):
        gui = self.make_gui()
        self.assertEqual(gui.state(), "withdrawn")
        self.assertEqual(gui.title(), "ALT RADAR — READ-ONLY MEXC scanner")

        # signals table: exact column ids and headings
        self.assertEqual(tuple(gui.tree["columns"]), SIGNAL_COLUMNS)
        for col in SIGNAL_COLUMNS:
            self.assertEqual(gui.tree.heading(col, "text"), SIGNAL_HEADINGS[col])

        labels = self.labels()
        self.assertEqual(labels[0], "ALT RADAR · MEXC perp scanner")
        self.assertIn(" READ-ONLY ", labels)              # permanent badge
        self.assertIn("TIER-2 UNVALIDATED", labels)
        for text in ("$", "Coins", "Interval s", "Dir", "Sort"):
            self.assertIn(text, labels)
        groups = {str(w.cget("text")) for w in self.widgets(ttk.LabelFrame)}
        for text in ("Scan", "Budget", "Stake", "Find", "View",
                     "Flag threshold", "Lists", "Data"):
            self.assertIn(text, groups)

        btn_texts = [str(w.cget("text")) for w in self.widgets(ttk.Button)]
        for text in ("Scan now", "Resume auto-scan", "Collect bars",
                     "Resolve outcomes", "Refresh stats", "Resolve now"):
            self.assertIn(text, btn_texts)
        self.assertEqual(btn_texts.count("Apply"), 2)      # stake + threshold

        # initial operator config
        self.assertEqual(gui.var_stake.get(), "0.1")
        self.assertEqual(gui.var_threshold.get(), "24")
        self.assertEqual(gui.var_coins.get(), "150")
        self.assertEqual(gui.var_interval.get(), "60")
        self.assertEqual(gui.var_dir.get(), "Both")
        self.assertEqual(gui.var_sort.get(), "score")

        dir_cb, sort_cb = self.toolbar_of_type(ttk.Combobox)
        self.assertEqual(tuple(dir_cb.cget("values")), DIR_CHOICES)
        self.assertEqual(tuple(sort_cb.cget("values")),
                         ("score", "early", "lean", "move", "vol"))

        # panes, status bar, detail placeholder
        frame_texts = [str(w.cget("text")) for w in self.widgets(ttk.LabelFrame)]
        self.assertIn("Signals", frame_texts)
        self.assertTrue(any(t.startswith("VETOED — excluded from ranking") for t in frame_texts))
        self.assertIn("Detail — selected coin (review only, no orders)", frame_texts)
        self.assertIn("Outcomes", frame_texts)
        # the ctor immediately submits its offline "stats" job -> activity
        self.assertEqual(gui.var_activity.get(), "activity: refreshing stats")
        self.assertEqual(gui.var_failed.get(), "failed 0/0 of last scan")
        self.assertIn("select a row for the full breakdown",
                      gui.detail.get("1.0", "end"))
        self.assertEqual(str(gui.detail.cget("state")), "disabled")
        self.assertEqual(gui.var_outcomes.get(), "no outcomes resolved yet")
        self.assertTrue(str(gui.bind("<Control-q>")))     # Ctrl+Q quit

    # ---- 2. worker → poll → render round-trip ------------------------------
    def test_stats_job_round_trips_through_the_worker(self):
        gui = self.make_gui()      # ctor queued an offline "stats" job
        self.assertTrue(self.pump_until(lambda: "rows" in gui.var_header.get()),
                        "stats result never reached the UI")
        header = gui.var_header.get()
        for frag in ("universe 0", "shown 0", "vetoed 0", "stake $0.10",
                     "MEXC –", "Bybit –", "logs 0 rows / 0 coins",
                     "flagged 0", "outcomes 0"):
            self.assertIn(frag, header, header)
        self.assertEqual(gui.scan_status, "ready")
        self.assertEqual(gui.var_statusline.get(), "ready")
        self.assertEqual(gui.var_activity.get(), "activity: idle")

    # ---- 3. table rendering ------------------------------------------------
    def test_set_cards_renders_ranked_rows(self):
        gui = self.make_gui()
        cards = [
            mkcard("AAA", score=70, direction="LONG", venue="BYBIT",
                   min_notional=50.0, price=1234.5, change_24h_pct=3.2,
                   quote_vol_24h=4_300_000, funding_rate=0.0001,
                   oi_change_pct=12.5, lean=0.5, earlyness=0.8),
            mkcard("BBB", score=40, direction="SHORT", venue="MEXC",
                   min_notional=None, price=0.000012, change_24h_pct=-1.5,
                   quote_vol_24h=600_000, funding_rate=-0.0002,
                   oi_change_pct=None, lean=-0.3, earlyness=0.2),
        ]
        gui.set_cards(cards)
        self.assertEqual(gui.tree.get_children(), ("AAA", "BBB"))   # score desc
        self.assertEqual(gui.tree.item("AAA", "values"),
                         ("1", "▲", "AAA", "1234.5", "+3.2%", "$4.3M", "+0.0100%",
                          "+12.5%", "+0.50", "0.80", "70.0", "–", "WATCH"))
        self.assertEqual(gui.tree.item("BBB", "values"),
                         ("2", "▼", "BBB", "0.000012", "-1.5%", "$600K", "-0.0200%",
                          "n/a", "-0.30", "0.20", "40.0", "–", "UNVALIDATED"))
        # A card carrying a shadow score shows it, labeled, without changing
        # the rank order (the live score still sorts the table).
        shadowed = mkcard("CCC", score=55, direction="LONG")
        shadowed.score_v3, shadowed.direction_v3 = 81.4, "LONG"
        gui.set_cards(cards + [shadowed])
        self.assertEqual(gui.tree.get_children(), ("AAA", "CCC", "BBB"))
        self.assertEqual(gui.tree.item("CCC", "values")[-2], "81.4 ▲")
        self.assertEqual(gui.tree.heading("shadow")["text"], "v3 SHADOW")
        gui.set_cards(cards)
        aaa_tags = set(gui.tree.item("AAA", "tags"))
        self.assertTrue(any(t.startswith("long") for t in aaa_tags), aaa_tags)
        self.assertIn("watch", aaa_tags)
        bbb_tags = set(gui.tree.item("BBB", "tags"))
        self.assertTrue(any(t.startswith("short") for t in bbb_tags), bbb_tags)
        self.assertEqual(gui.scan_status, "cards loaded (no scan)")
        self.assertEqual(gui.error_text, "")
        self.assertIn("shown 2 · vetoed 0", gui.var_header.get())
        self.assertEqual(gui.tree_vetoed.get_children(), ())

    def test_vetoed_list_populates_and_leaves_the_ranked_table(self):
        gui = self.make_gui()
        cards = [mkcard("AAA", score=70),
                 mkcard("CCC", score=20, vetoes=("late_move", "thin_book")),
                 mkcard("DDD", score=30, vetoes=("stale",))]
        gui.set_cards(cards)
        self.assertEqual(gui.tree.get_children(), ("AAA",))
        # vetoed: sorted like everything else, codes comma-joined
        self.assertEqual(gui.tree_vetoed.get_children(), ("DDD", "CCC"))
        self.assertEqual(gui.tree_vetoed.item("DDD", "values"),
                         ("▲", "DDD", "30.0", "stale"))
        self.assertEqual(gui.tree_vetoed.item("CCC", "values"),
                         ("▲", "CCC", "20.0", "late_move,thin_book"))
        self.assertTrue(all(t.startswith("vetoed")
                              for t in gui.tree_vetoed.item("CCC", "tags")))
        self.assertIn("shown 1 · vetoed 2", gui.var_header.get())

        # vetoed-only list: ranked table empties, vetoed section still fills
        gui.set_cards([mkcard("CCC", score=20, vetoes=("late_move", "thin_book")),
                       mkcard("DDD", score=30, vetoes=("stale",))])
        self.assertEqual(gui.tree.get_children(), ())
        self.assertEqual(gui.tree_vetoed.get_children(), ("DDD", "CCC"))
        self.assertIn("shown 0 · vetoed 2", gui.var_header.get())

    def test_empty_cards_clear_the_table(self):
        gui = self.make_gui()
        gui.set_cards([mkcard("AAA", score=70)])
        self.assertEqual(gui.tree.get_children(), ("AAA",))
        gui.set_cards([])
        self.assertEqual(gui.tree.get_children(), ())
        self.assertEqual(gui.tree_vetoed.get_children(), ())
        self.assertIn("shown 0 · vetoed 0", gui.var_header.get())

    # ---- 4. selection drives the detail pane -------------------------------
    def test_select_row_updates_detail(self):
        gui = self.make_gui()
        gui.set_cards([mkcard("AAA", score=70),
                       mkcard("BBB", score=40),
                       mkcard("CCC", score=20, vetoes=("late_move",))])
        self.assertIn("select a row for the full breakdown", gui.detail.get("1.0", "end"))

        # seed AAA's plan result so no plan job would even be queued
        gui._plan_cache["AAA"] = {"plan": None, "err": "offline test"}
        gui.tree.selection_set("AAA")
        gui.tree.event_generate("<<TreeviewSelect>>", when="now")
        self.assertEqual(gui.selected_coin, "AAA")
        det = gui.detail.get("1.0", "end")
        self.assertIn("AAA  ▲ LONG", det)
        self.assertIn("TRADE PLAN (review only — this app places no orders)", det)
        self.assertIn("no plan: offline test", det)
        self.assertNotIn("fetching", det)

        # uncached coin: plan job is queued (worker retired -> cannot run) and
        # the pane shows the fetching state
        gui.tree.selection_set("BBB")
        gui.tree.event_generate("<<TreeviewSelect>>", when="now")
        self.assertEqual(gui.selected_coin, "BBB")
        self.assertIn("no plan available (fetching…)", gui.detail.get("1.0", "end"))
        self.assertIn("BBB", gui._plans_pending)

        # selecting in the vetoed list clears the signals selection (and vice versa)
        gui.tree_vetoed.selection_set("CCC")
        gui.tree_vetoed.event_generate("<<TreeviewSelect>>", when="now")
        self.assertEqual(gui.selected_coin, "CCC")
        self.assertEqual(gui.tree.selection(), ())
        det = gui.detail.get("1.0", "end")
        self.assertIn("VETOES", det)
        self.assertIn("⨯ late_move: test reason", det)

    # ---- 4b. plan lane never blocks behind scans (regression) ---------------
    def _drain(self, q):
        out = []
        try:
            while True:
                out.append(q.get_nowait())
        except Exception:
            pass
        return out

    def test_plan_uses_dedicated_lane(self):
        """Plan fetches must not queue behind ~50s scans (worker lane).

        Regression test: clicks used to sit in the scan queue ("fetching…"
        until the running scan finished and the next one re-wiped the cache).
        """
        gui = self.make_gui()
        self._drain(gui._jobs)
        self._drain(gui._plan_jobs)
        gui._ensure_plan("ZZZ")
        plan_jobs = self._drain(gui._plan_jobs)
        scan_jobs = self._drain(gui._jobs)
        self.assertEqual(len(plan_jobs), 1)
        self.assertEqual(plan_jobs[0].get("coin"), "ZZZ")
        self.assertEqual(scan_jobs, [])

    def test_scan_keeps_cached_plan_for_present_coin(self):
        """A finished scan must not wipe a visible plan back to fetching."""
        gui = self.make_gui()
        gui._plan_cache["AAA"] = {"plan": None, "err": "old"}
        gui._plan_cache["ZZZ"] = {"plan": None, "err": "gone"}
        gui.selected_coin = "AAA"
        self._drain(gui._plan_jobs)
        gui._on_scan({"universe": 2, "stats": dict(gui.stats),
                      "cards": [mkcard("AAA", score=70),
                                mkcard("BBB", score=40)],
                      "failed": 0, "errors": {}, "status": "test scan"})
        # present coin: old plan stays visible (no flash to fetching…)
        self.assertIn("AAA", gui._plan_cache)
        self.assertEqual(gui._plan_cache["AAA"]["err"], "old")
        # departed coin: cache entry dropped
        self.assertNotIn("ZZZ", gui._plan_cache)
        # selected coin: silent background refresh queued on the plan lane
        refresh = [j for j in self._drain(gui._plan_jobs)
                   if j.get("coin") == "AAA"]
        self.assertEqual(len(refresh), 1)
        det = gui.detail.get("1.0", "end")
        self.assertNotIn("fetching", det)

    # ---- 4d. instant launch + scan prune ---------------------------------
    def test_launch_shows_saved_snapshot(self):
        from proto.store import Store
        db = os.path.join(self.tmp, "gui.db")
        store = Store(db)
        try:
            sc = mkcard("AAA", score=70)
            sc.magnitude_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
            sc.lean_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
            sid = store.log_signal(sc, flagged=True)
            store.upsert_current(sc, True, sid)
            bbb = mkcard("BBB", score=40, vetoes=("late_move",))
            sid_b = store.log_signal(bbb, flagged=False)
            store.upsert_current(bbb, False, sid_b)
        finally:
            store.close()
        gui = self.make_gui()
        # DB snapshot arrives asynchronously via the worker result queue
        # (production polls it every 120ms); pump until it lands.
        self.assertTrue(
            self.pump_until(lambda: "AAA" in gui.tree.get_children()),
            "worker disk_snapshot never rendered")
        self.assertIn("BBB", gui.tree_vetoed.get_children())
        self.assertIn("showing saved", gui.scan_status)
        self.assertIn("live scan running", gui.scan_status)

    def test_launch_empty_db_waits_for_scan(self):
        gui = self.make_gui()
        self.assertEqual(gui.tree.get_children(), ())
        self.assertIn("starting", gui.scan_status)

    def test_close_writes_and_relaunch_reads_snapshot(self):
        from proto import snapshot as snap
        gui = self.make_gui()
        gui.set_cards([mkcard("AAA", score=70), mkcard("BBB", score=40)])
        gui._plan_cache["AAA"] = {"plan": None, "err": "offline test"}
        gui._on_close()
        self.assertTrue(os.path.exists(snap.path_for(gui.db)))
        # relaunch with the same db: exact screen back, instantly, no scan
        gui2 = self.make_gui()
        self.assertIn("AAA", gui2.tree.get_children())
        self.assertIn("BBB", gui2.tree.get_children())
        self.assertIn("showing saved", gui2.scan_status)
        self.assertIn("live scan running", gui2.scan_status)

    def test_hit_stats_stream(self):
        gui = self.make_gui()
        # scan results carry plan stats -> panel line without manual refresh
        gui._on_scan({"universe": 1, "stats": dict(gui.stats),
                      "cards": [mkcard("AAA", score=70)],
                      "failed": 0, "errors": {}, "status": "s",
                      "plan": {"planned": 10, "stop_hit": 3, "tp1_hit": 5,
                               "tp2_hit": 1, "stop_pct": 30.0,
                               "tp1_pct": 50.0, "tp2_pct": 10.0}})
        panel = gui.var_outcomes.get()
        self.assertIn("stop 3 (30%)", panel)
        self.assertIn("TP1 5 (50%)", panel)
        self.assertIn("TP2 1 (10%)", panel)
        # auto tick refreshes stats on its own every 30s (no network job)
        gui._busy.discard("scan")
        gui.auto_scan = False
        gui._last_stats_ts = 0.0
        gui._tick()
        self.assertIn("stats", gui._busy)
        # single _on_stats definition (duplicate-handler regression guard)
        with open(os.path.join(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))),
                "gui", "app.py")) as f:
            src = f.read()
        self.assertEqual(src.count("def _on_stats"), 1)

    def test_find_box_filters_table(self):
        gui = self.make_gui()
        gui.set_cards([mkcard("QNT", score=70), mkcard("BTC", score=60)])
        self.assertIn("QNT", gui.tree.get_children())
        gui.var_search.set("btc")
        gui._on_search_change()
        self.assertNotIn("QNT", gui.tree.get_children())
        self.assertIn("BTC", gui.tree.get_children())
        gui.var_search.set("")
        gui._on_search_change()
        self.assertIn("QNT", gui.tree.get_children())

    def test_find_enter_selects_table_match(self):
        gui = self.make_gui()
        gui.set_cards([mkcard("QNT", score=70), mkcard("BTC", score=60)])
        gui.var_search.set("qnt")
        gui._on_search_commit()
        self.assertEqual(gui.selected_coin, "QNT")
        self.assertNotIn("lookup", gui._busy)

    def test_find_enter_without_match_submits_lookup(self):
        gui = self.make_gui()
        gui.set_cards([mkcard("BTC", score=60)])
        gui.var_search.set("qnt")
        gui._on_search_commit()
        self.assertIn("lookup", gui._busy)
        self.assertIn("looking up QNT", gui.scan_status)

    def test_lookup_result_joins_table(self):
        gui = self.make_gui()
        gui.set_cards([mkcard("BTC", score=60)])
        gui._on_lookup({"kind": "lookup", "ok": True, "coin": "QNT",
                        "card": mkcard("QNT", score=70), "cached": False})
        self.assertIn("QNT", gui.tree.get_children())
        self.assertEqual(gui.selected_coin, "QNT")
        self.assertIn("scored on demand", gui.scan_status)
        gui._on_lookup({"kind": "lookup", "ok": True, "coin": "ZZZ",
                        "card": None})
        self.assertIn("ZZZ", gui.error_text)

    def test_scan_prune_keeps_good_plans(self):
        gui = self.make_gui()
        gui._plan_cache["AAA"] = {"plan": pl.Plan(coin="AAA",
                                                   direction="LONG"),
                                    "err": None}
        gui._plan_cache["ZZZ"] = {"plan": None,
                                  "err": "coin is no longer in the last scan"}
        gui._plan_cache["GEO"] = {"plan": None, "err": "LONG levels out of order"}
        gui.selected_coin = "AAA"
        gui._on_scan({"universe": 3, "stats": dict(gui.stats),
                      "cards": [mkcard("AAA", score=70),
                                mkcard("GEO", score=60),
                                mkcard("BBB", score=40)],
                      "failed": 0, "errors": {}, "status": "test scan"})
        self.assertIn("AAA", gui._plan_cache)     # good plan kept, no flash
        self.assertNotIn("ZZZ", gui._plan_cache)  # departed coin dropped
        # stale-context error refetches; geometry failure stays put
        self.assertNotIn("ZZZ", gui._plans_pending)
        self.assertIn("GEO", gui._plan_cache)
    def test_segment_buttons(self):
        import tkinter as tk
        gui = self.make_gui()
        for label in ("★ Top 10", "👁 Watch", "+ New"):
            self.assertTrue(self.buttons(label), label)

        # empty digest: guidance error, no modal
        tops = [w for w in gui.winfo_children() if isinstance(w, tk.Toplevel)]
        self.assertEqual(tops, [])
        self.buttons("★ Top 10")[0].invoke()
        self.assertIn("no picks yet", gui.error_text)
        self.assertEqual([w for w in gui.winfo_children()
                          if isinstance(w, tk.Toplevel)], [])

        # seeded digest: modal opens with the coins
        gui.digest = {
            "picks": [mkcard("AAA", score=70)],
            "watch": [mkcard("BBB", score=40, min_notional=5.0)],
            "new": [{"coin": "CCC", "first_seen": 1_700_000_000,
                     "score": 60.0, "direction": "SHORT", "ts": 1_700_000_000}],
        }
        for label, coin in (("★ Top 10", "AAA"), ("👁 Watch", "BBB"),
                            ("+ New", "CCC")):
            self.buttons(label)[0].invoke()
            wins = [w for w in gui.winfo_children()
                    if isinstance(w, tk.Toplevel)]
            self.assertEqual(len(wins), 1, label)
            kids = wins[0].winfo_children()
            trees = [w for w in kids
                     if w.winfo_class() == "Treeview"]
            self.assertTrue(trees, label)
            self.assertIn(coin, trees[0].get_children(), label)
            wins[0].destroy()

    # ---- 5. toolbar validation --------------------------------------------
    def test_stake_and_threshold_apply_validation(self):
        gui = self.make_gui()
        stake_btn, thr_btn = self.buttons("Apply")   # creation order

        # -- good stake applies, clears stale plans, announces itself
        gui._plan_cache["AAA"] = {"plan": None, "err": "old"}
        gui.var_stake.set("0.25")
        stake_btn.invoke()
        self.assertEqual(gui.stake, 0.25)
        self.assertEqual(gui.var_stake.get(), "0.25")
        self.assertEqual(gui.error_text, "")
        self.assertEqual(gui.scan_status, "stake $0.25 applied")
        self.assertNotIn("AAA", gui._plan_cache)      # stale plans dropped

        # -- bad stakes rejected, previous value untouched and restored
        for bad in ("0", "-3", "abc"):
            gui.var_stake.set(bad)
            stake_btn.invoke()
            self.assertIn("stake must be a number > 0", gui.error_text, bad)
            self.assertEqual(gui.stake, 0.25, bad)
            self.assertEqual(gui.var_stake.get(), "0.25", bad)
            self.assertEqual(gui.var_statusline.get(), gui.error_text)
            # the LABEL itself must carry the message (regression test for the
            # status-line bug: configure(text=) on a textvariable-bound label)
            self.assertEqual(str(gui.lbl_statusline.cget("text")), gui.error_text)
            self.assertEqual(str(gui.lbl_statusline.cget("foreground")), COLOR_ERROR)

        # -- threshold boundaries: 0 and 100 are valid, outside is rejected
        gui.var_threshold.set("0")
        thr_btn.invoke()
        self.assertEqual(gui.log_threshold, 0.0)
        self.assertEqual(gui.scan_status, "flag threshold 0 applied")
        gui.var_threshold.set("100")
        thr_btn.invoke()
        self.assertEqual(gui.log_threshold, 100.0)
        gui.var_threshold.set("101")
        thr_btn.invoke()
        self.assertIn("log threshold must be between 0 and 100", gui.error_text)
        self.assertEqual(gui.log_threshold, 100.0)
        self.assertEqual(gui.var_threshold.get(), "100")
        gui.var_threshold.set("-1")
        thr_btn.invoke()
        self.assertEqual(gui.log_threshold, 100.0)

    def test_coins_and_interval_commit_validation(self):
        gui = self.make_gui()
        sp_coins, sp_interval = self.toolbar_of_type(ttk.Spinbox)

        # valid commit through the spinbox's bound <FocusOut> handler
        gui.var_coins.set("25")
        sp_coins.event_generate("<FocusOut>", when="now")
        self.assertEqual(gui.coins, 25)
        self.assertEqual(gui.var_coins.get(), "25")
        self.assertEqual(gui.scan_status,
                         "coins 25 applied (universe budget + scan size)")

        # out of range / non-integer rejected, previous value restored
        gui.var_coins.set("5")
        sp_coins.event_generate("<FocusOut>", when="now")
        self.assertIn("coins must be between 10 and 581", gui.error_text)
        self.assertEqual(gui.coins, 25)
        self.assertEqual(gui.var_coins.get(), "25")
        gui.var_coins.set("10.5")
        sp_coins.event_generate("<FocusOut>", when="now")
        self.assertIn("coins must be a whole number", gui.error_text)
        self.assertEqual(gui.coins, 25)

        # interval: 15s is the floor
        gui.var_interval.set("15")
        sp_interval.event_generate("<FocusOut>", when="now")
        self.assertEqual(gui.interval, 15)
        self.assertEqual(gui.scan_status, "auto-scan interval 15s applied")
        gui.var_interval.set("14")
        sp_interval.event_generate("<FocusOut>", when="now")
        self.assertIn("interval must be at least 15s", gui.error_text)
        self.assertEqual(gui.interval, 15)
        self.assertEqual(gui.var_interval.get(), "15")

    # ---- 6. auto-scan pause / resume --------------------------------------
    def test_pause_toggles_auto_scan_and_gates_the_tick(self):
        gui = self.make_gui()
        btn = self.buttons("Resume auto-scan")[0]
        self.assertFalse(gui.auto_scan)

        # paused: even an overdue tick queues nothing
        gui.last_scan_ts = 0.0
        gui._tick()
        self.assertNotIn("scan", gui._busy)
        self.assertTrue(gui._jobs.empty())

        # resume -> tick submits a scan carrying the current operator config
        btn.invoke()
        self.assertTrue(gui.auto_scan)
        self.assertEqual(str(btn.cget("text")), "Pause auto-scan")
        self.assertEqual(gui.scan_status, "auto-scan resumed (60s)")
        gui._tick()
        self.assertIn("scan", gui._busy)
        job = gui._jobs.get_nowait()
        self.assertEqual(job, {"cmd": "scan", "stake": 0.1, "coins": 150,
                               "log_threshold": 24.0})

        # pause again
        btn.invoke()
        self.assertFalse(gui.auto_scan)
        self.assertEqual(str(btn.cget("text")), "Resume auto-scan")
        self.assertEqual(gui.scan_status, "auto-scan paused")

    # ---- 7. dir / sort combos ---------------------------------------------
    def test_dir_and_sort_filters_re_render_the_table(self):
        gui = self.make_gui()
        gui.set_cards([mkcard("L1", score=30, direction="LONG", quote_vol_24h=100),
                       mkcard("S1", score=70, direction="SHORT", quote_vol_24h=1000),
                       mkcard("L2", score=50, direction="LONG", quote_vol_24h=5000)])
        self.assertEqual(gui.tree.get_children(), ("S1", "L2", "L1"))  # score desc

        dir_cb, sort_cb = self.toolbar_of_type(ttk.Combobox)

        gui.var_dir.set("Long")
        dir_cb.event_generate("<<ComboboxSelected>>", when="now")
        self.assertEqual(gui.tree.get_children(), ("L2", "L1"))
        self.assertIn("shown 2", gui.var_header.get())

        gui.var_dir.set("Short")
        dir_cb.event_generate("<<ComboboxSelected>>", when="now")
        self.assertEqual(gui.tree.get_children(), ("S1",))

        gui.var_dir.set("Both")
        dir_cb.event_generate("<<ComboboxSelected>>", when="now")
        self.assertEqual(gui.tree.get_children(), ("S1", "L2", "L1"))

        gui.var_sort.set("vol")
        sort_cb.event_generate("<<ComboboxSelected>>", when="now")
        self.assertEqual(gui.tree.get_children(), ("L2", "S1", "L1"))  # vol desc

        # invalid choices reset the widget and raise a status message
        gui.var_sort.set("bogus")
        sort_cb.event_generate("<<ComboboxSelected>>", when="now")
        self.assertEqual(gui.var_sort.get(), "score")
        self.assertIn("unknown sort key: 'bogus'", gui.error_text)
        gui.var_dir.set("sideways")
        dir_cb.event_generate("<<ComboboxSelected>>", when="now")
        self.assertEqual(gui.var_dir.get(), "Both")
        self.assertIn("unknown direction filter: 'sideways'", gui.error_text)

    # ---- 8. failure handling ----------------------------------------------
    def test_scan_failure_keeps_the_previous_table(self):
        gui = self.make_gui()
        gui.set_cards([mkcard("AAA", score=70), mkcard("BBB", score=40)])
        before = gui.tree.get_children()
        gui._handle_msg({
            "kind": "scan", "ok": False, "error": "URLError: timed out",
            "venue": {"mexc": {"ok": False, "fails": 3, "last_err": "boom"},
                      "bybit": {"ok": True, "fails": 0, "last_err": None}},
        })
        self.assertEqual(gui.tree.get_children(), before)          # table kept
        self.assertEqual(len(gui.cards), 2)
        self.assertEqual(gui.scan_status,
                         "scan failed — keeping previous table (URLError: timed out)")
        self.assertEqual(gui.error_text, "scan failed: URLError: timed out")
        self.assertFalse(gui.mexc_ok)
        header = gui.var_header.get()
        self.assertIn("MEXC DEGRADED", header)
        self.assertIn("Bybit ok", header)
        self.assertEqual(str(gui.lbl_statusline.cget("foreground")), COLOR_ERROR)

    def test_failed_universe_refresh_keeps_the_previous_table(self):
        gui = self.make_gui()
        gui.set_cards([mkcard("AAA", score=70)])
        gui._handle_msg({
            "kind": "scan", "ok": True, "cards": [], "universe": 0, "failed": 0,
            "errors": {}, "status": "scanned 0", "universe_error": "MEXC down",
            "stats": None, "venue": {},
        })
        self.assertEqual(gui.tree.get_children(), ("AAA",))
        self.assertIn("universe unavailable (MEXC down)", gui.error_text)
        self.assertIn("keeping previous table", gui.error_text)
        self.assertEqual(gui.scan_status, "scan failed, showing previous table")

    # ---- 9. outcomes panel -------------------------------------------------
    def test_outcomes_panel_renders_stats_and_results(self):
        gui = self.make_gui()
        stats = {"rows": 10, "flagged": 2, "coins": 5, "longs": 4,
                 "shorts": 3, "outcomes": 3}
        outcome = {
            "counts": {"1h": 1, "4h": 2, "24h": 0, "7d": 0}, "total": 3,
            "direction": {"LONG": {"count": 2, "avg_return": 5.5, "hit_rate": 0.5},
                          "SHORT": {"count": 1, "avg_return": -2.0, "hit_rate": 0.0}},
        }
        gui._handle_msg({"kind": "stats", "ok": True, "stats": stats,
                         "outcome": outcome})
        panel = gui.var_outcomes.get()
        self.assertIn("1h 1 · 4h 2 · 24h 0 · 7d 0   (total 3 resolved)", panel)
        self.assertIn("LONG: n=2 · avg signed return +5.50% · hit rate 50%", panel)
        self.assertIn("SHORT: n=1 · avg signed return -2.00% · hit rate 0%", panel)
        for frag in ("logs 10 rows / 5 coins", "flagged 2", "outcomes 3"):
            self.assertIn(frag, gui.var_header.get())

        # resolve result: status line + zero-shape panel
        gui._handle_msg({"kind": "resolve", "ok": True, "resolved": 2,
                         "stats": stats, "outcome": zero_outcome()})
        self.assertEqual(gui.scan_status, "resolved 2 pending outcome(s)")
        panel = gui.var_outcomes.get()
        self.assertIn("(total 0 resolved)", panel)
        self.assertIn("LONG: no resolved outcomes yet", panel)
        self.assertIn("SHORT: no resolved outcomes yet", panel)

        # collect result
        gui._handle_msg({"kind": "collect", "ok": True, "coins": 42, "bars": 99})
        self.assertEqual(gui.scan_status,
                         "collected 5m bars for 42 coins (+99 new bars)")

        # generic failure surfaces in the status bar, never crashes
        gui._handle_msg({"kind": "stats", "ok": False, "error": "db locked"})
        self.assertEqual(gui.error_text, "stats failed: db locked")
        self.assertEqual(gui.var_statusline.get(), "stats failed: db locked")


if __name__ == "__main__":
    unittest.main(verbosity=2)
