"""
Task 6 integration tests: epoch controls, calibration gating, and the
guarantee that the episode measurement path cannot touch legacy behaviour.

Offline only: no network calls. Each check is a hard assertion about the
plan's Task 6 interface contract:

- UI/daemon display cadence + calibration status separately from legacy
  scan-observation metrics, and measurement never alters scores or ranks.
- Epoch activation is an explicit operator action after a passing
  calibration; it records `episode_epoch_ts` in `meta` and is never
  automatic on startup.
- Existing DB migration and startup paths stay non-blocking and safe for
  legacy-only databases.

Run: python3 tests/test_epoch_integration.py
"""

import os
import sys
import tempfile
import shutil
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.store import Store
from proto.scorer import Scorecard
from proto import measurement as measuremod

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def mkcard(coin, direction="LONG", score=50.0, price=100.0):
    sc = Scorecard(coin=coin, direction=direction, score=score, price=price)
    sc.magnitude_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
    sc.lean_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
    sc.vetoes = []
    # The stake-aware flag rule fails closed without these, so a card meant
    # to be QUALIFYING must carry realistic liquidity fields.
    sc.min_notional = 0.01
    sc.quote_vol_24h = 1e6
    sc.spread_pct = 0.1
    return sc


def fresh_store(tag="t"):
    tmp = tempfile.mkdtemp(prefix=f"epoch-{tag}-")
    return Store(os.path.join(tmp, "m.db")), tmp


def seed_good_gaps(store, config_hash, coin="BTC", start=1000.0, n=6,
                   step=300.0):
    """Seed clean 5-minute-spaced QUALIFYING observations that satisfy
    the p95<=600s / p99<=900s calibration."""
    for i in range(n):
        sc = mkcard(coin)
        sid = store.log_signal(sc, flagged=True)
        measuremod.process_observation(
            store,
            measuremod.ObservationEvent.qualifying(coin, "MEXC", config_hash,
                                                   start + i * step, sid),
            sc, {"flag_rule": "1"}, None)


# --------------------------------------------------------------------------
print("=== epoch unset by default on a fresh DB ===")
store, tmp = fresh_store("default")
check("no epoch on a fresh DB", measuremod.epoch_ts(store) is None,
      str(measuremod.epoch_ts(store)))
check("meta has no episode_epoch_ts",
      store.get_meta(measuremod.EPOCH_KEY) is None)
store.close()
shutil.rmtree(tmp, ignore_errors=True)

# --------------------------------------------------------------------------
print("\n=== activation rejected before calibration passes ===")
store, tmp = fresh_store("reject")
h, _ = measuremod.config_snapshot(stake=0.1, log_threshold=24.0,
                                  leverage_cap=10, universe_budget=150)
# One sparse observation: a single gap far over the p95 threshold.
sc = mkcard("BTC")
sid = store.log_signal(sc, flagged=True)
measuremod.process_observation(
    store,
    measuremod.ObservationEvent.qualifying("BTC", "MEXC", h, 1000.0, sid),
    sc, {"flag_rule": "1"}, None)
sc2 = mkcard("BTC")
sid2 = store.log_signal(sc2, flagged=True)
measuremod.process_observation(
    store,
    measuremod.ObservationEvent.qualifying("BTC", "MEXC", h, 1000.0 + 5000.0,
                                           sid2),
    sc2, {"flag_rule": "1"}, None)

stats = measuremod.cadence_stats(store)
check("calibration fails on sparse cadence", not stats["calibration"]["pass"],
      str(stats["calibration"]))
check("p95 gap recorded", stats["gap_p95_s"] >= 5000, str(stats["gap_p95_s"]))
check("epoch activation refused",
      measuremod.enable_epoch(store, 5000.0) is False)
check("epoch still unset after refusal", measuremod.epoch_ts(store) is None)
store.close()
shutil.rmtree(tmp, ignore_errors=True)

# --------------------------------------------------------------------------
print("\n=== activation succeeds only after calibration passes ===")
store, tmp = fresh_store("pass")
seed_good_gaps(store, h)
stats = measuremod.cadence_stats(store)
check("calibration passes on clean 5m cadence",
      stats["calibration"]["pass"], str(stats["calibration"]))
check("p95 within 600s", stats["gap_p95_s"] <= 600, str(stats["gap_p95_s"]))
check("p99 within 900s", stats["gap_p99_s"] <= 900, str(stats["gap_p99_s"]))
check("epoch activation succeeds",
      measuremod.enable_epoch(store, 5000.0) is True)
check("epoch recorded in meta", measuremod.epoch_ts(store) == 5000,
      str(measuremod.epoch_ts(store)))
# The cohort boundary is immutable: a later call must never move it.
check("epoch is written once (immutable boundary)",
      measuremod.enable_epoch(store, 9000.0) is False)
check("epoch did not move", measuremod.epoch_ts(store) == 5000,
      str(measuremod.epoch_ts(store)))
store.close()
shutil.rmtree(tmp, ignore_errors=True)

# --------------------------------------------------------------------------
print("\n=== old signal rows are excluded from episodes ===")
# The epoch gate is enforced by run_cycle(), the production entry point, not
# by the process_observation() lifecycle primitive (which stays testable
# offline). These checks drive run_cycle exactly as the daemon/GUI do.
cfg = measuremod.measurement_config(0.1, 24.0, 10)
# The seed must use the SAME config_hash the scheduler will use, or the
# post-epoch attempt lands in a different cohort and creates its own episode.
h = cfg["config_hash"]
store, tmp = fresh_store("oldrows")
seed_good_gaps(store, h)
measuremod.enable_epoch(store, 1000.0 + 5 * 300.0)   # epoch after the seed
EPOCH = measuremod.epoch_ts(store)
assert EPOCH is not None, "epoch must be set for the pre/post-epoch checks"

before = store.conn.execute(
    "SELECT COUNT(*) FROM signal_episode").fetchone()[0]


def run_one(store, coin, obs_ts, config):
    """Feed one coin through run_cycle at a fixed obs_ts."""
    card = mkcard(coin)
    return measuremod.run_cycle(
        store,
        [(coin, coin, {"card": card})],
        config=config,
        now_fn=lambda: obs_ts,
        score_one=lambda *a, **k: card,
        attempt_id_factory=lambda c, ts: f"{c}-{int(ts)}")


cfg = measuremod.measurement_config(0.1, 24.0, 10)

# A pre-epoch attempt must not create an episode.
run_one(store, "ETH", EPOCH - 60.0, cfg)
after_old = store.conn.execute(
    "SELECT COUNT(*) FROM signal_episode").fetchone()[0]
check("pre-epoch observation creates no new episode",
      after_old == before, f"{before} -> {after_old}")

# A post-epoch attempt does create one.
run_one(store, "ETH", EPOCH + 60.0, cfg)
after_new = store.conn.execute(
    "SELECT COUNT(*) FROM signal_episode").fetchone()[0]
check("post-epoch observation creates an episode",
      after_new == after_old + 1, f"{after_old} -> {after_new}")
store.close()
shutil.rmtree(tmp, ignore_errors=True)

# --------------------------------------------------------------------------
print("\n=== run_cycle creates nothing before the epoch is enabled ===")
store, tmp = fresh_store("noepoch")
seed_good_gaps(store, h)
stats = measuremod.cadence_stats(store)
check("calibration passes", stats["calibration"]["pass"])
check("epoch deliberately not enabled yet", measuremod.epoch_ts(store) is None)
before = store.conn.execute(
    "SELECT COUNT(*) FROM signal_episode").fetchone()[0]
run_one(store, "ETH", 1000.0 + 9 * 300.0, cfg)
after = store.conn.execute(
    "SELECT COUNT(*) FROM signal_episode").fetchone()[0]
check("run_cycle creates no episode while the epoch is unset",
      after == before, f"{before} -> {after}")
check("attempts are still journaled for calibration",
      measuremod.cadence_stats(store)["attempts"] > 0)
store.close()
shutil.rmtree(tmp, ignore_errors=True)

# --------------------------------------------------------------------------
print("\n=== measurement never alters scores or ranks ===")
store, tmp = fresh_store("ranks")
# A legacy row written before any measurement ran.
legacy_sc = mkcard("LEGACY", "SHORT", score=42.0, price=7.0)
store.log_signal(legacy_sc, flagged=True)
legacy_snapshot = store.conn.execute(
    "SELECT coin, score, direction, flagged, flag_version FROM signal_log"
).fetchall()
seed_good_gaps(store, h)
after_snapshot = store.conn.execute(
    "SELECT coin, score, direction, flagged, flag_version FROM signal_log"
).fetchall()
check("existing signal_log rows are byte-identical after measurement",
      legacy_snapshot == after_snapshot[:len(legacy_snapshot)],
      f"{legacy_snapshot} vs {after_snapshot[:len(legacy_snapshot)]}")
# The episode path never rewrites a legacy row's score or direction.
row = store.conn.execute(
    "SELECT score, direction, flagged FROM signal_log WHERE coin='LEGACY'"
).fetchone()
check("legacy score/direction preserved",
      row == (42.0, "SHORT", 1), str(row))
store.close()
shutil.rmtree(tmp, ignore_errors=True)

# --------------------------------------------------------------------------
print("\n=== legacy-only DB stays non-blocking and safe ===")
tmp = tempfile.mkdtemp(prefix="epoch-legacy-")
legacy_path = os.path.join(tmp, "legacy.db")
import sqlite3
conn = sqlite3.connect(legacy_path)
# An old-format signal_log: no config_hash, no episode columns at all.
conn.execute(
    "CREATE TABLE signal_log (id INTEGER PRIMARY KEY, ts INTEGER, coin TEXT,"
    " venue TEXT DEFAULT 'MEXC', flagged INTEGER DEFAULT 1, score REAL,"
    " lean REAL, direction TEXT, earlyness REAL, mag_vol REAL, mag_book REAL,"
    " mag_oi REAL, lean_vol REAL, lean_book REAL, lean_oi REAL, price REAL,"
    " change_24h_pct REAL, quote_vol_24h REAL, spread_pct REAL,"
    " funding_rate REAL, min_notional REAL, veto_codes TEXT,"
    " tradeable INTEGER DEFAULT 1, tier INTEGER DEFAULT 2)")
conn.execute("INSERT INTO signal_log (ts, coin, score, direction)"
             " VALUES (1700000000,'LEGACY',55.0,'LONG')")
conn.commit()
conn.close()

try:
    st = Store(legacy_path)
    check("legacy DB opens without error", True)
    check("legacy row preserved", st.count() == 1, str(st.count()))
    check("legacy row keeps its flag_version=1",
          st.conn.execute("SELECT flag_version FROM signal_log").fetchone()[0] == 1)
    check("no epoch on a legacy DB", measuremod.epoch_ts(st) is None)
    # The measurement/status surface must not raise on a legacy-only DB.
    stats = measuremod.cadence_stats(st)
    check("cadence_stats safe on legacy DB", stats["attempts"] == 0,
          str(stats.get("attempts")))
    check("no episode created from legacy rows",
          st.conn.execute("SELECT COUNT(*) FROM signal_episode").fetchone()[0] == 0)
    st.close()
except Exception as e:
    check("legacy DB opens without error", False, repr(e))
shutil.rmtree(tmp, ignore_errors=True)

# --------------------------------------------------------------------------
print("\n=== startup never enables the epoch automatically ===")
store, tmp = fresh_store("auto")
seed_good_gaps(store, h)
stats = measuremod.cadence_stats(store)
check("calibration passes", stats["calibration"]["pass"])
# Even with calibration satisfied, merely constructing/using the store and
# reading status must not set the epoch: activation is operator-only.
check("epoch still unset without an explicit activation",
      measuremod.epoch_ts(store) is None)
store.close()
shutil.rmtree(tmp, ignore_errors=True)

# --------------------------------------------------------------------------
print("\n=== daemon operator surfaces (status + explicit activation) ===")

daemon_src = open(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "proto", "daemon.py")).read()
check("daemon exposes read-only --measure-status",
      '"--measure-status"' in daemon_src)
check("daemon exposes explicit --enable-measurement-epoch",
      '"--enable-measurement-epoch"' in daemon_src)
check("status/activation never runs inside the scan loop",
      "if args.measure_status or args.enable_measurement_epoch:" in daemon_src)
check("activation is delegated to the calibration gate",
      "measuremod.enable_epoch(app.store, time.time())" in daemon_src)

# The activation must be refused on an uncalibrated DB, end to end.
store, tmp = fresh_store("daemon")
h2, _ = measuremod.config_snapshot(stake=0.1, log_threshold=24.0,
                                   leverage_cap=10, universe_budget=0)
check("activation refused on an uncalibrated DB",
      measuremod.enable_epoch(store, time.time()) is False)
check("epoch remains unset", measuremod.epoch_ts(store) is None)
store.close()
shutil.rmtree(tmp, ignore_errors=True)

print("\n" + ("ALL PASS" if not FAILURES
              else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
