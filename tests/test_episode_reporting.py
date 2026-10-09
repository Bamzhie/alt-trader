"""
Episode reporting tests: denominators, cohort separation, concentration,
bootstrap threshold, forbidden net-P&L fields.

Run: python3 tests/test_episode_reporting.py
"""

import os
import re
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.store import Store
from proto.scorer import Scorecard
from proto.planner import Plan
from proto.measurement import (
    config_snapshot,
    ObservationClass,
    ObservationEvent,
    process_observation,
)
from proto import episode_report


VERSIONS = {"flag_rule": 2, "plan_rule": "v1.0.0",
            "episode_rule": "v2.0.0", "outcome_rule": "v2.0.0",
            "cost_model": "v0.1.0-unstable"}


def mkcard(coin, direction="LONG", score=50.0, price=100.0):
    sc = Scorecard(coin=coin, direction=direction, score=score, price=price)
    sc.magnitude_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
    sc.lean_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
    sc.vetoes = []
    sc.tradeable = True
    return sc


def mkplan(coin, direction="LONG"):
    if direction == "LONG":
        return Plan(coin=coin, direction=direction, entry_low=99.9,
                    entry_high=100.1, stop=98.0, tp1=102.0, tp2=105.0,
                    leverage=10, notional=1.0, max_loss=0.1)
    return Plan(coin=coin, direction=direction, entry_low=99.9,
                entry_high=100.1, stop=102.0, tp1=98.0, tp2=95.0,
                leverage=10, notional=1.0, max_loss=0.1)


class EpisodeReportingTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ep-report-")
        self.store = Store(os.path.join(self.tmp, "t.db"))
        self.cfg, self.cfg_json = config_snapshot(
            stake=0.1, log_threshold=24.0, leverage_cap=10,
            universe_budget=150)
        self.other_cfg, self.other_json = config_snapshot(
            stake=0.5, log_threshold=24.0, leverage_cap=10,
            universe_budget=150)

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- helpers
    def start_episode(self, coin, direction="LONG", obs_ts=1000.0,
                      cfg=None, cfg_json=None, score=50.0, with_plan=True,
                      versions=None):
        """One QUALIFYING observation -> one new episode. Returns episode id."""
        cfg = cfg or self.cfg
        cfg_json = cfg_json or self.cfg_json
        versions = versions or VERSIONS
        sc = mkcard(coin, direction, score=score)
        sid = self.store.log_signal(sc, flagged=True)
        plan = mkplan(coin, direction) if with_plan else None
        res = process_observation(
            self.store,
            ObservationEvent.qualifying(coin, "MEXC", cfg, obs_ts, sid),
            plan if with_plan else None,
            versions, cfg_json, card=sc)
        return res["episode_id"]

    def set_state(self, episode_id, state, close_reason=None, after_gap=0):
        self.store.conn.execute(
            "UPDATE signal_episode SET state=?, close_reason=?, after_gap=?"
            " WHERE id=?",
            (state, close_reason, after_gap, episode_id))
        self.store.conn.commit()

    def add_outcome(self, episode_id, entry_status, trade_status=None,
                    tp1_before_stop=0, stop_bar_idx=None, tp2_bar_idx=None):
        self.store.conn.execute(
            """INSERT INTO episode_outcome
                   (episode_id, entry_status, fill_ts, trade_status,
                    tp1_before_stop, stop_bar_idx, tp2_bar_idx)
               VALUES (?,?,?,?,?,?,?)""",
            (episode_id, entry_status, 2000, trade_status, tp1_before_stop,
             stop_bar_idx, tp2_bar_idx))
        self.store.conn.commit()

    def add_horizon(self, episode_id, horizon, status):
        self.store.conn.execute(
            "INSERT INTO episode_horizon (episode_id, horizon, status)"
            " VALUES (?,?,?)", (episode_id, horizon, status))
        self.store.conn.commit()


class TestSummaryCounts(EpisodeReportingTestCase):
    """Counts carry explicit denominators (spec 11)."""

    def test_summary_counts_with_explicit_denominators(self):
        # started=6: 1 NO_PLAN, 1 PENDING_ENTRY, 3 FILLED (stop/tp2/expiry),
        # 1 UNFILLED.
        noplan = self.start_episode("A", with_plan=False)
        pending = self.start_episode("B")
        stopped = self.start_episode("C")
        tp2 = self.start_episode("D")
        expired = self.start_episode("E")
        unfilled = self.start_episode("F")
        self.add_outcome(pending, "PENDING_ENTRY", "OPEN")
        self.add_outcome(stopped, "FILLED", "STOPPED", tp1_before_stop=1)
        self.add_outcome(tp2, "FILLED", "TP2")
        self.add_outcome(expired, "FILLED", "EXPIRED")
        self.add_outcome(unfilled, "UNFILLED", None)

        s = episode_report.summary(self.store)
        self.assertEqual(s["started"], 6)
        self.assertEqual(s["plan"]["NO_PLAN"], 1)
        self.assertEqual(s["plan"]["PLANNED"], 5)
        self.assertEqual(s["entry"]["FILLED"], 3)
        self.assertEqual(s["entry"]["UNFILLED"], 1)
        self.assertEqual(s["entry"]["PENDING_ENTRY"], 1)
        self.assertEqual(s["trade"]["STOPPED"], 1)
        self.assertEqual(s["trade"]["TP2"], 1)
        self.assertEqual(s["trade"]["EXPIRED"], 1)
        self.assertEqual(s["trade"]["tp1_before_stop"], 1)
        # Every count block names its denominator.
        self.assertEqual(s["entry"]["denominator"], "planned episodes")
        self.assertEqual(s["trade"]["denominator"], "filled plans")
    def test_fill_rate_denominator_is_filled_plus_unfilled(self):
        a = self.start_episode("A")   # FILLED
        b = self.start_episode("B")   # FILLED
        c = self.start_episode("C")   # UNFILLED
        d = self.start_episode("D")   # PENDING_ENTRY (excluded)
        self.add_outcome(a, "FILLED", "EXPIRED")
        self.add_outcome(b, "FILLED", "STOPPED")
        self.add_outcome(c, "UNFILLED", None)
        self.add_outcome(d, "PENDING_ENTRY", "OPEN")

        s = episode_report.summary(self.store)
        fill = s["rates"]["fill_rate"]
        self.assertEqual(fill["numerator"], 2)
        self.assertEqual(fill["denominator_count"], 3)
        self.assertEqual(fill["denominator"], "filled + unfilled plans")
        self.assertAlmostEqual(fill["pct"], 100.0 * 2 / 3)
        # PENDING_ENTRY is neither a loss nor in the fill-rate denominator.
        unf = s["rates"]["unfilled_rate"]
        self.assertEqual(unf["denominator"], "started")
        self.assertEqual(unf["denominator_count"], 4)
        self.assertAlmostEqual(unf["pct"], 100.0 * 1 / 4)

    def test_terminal_rates_use_terminal_filled_denominator(self):
        a = self.start_episode("A")
        b = self.start_episode("B")
        c = self.start_episode("C")
        d = self.start_episode("D")   # still OPEN: not terminal
        self.add_outcome(a, "FILLED", "STOPPED", stop_bar_idx=1)
        self.add_outcome(b, "FILLED", "TP2", tp2_bar_idx=5)
        self.add_outcome(c, "FILLED", "EXPIRED")
        self.add_outcome(d, "FILLED", "OPEN")

        s = episode_report.summary(self.store)
        self.assertEqual(s["rates"]["terminal_filled"], 3)
        for name in ("stop_rate", "tp2_rate", "expiry_rate"):
            r = s["rates"][name]
            self.assertEqual(r["denominator"], "terminal filled plans")
            self.assertEqual(r["denominator_count"], 3)
            self.assertAlmostEqual(r["pct"], 100.0 / 3)
        to_date = s["rates"]["to_date_touch"]
        self.assertIn("to date", to_date["label"])
        self.assertEqual(to_date["denominator"], "filled plans")
        self.assertEqual(to_date["filled"], 4)
        self.assertEqual(to_date["stop_touches"], 1)
        self.assertEqual(to_date["tp2_touches"], 1)

    def test_pending_and_unavailable_excluded_from_rates(self):
        a = self.start_episode("A")
        b = self.start_episode("B")
        self.add_outcome(a, "UNAVAILABLE", "UNAVAILABLE")
        self.add_outcome(b, "PENDING_ENTRY", "OPEN")
        s = episode_report.summary(self.store)
        fill = s["rates"]["fill_rate"]
        self.assertEqual(fill["denominator_count"], 0)
        self.assertEqual(fill["denominator"], "filled + unfilled plans")
        self.assertEqual(fill["pct"], 0.0)
        self.assertEqual(s["rates"]["terminal_filled"], 0)

    def test_horizon_matured_pending_unavailable_reported_separately(self):
        a = self.start_episode("A")
        b = self.start_episode("B")
        c = self.start_episode("C")
        self.add_outcome(a, "FILLED", "EXPIRED")
        self.add_outcome(b, "FILLED", "STOPPED")
        self.add_outcome(c, "FILLED", "TP2")
        self.add_horizon(a, "1h", "MATURED")
        self.add_horizon(b, "1h", "PENDING")
        self.add_horizon(c, "4h", "UNAVAILABLE")

        s = episode_report.summary(self.store)
        h1 = s["horizons"]["1h"]
        self.assertEqual((h1["matured"], h1["pending"], h1["unavailable"]),
                         (1, 1, 0))
        h4 = s["horizons"]["4h"]
        self.assertEqual((h4["matured"], h4["pending"], h4["unavailable"]),
                         (0, 0, 1))
        self.assertEqual(h1["denominator"], "filled plans with horizon rows")

    def test_lifecycle_and_after_gap_counts(self):
        a = self.start_episode("A")
        b = self.start_episode("B")
        c = self.start_episode("C")
        self.set_state(a, "CLOSED", "REARM_CONFIRMED")
        self.set_state(b, "CLOSED", "COVERAGE_LOST")
        self.set_state(c, "CLOSED", "REVERSAL", after_gap=1)
        s = episode_report.summary(self.store)
        self.assertEqual(s["started"], 3)
        self.assertEqual(s["open"]["count"], 0)
        self.assertEqual(s["closed"]["count"], 3)
        self.assertEqual(s["closed"]["by_reason"]["REARM_CONFIRMED"], 1)
        self.assertEqual(s["closed"]["by_reason"]["COVERAGE_LOST"], 1)
        self.assertEqual(s["closed"]["by_reason"]["REVERSAL"], 1)
        self.assertEqual(s["after_gap"]["count"], 1)
        self.assertEqual(s["after_gap"]["denominator"], "started")

    def test_open_episodes_counted_open_or_rearming(self):
        a = self.start_episode("A")
        b = self.start_episode("B")
        self.set_state(b, "REARMING")
        s = episode_report.summary(self.store)
        self.assertEqual(s["open"]["count"], 2)
        self.assertEqual(s["open"]["OPEN"], 1)
        self.assertEqual(s["open"]["REARMING"], 1)
        self.assertEqual(s["open"]["denominator"], "started")

    def test_empty_database_reports_zeroed_shape(self):
        s = episode_report.summary(self.store)
        self.assertEqual(s["started"], 0)
        self.assertEqual(s["rates"]["fill_rate"]["denominator_count"], 0)
        self.assertEqual(s["rates"]["fill_rate"]["denominator"],
                         "filled + unfilled plans")
        self.assertIsNone(s["statistics"]["bootstrap"])
        self.assertEqual(episode_report.breakdowns(self.store,
                                                   dimension="direction"), [])

    def test_coverage_reports_gap_indicators_per_coin(self):
        a = self.start_episode("A", obs_ts=1000.0)
        # two valid observations 20 minutes apart: a coverage gap > 15m.
        sc = mkcard("A", "LONG", score=55.0)
        sid = self.store.log_signal(sc, flagged=True)
        process_observation(
            self.store,
            ObservationEvent.qualifying("A", "MEXC", self.cfg, 2200.0, sid),
            mkplan("A"), VERSIONS, self.cfg_json, card=sc)
        s = episode_report.summary(self.store)
        cov = s["coverage"]
        self.assertEqual(cov["coins"], 1)
        # The gap closes the first episode and the second observation opens
        # a new one; coverage measures the data stream, not the episode.
        self.assertEqual(cov["max_observation_gap_s"], 1200)
        self.assertEqual(cov["observation_gaps_over_15m"], 1)
        self.assertEqual(s["started"], 2)
        self.assertEqual(cov["denominator"], "cohort episodes")


class TestCohortSeparation(EpisodeReportingTestCase):
    """Episodes never merge across config_hash or rule versions (spec 11)."""

    def test_config_hash_filter_separates_cohorts(self):
        self.start_episode("A", cfg=self.cfg, cfg_json=self.cfg_json)
        self.start_episode("B", cfg=self.other_cfg, cfg_json=self.other_json)
        s = episode_report.summary(self.store, config_hash=self.cfg)
        self.assertEqual(s["started"], 1)
        self.assertEqual(s["cohort"]["config_hash"], self.cfg)

    def test_mixed_configs_are_listed_and_warned(self):
        self.start_episode("A", cfg=self.cfg, cfg_json=self.cfg_json)
        self.start_episode("B", cfg=self.other_cfg, cfg_json=self.other_json)
        s = episode_report.summary(self.store)
        self.assertTrue(s["cohort"]["mixed_configs_warning"])
        self.assertEqual(len(s["cohort"]["config_hashes_seen"]), 2)
        self.assertEqual(len(s["by_cohort"]), 2)
        self.assertEqual(sum(c["report"]["started"]
                             for c in s["by_cohort"]), 2)

    def test_rule_version_filter_separates_cohorts(self):
        self.start_episode("A", versions=VERSIONS)
        old = dict(VERSIONS, episode_rule="v1.9.0")
        self.start_episode("B", versions=old)
        s = episode_report.summary(
            self.store, rule_versions={"episode_rule": "v1.9.0"})
        self.assertEqual(s["started"], 1)
        s2 = episode_report.summary(self.store,
                                    rule_versions={"episode_rule": "v2.0.0"})
        self.assertEqual(s2["started"], 1)

    def test_legacy_signal_rows_never_enter_episode_metrics(self):
        # A legacy signal_log row without an episode must not be counted.
        self.store.log_signal(mkcard("LEGACY"), flagged=True)
        self.start_episode("A")
        s = episode_report.summary(self.store)
        self.assertEqual(s["started"], 1)


class TestConcentration(EpisodeReportingTestCase):
    """Altcoins co-move: report top-coin and busiest-day concentration."""

    def test_top_coin_and_busiest_day_concentration(self):
        # X: two same-day observations 5 minutes apart = ONE episode; a
        # next-day observation is a >15m gap, so a second episode starts
        # (after_gap). Y: one episode on the second day.
        for ts in (1704067200, 1704067500):   # 2024-01-01 UTC
            self.start_episode("X", obs_ts=float(ts))
        self.start_episode("X", obs_ts=1704153600.0)  # 2024-01-02 UTC
        self.start_episode("Y", obs_ts=1704153600.0)  # 2024-01-02 UTC
        s = episode_report.summary(self.store)
        conc = s["concentration"]
        self.assertEqual(s["started"], 3)
        self.assertEqual(conc["top_coin"], "X")
        self.assertAlmostEqual(conc["top_coin_share_pct"], 100.0 * 2 / 3)
        self.assertEqual(conc["busiest_utc_day"], "2024-01-02")
        self.assertAlmostEqual(conc["busiest_day_share_pct"], 100.0 * 2 / 3)
        self.assertEqual(conc["denominator"], "started")

    def test_after_gap_episode_counted_separately(self):
        # Same coin, two days apart: the second episode is after_gap.
        self.start_episode("X", obs_ts=1704067200.0)
        self.start_episode("X", obs_ts=1704153600.0)
        s = episode_report.summary(self.store)
        self.assertEqual(s["started"], 2)
        self.assertEqual(s["after_gap"]["count"], 1)
        self.assertEqual(s["closed"]["count"], 1)   # first closed COVERAGE_LOST
        self.assertEqual(s["closed"]["by_reason"]["COVERAGE_LOST"], 1)


class TestBootstrap(EpisodeReportingTestCase):
    """Coin-cluster bootstrap 95% CI only with >= 30 distinct coins."""

    def _n_coin_cohort(self, n, fill_every=3):
        for i in range(n):
            coin = f"C{i:03d}"
            ep = self.start_episode(coin)
            entry = "FILLED" if (i % fill_every) else "UNFILLED"
            trade = "EXPIRED" if entry == "FILLED" else None
            self.add_outcome(ep, entry, trade)

    def test_no_bootstrap_below_30_coins(self):
        self._n_coin_cohort(29)
        s = episode_report.summary(self.store)
        self.assertEqual(s["statistics"]["coin_clusters"], 29)
        self.assertIsNone(s["statistics"]["bootstrap"])
        self.assertIn("30", s["statistics"]["bootstrap_note"])

    def test_bootstrap_interval_reported_at_30_coins(self):
        self._n_coin_cohort(30)
        s = episode_report.summary(self.store)
        self.assertEqual(s["statistics"]["coin_clusters"], 30)
        boot = s["statistics"]["bootstrap"]
        self.assertIsNotNone(boot)
        self.assertEqual(boot["level"], "95%")
        lo, hi = boot["fill_rate_ci"]
        self.assertLessEqual(lo, hi)
        self.assertGreaterEqual(lo, 0.0)
        self.assertLessEqual(hi, 100.0)
        # deterministic: two calls agree.
        s2 = episode_report.summary(self.store)
        self.assertEqual(boot["fill_rate_ci"],
                         s2["statistics"]["bootstrap"]["fill_rate_ci"])


class TestForbiddenOutput(EpisodeReportingTestCase):
    """No net P&L, net win rate or simulated trade return (spec 10, 14)."""

    FORBIDDEN = re.compile(
        r"pnl|profit|win_rate|net_return|simulated|trade_return|expectancy|"
        r"roi\b|edge_pct|realized_return|total_return", re.I)

    def _walk_keys(self, obj, skip_keys=()):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in skip_keys:
                    continue
                yield str(k)
                yield from self._walk_keys(v, skip_keys)
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                yield from self._walk_keys(v, skip_keys)

    def test_no_forbidden_net_return_fields_anywhere(self):
        a = self.start_episode("A")
        b = self.start_episode("B")
        self.add_outcome(a, "FILLED", "STOPPED", tp1_before_stop=1)
        self.add_outcome(b, "FILLED", "TP2")
        self.add_horizon(a, "1h", "MATURED")
        for i in range(30):
            ep = self.start_episode(f"K{i:03d}")
            self.add_outcome(ep, "FILLED", "EXPIRED")
        payloads = [episode_report.summary(self.store)]
        for dim in ("direction", "score_band", "setup"):
            payloads.append(
                episode_report.breakdowns(self.store, dimension=dim))
        for payload in payloads:
            for key in self._walk_keys(payload, skip_keys=("disclosure",)):
                self.assertIsNone(
                    self.FORBIDDEN.search(key),
                    f"forbidden net-return field: {key!r}")
        # Explicit disclosure of the prohibition. `disclosure` is the one
        # subtree allowed to name net P&L: every key there is a boolean
        # saying it is NOT reported.
        disc = episode_report.summary(self.store)["disclosure"]
        self.assertFalse(disc["net_pnl_reported"])
        self.assertFalse(disc["net_win_rate_reported"])
        self.assertFalse(disc["simulated_trade_return_reported"])
        self.assertIn("exit policy", disc["exit_policy"])

    def test_horizon_output_has_counts_not_returns(self):
        a = self.start_episode("A")
        self.add_outcome(a, "FILLED", "TP2")
        self.add_horizon(a, "1h", "MATURED")
        h = episode_report.summary(self.store)["horizons"]["1h"]
        self.assertEqual(set(h) >= {"matured", "pending", "unavailable"}, True)
        for k, v in h.items():
            self.assertNotIsInstance(v, float,
                                     f"horizon exposes a float: {k}={v}")


class TestBreakdowns(EpisodeReportingTestCase):
    """Exploratory cohorts: direction, score band, setup; min thresholds."""

    def _cohort(self, n_coins, direction="LONG", scores=None):
        for i in range(n_coins):
            coin = f"{direction[0]}{i:03d}"
            score = 50.0 if scores is None else scores[i % len(scores)]
            self.start_episode(coin, direction=direction, score=score)

    def test_direction_cells_meeting_threshold_are_exploratory(self):
        # 20 episodes across 20 coins: over both thresholds.
        for i in range(20):
            self.start_episode(f"L{i:03d}", direction="LONG")
        cells = episode_report.breakdowns(self.store, dimension="direction")
        self.assertEqual(len(cells), 1)
        c = cells[0]
        self.assertEqual(c["value"], "LONG")
        self.assertEqual(c["episodes"], 20)
        self.assertEqual(c["coins"], 20)
        self.assertTrue(c["exploratory"])
        self.assertIn("min_episodes", c)

    def test_cells_below_threshold_are_excluded(self):
        for i in range(3):   # 3 episodes / 3 coins: too small
            self.start_episode(f"S{i}", direction="SHORT")
        cells = episode_report.breakdowns(self.store, dimension="direction")
        self.assertEqual(cells, [])

    def test_score_band_and_setup_dimensions_supported(self):
        # 20 coins per cell: the exploratory threshold is 20 episodes over
        # 10 coins, so one episode per coin is enough only with 20 coins.
        for i in range(20):
            self.start_episode(f"B{i}", score=85.0)          # 80-100 band
            self.start_episode(f"N{i}", score=45.0, with_plan=False)
        bands = episode_report.breakdowns(self.store, dimension="score_band")
        self.assertEqual({c["value"] for c in bands}, {"80-100", "40-59"})
        for c in bands:
            self.assertEqual(c["episodes"], 20)
            self.assertTrue(c["exploratory"])
        setups = episode_report.breakdowns(self.store, dimension="setup")
        self.assertEqual({c["value"] for c in setups},
                         {"PLANNED", "NO_PLAN"})

    def test_unsupported_dimension_is_rejected(self):
        with self.assertRaises(ValueError):
            episode_report.breakdowns(self.store, dimension="venue")

    def test_breakdown_rates_share_summary_denominators(self):
        # 20 episodes across 20 coins so the cell clears the thresholds;
        # 12 FILLED (6 stopped, 6 TP2) and 8 UNFILLED.
        for i in range(20):
            ep = self.start_episode(f"R{i:03d}")
            entry = "FILLED" if i < 12 else "UNFILLED"
            trade = "STOPPED" if i < 6 else ("TP2" if i < 12 else None)
            self.add_outcome(ep, entry, trade)
        cells = episode_report.breakdowns(self.store, dimension="direction")
        self.assertEqual(len(cells), 1)
        r = cells[0]["rates"]
        self.assertEqual(cells[0]["episodes"], 20)
        self.assertEqual(cells[0]["coins"], 20)
        self.assertEqual(r["fill_rate"]["denominator_count"], 20)
        self.assertEqual(r["fill_rate"]["numerator"], 12)
        self.assertEqual(r["terminal_filled"], 12)
        self.assertEqual(r["stop_rate"]["denominator_count"], 12)
        self.assertEqual(r["stop_rate"]["denominator"],
                         "terminal filled plans")


if __name__ == "__main__":
    unittest.main(verbosity=2)
