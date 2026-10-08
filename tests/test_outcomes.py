"""Outcome resolver tests: symmetric returns, bar mapping, collector-backed 7d.

Contract (plan Task 5 / spec SS5 "Outcome timing" + "Collector coverage"):
  fut        = 5m bars with ts > signal.ts (the entry bar is fut[0])
  horizon h  = signed return from signal price to close of fut[need-1]
  excursions = max/min signed returns over fut[:need]
  horizons   = 1h 12, 4h 48, 24h 288, 7d 2016 bars of 5m
  7d comes from collector bars ONLY - MEXC klines caps at 2000 bars
  fewer than `need` future bars -> horizon stays unresolved, never zero-filled
  a coin that left the scan rotation still resolves: the resolver needs the
  venue symbol map plus collector files, never build_universe.

Offline: temp SQLite store, synthetic bars, patched venue feeds.
"""

import inspect
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import collector as colmod
from proto import mexc as mexcmod
from proto import outcomes as outmod
from proto import scan as scanmod
from proto.store import Store

FAILURES = []

SIGNAL_TS = 1_700_000_000
ENTRY = 100.0  # signal price the outcome is measured from


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


def near(a, b):
    return a is not None and abs(a - b) < 1e-6


def future_bars(n=2016, step=0.01, spike=101.0):
    """n future 5m bars: +step*100%/bar trend with a spike at bar 5, so
    `return from fut[need-1]` and `max over fut[:need]` are distinguishable.
    Includes one bar exactly AT the signal ts - the window is strictly
    `ts > signal.ts`, so that bar (c=999) must never enter fut."""
    bars = [{"ts": SIGNAL_TS, "o": ENTRY, "h": ENTRY, "l": ENTRY,
             "c": 999.0, "vol": 1.0, "amount": 1.0}]
    for i in range(n):
        c = spike if i == 5 else ENTRY + step * (i + 1)
        bars.append({"ts": SIGNAL_TS + 300 * (i + 1), "o": ENTRY, "h": c,
                     "l": ENTRY, "c": c, "vol": 1.0, "amount": 1.0})
    return bars


def insert_signal(store, coin, price=ENTRY, direction="LONG", ts=SIGNAL_TS):
    cur = store.conn.execute(
        "INSERT INTO signal_log (ts, coin, price, direction) VALUES (?,?,?,?)",
        (ts, coin, price, direction))
    store.conn.commit()
    return cur.lastrowid


def outcome_rows(store, sid):
    return {r[0]: r[1:] for r in store.conn.execute(
        "SELECT horizon, return_pct, max_fav, max_adv FROM outcome_log"
        " WHERE signal_id=?", (sid,))}


def rotation_fixture():
    """160-coin venue universe, all tradeable at stake 0.10. Volume rank 115
    is coin OLD - outside the 150 the scan rotation picks up."""
    tk, det = {}, {}
    for rank in range(160):
        coin = "OLD" if rank == 115 else f"COIN{rank:03d}"
        sym = coin + "_USDT"
        tk[sym] = {"lastPrice": 1.0, "amount24": float((160 - rank) * 1_000_000),
                   "riseFallRate": 0.01, "fundingRate": 0.0}
        det[sym] = {"minVol": 1, "contractSize": 0.05}
    return tk, det


def install_scan_feeds(tk, det):
    """Patch scan's venue feeds (offline). Returns a restore callable."""
    orig = (scanmod.mexc.tickers, scanmod.mexc.details, scanmod._bybit_symbol_set)
    scanmod.mexc.tickers = lambda: tk
    scanmod.mexc.details = lambda: det
    scanmod._bybit_symbol_set = lambda: None

    def restore():
        (scanmod.mexc.tickers, scanmod.mexc.details,
         scanmod._bybit_symbol_set) = orig
    return restore


def test_signed_return_mirrors():
    print("=== test_signed_return_mirrors ===")
    check("long +10 mirrors", abs(outmod.signed_return(100, 110, "LONG") - 10.0) < 1e-9)
    check("short -10 mirrors",
          abs(outmod.signed_return(100, 90, "SHORT") - 10.0) < 1e-9,
          str(outmod.signed_return(100, 90, "SHORT")))
    check("long loss negative", outmod.signed_return(100, 90, "LONG") < 0)
    check("short loss negative", outmod.signed_return(100, 110, "SHORT") < 0)


def test_rotated_out_coin_still_resolves():
    """Review focus: a coin that leaves rotation between signal and outcome
    must still resolve - from collector bars for 7d, never REST."""
    print("=== test_rotated_out_coin_still_resolves ===")
    tmp = tempfile.mkdtemp()
    st = Store(os.path.join(tmp, "outcomes.db"))
    sid = insert_signal(st, coin="OLD")
    colmod.append_bars(tmp, "OLD", future_bars(2016))

    # The signal coin sits in the venue map but outside the current rotation.
    tk, det = rotation_fixture()
    restore = install_scan_feeds(tk, det)
    try:
        rotation = scanmod.build_universe(0.10)
    finally:
        restore()
    rotation_coins = [c for _, c in rotation]
    check("fixture: rotation fills 150", len(rotation_coins) == 150,
          str(len(rotation_coins)))
    check("fixture: signal coin absent from current rotation",
          "OLD" not in rotation_coins, str(rotation_coins[:5]))
    symbol_map = {scanmod.canon(s): s for s in tk}   # venue map, not rotation

    # REST answers with only 500 bars (cap: 2000) -> 7d is impossible from it.
    rest_calls = []

    def fake_klines(sym, interval="5m", limit=200):
        rest_calls.append((sym, interval, limit))
        return future_bars(500, step=0.02, spike=101.5)

    orig_klines = mexcmod.klines
    mexcmod.klines = fake_klines
    try:
        done = outmod.resolve_pending(st, symbol_map, data_dir=tmp)
    finally:
        mexcmod.klines = orig_klines

    check("all four horizons resolved", done == 4, str(done))
    rows = outcome_rows(st, sid)
    check("outcome rows for 1h/4h/24h/7d",
          set(rows) == {"1h", "4h", "24h", "7d"}, str(sorted(rows)))

    # Short horizons come from REST (default rest+collector): step 0.02 bars.
    check("1h return = close of REST fut[11]", near(rows.get("1h", (None,))[0], 0.24),
          str(rows.get("1h")))
    r4 = rows.get("4h", (None, None, None))
    check("4h return fut[47] < max_fav (spike inside window)",
          near(r4[0], 0.96) and near(r4[1], 1.5), str(r4))

    # REST held only 500 bars - these values can ONLY be collector-derived.
    r7 = rows.get("7d", (None, None, None))
    check("7d return = close of collector fut[2015]", near(r7[0], 20.16), str(r7))
    check("7d excursions over collector fut[:2016]",
          near(r7[1], 20.16) and near(r7[2], 0.01), str(r7))

    check("REST fetched once per signal, never for 7d",
          rest_calls == [("OLD_USDT", "5m", 2000)], str(rest_calls))
    check("resolver never consults the scan rotation",
          "build_universe" not in inspect.getsource(outmod.resolve_pending))
    st.close()


def test_insufficient_bars_stay_unresolved():
    """Spec: fewer than `need` future bars -> horizon stays unresolved,
    never zero-filled."""
    print("=== test_insufficient_bars_stay_unresolved ===")
    tmp = tempfile.mkdtemp()
    st = Store(os.path.join(tmp, "thin.db"))
    sid = insert_signal(st, coin="THIN")
    colmod.append_bars(tmp, "THIN", future_bars(100))        # < 2016 for 7d

    def fake_klines(sym, interval="5m", limit=200):
        return future_bars(5, step=0.02, spike=101.5)        # < 12 for 1h

    orig = mexcmod.klines
    mexcmod.klines = fake_klines
    try:
        done = outmod.resolve_pending(st, {"THIN": "THIN_USDT"},
                                      horizons=("1h", "7d"), data_dir=tmp)
    finally:
        mexcmod.klines = orig
    check("nothing resolved", done == 0, str(done))
    check("no zero-filled rows written", len(outcome_rows(st, sid)) == 0,
          str(outcome_rows(st, sid)))
    st.close()


def test_collector_mode_reads_only_local():
    """bar_source="collector": every horizon from collector files, REST never
    touched (offline / degraded-venue path)."""
    print("=== test_collector_mode_reads_only_local ===")
    tmp = tempfile.mkdtemp()
    st = Store(os.path.join(tmp, "offline.db"))
    sid = insert_signal(st, coin="OFF")
    colmod.append_bars(tmp, "OFF", future_bars(2016))

    rest_calls = []

    def fake_klines(sym, interval="5m", limit=200):
        rest_calls.append((sym, interval, limit))
        return future_bars(500, step=0.02, spike=101.5)

    orig = mexcmod.klines
    mexcmod.klines = fake_klines
    try:
        done = outmod.resolve_pending(st, {"OFF": "OFF_USDT"},
                                      horizons=("1h", "7d"),
                                      bar_source="collector", data_dir=tmp)
    finally:
        mexcmod.klines = orig
    rows = outcome_rows(st, sid)
    check("both horizons resolved offline", done == 2 and set(rows) == {"1h", "7d"},
          str(rows))
    check("1h return is the collector value 0.12, not the REST 0.24",
          near(rows.get("1h", (None,))[0], 0.12), str(rows.get("1h")))
    check("7d return from collector", near(rows.get("7d", (None,))[0], 20.16),
          str(rows.get("7d")))
    check("REST never touched", rest_calls == [], str(rest_calls))
    st.close()


def test_bar_source_contract():
    print("=== test_bar_source_contract ===")
    try:
        outmod.resolve_pending(Store(os.path.join(tempfile.mkdtemp(), "x.db")),
                               {}, bar_source="ftp")
        err = None
    except ValueError as e:
        err = str(e)
    check("unknown bar_source raises ValueError", err is not None, str(err))
    check("default bar_source is rest+collector",
          outmod.resolve_pending.__defaults__
          == (("1h", "4h", "24h", "7d"), "rest+collector", "data/bars"),
          str(outmod.resolve_pending.__defaults__))


def test_pending_outcomes_oldest_first_untruncated():
    """Review finding (Important): pending_outcomes returned the NEWEST 500
    rows (ORDER BY id DESC LIMIT 500), so once the backlog passes 500 the
    oldest signals are never visited and never resolve. Pin: every row is
    returned, oldest first; the optional `limit` arg stays for callers that
    bound the scan (backward-compatible signature)."""
    print("=== pending_outcomes: oldest first, no silent 500-row truncation ===")
    st = Store(os.path.join(tempfile.mkdtemp(), "pending.db"))
    n = 520
    st.conn.executemany(
        "INSERT INTO signal_log (ts, coin, price, direction) VALUES (?,?,?,?)",
        [(SIGNAL_TS + i, f"BACK{i:03d}", ENTRY, "LONG") for i in range(n)])
    st.conn.commit()

    rows = st.pending_outcomes()
    ids = [r[0] for r in rows]
    check(f"all {n} pending rows returned (cap used to keep 500)",
          len(rows) == n, str(len(rows)))
    check("oldest first", ids == sorted(ids), str(ids[:5]))
    check("first row is the oldest signal", ids and ids[0] == 1, str(ids[:3]))
    check("last row is the newest signal", ids and ids[-1] == n, str(ids[-3:]))
    check("explicit limit still honored (signature backward-compatible)",
          len(st.pending_outcomes(limit=10)) == 10,
          str(len(st.pending_outcomes(limit=10))))
    st.close()


def test_resolver_visits_oldest_beyond_500_pending():
    """End-to-end form of the same finding: with >500 pending rows the OLDEST
    signal must still be resolved. The GHOST* backlog rows are absent from
    the symbol map, so they are skipped without any fetch - only ordering and
    the cap decide whether the old signal at id 1 is ever reached."""
    print("=== resolver reaches the oldest signal past a 500-row backlog ===")
    tmp = tempfile.mkdtemp()
    st = Store(os.path.join(tmp, "backlog.db"))
    oldest = insert_signal(st, coin="OLD")
    st.conn.executemany(
        "INSERT INTO signal_log (ts, coin, price, direction) VALUES (?,?,?,?)",
        [(SIGNAL_TS, f"GHOST{i:03d}", ENTRY, "LONG") for i in range(504)])
    st.conn.commit()
    colmod.append_bars(tmp, "OLD", future_bars(48))

    done = outmod.resolve_pending(st, {"OLD": "OLD_USDT"}, horizons=("1h",),
                                  bar_source="collector", data_dir=tmp)
    check("backlog exceeds the old 500 cap", st.count() > 500, str(st.count()))
    check("oldest signal visited and resolved", done == 1, str(done))
    check("outcome row written for the oldest id",
          "1h" in outcome_rows(st, oldest), str(outcome_rows(st, oldest)))
    st.close()


def run(fn):
    print(f"--- {fn.__name__}")
    try:
        fn()
    except Exception as e:
        print(f"  FAIL  {fn.__name__}  raised {type(e).__name__}: {e}")
        FAILURES.append(fn.__name__)


for t in (test_signed_return_mirrors,
          test_rotated_out_coin_still_resolves,
          test_insufficient_bars_stay_unresolved,
          test_collector_mode_reads_only_local,
          test_bar_source_contract,
          test_pending_outcomes_oldest_first_untruncated,
          test_resolver_visits_oldest_beyond_500_pending):
    run(t)

print("\n" + ("ALL PASS" if not FAILURES else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
