"""Task 1: immutable epoch records + watcher-only scan write gate.

Offline only: no network. Pins the Task 1 interface contract:

- append_epoch(conn, mode) writes an immutable (epoch_id, mode, cut_ts)
  row and moves the active pointer; rollback is a NEW row, never an
  overwrite.
- writes_signals(conn) is False while a watcher-only epoch is active.
- While watcher-only is active, no signal_log / coin_state / plan_log
  write lands (log_signal, upsert_current, plan-attach call sites gated).
- Pre-cut rows keep their epoch label; cohort queries separate epochs
  (no pooling across the cut).

Run: python3 tests/test_epoch_cut.py
"""

import os
import sys
import tempfile
import shutil

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto.store import Store
from proto.scorer import Scorecard

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
    sc.min_notional = 0.01
    sc.quote_vol_24h = 1e6
    sc.spread_pct = 0.1
    return sc


class FakePlan:
    direction = "LONG"
    entry_low = 99.0
    entry_high = 101.0
    stop = 97.0
    tp1 = 103.0
    tp2 = 105.0
    leverage = 5
    notional = 100.0
    max_loss = 3.0
    valid = True


def fresh_store(tag="t"):
    tmp = tempfile.mkdtemp(prefix=f"epoch-cut-{tag}-")
    return Store(os.path.join(tmp, "m.db")), tmp


# --------------------------------------------------------------------------
print("=== watcher-only blocks signal writes; rollback is a new row ===")
from proto.store import append_epoch, active_epoch, writes_signals  # noqa: E402

store, tmp = fresh_store("gate")
conn = store.conn
check("writes allowed before any epoch", writes_signals(conn) is True)
eid = append_epoch(conn, "watcher-only")
check("watcher-only blocks signal writes", writes_signals(conn) is False)
eid2 = append_epoch(conn, "scan")
check("rollback is a new row (never an overwrite)", eid2 != eid,
      f"{eid} vs {eid2}")
check("scan epoch re-enables signal writes", writes_signals(conn) is True)
ep = active_epoch(conn)
check("active pointer follows the latest row",
      isinstance(ep, dict) and ep.get("epoch_id") == eid2
      and ep.get("mode") == "scan", str(ep))
rows = conn.execute(
    "SELECT epoch_id, mode FROM epoch_log ORDER BY cut_ts, epoch_id"
).fetchall()
check("both epoch rows retained (append-only, nothing overwritten)",
      len(rows) == 2 and {r[1] for r in rows} == {"watcher-only", "scan"},
      str(rows))
store.close()
shutil.rmtree(tmp, ignore_errors=True)

# --------------------------------------------------------------------------
print("\n=== gated writes land nothing while watcher-only is active ===")
store, tmp = fresh_store("writes")
conn = store.conn
append_epoch(conn, "watcher-only")
n_sig0 = store.count()
n_state0 = conn.execute("SELECT COUNT(*) FROM coin_state").fetchone()[0]
n_plan0 = conn.execute("SELECT COUNT(*) FROM plan_log").fetchone()[0]
sid = store.log_signal(mkcard("BTC"), flagged=True)
store.upsert_current(mkcard("BTC"), True, 9999)
store.log_plan(9999, FakePlan())
check("log_signal writes nothing while watcher-only",
      store.count() == n_sig0, f"{n_sig0} -> {store.count()}")
check("log_signal returns None while watcher-only", sid is None, str(sid))
check("upsert_current writes nothing while watcher-only",
      conn.execute("SELECT COUNT(*) FROM coin_state").fetchone()[0] == n_state0)
check("plan-attach writes nothing while watcher-only",
      conn.execute("SELECT COUNT(*) FROM plan_log").fetchone()[0] == n_plan0)
append_epoch(conn, "scan")  # rollback
sid = store.log_signal(mkcard("BTC"), flagged=True)
store.upsert_current(mkcard("BTC"), True, sid)
store.log_plan(sid, FakePlan())
check("signal write lands after rollback to scan",
      store.count() == n_sig0 + 1, str(store.count()))
check("board write lands after rollback",
      conn.execute("SELECT COUNT(*) FROM coin_state").fetchone()[0]
      == n_state0 + 1)
check("plan-attach lands after rollback",
      conn.execute("SELECT COUNT(*) FROM plan_log").fetchone()[0]
      == n_plan0 + 1)
store.close()
shutil.rmtree(tmp, ignore_errors=True)

# --------------------------------------------------------------------------
print("\n=== pre-cut rows keep their epoch label; cohorts never pool ===")
store, tmp = fresh_store("cohort")
conn = store.conn
pre_eid = append_epoch(conn, "scan")
pre_sid = store.log_signal(mkcard("PRE"), flagged=True)
append_epoch(conn, "watcher-only")
append_epoch(conn, "scan")
post_rows = conn.execute(
    "SELECT epoch_id FROM signal_log WHERE id=?", (pre_sid,)).fetchone()
check("pre-cut row keeps its epoch label", post_rows[0] == pre_eid,
      str(post_rows))
post_sid = store.log_signal(mkcard("POST"), flagged=True)
labels = dict(conn.execute(
    "SELECT id, epoch_id FROM signal_log WHERE id IN (?,?)",
    (pre_sid, post_sid)).fetchall())
check("post-rollback row belongs to the new epoch, not the pre-cut one",
      labels[post_sid] != labels[pre_sid], str(labels))
pre_cohort = conn.execute(
    "SELECT COUNT(*) FROM signal_log WHERE epoch_id=?", (pre_eid,)).fetchone()[0]
check("cohort query separates epochs (no pooling)",
      pre_cohort == 1
      and store.count() == 2, f"pre-cohort={pre_cohort} total={store.count()}")
from proto.store import signals_in_epoch  # noqa: E402
check("signals_in_epoch returns the pre-cut cohort only",
      [r["id"] for r in signals_in_epoch(conn, pre_eid)] == [pre_sid])
store.close()
shutil.rmtree(tmp, ignore_errors=True)

# --------------------------------------------------------------------------
print("\n=== measurement universe policy is the stakeless revision ===")
from proto.scan import MEASUREMENT_UNIVERSE_POLICY  # noqa: E402

check("policy constant bumped",
      MEASUREMENT_UNIVERSE_POLICY == "mexc_full_stakeless_v2",
      MEASUREMENT_UNIVERSE_POLICY)

print("\n" + ("ALL PASS" if not FAILURES
              else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
