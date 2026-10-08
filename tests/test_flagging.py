"""Flagging tests: stake-aware is_actionable (fail-closed on None), the 24h
verticality veto, and signal_log flag versioning.

Contract (plan Task 4 / spec SS3.2, SS4, SS6.1):
  is_actionable(stake) = self.actionable
                         and min_notional is not None and min_notional <= stake
  flagged              = is_actionable(stake) and score >= 24  (None never flags)
  abs(change_24h) >= 35 -> veto code `late_move` (alongside 1h >= 12)
  signal_log.flag_version: pre-v2 rows 1, rows written from now on 2

Offline: synthetic bars/cards and a temp SQLite file. The only app coupling is
source inspection of the flag line, so no network is touched.
"""

import inspect
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import app as appmod
from proto import scorer
from proto.scorer import Scorecard, Veto
from proto.store import Store

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def is_actionable(card, stake):
    """Call Scorecard.is_actionable, returning None while the method does not
    exist yet. Keeps the whole suite running in the RED phase so every missing
    behaviour is reported as a FAIL instead of the first one aborting the run.
    """
    fn = getattr(card, "is_actionable", None)
    return None if fn is None else fn(stake)


def flagged(card, stake, threshold=24.0):
    """The global flag rule: is_actionable(stake) and score >= threshold."""
    return (is_actionable(card, stake) is True) and card.score >= threshold


def mkcard(coin, score, direction="LONG", vetoes=(), min_not=0.01,
           tradeable=True):
    sc = Scorecard(coin=coin, score=score, direction=direction, lean=0.5,
                   earlyness=0.5, price=1.0, quote_vol_24h=1e6,
                   spread_pct=0.1, funding_rate=0.0001,
                   min_notional=min_not, tradeable=tradeable)
    sc.vetoes = [Veto(v, "reason") for v in vetoes]
    sc.magnitude_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
    sc.lean_parts = {"VOL": 0.5, "BOOK": 0.4, "OI": 0.3}
    return sc


def mk_bars(n=150):
    return [{"ts": 1700000000 + i * 300, "o": 100.0, "h": 100.4, "l": 99.6,
             "c": 100.0, "vol": 1000.0, "amount": 100000.0} for i in range(n)]


BIDS = [(100.0, 50.0), (99.0, 30.0), (98.0, 20.0)]
ASKS = [(101.0, 50.0), (102.0, 30.0), (103.0, 20.0)]

# Pre-v2 signal_log: the schema as it exists on disk before this task, used to
# prove the migration backfills history to flag_version 1.
OLD_SCHEMA = """
CREATE TABLE signal_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              INTEGER NOT NULL,
    coin            TEXT    NOT NULL,
    venue           TEXT    NOT NULL DEFAULT 'MEXC',
    flagged         INTEGER NOT NULL DEFAULT 1,
    score           REAL,
    lean            REAL,
    direction       TEXT,
    earlyness       REAL,
    mag_vol         REAL,
    mag_book        REAL,
    mag_oi          REAL,
    lean_vol        REAL,
    lean_book       REAL,
    lean_oi         REAL,
    price           REAL,
    change_24h_pct  REAL,
    quote_vol_24h   REAL,
    spread_pct      REAL,
    funding_rate    REAL,
    min_notional    REAL,
    veto_codes      TEXT,
    tradeable       INTEGER NOT NULL DEFAULT 1,
    tier            INTEGER NOT NULL DEFAULT 2
);
"""


def test_none_min_notional_never_flags():
    print("=== test_none_min_notional_never_flags ===")
    sc = mkcard("NONE", 90.0, "LONG", min_not=None)
    check("fixture: high score above the 24 bar", sc.score >= 24.0, str(sc.score))
    check("fixture: still ranks (property actionable unchanged)",
          sc.actionable is True, str(sc.actionable))
    check("None min_notional is fail-closed: is_actionable(stake) -> False",
          is_actionable(sc, 0.10) is False, str(is_actionable(sc, 0.10)))
    check("flag rule never flags it despite score 90",
          flagged(sc, 0.10) is False,
          f"score={sc.score} stake=0.10")


def test_24h_runup_vetoed():
    print("\n=== test_24h_runup_vetoed ===")
    flat = mk_bars()

    def score(change_24h):
        return scorer.score_coin("RUN", flat, BIDS, ASKS,
                                 quote_vol_24h=5_000_000, spread_pct=0.2,
                                 change_1h_pct=1.0, change_24h_pct=change_24h,
                                 price=100.0, min_notional=0.01, tier=1)

    up = score(40.0)
    codes = {v.code for v in up.vetoes}
    check("change_24h=40 -> late_move in vetoes", "late_move" in codes,
          str(codes))
    check("only the 24h rule fires (1h move stays under 12%)",
          codes == {"late_move"}, str(codes))
    reason = next((v.reason for v in up.vetoes if v.code == "late_move"), "")
    check("reason names the 24h move", "24h" in reason, reason or "no veto")

    dn = score(-40.0)
    check("mirror: change_24h=-40 vetoed too",
          "late_move" in {v.code for v in dn.vetoes},
          str([v.code for v in dn.vetoes]))
    check("exactly at the threshold (35) fires",
          "late_move" in {v.code for v in score(35.0).vetoes},
          str([v.code for v in score(35.0).vetoes]))
    check("just under (34.9) does not fire",
          "late_move" not in {v.code for v in score(34.9).vetoes},
          str([v.code for v in score(34.9).vetoes]))
    check("1h rule still fires alongside (change_1h=25)",
          "late_move" in {v.code for v in
                          scorer.score_coin("H", flat, BIDS, ASKS,
                                            quote_vol_24h=5_000_000,
                                            spread_pct=0.2, change_1h_pct=25.0,
                                            change_24h_pct=5.0, price=100.0,
                                            min_notional=0.01, tier=1).vetoes},
          "missing 1h late_move")
    check("vetoed run-up never flags",
          flagged(up, 0.10, threshold=0.0) is False
          and is_actionable(up, 0.10) is False,
          str([v.code for v in up.vetoes]))


def test_apply_vetoes_keeps_5_arg_signature():
    """Regression: the pre-v2 5-arg call must keep working. The 24h param
    defaults to 0.0, so veto behaviour is identical when it is absent."""
    print("\n=== test_apply_vetoes_keeps_5_arg_signature ===")
    bars = mk_bars()
    legacy = Scorecard(coin="LEGACY")
    try:
        scorer.apply_vetoes(legacy, bars, 5_000_000, 7.0, 25.0)
        raised = None
    except TypeError as e:
        raised = str(e)
    check("old 5-arg call does not raise TypeError", raised is None,
          raised or "")

    current = Scorecard(coin="CURRENT")
    scorer.apply_vetoes(current, bars, 5_000_000, 7.0, 25.0, 0.0)
    check("5-arg and explicit change_24h=0.0 produce identical vetoes",
          [(v.code, v.reason) for v in legacy.vetoes]
          == [(v.code, v.reason) for v in current.vetoes],
          f"{[(v.code, v.reason) for v in legacy.vetoes]} vs "
          f"{[(v.code, v.reason) for v in current.vetoes]}")
    codes = {v.code for v in legacy.vetoes}
    check("veto set is the pre-v2 one (wide_spread + 1h late_move)",
          codes == {"wide_spread", "late_move"}, str(codes))
    check("absent 24h argument runs no 24h rule",
          not any("in 24h" in v.reason for v in legacy.vetoes),
          str([v.reason for v in legacy.vetoes]))


test_none_min_notional_never_flags()
test_24h_runup_vetoed()
test_apply_vetoes_keeps_5_arg_signature()

print("\n=== is_actionable(stake) matrix (actionable property untouched) ===")
fit = mkcard("FIT", 60.0, "LONG", min_not=0.01)
check("min_notional 0.01 <= stake 0.10 -> True",
      is_actionable(fit, 0.10) is True, str(is_actionable(fit, 0.10)))
check("boundary: min_notional == stake -> True",
      is_actionable(mkcard("EDGE", 50.0, min_not=0.10), 0.10) is True)
over = mkcard("OVER", 60.0, "LONG", min_not=0.50)
check("min_notional 0.50 > stake 0.10 -> False",
      is_actionable(over, 0.10) is False, str(is_actionable(over, 0.10)))
check("property stays stake-agnostic: over-stake coin still ranks",
      over.actionable is True, str(over.actionable))
check("vetoed coin never actionable",
      is_actionable(mkcard("V", 60.0, vetoes=("low_volume",)), 10.0) is False)
check("NEUTRAL direction never actionable",
      is_actionable(mkcard("N", 60.0, direction="NEUTRAL"), 10.0) is False)
check("untradeable never actionable",
      is_actionable(mkcard("U", 60.0, tradeable=False), 10.0) is False)
check("unknown stake fails closed",
      is_actionable(fit, None) is False, str(is_actionable(fit, None)))

print("\n=== flagged = is_actionable(stake) and score >= 24 ===")
check("score 90 + fit min -> flagged",
      flagged(mkcard("HI", 90.0, min_not=0.01), 0.10) is True)
check("score 23.9 below the bar -> not flagged",
      flagged(mkcard("LO", 23.9, min_not=0.01), 0.10) is False)
check("score exactly 24 -> flagged",
      flagged(mkcard("AT", 24.0, min_not=0.01), 0.10) is True)
check("score 99 but None min -> never flagged",
      flagged(mkcard("NM", 99.0, min_not=None), 0.10) is False)
check("score 99 but over-stake min -> never flagged",
      flagged(mkcard("OV", 99.0, min_not=5.0), 0.10) is False)

print("\n=== app.py flag line uses is_actionable(stake) ===")
scan_src = inspect.getsource(appmod.App.scan_once)
check("scan_once flags with sc.is_actionable(self.args.stake)",
      "sc.is_actionable(self.args.stake)" in scan_src,
      "flag line still uses the stake-agnostic property")
check("scan_once still gates on the log threshold",
      "self.args.log_threshold" in scan_src, "log threshold gate missing")
main_src = inspect.getsource(appmod.main)
check("default log threshold is 24 (global constraint)",
      '"--log-threshold", type=float, default=24.0' in main_src,
      "argparse default drifted from 24")

print("\n=== signal_log.flag_version: old rows 1, new rows 2 ===")
tmp = tempfile.mkdtemp()
fresh = Store(os.path.join(tmp, "fresh.db"))
fresh.log_signal(mkcard("FRESH", 70.0), flagged=True)
row = fresh.conn.execute(
    "SELECT flag_version FROM signal_log WHERE coin='FRESH'").fetchone()
check("new row on a fresh DB is flag_version 2", row is not None and row[0] == 2,
      str(row))
fresh.close()

old_path = os.path.join(tmp, "legacy.db")
conn = sqlite3.connect(old_path)
conn.execute(OLD_SCHEMA)
conn.execute(
    "INSERT INTO signal_log (ts, coin, flagged, score, direction)"
    " VALUES (1700000000,'LEGACY',1,55.0,'LONG')")
conn.commit()
conn.close()

lg = Store(old_path)
row = lg.conn.execute(
    "SELECT flag_version FROM signal_log WHERE coin='LEGACY'").fetchone()
check("pre-v2 row backfilled to flag_version 1", row is not None and row[0] == 1,
      str(row))
lg.log_signal(mkcard("POST", 66.0), flagged=True)
row = lg.conn.execute(
    "SELECT flag_version FROM signal_log WHERE coin='POST'").fetchone()
check("row written after the migration is flag_version 2",
      row is not None and row[0] == 2, str(row))
check("history survives the migration", lg.count() == 2, str(lg.count()))
lg.close()

lg2 = Store(old_path)          # reopen: the migration must be idempotent
rows = dict(lg2.conn.execute("SELECT coin, flag_version FROM signal_log"))
check("reopen keeps versions stable (migration idempotent)",
      rows == {"LEGACY": 1, "POST": 2}, str(rows))
lg2.close()

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
