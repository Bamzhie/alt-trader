"""Collection-only loop: bars, funding, settlements, and price archives.

This is the runtime replacement for the scanner daemon. It never opens the
signals or quant databases, never scores a coin, and never resolves plans.
"""

import threading
import time

from . import collector as colmod
from . import funding as fmod
from . import prices as pmod
from . import settlements as smod


class CollectionState:
    """Thread-safe, real-only collection status for the HTTP health routes."""

    def __init__(self, data_dir, funding_dir, settlements_dir, prices_dir,
                 every_s):
        self.data_dir = data_dir
        self.funding_dir = funding_dir
        self.settlements_dir = settlements_dir
        self.prices_dir = prices_dir
        self.every_s = int(every_s)
        self.started_ts = int(time.time())
        self._lock = threading.Lock()
        self._state = {
            "status": "starting",
            "first_success_ts": None,
            "last_cycle_ts": None,
            "last_success_ts": None,
            "last_duration_s": None,
            "cycle_count": 0,
            "next_due_ts": None,
            "last_error": None,
            "bars": None,
            "funding": None,
            "settlements": None,
            "prices": None,
        }

    def snapshot(self):
        with self._lock:
            data = dict(self._state)
        data.update({
            "data_dir": self.data_dir,
            "funding_dir": self.funding_dir,
            "settlements_dir": self.settlements_dir,
            "prices_dir": self.prices_dir,
            "every_s": self.every_s,
            "started_ts": self.started_ts,
            "now_ts": int(time.time()),
        })
        return data

    def record(self, *, status, duration_s, bars, funding, settlements,
               prices, error=None):
        now = int(time.time())
        with self._lock:
            self._state.update({
                "status": status,
                "last_cycle_ts": now,
                "last_duration_s": round(float(duration_s), 3),
                "cycle_count": self._state["cycle_count"] + 1,
                "next_due_ts": now + self.every_s,
                "last_error": error,
                "bars": bars,
                "funding": funding,
                "settlements": settlements,
                "prices": prices,
            })
            if status in ("healthy", "degraded"):
                self._state["last_success_ts"] = now
                if self._state["first_success_ts"] is None:
                    self._state["first_success_ts"] = now


def _bars_summary(counts):
    errors = dict(getattr(counts, "errors", {}) or {})
    sample = list(errors.items())[:3]
    return {
        "coins": len(counts),
        "new_bars": sum(counts.values()),
        "failed_coins": len(errors),
        "stopped": bool(getattr(counts, "stopped", False)),
        "errors": [{"coin": c, "error": e} for c, e in sample],
    }


def _guarded(name, fn, **kw):
    t0 = time.monotonic()
    try:
        return {"ok": True, "result": fn(**kw),
                "seconds": round(time.monotonic() - t0, 3), "error": None}
    except Exception as e:
        return {"ok": False, "result": None,
                "seconds": round(time.monotonic() - t0, 3),
                "error": f"{type(e).__name__}: {e}"}


def _summarize_funding(result):
    rows = result or {}
    return {"coins": len(rows), "new_rows": sum(rows.values())}


def _summarize_settlements(result):
    rows = result or {}
    return {"coins": len(rows), "new_rows": sum(
        r.get("new", 0) for r in rows.values()), "errors": sum(
            1 for r in rows.values() if "error" in r)}


def _summarize_prices(result):
    rows = result or {}
    return {"series": len(rows), "new_rows": sum(
        r.get("new", 0) for s in rows.values() for r in s.values()),
        "errors": sum(1 for s in rows.values() for r in s.values()
                      if "error" in r)}


def run_collection_loop(state, stop_event=None, iterations=None,
                        data_dir="data/bars", funding_dir="data/funding",
                        settlements_dir="data/funding_settlements",
                        prices_dir="data/prices"):
    """Collect every archive on a fixed cadence until stopped.

    ``iterations`` is for tests/one-shot runs; None means forever. A bars
    failure is recorded, never raised; venue archive hooks are independent.
    """
    stop = stop_event or threading.Event()
    n = 0
    while not stop.is_set() and (iterations is None or n < iterations):
        t0 = time.monotonic()
        bars = None
        error = None
        try:
            counts = colmod.collect_full_universe(
                data_dir=data_dir, stop_event=stop)
            bars = _bars_summary(counts)
            if bars["stopped"] or (bars["failed_coins"] and not bars["coins"]):
                status = "failed"
                error = "bar collection failed or was interrupted"
            elif bars["failed_coins"] or not bars["coins"]:
                status = "degraded"
                error = "some coins failed" if bars["failed_coins"] else (
                    "no eligible coins")
            else:
                status = "healthy"
        except Exception as e:
            status = "failed"
            error = f"{type(e).__name__}: {e}"
            bars = {"coins": 0, "new_bars": 0, "failed_coins": 0,
                    "stopped": False, "errors": []}

        funding = _guarded("funding", fmod.collect_funding,
                           data_dir=funding_dir)
        if funding["ok"]:
            funding["result"] = _summarize_funding(funding["result"])
        settlements = _guarded("settlements", smod.maybe_collect,
                               data_dir=settlements_dir)
        if settlements["ok"]:
            settlements["result"] = _summarize_settlements(
                settlements["result"])
        prices = _guarded("prices", pmod.maybe_collect,
                          data_dir=prices_dir)
        if prices["ok"]:
            prices["result"] = _summarize_prices(prices["result"])

        state.record(status=status, duration_s=time.monotonic() - t0,
                     bars=bars, funding=funding, settlements=settlements,
                     prices=prices, error=error)
        n += 1
        if stop.is_set() or (iterations is not None and n >= iterations):
            break
        remaining = state.every_s - (time.monotonic() - t0)
        if remaining > 0:
            stop.wait(remaining)
    return state.snapshot()
