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
"""
import gzip
import os
import time
from collections import deque

from . import barrier as B
from . import risk as riskmod
from . import gates as gatesmod
from .bars import load_coin, list_coins, contiguous_runs, valid_bar
from .config import DEFAULT, BAR_S, BARS_PER_DAY
from .registry import REGISTERED, by_id
from .selector import Selector, utc_day
from .stream import FeatureStream, Cusum

RECENT_BARS = 330                       # >= longest horizon + margin
REPLAY_BARS = (DEFAULT.feat.rvol_lags_days * BARS_PER_DAY
               + DEFAULT.feat.warmup_bars + 120)
TAIL_ROWS = 600


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
                         "gaps": 0, "invalid": 0}

    # ---- per-coin state ------------------------------------------------
    def _rebuild(self, coin, reason):
        cs = CoinState(self.cfg, self.hs)
        bars = load_coin(self.data_dir, coin)
        if not bars:
            cs.reason = "no bars"
            self.states[coin] = cs
            return cs
        runs = contiguous_runs(bars["ts"])
        a, z = runs[-1]
        a = max(a, z - REPLAY_BARS)
        for i in range(a, z):
            self._feed(cs, coin, bars["ts"][i], bars["o"][i], bars["h"][i],
                       bars["l"][i], bars["c"][i], bars["amt"][i], emit=False)
            if not cs.ok and cs.reason == "invalid bar":
                break
        if cs.reason == "uninitialized":
            cs.ok = True
            cs.reason = ""
        self.states[coin] = cs
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
            promo = gatesmod.promotion_for(self.promo_dir, rid, self.cfg.config_hash())
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

    def step(self):
        """One cycle over the whole universe; returns a status dict."""
        t0 = time.time()
        coins = list_coins(self.data_dir)
        if "BTC" not in coins:
            self.store.set_meta("auto_pause", "1")
            self.store.set_meta("auto_pause_reason", "no BTC bars")
            return {"ok": False, "reason": "BTC bars missing"}
        self._advance("BTC", emit=False)
        for coin in coins:
            if coin == "BTC":
                continue
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
                **self.counters}

    def status(self):
        st = self.states
        return {"coins": len(st), "ok": sum(1 for s in st.values() if s.ok),
                "btc_last": self.btc_last,
                "paused": self.paused(),
                "pause_reason": self.store.get_meta("auto_pause_reason", ""),
                **self.counters}
