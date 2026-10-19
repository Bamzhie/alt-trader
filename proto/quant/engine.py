"""Watcher: per-coin state refreshed once per newly CLOSED 5-minute bar.

Reads ONLY the collector's bar files (no per-coin REST calls), so a full
576-coin cycle costs seconds, not minutes - which is why it does not inherit
the scan/measurement cadence gaps.

State policy (the "never recompute" rule has a crash policy):
  * start-up and any gap: rebuild the coin from its last contiguous run in the
    bar file by replaying the SAME FeatureStream used in research (batch and
    incremental are identical by construction); no events are emitted from
    replayed history; features stay unavailable until the warm-up is satisfied
  * duplicate / out-of-order bars are ignored (watermark + sort/dedupe)
  * invalid bars drop the coin to unavailable until rebuilt
  * the global pause stops emission when data is stale or a manual flag is set;
    state keeps advancing so recovery is immediate

Events from unpromoted rules are SHADOW: logged and measured, never shown as
actionable.  Nothing here places orders.

Local shadow-only contract (ALT RADAR integration): the embedded watcher
never consults promotions at emission time, so every persisted row is
``mode='shadow'`` even when a passed promotion record exists for the rule.
A passed promotion is a research fact (readable via promotions.json and
``GET /quant-status``); it NEVER switches live emission to ACTIONABLE.
Enabling ACTIONABLE emission requires a separately approved stage.
"""
import gzip
import heapq
import os
import time
from collections import deque

from . import barrier as B
from . import risk as riskmod
from .bars import load_coin, list_coins, contiguous_runs, valid_bar
from .config import DEFAULT, BAR_S, BARS_PER_DAY
from .registry import REGISTERED, by_id
from .selector import Selector, utc_day
from .stream import FeatureStream, Cusum

RECENT_BARS = 330                       # >= longest horizon + margin
REPLAY_BARS = (DEFAULT.feat.rvol_lags_days * BARS_PER_DAY
               + DEFAULT.feat.warmup_bars + 120)
TAIL_ROWS = 600

# The rebuild only ever replays the LAST REPLAY_BARS bars, so the archive
# only has to be read that far back.  The bound is the one the feature
# warmup needs: 14 days of same-slot volume for rvol (14 * 288 = 4032) plus
# the 6-day warmup (6 * 288 = 1728) = 5760, and REPLAY_BARS (5880) keeps
# the historical margin.  The bounded reader is EXACTLY equivalent to the
# full parse: it keeps the same REPLAY_BARS largest timestamps with the same
# "first occurrence of a duplicate wins" rule, so the replayed window - and
# therefore every feature value - is identical (see
# tests/test_ws_f_startup.py).
BOUNDED_REBUILD_BARS = max(15 * BARS_PER_DAY, REPLAY_BARS)


def read_tail(path, n=TAIL_ROWS):
    """Last n rows of a bar file as sorted, de-duplicated tuples
    (ts,o,h,l,c,vol,amt).  Does not assume file order."""
    dq = deque(maxlen=n)
    try:
        with gzip.open(path, "rt", newline="") as f:
            next(f, None)
            for line in f:
                dq.append(line)
    except (OSError, EOFError):
        return []
    rows = {}
    for line in dq:
        p = line.strip().split(",")
        try:
            rows[int(p[0])] = (int(p[0]), float(p[1]), float(p[2]), float(p[3]),
                               float(p[4]), float(p[5]), float(p[6]))
        except (ValueError, IndexError):
            continue
    return [rows[t] for t in sorted(rows)]


def load_coin_window(data_dir, coin, n=BOUNDED_REBUILD_BARS):
    """The n newest bars of a coin's archive, in timestamp order.

    One streaming pass over the gzip file that keeps ONLY the n largest
    timestamps (first occurrence of a duplicate wins, exactly like
    ``bars.load_coin``), so a 28k-row archive is never materialised in
    full.  The result has the same shape as ``load_coin`` and selects the
    same bars as "the last n of the full parse", so a rebuild over it
    replays EXACTLY the same window (and produces identical features) as a
    rebuild over the whole archive.

    Only the timestamp of every row is parsed while streaming; the OHLCV
    values are parsed for the n rows that are kept.  A row that is not
    well formed (a truncated archive, say) makes the whole coin fall back
    to the full parse, so the window is never silently different from the
    one ``load_coin`` would have produced.

    Returns None when the file is missing or has no usable header (the
    same contract as ``load_coin``).
    """
    path = os.path.join(data_dir, f"{coin}.csv.gz")
    if not os.path.exists(path):
        return None
    keep = {}
    heap = []
    seen = set()
    malformed = False
    try:
        with gzip.open(path, "rt", newline="") as f:
            header = next(f, None)
            if not header or not header.startswith("ts"):
                return None
            for line in f:
                i = line.find(",")
                if i < 0:
                    continue
                try:
                    ts = int(line[:i])
                except ValueError:
                    continue
                if ts in seen:
                    continue
                seen.add(ts)
                if line.count(",") != 6:
                    # A row that is not well formed: fall back to the full
                    # parse instead of guessing.
                    malformed = True
                    break
                if len(heap) < n:
                    heapq.heappush(heap, ts)
                    keep[ts] = line
                elif ts > heap[0]:
                    old = heapq.heapreplace(heap, ts)
                    keep.pop(old, None)
                    keep[ts] = line
    except (OSError, EOFError):
        return None
    if malformed:
        return load_coin(data_dir, coin)
    if not keep:
        return None
    rows = {}
    for ts, line in keep.items():
        p = line.split(",")
        try:
            rows[ts] = (float(p[1]), float(p[2]), float(p[3]),
                        float(p[4]), float(p[5]), float(p[6]))
        except (ValueError, IndexError):
            # A kept row does not parse: the full parse decides.
            return load_coin(data_dir, coin)
    ts = sorted(rows)
    out = {"ts": ts, "o": [], "h": [], "l": [], "c": [], "vol": [], "amt": []}
    for t in ts:
        o, h, l, c, v, a = rows[t]
        out["o"].append(o); out["h"].append(h); out["l"].append(l)
        out["c"].append(c); out["vol"].append(v); out["amt"].append(a)
    return out


def rebuild_window(bars, n=REPLAY_BARS):
    """The replay window ``(a, z)`` of a sorted bar list.

    ``z`` is the exclusive end of the last contiguous run; ``a`` is that
    run's start pulled back to at most ``n`` bars.  Both the full parse and
    the bounded window select the SAME bars through this function, which is
    what makes the two rebuilds feature-identical.
    """
    if not bars or not bars.get("ts"):
        return None
    runs = contiguous_runs(bars["ts"])
    if not runs:
        return None
    a, z = runs[-1]
    a = max(a, z - n)
    return a, z


class CoinState:
    __slots__ = ("stream", "last_ts", "recent", "cusums", "ok", "reason", "sig")

    def __init__(self, cfg, hs):
        self.stream = FeatureStream(cfg.feat)
        self.last_ts = None
        self.recent = deque(maxlen=RECENT_BARS)
        self.cusums = {h: Cusum(h, cfg.feat.cusum_k, cfg.feat.cusum_cooldown)
                       for h in hs}
        self.ok = False
        self.reason = "uninitialized"
        self.sig = None                 # (size, mtime_ns) of the file last read


class Watcher:
    def __init__(self, data_dir, store, cfg=DEFAULT, rules=REGISTERED,
                 promo_dir=None, now_fn=time.time, equity=1000.0):
        self.data_dir = data_dir
        self.store = store
        self.cfg = cfg
        self.rules = tuple(rules)
        self.now_fn = now_fn
        self.equity = equity
        self.promo_dir = promo_dir or os.path.dirname(store.path) or "."
        self.hs = sorted({r.params.cusum_h for r in self.rules
                          if r.params.family == "residual_shock"})
        self.states = {}
        self.btc_ret = {}
        self.btc_last = None
        self.selector = Selector(cfg, store, riskmod.Exposure(cfg.risk))
        self.counters = {"emitted": 0, "suppressed": 0, "resolved": 0,
                         "gaps": 0, "invalid": 0, "resumed": 0,
                         "watermark_rollback": 0}
        # Persisted per-coin watermarks, read back from the quant store
        # (WS-F): they say which bars were already processed, so a restart
        # never re-emits them.
        self._watermarks = {}

    # ---- per-coin state ------------------------------------------------
    def _rebuild(self, coin, reason, full=False):
        """Replay a coin's last contiguous run into a fresh state.

        ``full=False`` (the default) reads only the bounded tail window the
        feature warmup needs; ``full=True`` parses the whole archive (the
        historical behaviour) and is kept for the equivalence test.  Both
        replay the SAME bars, so both produce identical features.

        The persisted ``quant_watermark`` of the coin is read back: it
        proves which bars were already processed, and it lets the rebuild
        skip the redundant full-file tail read that would follow it (the
        rebuild already covers the file's current content).  If the archive
        is BEHIND the watermark (the file changed underneath) the shortcut
        is refused and the current behaviour is kept.
        """
        cs = CoinState(self.cfg, self.hs)
        path = os.path.join(self.data_dir, f"{coin}.csv.gz")
        try:
            st_before = os.stat(path)
            sig_before = (st_before.st_size, st_before.st_mtime_ns)
        except OSError:
            sig_before = None
        bars = (load_coin(self.data_dir, coin) if full
                else load_coin_window(self.data_dir, coin,
                                      BOUNDED_REBUILD_BARS))
        if not bars:
            cs.reason = "no bars"
            self.states[coin] = cs
            return cs
        window = rebuild_window(bars, REPLAY_BARS)
        if window is None:
            cs.reason = "no bars"
            self.states[coin] = cs
            return cs
        a, z = window
        max_ts = bars["ts"][z - 1] if z else None
        for i in range(a, z):
            self._feed(cs, coin, bars["ts"][i], bars["o"][i], bars["h"][i],
                       bars["l"][i], bars["c"][i], bars["amt"][i], emit=False)
            if not cs.ok and cs.reason == "invalid bar":
                break
        if cs.reason == "uninitialized":
            cs.ok = True
            cs.reason = ""
        self.states[coin] = cs
        # The persisted watermark (read back from the quant store) says which
        # bars were already processed before the restart.  A watermark
        # AHEAD of the archive means the file changed underneath: the
        # shortcut is refused and the current behaviour is kept.
        wm = self._watermarks.get(coin)
        if wm is not None and max_ts is not None and wm > max_ts:
            self.counters["watermark_rollback"] = (
                self.counters.get("watermark_rollback", 0) + 1)
            return cs
        if wm is not None:
            self.counters["resumed"] = self.counters.get("resumed", 0) + 1
        # The rebuild consumed the whole window and the file did not change
        # while it was read: the next advance can skip the tail read.
        if (cs.ok and max_ts is not None and cs.last_ts == max_ts
                and sig_before is not None):
            try:
                st_after = os.stat(path)
                sig_after = (st_after.st_size, st_after.st_mtime_ns)
            except OSError:
                sig_after = None
            if sig_after is not None and sig_after == sig_before:
                cs.sig = sig_after
        return cs

    def _feed(self, cs, coin, ts, o, h, l, c, amt, emit):
        if coin == "BTC":
            if cs.last_ts is not None and ts - cs.last_ts == BAR_S and cs.recent:
                prev = cs.recent[-1][4]
                import math
                self.btc_ret[ts] = math.log(c / prev) if prev > 0 and c > 0 else 0.0
                if len(self.btc_ret) > 4000:
                    for k in sorted(self.btc_ret)[:1000]:
                        del self.btc_ret[k]
            self.btc_last = ts if (self.btc_last is None or ts > self.btc_last) else self.btc_last
        if not valid_bar(o, h, l, c):
            cs.ok, cs.reason = False, "invalid bar"
            self.counters["invalid"] += 1
            return None
        snap = None
        try:
            snap = cs.stream.update(ts, o, h, l, c, amt, self.btc_ret.get(ts, 0.0))
        except ValueError:
            cs.ok, cs.reason = False, "invalid bar"
            return None
        cs.recent.append((ts, o, h, l, c))
        cs.last_ts = ts
        if snap is None:
            return None
        events = []
        for hh, cu in cs.cusums.items():
            s = cu.update(snap.n, snap.z)
            if s:
                events.append(("residual_shock", hh, s))
        if (snap.up_start or snap.dn_start) and snap.rvol is not None:
            events.append(("breakout_start", snap.rvol, 1 if snap.up_start else -1))
        if emit and events:
            self._candidates(coin, snap, events)
        return snap

    # ---- candidates -> selector -> store -------------------------------
    def _candidates(self, coin, snap, events):
        now = self.now_fn()
        sel = self.cfg.selector
        age_bars = (now - (snap.ts + BAR_S)) / BAR_S
        day = time.strftime("%Y-%m-%d", time.gmtime(snap.ts))
        paused = self.paused()
        for spec in self.rules:
            p = spec.params
            brk = None
            for fam, param, side in events:
                if fam != p.family:
                    continue
                if fam == "breakout_start" and param < p.rvol_min:
                    continue
                if fam == "residual_shock" and param != p.cusum_h:
                    continue
                brk = side
            if brk is None:
                continue
            side = -brk if p.policy == "fade" else brk
            rid = spec.rule_id
            if paused:
                self.store.bump_suppressed(day, rid, "paused")
                self.counters["suppressed"] += 1
                continue
            if age_bars > sel.max_signal_age_bars:
                self.store.bump_suppressed(day, rid, "stale_signal")
                self.counters["suppressed"] += 1
                continue
            if snap.sigma <= 1e-6:
                self.store.bump_suppressed(day, rid, "no_vol")
                continue
            plan = riskmod.plan_levels(snap.close, side, snap.sigma, spec.geometry)
            # Local shadow-only contract: the embedded watcher is research
            # logging only. Passed promotions are research facts (readable
            # via promotions.json and /quant-status); they NEVER switch
            # live emission to ACTIONABLE — that requires a separately
            # approved stage. The watcher therefore never consults
            # promotions here, so every emitted row is SHADOW.
            promo = None
            ok, why, mode, cost = self.selector.decide(
                spec, coin, snap.ts, snap.qv24, plan["stop_frac"], promo)
            if not ok:
                self.store.bump_suppressed(day, rid, why)
                self.counters["suppressed"] += 1
                continue
            size = riskmod.size_position(self.equity, plan["stop_frac"], self.cfg.risk)
            feats = {"rvol": snap.rvol, "z": snap.z, "z1h": snap.z1h,
                     "z4h": snap.z4h, "beta": snap.beta, "break_side": brk,
                     "cost_r": cost / plan["stop_frac"],
                     "entry_assumption": "next bar open (market), no fill model",
                     "size": {k: size[k] for k in ("notional", "leverage",
                                                   "risk_frac_eff", "capped")},
                     "warnings": size["warnings"] + (
                         ["RESEARCH SHADOW: no validated edge; not actionable"]
                         if mode == "SHADOW" else [])}
            eid = self.store.insert_event({
                "rule_id": rid, "config_hash": self.cfg.config_hash(),
                "coin": coin, "side": side, "bar_ts": snap.ts, "mode": mode,
                "role": spec.role, "ref_price": snap.close, "stop": plan["stop"],
                "tp": plan["tp"], "stop_frac": plan["stop_frac"],
                "horizon": spec.geometry.horizon_bars, "r_tp": spec.geometry.r_tp,
                "cost_frac": cost, "sigma": snap.sigma, "qv24": snap.qv24,
                "features": feats})
            if eid is not None:
                self.counters["emitted"] += 1

    # ---- pause ---------------------------------------------------------
    def paused(self):
        if self.store.get_meta("pause") == "1":
            return True
        return self.store.get_meta("auto_pause") == "1"

    def _update_pause(self):
        sel = self.cfg.selector
        now = self.now_fn()
        reason = None
        if self.btc_last is None:
            reason = "no BTC bars"
        elif now - (self.btc_last + BAR_S) > (sel.stale_coin_bars + 1) * BAR_S:
            reason = "BTC bars stale"
        else:
            live = [c for c, s in self.states.items() if c != "BTC"]
            if live:
                stale = 0
                for c in live:
                    s = self.states[c]
                    if (not s.ok) or s.last_ts is None or \
                            (self.btc_last - s.last_ts) / BAR_S > sel.stale_coin_bars:
                        stale += 1
                if stale / len(live) > sel.pause_stale_fraction:
                    reason = f"{stale}/{len(live)} coins stale"
        self.store.set_meta("auto_pause", "1" if reason else "0")
        self.store.set_meta("auto_pause_reason", reason or "")
        return reason

    # ---- one cycle -----------------------------------------------------
    def _advance(self, coin, emit=True):
        path = os.path.join(self.data_dir, f"{coin}.csv.gz")
        if not os.path.exists(path):
            return
        cs = self.states.get(coin)
        if cs is None:
            self._rebuild(coin, "start")
            cs = self.states[coin]
            if coin == "BTC" or cs.last_ts is None:
                return
        stt = os.stat(path)
        sig = (stt.st_size, stt.st_mtime_ns)
        if cs.sig == sig and cs.ok:
            return                                      # file unchanged: nothing new
        tail = read_tail(path)
        if not tail:
            return
        cs.sig = sig
        newest = tail[-1][0]
        if cs.last_ts is not None and newest < cs.last_ts:
            tail = []
        new = [r for r in tail if cs.last_ts is None or r[0] > cs.last_ts]
        now = self.now_fn()
        limit = self.btc_last if (coin != "BTC" and self.btc_last) else None
        for r in new:
            ts = r[0]
            if ts + BAR_S > now:
                break                                  # bar not closed yet
            if limit is not None and ts > limit:
                break                                  # keep BTC aligned
            if cs.last_ts is not None and ts - cs.last_ts != BAR_S:
                self.counters["gaps"] += 1
                self._rebuild(coin, "gap")
                return
            if not cs.ok and cs.reason == "invalid bar":
                self._rebuild(coin, "invalid")
                return
            self._feed(cs, coin, r[0], r[1], r[2], r[3], r[4], r[6], emit)
        if cs.last_ts is not None:
            self.store.set_watermark(coin, cs.last_ts)

    def _resolve_open(self):
        specs = by_id()
        n = 0
        for ev in self.store.open_events():
            cs = self.states.get(ev["coin"])
            if cs is None or not cs.recent:
                continue
            bars = [b for b in cs.recent if b[0] > ev["bar_ts"]]
            if not bars:
                continue
            spec = specs.get(ev["rule_id"])
            dd = (B.stop_distance(ev["sigma"], spec.geometry.horizon_bars,
                                  spec.geometry.k_stop) if spec else
                  abs(__import__("math").log(1 + ev["stop_frac"])))
            out = B.label_stream(bars, ev["side"], dd, ev["horizon"], ev["r_tp"])
            if out is None:
                continue
            gross, kind, off, entry = out
            self.store.resolve(ev["id"], kind, entry, bars[min(off, len(bars) - 1)][0],
                               off, gross, B.net_r(gross, dd, ev["cost_frac"]))
            n += 1
        self.counters["resolved"] += n
        return n

    def step(self, should_stop=None):
        """One cycle over the whole universe; returns a status dict.

        ``should_stop`` (WS-F) is an optional callable checked between
        coins: when it returns True the cycle stops promptly so a SIGTERM
        drains inside the operator's grace instead of waiting for the whole
        universe.  The coins that were not visited keep their state; the
        next cycle picks them up.
        """
        t0 = time.time()
        # Read the persisted watermarks back once per cycle: they are the
        # proof that a restart resumes instead of re-processing bars.
        try:
            self._watermarks = self.store.watermarks()
        except Exception:
            self._watermarks = {}
        coins = list_coins(self.data_dir)
        if "BTC" not in coins:
            self.store.set_meta("auto_pause", "1")
            self.store.set_meta("auto_pause_reason", "no BTC bars")
            return {"ok": False, "reason": "BTC bars missing"}
        interrupted = False
        self._advance("BTC", emit=False)
        for coin in coins:
            if coin == "BTC":
                continue
            if should_stop is not None and should_stop():
                interrupted = True
                break
            try:
                self._advance(coin)
            except Exception as e:                      # one coin never kills a cycle
                cs = self.states.get(coin)
                if cs is not None:
                    cs.ok, cs.reason = False, f"{type(e).__name__}"
        reason = self._update_pause()
        resolved = self._resolve_open()
        self.store.commit()
        return {"ok": True, "coins": len(self.states), "paused": reason,
                "resolved": resolved, "seconds": round(time.time() - t0, 2),
                "interrupted": interrupted,
                "watermarks": len(self._watermarks),
                **self.counters}

    def status(self):
        st = self.states
        return {"coins": len(st), "ok": sum(1 for s in st.values() if s.ok),
                "btc_last": self.btc_last,
                "paused": self.paused(),
                "pause_reason": self.store.get_meta("auto_pause_reason", ""),
                "watermarks": len(self._watermarks),
                **self.counters}
