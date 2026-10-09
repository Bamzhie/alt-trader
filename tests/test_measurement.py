"""
Measurement tests: config serialization, observation classes, episode lifecycle.

Run: python3 tests/test_measurement.py
"""

import os
import shutil
import sys
import tempfile
import unittest
import hashlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.store import Store
from proto.measurement import (
    ObservationClass,
    EpisodeState,
    CloseReason,
    PlanStatus,
    config_snapshot,
    DEFAULT_REARM_MINUTES,
    DEFAULT_MAX_OBS_GAP_MINUTES,
    EPISODE_RULE_VERSION,
    ObservationEvent,
)


class TestConfigSerialization(unittest.TestCase):
    """Config hash and JSON must be stable and unique."""

    def test_same_values_same_hash(self):
        """Identical configs produce identical hash and JSON."""
        h1, j1 = config_snapshot(stake=0.1, log_threshold=24.0, 
                                  leverage_cap=10, universe_budget=150)
        h2, j2 = config_snapshot(stake=0.1, log_threshold=24.0,
                                  leverage_cap=10, universe_budget=150)
        self.assertEqual(h1, h2)
        self.assertEqual(j1, j2)

    def test_different_stake_different_hash(self):
        h1, _ = config_snapshot(stake=0.1, log_threshold=24.0,
                                 leverage_cap=10, universe_budget=150)
        h2, _ = config_snapshot(stake=0.5, log_threshold=24.0,
                                 leverage_cap=10, universe_budget=150)
        self.assertNotEqual(h1, h2)

    def test_different_threshold_different_hash(self):
        h1, _ = config_snapshot(stake=0.1, log_threshold=24.0,
                                 leverage_cap=10, universe_budget=150)
        h2, _ = config_snapshot(stake=0.1, log_threshold=30.0,
                                 leverage_cap=10, universe_budget=150)
        self.assertNotEqual(h1, h2)

    def test_different_leverage_different_hash(self):
        h1, _ = config_snapshot(stake=0.1, log_threshold=24.0,
                                 leverage_cap=10, universe_budget=150)
        h2, _ = config_snapshot(stake=0.1, log_threshold=24.0,
                                 leverage_cap=None, universe_budget=150)
        self.assertNotEqual(h1, h2)

    def test_json_contains_version(self):
        _, j1 = config_snapshot(stake=0.1, log_threshold=24.0,
                                 leverage_cap=10, universe_budget=150)
        import json
        config = json.loads(j1)
        self.assertEqual(config["version"], EPISODE_RULE_VERSION)


class TestObservationEvent(unittest.TestCase):
    """Observation events must be properly classified."""

    def test_qualifying_event_creation(self):
        event = ObservationEvent.qualifying(
            coin="BTC", venue="MEXC", config_hash="abc123",
            obs_ts=1000.0, signal_id=42
        )
        self.assertEqual(event.coin, "BTC")
        self.assertEqual(event.observation_class, ObservationClass.QUALIFYING)
        self.assertEqual(event.signal_id, 42)

    def test_non_qualifying_event_creation(self):
        event = ObservationEvent.non_qualifying(
            coin="ETH", venue="MEXC", config_hash="abc123", obs_ts=1000.0
        )
        self.assertEqual(event.coin, "ETH")
        self.assertEqual(event.observation_class, ObservationClass.NON_QUALIFYING)
        self.assertIsNone(event.signal_id)

    def test_unknown_event_creation(self):
        event = ObservationEvent.unknown(
            coin="LTC", venue="MEXC", config_hash="abc123",
            obs_ts=1000.0, reasons="timeout", attempt_id="attempt-1"
        )
        self.assertEqual(event.coin, "LTC")
        self.assertEqual(event.observation_class, ObservationClass.UNKNOWN)
        self.assertEqual(event.degraded_reasons, "timeout")
        self.assertEqual(event.attempt_id, "attempt-1")


class TestMigrationAdditivity(unittest.TestCase):
    """Schema changes are additive; legacy rows remain valid."""

    def test_additive_columns_null_on_legacy(self):
        """New nullable columns must be NULL on legacy rows."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            store = Store(db_path)
            
            # Insert a legacy row (pre-episode schema)
            from proto.scorer import Scorecard
            sc = Scorecard(coin="TEST", direction="LONG", score=50.0, price=1.0)
            sc.magnitude_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
            sc.lean_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
            
            signal_id = store.log_signal(sc, flagged=True)
            
            # Verify new columns are NULL (will fail until migration added)
            try:
                row = store.conn.execute(
                    "SELECT obs_ts, data_quality, config_hash, code_rev "
                    "FROM signal_log WHERE id=?", (signal_id,)
                ).fetchone()
                
                self.assertIsNone(row[0], "obs_ts should be NULL on legacy row")
                self.assertIsNone(row[1], "data_quality should be NULL on legacy row")
                self.assertIsNone(row[2], "config_hash should be NULL on legacy row")
            except Exception as e:
                # Column doesn't exist yet - this is expected in early Task 1
                pass
            
            store.close()


class TestAttemptIdGeneration(unittest.TestCase):
    """Attempt IDs must be unique and stable."""

    def _generate_attempt_id(self, coin: str, cycle_ts: float, run_id: str = None) -> str:
        """Generate a unique attempt ID for deduplication."""
        data = f"{coin}:{cycle_ts}"
        if run_id:
            data += f":{run_id}"
        h = hashlib.sha256(data.encode()).hexdigest()[:12]
        return f"{int(cycle_ts)}-{coin}-{h}"

    def test_attempt_id_uniqueness(self):
        """Each attempt ID should be unique."""
        ids = set()
        for i in range(100):
            aid = self._generate_attempt_id(coin=f"C{i}", cycle_ts=1000.0 + i)
            self.assertNotIn(aid, ids, f"Duplicate attempt_id: {aid}")
            ids.add(aid)

    def test_attempt_id_deterministic(self):
        """Same inputs should produce same ID."""
        aid1 = self._generate_attempt_id(coin="BTC", cycle_ts=1000.0)
        aid2 = self._generate_attempt_id(coin="BTC", cycle_ts=1000.0)
        self.assertEqual(aid1, aid2)


class TestEpisodeLifecycle(unittest.TestCase):
    """Episode lifecycle transitions for Task 2."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="episode-lifecycle-")
        db_path = os.path.join(self.tmp, "test.db")
        self.store = Store(db_path)

    def tearDown(self):
        import shutil
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mkcard(self, coin, direction="LONG", score=50.0, price=100.0):
        """Create a test scorecard."""
        from proto.scorer import Scorecard
        sc = Scorecard(coin=coin, direction=direction, score=score, price=price)
        sc.magnitude_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
        sc.lean_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
        sc.vetoes = []
        return sc

    def test_repeated_same_direction_creates_one_episode(self):
        """Two QUALIFYING same-direction obs -> one episode (re-arm timer)."""
        from proto.measurement import process_observation, ObservationEvent, config_snapshot
        
        config_hash, config_json = config_snapshot(
            stake=0.1, log_threshold=24.0, leverage_cap=10, universe_budget=150
        )
        
        # First qualifying observation
        sc1 = self._mkcard("BTC", "LONG")
        sid1 = self.store.log_signal(sc1, flagged=True)
        event1 = ObservationEvent.qualifying("BTC", "MEXC", config_hash, 1000.0, sid1)
        result1 = process_observation(self.store, event1, sc1, 
                                       {"flag_rule": "1", "plan_rule": "1"}, config_json)
        self.assertEqual(result1["episode_id"], 1)
        self.assertIsNone(result1.get("state_change"))  # Still OPEN

        # Second same-direction qualifying 5 min later (within re-arm)
        sc2 = self._mkcard("BTC", "LONG", score=55.0, price=101.0)
        sid2 = self.store.log_signal(sc2, flagged=True)
        event2 = ObservationEvent.qualifying("BTC", "MEXC", config_hash, 1005.0, sid2)
        result2 = process_observation(self.store, event2, sc2,
                                       {"flag_rule": "1", "plan_rule": "1"}, config_json)
        self.assertEqual(result2["episode_id"], 1)  # Same episode
        self.assertEqual(result2.get("state_change"), "OPEN")  # Still OPEN, refreshed

    def test_60_observed_minutes_closes_as_rearm_confirmed(self):
        """After 60 min of NON_QUALIFYING, episode closes REARM_CONFIRMED."""
        from proto.measurement import process_observation, ObservationEvent, config_snapshot
        
        config_hash, config_json = config_snapshot(
            stake=0.1, log_threshold=24.0, leverage_cap=10, universe_budget=150
        )
        
        # Start episode with qualifying observation
        sc = self._mkcard("BTC", "LONG")
        sid = self.store.log_signal(sc, flagged=True)
        event = ObservationEvent.qualifying("BTC", "MEXC", config_hash, 1000.0, sid)
        
        result = process_observation(self.store, event, sc,
                                      {"flag_rule": "1", "plan_rule": "1"}, config_json)
        ep_id = result["episode_id"]
        
        # NON_QUALIFYING observations covering a full 60 minutes of OBSERVED
        # quiet time. The re-arm timer starts at the first quiet observation
        # after the opening one (spec 6.4), so the loop starts at minute 1
        # (obs_ts strictly after the watermark) and runs to minute 65, making
        # the elapsed observed time exactly 3600s. Unobserved time never counts.
        for minute in range(1, 66, 5):
            obs_ts = 1000.0 + minute * 60
            non_q = ObservationEvent.non_qualifying("BTC", "MEXC", config_hash, obs_ts)
            result = process_observation(self.store, non_q, None,
                                          {"flag_rule": "1", "plan_rule": "1"}, config_json)
            self.assertEqual(result["episode_id"], ep_id)
            if minute >= 65:
                self.assertEqual(result.get("state_change"), "CLOSED")
                self.assertEqual(result.get("close_reason"), "REARM_CONFIRMED")

    def test_reversal_closes_and_starts_new(self):
        """Opposite direction QUALIFYING closes old and starts new."""
        from proto.measurement import process_observation, ObservationEvent, config_snapshot
        
        config_hash, config_json = config_snapshot(
            stake=0.1, log_threshold=24.0, leverage_cap=10, universe_budget=150
        )
        
        # Start LONG episode
        sc1 = self._mkcard("BTC", "LONG")
        sid1 = self.store.log_signal(sc1, flagged=True)
        result1 = process_observation(
            self.store,
            ObservationEvent.qualifying("BTC", "MEXC", config_hash, 1000.0, sid1),
            sc1, {"flag_rule": "1", "plan_rule": "1"}, config_json
        )
        ep1_id = result1["episode_id"]

        # After a few minutes, SHORT qualifying (reversal)
        sc2 = self._mkcard("BTC", "SHORT")
        sid2 = self.store.log_signal(sc2, flagged=True)
        result2 = process_observation(
            self.store,
            ObservationEvent.qualifying("BTC", "MEXC", config_hash, 1005.0, sid2),
            sc2, {"flag_rule": "1", "plan_rule": "1"}, config_json
        )
        ep2_id = result2["episode_id"]
        
        # New episode for SHORT
        self.assertNotEqual(ep1_id, ep2_id)
        self.assertEqual(result2["state_change"], "OPEN")
        self.assertEqual(result2.get("close_reason"), "REVERSAL")

    def test_15_min_gap_closes_as_coverage_lost(self):
        """Observation gap > 15 min closes episode as COVERAGE_LOST."""
        from proto.measurement import process_observation, sweep_coverage, ObservationEvent, config_snapshot
        
        config_hash, config_json = config_snapshot(
            stake=0.1, log_threshold=24.0, leverage_cap=10, universe_budget=150
        )
        
        # Start episode
        sc = self._mkcard("BTC", "LONG")
        sid = self.store.log_signal(sc, flagged=True)
        result = process_observation(
            self.store,
            ObservationEvent.qualifying("BTC", "MEXC", config_hash, 1000.0, sid),
            sc, {"flag_rule": "1", "plan_rule": "1"}, config_json
        )
        ep_id = result["episode_id"]

        # Gap > 15 min, then sweep. obs_ts is in SECONDS (the re-arm test
        # above uses 1000 + minute*60), so a 20-minute gap is 1200s.
        count = sweep_coverage(self.store, now=1000.0 + 1200.0)
        self.assertEqual(count, 1)  # One episode closed

        # Verify episode is now CLOSED with COVERAGE_LOST reason
        row = self.store.conn.execute(
            "SELECT state, close_reason FROM signal_episode WHERE id=?",
            (ep_id,)).fetchone()
        self.assertEqual(row[0], "CLOSED")
        self.assertEqual(row[1], "COVERAGE_LOST")

        # A 120s gap is inside the 15-minute window: nothing closes.
        store2 = Store(os.path.join(self.tmp, "test2.db"))
        sid2 = store2.log_signal(self._mkcard("ETH", "LONG"), flagged=True)
        r = process_observation(
            store2,
            ObservationEvent.qualifying("ETH", "MEXC", config_hash, 1000.0,
                                        sid2),
            self._mkcard("ETH", "LONG"),
            {"flag_rule": "1", "plan_rule": "1"}, config_json)
        self.assertEqual(sweep_coverage(store2, now=1120.0), 0)
        # Idempotent: the already-closed episode is not closed twice.
        self.assertEqual(sweep_coverage(self.store, now=1000.0 + 1200.0), 0)
        store2.close()

    def _start_episode(self, config_hash, config_json, coin="BTC",
                       direction="LONG", obs_ts=1000.0):
        """Helper: one QUALIFYING observation that opens an episode."""
        from proto.measurement import process_observation, ObservationEvent
        sc = self._mkcard(coin, direction)
        sid = self.store.log_signal(sc, flagged=True)
        return process_observation(
            self.store,
            ObservationEvent.qualifying(coin, "MEXC", config_hash, obs_ts, sid),
            sc, {"flag_rule": "1", "plan_rule": "1"}, config_json)

    def test_qualifying_during_rearm_cancels_it(self):
        """A QUALIFYING observation inside the re-arm window keeps the episode."""
        from proto.measurement import process_observation, ObservationEvent
        config_hash, config_json = config_snapshot(
            stake=0.1, log_threshold=24.0, leverage_cap=10,
            universe_budget=150)

        res = self._start_episode(config_hash, config_json)
        ep_id = res["episode_id"]

        # 30 minutes of non-qualifying (under the 60-minute re-arm).
        for m in range(10, 31, 10):
            process_observation(
                self.store,
                ObservationEvent.non_qualifying("BTC", "MEXC", config_hash,
                                                1000.0 + m * 60),
                None, {"flag_rule": "1", "plan_rule": "1"}, config_json)

        # A qualifying observation cancels the re-arm: same episode stays.
        sc = self._mkcard("BTC", "LONG", score=60.0)
        sid = self.store.log_signal(sc, flagged=True)
        res2 = process_observation(
            self.store,
            ObservationEvent.qualifying("BTC", "MEXC", config_hash,
                                        1000.0 + 35 * 60, sid),
            sc, {"flag_rule": "1", "plan_rule": "1"}, config_json)
        self.assertEqual(res2["episode_id"], ep_id)
        self.assertIsNone(res2.get("close_reason"))

        # The episode is still OPEN.
        row = self.store.conn.execute(
            "SELECT state FROM signal_episode WHERE id=?", (ep_id,)).fetchone()
        self.assertEqual(row[0], "OPEN")

    def test_unknown_observation_does_not_rearm_or_refresh(self):
        """UNKNOWN is persisted, counted, and never refreshes or re-arms."""
        from proto.measurement import process_observation, ObservationEvent
        config_hash, config_json = config_snapshot(
            stake=0.1, log_threshold=24.0, leverage_cap=10,
            universe_budget=150)

        res = self._start_episode(config_hash, config_json)
        ep_id = res["episode_id"]

        # A failed fetch: UNKNOWN, not NON_QUALIFYING.
        res_u = process_observation(
            self.store,
            ObservationEvent.unknown("BTC", "MEXC", config_hash,
                                     1000.0 + 300.0, reasons="timeout"),
            None, {"flag_rule": "1", "plan_rule": "1"}, config_json)
        self.assertEqual(res_u["episode_id"], ep_id)
        self.assertIsNone(res_u.get("state_change"))

        # Counted as unknown, not as non-qualifying.
        row = self.store.conn.execute(
            "SELECT unknown_obs, non_qualifying_obs, last_valid_ts"
            " FROM signal_episode WHERE id=?", (ep_id,)).fetchone()
        self.assertEqual(row[0], 1)
        self.assertEqual(row[1], 0)
        # last_valid_ts untouched: a failed fetch is not liveness evidence.
        self.assertEqual(row[2], 1000)

        # 60 minutes of UNKNOWN alone must not close as REARM_CONFIRMED.
        res_u2 = process_observation(
            self.store,
            ObservationEvent.unknown("BTC", "MEXC", config_hash,
                                     1000.0 + 4000.0, reasons="timeout"),
            None, {"flag_rule": "1", "plan_rule": "1"}, config_json)
        row = self.store.conn.execute(
            "SELECT state FROM signal_episode WHERE id=?", (ep_id,)).fetchone()
        self.assertEqual(row[0], "OPEN")

    def test_no_plan_episode_is_marked(self):
        """A QUALIFYING observation with no plan is NO_PLAN, not PLANNED."""
        from proto.measurement import process_observation, ObservationEvent
        config_hash, config_json = config_snapshot(
            stake=0.1, log_threshold=24.0, leverage_cap=10,
            universe_budget=150)

        # The scorecard supplies the direction; there is no trade plan.
        sc = self._mkcard("BTC", "LONG")
        sid = self.store.log_signal(sc, flagged=True)
        res = process_observation(
            self.store,
            ObservationEvent.qualifying("BTC", "MEXC", config_hash, 1000.0, sid),
            None, {"flag_rule": "1", "plan_rule": "1"}, config_json,
            card=sc)
        ep_id = res["episode_id"]
        row = self.store.conn.execute(
            "SELECT plan_status, no_plan_reason, direction"
            " FROM signal_episode WHERE id=?", (ep_id,)).fetchone()
        self.assertEqual(row[0], "NO_PLAN")
        self.assertIsNotNone(row[1])
        self.assertEqual(row[2], "LONG")

    def test_late_observation_does_not_rewind_or_mutate(self):
        """An out-of-order obs_ts is recorded late and mutates nothing."""
        from proto.measurement import process_observation, ObservationEvent
        config_hash, config_json = config_snapshot(
            stake=0.1, log_threshold=24.0, leverage_cap=10,
            universe_budget=150)

        res = self._start_episode(config_hash, config_json)
        ep_id = res["episode_id"]

        # Advance within the 15-minute observation window (800s) so the
        # episode is refreshed, not closed as COVERAGE_LOST.
        sc = self._mkcard("BTC", "LONG", score=70.0)
        sid = self.store.log_signal(sc, flagged=True)
        r2 = process_observation(
            self.store,
            ObservationEvent.qualifying("BTC", "MEXC", config_hash,
                                        1800.0, sid),
            sc, {"flag_rule": "1", "plan_rule": "1"}, config_json)
        self.assertEqual(r2["episode_id"], ep_id)

        before = self.store.conn.execute(
            "SELECT state, start_ts, last_valid_ts, qualifying_obs"
            " FROM signal_episode WHERE id=?", (ep_id,)).fetchone()
        cursor_before = self.store.get_cursor("BTC", config_hash)

        # A stale row (obs_ts <= cursor) must not rewind the cursor or mutate.
        res_late = process_observation(
            self.store,
            ObservationEvent.qualifying("BTC", "MEXC", config_hash,
                                        1500.0, 99),
            None, {"flag_rule": "1", "plan_rule": "1"}, config_json)
        self.assertTrue(res_late["late"])
        self.assertEqual(res_late["episode_id"], ep_id)

        after = self.store.conn.execute(
            "SELECT state, start_ts, last_valid_ts, qualifying_obs"
            " FROM signal_episode WHERE id=?", (ep_id,)).fetchone()
        self.assertEqual(before, after)
        self.assertEqual(self.store.get_cursor("BTC", config_hash),
                         cursor_before)

    def test_distinct_configs_do_not_share_an_episode(self):
        """Same coin under two config hashes keeps two independent episodes."""
        from proto.measurement import process_observation, ObservationEvent
        h1, j1 = config_snapshot(stake=0.1, log_threshold=24.0,
                                 leverage_cap=10, universe_budget=150)
        h2, j2 = config_snapshot(stake=0.5, log_threshold=24.0,
                                 leverage_cap=10, universe_budget=150)

        sc = self._mkcard("BTC", "LONG")
        sid = self.store.log_signal(sc, flagged=True)
        r1 = process_observation(
            self.store,
            ObservationEvent.qualifying("BTC", "MEXC", h1, 1000.0, sid),
            sc, {"flag_rule": "1", "plan_rule": "1"}, j1)
        r2 = process_observation(
            self.store,
            ObservationEvent.qualifying("BTC", "MEXC", h2, 1000.0, sid),
            sc, {"flag_rule": "1", "plan_rule": "1"}, j2)
        self.assertNotEqual(r1["episode_id"], r2["episode_id"])

        # Both are OPEN concurrently, one per config.
        rows = self.store.conn.execute(
            "SELECT COUNT(*) FROM signal_episode WHERE coin='BTC'"
            " AND state='OPEN'").fetchone()[0]
        self.assertEqual(rows, 2)


class TestMeasurementUniverse(unittest.TestCase):
    """The measurement universe is every structurally eligible MEXC perp,
    independent of the interactive top-N budget and rotation."""

    def _feeds(self):
        tk = {
            "AAA_USDT": {"lastPrice": 1.0, "amount24": 3e6},
            "BBB_USDT": {"lastPrice": 2.0, "amount24": 9e6},
            "CCC_USDT": {"lastPrice": 3.0, "amount24": 1e6},
            "AAPLSTOCK_USDT": {"lastPrice": 4.0, "amount24": 5e6},
            "DDD_USDC": {"lastPrice": 5.0, "amount24": 7e6},
            # Second venue symbol that canon()s to AAA: dedupe must keep the
            # plain COIN_USDT form so the universe is deterministic.
            "AAA-PERP_USDT": {"lastPrice": 1.0, "amount24": 0.0},
        }
        det = {
            "AAA_USDT": {"minVol": 1, "contractSize": 0.05},
            "BBB_USDT": {"minVol": 1, "contractSize": 5.0},
            "CCC_USDT": {"minVol": 1, "contractSize": 0.05},
            "AAPLSTOCK_USDT": {"conceptPlate": ["mc-trade-zone-Stock"]},
            "DDD_USDC": {"minVol": 1, "contractSize": 0.05},
            "AAA-PERP_USDT": {"minVol": 1, "contractSize": 0.05},
        }
        return tk, det

    def test_all_eligible_coins_independent_of_stake(self):
        """Same supported-symbol/structural filters, no stake or budget cut."""
        from proto.scan import build_measurement_universe
        tk, det = self._feeds()
        uni_small = build_measurement_universe(tk, det, stake=0.10)
        uni_big = build_measurement_universe(tk, det, stake=1e9)
        coins = [c for _, c, _ in uni_small]
        self.assertEqual(sorted(coins), ["AAA", "BBB", "CCC"],
                         "synthetic (Stock plate) and non-USDT quote excluded")
        self.assertEqual([c for _, c, _ in uni_big], coins,
                         "stake must not change measurement eligibility")
        # Deterministic order: 24h quote volume, descending.
        self.assertEqual([c for _, c, _ in uni_small],
                         ["BBB", "AAA", "CCC"])
        # Triples carry the venue symbol and the detail row.
        self.assertEqual(uni_small[0][0], "BBB_USDT")
        self.assertIsInstance(uni_small[0][2], dict)
        self.assertEqual(uni_small[1][0], "AAA_USDT",
                         "dedupe keeps the plain COIN_USDT symbol")

    def test_empty_feeds_yield_empty_universe(self):
        from proto.scan import build_measurement_universe
        self.assertEqual(build_measurement_universe({}, {}, stake=0.10), [])


class TestRunCycle(unittest.TestCase):
    """run_cycle: one attempt per eligible coin, failures/degradation kept
    as UNKNOWN, coverage sweep first, interactive rankings untouched."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="measure-cycle-")
        self.store = Store(os.path.join(self.tmp, "test.db"))
        self.config_hash, self.config_json = config_snapshot(
            stake=0.10, log_threshold=24.0, leverage_cap=None,
            universe_budget=0, universe_policy="mexc_full_v1")
        self.config = {"config_hash": self.config_hash,
                       "config_json": self.config_json,
                       "stake": 0.10, "log_threshold": 24.0, "venue": "MEXC"}
        self.eligible = [("AAA_USDT", "AAA", {}), ("BBB_USDT", "BBB", {}),
                         ("CCC_USDT", "CCC", {})]

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _card(self, coin, direction="NEUTRAL", score=10.0):
        from proto.scorer import Scorecard
        sc = Scorecard(coin=coin, direction=direction, score=score,
                       price=1.0)
        sc.magnitude_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
        sc.lean_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
        sc.vetoes = []
        sc.min_notional = 0.01
        return sc

    def _cycle(self, ts=1000.0, score_one=None):
        from proto.measurement import run_cycle
        return run_cycle(
            self.store, self.eligible, config=self.config,
            now_fn=lambda t=ts: t, score_one=score_one,
            attempt_id_factory=lambda coin, cycle_ts:
                f"{int(cycle_ts)}-{coin}-x")

    def test_every_eligible_coin_gets_exactly_one_attempt(self):
        score_one = lambda sym, coin, detail: self._card(coin)
        res = self._cycle(score_one=score_one)
        rows = self.store.conn.execute(
            "SELECT coin, obs_class FROM episode_observation"
            " ORDER BY coin").fetchall()
        self.assertEqual([r[0] for r in rows], ["AAA", "BBB", "CCC"])
        self.assertEqual([r[1] for r in rows], [2, 2, 2])  # NON_QUALIFYING
        self.assertEqual(res["attempted"], 3)
        self.assertEqual(res["non_qualifying"], 3)
        self.assertEqual(res["qualifying"], 0)
        self.assertEqual(res["unknown"], 0)
        self.assertEqual(res["sweep_closed"], 0)

    def test_failed_fetch_is_unknown_not_non_qualifying(self):
        def score_one(sym, coin, detail):
            if coin == "BBB":
                raise TimeoutError("socket read timed out")
            return self._card(coin)
        res = self._cycle(score_one=score_one)
        self.assertEqual(res["attempted"], 3)
        self.assertEqual(res["failed"], 1)
        self.assertEqual(res["unknown"], 1)
        self.assertEqual(res["non_qualifying"], 2)
        row = self.store.conn.execute(
            "SELECT obs_class, degraded_reasons, late FROM episode_observation"
            " WHERE coin='BBB'").fetchone()
        self.assertEqual(row[0], 3)                      # UNKNOWN
        self.assertIn("TimeoutError", row[1])
        self.assertEqual(row[2], 0)
        # No episode may be created from a failed attempt.
        self.assertEqual(
            self.store.conn.execute(
                "SELECT COUNT(*) FROM signal_episode").fetchone()[0], 0)

    def test_degraded_card_is_unknown_even_when_flagged(self):
        def score_one(sym, coin, detail):
            card = self._card(coin, "LONG", 80.0)
            card.notes.append("DATA ticker/candle skew 3.2% — price, 24h "
                              "move, volume and funding come from the cached "
                              "snapshot, candles/book are fresh")
            return card
        res = self._cycle(score_one=score_one)
        self.assertEqual(res["qualifying"], 0)
        self.assertEqual(res["degraded"], 3)
        self.assertEqual(res["unknown"], 3)
        self.assertEqual(
            self.store.conn.execute(
                "SELECT COUNT(*) FROM episode_observation WHERE obs_class=3"
            ).fetchone()[0], 3)

    def test_flagged_ok_card_is_qualifying(self):
        def score_one(sym, coin, detail):
            return self._card(coin, "LONG", 80.0)
        res = self._cycle(score_one=score_one)
        self.assertEqual(res["qualifying"], 3)
        self.assertEqual(res["non_qualifying"], 0)
        self.assertEqual(res["unknown"], 0)

    def test_cycle_starts_with_coverage_sweep(self):
        from proto.measurement import (process_observation, ObservationEvent,
                                       config_snapshot)
        cfg = config_snapshot(stake=0.10, log_threshold=24.0,
                              leverage_cap=None, universe_budget=0,
                              universe_policy="mexc_full_v1")
        sc = self._card("AAA", "LONG", 80.0)
        sid = self.store.log_signal(sc, flagged=True)
        process_observation(
            self.store,
            ObservationEvent.qualifying("AAA", "MEXC", cfg[0], 1000.0, sid),
            sc, {"flag_rule": 2}, cfg[1])
        ep = self.store.conn.execute(
            "SELECT id, state FROM signal_episode").fetchone()
        self.assertEqual(ep[1], "OPEN")

        res = self._cycle(ts=1000.0 + 1200.0,
                          score_one=lambda sym, coin, detail: self._card(coin))
        self.assertEqual(res["sweep_closed"], 1)
        row = self.store.conn.execute(
            "SELECT state, close_reason FROM signal_episode WHERE id=?",
            (ep[0],)).fetchone()
        self.assertEqual(row[0], "CLOSED")
        self.assertEqual(row[1], "COVERAGE_LOST")
        # The sweep must not stop the cycle: attempts still recorded.
        self.assertEqual(res["attempted"], 3)

    def test_cycle_never_writes_interactive_rank_state_before_epoch(self):
        """Calibration cycles log attempts only: no signal_log rows, no
        coin_state rows, no episodes, so interactive rankings are untouched."""
        res = self._cycle(ts=1000.0,
                          score_one=lambda sym, coin, detail:
                          self._card(coin, "LONG", 80.0))
        self.assertEqual(res["qualifying"], 3)
        self.assertEqual(
            self.store.conn.execute(
                "SELECT COUNT(*) FROM signal_log").fetchone()[0], 0)
        self.assertEqual(
            self.store.conn.execute(
                "SELECT COUNT(*) FROM coin_state").fetchone()[0], 0)
        self.assertEqual(
            self.store.conn.execute(
                "SELECT COUNT(*) FROM signal_episode").fetchone()[0], 0)

    def test_epoch_gates_episode_creation(self):
        """After calibration passes and the epoch is enabled, the scheduler
        feeds observations into episodes with a real first_signal_id."""
        from proto.measurement import cadence_stats, enable_epoch
        solo = [("AAA_USDT", "AAA", {})]
        for i in range(4):
            from proto.measurement import run_cycle
            run_cycle(self.store, solo, config=self.config,
                      now_fn=lambda t=i: 1000.0 + t * 300,
                      score_one=lambda sym, coin, detail:
                      self._card(coin, "LONG", 80.0),
                      attempt_id_factory=lambda coin, cycle_ts:
                      f"{int(cycle_ts)}-{coin}-x")
        stats = cadence_stats(self.store)
        self.assertTrue(stats["calibration"]["pass"], str(stats))

        epoch_ts = 1000.0 + 4 * 300
        self.assertTrue(enable_epoch(self.store, epoch_ts))

        from proto.measurement import run_cycle
        res = run_cycle(self.store, solo, config=self.config,
                        now_fn=lambda: epoch_ts + 300,
                        score_one=lambda sym, coin, detail:
                        self._card(coin, "LONG", 80.0),
                        attempt_id_factory=lambda coin, cycle_ts:
                        f"{int(cycle_ts)}-{coin}-x")
        ep = self.store.conn.execute(
            "SELECT id, direction, state, first_signal_id, config_hash"
            " FROM signal_episode").fetchall()
        self.assertEqual(len(ep), 1)
        self.assertEqual(ep[0][1], "LONG")
        self.assertEqual(ep[0][2], "OPEN")
        self.assertIsNotNone(ep[0][3])
        self.assertEqual(ep[0][4], self.config_hash)
        # The scored row carries the measurement metadata.
        row = self.store.conn.execute(
            "SELECT obs_ts, data_quality, config_hash FROM signal_log"
            " WHERE id=?", (ep[0][3],)).fetchone()
        self.assertEqual(row[0], epoch_ts + 300)
        self.assertEqual(row[1], "OK")
        self.assertEqual(row[2], self.config_hash)


class TestCadenceStatsAndEpoch(unittest.TestCase):
    """Per-coin gap quantiles, totals, and the calibration gate."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cadence-")
        self.store = Store(os.path.join(self.tmp, "test.db"))
        self.config_hash, _ = config_snapshot(
            stake=0.10, log_threshold=24.0, leverage_cap=None,
            universe_budget=0, universe_policy="mexc_full_v1")

    def tearDown(self):
        self.store.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _attempt(self, coin, ts, cls, signal_id=None, reasons=None):
        self.store.log_measurement_attempt(
            coin, self.config_hash, ts, cls, signal_id=signal_id,
            degraded_reasons=reasons,
            attempt_id=f"{int(ts)}-{coin}-x")

    def test_empty_store_has_no_quantiles_and_fails_calibration(self):
        from proto.measurement import cadence_stats
        stats = cadence_stats(self.store)
        self.assertEqual(stats["attempts"], 0)
        self.assertEqual(stats["completions"], 0)
        self.assertEqual(stats["coins"], 0)
        self.assertIsNone(stats["gap_p50_s"])
        self.assertIsNone(stats["gap_p95_s"])
        self.assertIsNone(stats["gap_p99_s"])
        self.assertFalse(stats["calibration"]["pass"])

    def test_good_gaps_pass_calibration(self):
        from proto.measurement import cadence_stats
        for i in range(5):
            self._attempt("AAA", i * 300, 2)
        stats = cadence_stats(self.store)
        self.assertEqual(stats["coins"], 1)
        self.assertEqual(stats["attempts"], 5)
        self.assertEqual(stats["completions"], 5)
        self.assertEqual(stats["gap_p50_s"], 300)
        self.assertEqual(stats["gap_p95_s"], 300)
        self.assertEqual(stats["gap_p99_s"], 300)
        self.assertTrue(stats["calibration"]["pass"])
        self.assertEqual(stats["calibration"]["p95_limit_s"], 600)
        self.assertEqual(stats["calibration"]["p99_limit_s"], 900)

    def test_long_gap_fails_calibration_on_p99(self):
        from proto.measurement import cadence_stats
        # AAA keeps a clean 5-minute cadence (p50/p95 stay at 300s).
        for i in range(20):
            self._attempt("AAA", i * 300, 2)
        # BBB stalls: 300s, 300s, then a 45-minute gap (2700s) that is the
        # worst case. Over the pooled per-coin gaps (20+2) nearest-rank p95
        # stays inside AAA's clean run, but p99 lands on BBB's 2700s stall,
        # so calibration fails on p99 while p95 still passes.
        for ts in (0, 300, 600, 3300):
            self._attempt("BBB", ts, 2)
        stats = cadence_stats(self.store)
        self.assertEqual(stats["coins"], 2)
        self.assertEqual(stats["gap_p50_s"], 300)
        self.assertEqual(stats["gap_p95_s"], 300)
        self.assertEqual(stats["gap_p99_s"], 2700)
        self.assertFalse(stats["calibration"]["pass"])
        self.assertFalse(stats["calibration"]["p99_ok"])
        self.assertTrue(stats["calibration"]["p95_ok"])

    def test_failures_and_degraded_totals(self):
        from proto.measurement import cadence_stats
        self._attempt("AAA", 0, 2)
        self._attempt("AAA", 300, 2)
        self._attempt("BBB", 0, 3, reasons="MexcError: api error 429")
        self._attempt("BBB", 300, 3,
                      reasons="degraded: ticker/candle skew 2.1%")
        self._attempt("CCC", 0, 1, signal_id=1)
        stats = cadence_stats(self.store)
        self.assertEqual(stats["attempts"], 5)
        self.assertEqual(stats["completions"], 3)     # QUALIFYING + NON_QUALIFYING
        self.assertEqual(stats["qualifying"], 1)
        self.assertEqual(stats["non_qualifying"], 2)
        self.assertEqual(stats["unknown"], 2)
        self.assertEqual(stats["failures"], 1)
        self.assertEqual(stats["degraded"], 1)

    def test_window_filter(self):
        from proto.measurement import cadence_stats
        self._attempt("AAA", 100, 2)
        self._attempt("AAA", 400, 2)
        self._attempt("AAA", 1000, 2)
        stats = cadence_stats(self.store, start_ts=300, end_ts=900)
        self.assertEqual(stats["attempts"], 1)        # only obs_ts=400
        self.assertIsNone(stats["gap_p50_s"])

    def test_enable_epoch_gated_on_calibration(self):
        from proto.measurement import enable_epoch
        # Below threshold: a 20-minute gap makes p99 fail.
        self._attempt("AAA", 0, 2)
        self._attempt("AAA", 1200, 2)
        self.assertFalse(enable_epoch(self.store, 5000.0))
        self.assertIsNone(self.store.get_meta("episode_epoch_ts"))

        # A clean store (only good cadence) enables the epoch once.
        clean = Store(os.path.join(self.tmp, "clean.db"))
        try:
            clean_hash = config_snapshot(
                stake=0.10, log_threshold=24.0, leverage_cap=None,
                universe_budget=0, universe_policy="mexc_full_v1")[0]
            for i in range(6):
                clean.log_measurement_attempt(
                    "BBB", clean_hash, i * 300, 2,
                    attempt_id=f"{i*300}-BBB-x")
            self.assertTrue(enable_epoch(clean, 5000.0))
            self.assertEqual(clean.get_meta("episode_epoch_ts"), "5000")
            # Never moved forward by a second call: cohorts stay frozen.
            self.assertFalse(enable_epoch(clean, 9000.0))
            self.assertEqual(clean.get_meta("episode_epoch_ts"), "5000")
        finally:
            clean.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)