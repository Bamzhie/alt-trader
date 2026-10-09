"""Regression tests for episode measurement production-path review findings."""

import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import measurement, outcomes
from proto.collector import append_bars
from proto.planner import Plan
from proto.scorer import Scorecard
from proto.store import Store


class EpisodeRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "signals.db"))
        self.cfg, self.cfg_json = measurement.config_snapshot(
            0.1, 24, 10, 0, "mexc_full")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def start_episode(self, ts=1_700_000_000, plan=None):
        card = Scorecard(coin="BTC", direction="LONG", score=80, price=100)
        sid = self.store.log_signal(card, flagged=True)
        event = measurement.ObservationEvent.qualifying(
            "BTC", "MEXC", self.cfg, ts, sid)
        result = measurement.process_observation(
            self.store, event, plan or Plan(
                coin="BTC", direction="LONG", entry_low=99.9,
                entry_high=100.1, stop=98, tp1=104, tp2=110,
                leverage=5, notional=10, max_loss=1),
            {"flag_rule": 2}, self.cfg_json, card=card)
        return result["episode_id"]

    def test_qualifying_episode_freezes_its_plan(self):
        episode_id = self.start_episode()
        row = self.store.episode_plan_row(episode_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["entry_high"], 100.1)

    def test_invalid_plan_is_no_plan(self):
        episode_id = self.start_episode(plan=Plan(coin="BTC", direction="LONG"))
        ep = self.store.open_episode("BTC", self.cfg)
        self.assertEqual(ep["plan_status"], "NO_PLAN")
        self.assertIsNone(self.store.episode_plan_row(episode_id))

    def test_entry_slots_align_to_exchange_five_minute_grid(self):
        t0 = 1_700_000_001
        slots = outcomes.entry_slots(t0)
        self.assertTrue(slots)
        self.assertEqual(slots[0] % 300, 0)
        self.assertGreater(slots[0], t0)
        self.assertLessEqual(slots[0] + 300, t0 + 3600)

    def test_collector_bars_are_normalized_for_episode_resolver(self):
        data_dir = os.path.join(self.tmp.name, "bars")
        append_bars(data_dir, "BTC", [{"ts": 1_700_000_000,
                    "o": 100, "h": 101, "l": 99, "c": 100,
                    "vol": 1, "amount": 100}])
        bars = outcomes._default_bar_loader("BTC", data_dir)
        self.assertEqual(bars[0]["open_ts"], 1_700_000_000)

    def test_invalid_ohlc_is_not_usable(self):
        bad = {"open_ts": 1_700_000_000, "o": 100, "h": 99,
               "l": 98, "c": 100}
        self.assertFalse(outcomes.is_valid_bar(bad, now=1_700_000_300))

    def test_after_gap_is_recorded_after_coverage_sweep(self):
        self.start_episode(ts=1_700_000_000)
        self.assertEqual(measurement.sweep_coverage(
            self.store, 1_700_000_901), 1)
        card = Scorecard(coin="BTC", direction="LONG", score=80, price=100)
        sid = self.store.log_signal(card, flagged=True)
        event = measurement.ObservationEvent.qualifying(
            "BTC", "MEXC", self.cfg, 1_700_000_902, sid)
        measurement.process_observation(
            self.store, event, Plan(coin="BTC", direction="LONG",
                entry_low=99.9, entry_high=100.1, stop=98, tp1=104,
                tp2=110), {"flag_rule": 2}, self.cfg_json, card=card)
        ep = self.store.open_episode("BTC", self.cfg)
        self.assertEqual(ep["after_gap"], 1)

    def test_mixed_configs_are_not_aggregated_in_breakdowns(self):
        first = self.start_episode()
        other_cfg, other_json = measurement.config_snapshot(
            0.5, 24, 10, 0, "mexc_full")
        card = Scorecard(coin="ETH", direction="LONG", score=80, price=100)
        sid = self.store.log_signal(card, flagged=True)
        measurement.process_observation(
            self.store,
            measurement.ObservationEvent.qualifying(
                "ETH", "MEXC", other_cfg, 1_700_000_100, sid),
            Plan(coin="ETH", direction="LONG", entry_low=99.9,
                 entry_high=100.1, stop=98, tp1=104, tp2=110),
            {"flag_rule": 2}, other_json, card=card)
        from proto import episode_report
        result = episode_report.summary(self.store)
        self.assertTrue(result["cohort"]["mixed_configs_warning"])
        self.assertEqual(len(result["by_cohort"]), 2)
        self.assertEqual(sum(x["report"]["started"]
                             for x in result["by_cohort"]), 2)
        groups = episode_report.breakdowns(
            self.store, dimension="direction", min_episodes=0, min_coins=0)
        self.assertEqual(len({x["cohort"]["config_hash"] for x in groups}), 2)

    def test_measurement_cycle_can_score_concurrently_and_journal_all(self):
        barrier = threading.Barrier(3)
        eligible = [(f"{coin}_USDT", coin, {})
                    for coin in ("BTC", "ETH", "SOL")]

        def score_one(sym, coin, detail):
            barrier.wait(timeout=3)
            return Scorecard(coin=coin, direction="NEUTRAL", score=10,
                             price=100)

        counts = measurement.run_cycle(
            self.store, eligible,
            config={"config_hash": self.cfg, "stake": 0.1,
                    "log_threshold": 24},
            now_fn=lambda: 1_700_000_000,
            score_one=score_one, max_workers=3)
        self.assertEqual(counts["attempted"], 3)
        self.assertEqual(counts["failed"], 0)
        self.assertEqual(self.store.conn.execute(
            "SELECT COUNT(*) FROM episode_observation").fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
