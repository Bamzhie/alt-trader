"""Unattended data-collection daemon: scan + bars + outcomes, forever.

Runs the full measurement loop without the GUI: every --interval seconds a
scan cycle (signals AND shadow rows logged, same as the app), every
--collect-every seconds the full-universe 5m bar collector, every
--resolve-every seconds the outcome resolver. Designed for systemd
(see altradar.service) or nohup. Read-only: places no orders, holds no keys.

If the GUI runs with auto-scan ON at the same time, both log signals and
rows double up (harmless for stats denominators, but noisy) — pause the
GUI (p) or close it while the daemon owns collection.

Workstream F (startup & first-sweep): every stage of an iteration is timed
and its outcome recorded (``proto/daemon_timing.py``), the per-stage
due-times are persisted so a restart does NOT repeat work that is not due,
the stop flag is observed between stages and between per-coin units so a
SIGTERM drains promptly, and exactly ONE sweep record is written per
completed iteration.
"""

import argparse
import inspect
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from .app import App
from . import collector as colmod
from . import funding as fmod
from . import settlements as smod
from . import prices as pmod
from . import measurement as measuremod
from . import outcomes as outmod
from .daemon_timing import (Cadence, TimingLog, CADENCE_FILE, STAGES,
                             TIMING_FILE, format_stages, sidecar_path)
from .scan import MAX_WORKERS, build_universe
from .store import Store


class _StopRequested(Exception):
    """Raised inside a stage that must stop now (the operator's SIGTERM)."""


def full_symbol_map():
    """{coin: symbol} for the whole venue (not the scan rotation)."""
    try:
        return {cn: sy for sy, cn in build_universe(float("inf"), budget=None)}
    except Exception as e:
        print(f"symbol map refresh failed: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return {}


def resolve_batch(db_path):
    """Resolve legacy and episode outcomes on an isolated DB connection.

    Outcome resolution can walk thousands of plans. Keeping it off the
    cadence loop prevents that work from stretching per-coin measurement
    gaps beyond the calibration limits.
    """
    store = Store(db_path)
    try:
        symbol_map = full_symbol_map()
        done = outmod.resolve_pending(store, symbol_map)
        plans = outmod.resolve_plans(store, symbol_map)
        episodes = outmod.resolve_episode_plans(
            store, symbol_map, now=time.time())
        return {"outcomes": done, "plans": plans,
                "episode_outcomes": episodes["resolved"]}
    finally:
        store.close()


def main():
    """Thin legacy entrypoint: exec the service headless.

    All flags are preserved: the service parser is a superset of the
    daemon flags, so argv passes through untouched.
    """
    from .service import main as service_main
    return service_main()


def build_parser():
    """The daemon CLI flags (shared with the service by import, never copied)."""
    ap = argparse.ArgumentParser(description="ALT RADAR collection daemon")
    ap.add_argument("--stake", type=float, default=0.10)
    ap.add_argument("--coins", type=int, default=150)
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--db", default="data/signals.db")
    ap.add_argument("--log-threshold", type=float, default=24.0)
    ap.add_argument("--use-v3", action="store_true",
                    help="use score_v3 for the flag threshold and ranking "
                         "(instead of the v2 score). Recommend: --use-v3 --log-threshold 40")
    ap.add_argument("--collect-every", type=int, default=300)
    ap.add_argument("--resolve-every", type=int, default=3600)
    ap.add_argument("--measure-every", type=int, default=300,
                    help="seconds between all-eligible measurement cycles"
                         " (cadence/calibration; independent of the scan)")
    ap.add_argument("--iterations", type=int, default=None,
                    help="stop after N scan cycles (default: forever)")
    ap.add_argument("--no-scan", action="store_true",
                    help="skip scan cycles: collector + resolver only (use "
                         "alongside a GUI that already logs signals)")
    ap.add_argument("--measure-status", action="store_true",
                    help="print measurement cadence/calibration/epoch status "
                         "and exit (read-only; never enables the epoch)")
    ap.add_argument("--enable-measurement-epoch", action="store_true",
                    help="EXPLICIT operator action: enable episode creation "
                         "after calibration passes. Refused while calibration "
                         "fails, and never moves an epoch already set.")
    ap.add_argument("--quant-shadow", action="store_true",
                    help="run the quant shadow watcher once per sweep "
                         "(research-only events; never affects sweeps)")
    ap.add_argument("--quant-db", default="data/quant.db")
    ap.add_argument("--quant-bars", default="data/bars")
    ap.add_argument("--quant-equity", type=float, default=1000.0)
    ap.add_argument("--settlements-dir", default=None,
                    help="settlement archive dir for the WS4 collect hook "
                         "(default: data/funding_settlements sibling of "
                         "--quant-bars); when None the module default "
                         "SETTLEMENTS_DIR is used")
    ap.add_argument("--prices-dir", default=None,
                    help="mark/index price archive root for the WS4 collect "
                         "hook (default: data/prices sibling of --quant-bars); "
                         "when None the module default PRICES_DIR is used")
    return ap


def make_app(args):
    """Build the scan App from daemon-style args (shared, never copied)."""
    app_args = SimpleNamespace(
        stake=args.stake, coins=args.coins, interval=args.interval,
        universe_budget=args.coins, log_threshold=args.log_threshold,
        db=args.db, write_logs=True, use_v3=args.use_v3)
    return App(app_args)


def maybe_handle_measure_status(app, args):
    """Read-only status / explicit epoch activation; True when handled.

    Runs before the loop and exits: epoch activation is an explicit
    operator action only — refused while calibration fails and never
    moving an epoch that is already set (spec §12).
    """
    if args.measure_status or args.enable_measurement_epoch:
        stats = measuremod.cadence_stats(app.store)
        cal = stats["calibration"]
        epoch = measuremod.epoch_ts(app.store)
        print(f"measurement status: coins {stats['coins']} "
              f"attempts {stats['attempts']} "
              f"(fail {stats['failures']} degraded {stats['degraded']})")
        print(f"  observation gaps: p50 {stats['gap_p50_s']}s "
              f"p95 {stats['gap_p95_s']}s p99 {stats['gap_p99_s']}s "
              f"(limits p95<=600s p99<=900s)")
        print(f"  calibration: {'PASS' if cal['pass'] else 'FAIL'}")
        if epoch is None:
            print("  epoch: UNSET (episode creation disabled)")
        else:
            import datetime as _dt
            print(f"  epoch: SET at {epoch} "
                  f"({_dt.datetime.fromtimestamp(epoch).isoformat()})")
        if args.enable_measurement_epoch:
            if measuremod.enable_epoch(app.store, time.time()):
                print("  epoch ENABLED: episode creation is now active.")
            elif epoch is not None:
                print("  epoch already set; left unchanged (immutable).")
            else:
                print("  epoch NOT enabled: calibration has not passed. Fix "
                      "the scheduler/universe and re-run calibration.")
        app.store.close()
        return True
    return False


def record_completed_sweep(db_path, eligible_count, evaluated_count):
    """Record one completed universe sweep.

    Exactly one ``sweep.completed`` SSE event (via the service emitter)
    plus one journal event + dashboard_snapshot row. The scan loop calls
    this once per completed sweep — a scan cycle, or in --no-scan mode a
    successful collect cycle (collect passes evaluated_count=0 honestly);
    failures inside are reported, never raised, so a sweep record can
    never break the loop.
    """
    from . import service as svc
    svc.record_sweep_completed(eligible_count, evaluated_count)
    import sqlite3
    from . import watcher_journal as jmod
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        jmod.record_sweep(conn, eligible_count, evaluated_count)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _accepts_duration_kwarg(fn):
    """True when *fn* can be called as ``fn(eligible, evaluated,
    duration_s=...)``.

    Decided by signature inspection, ONCE, before the loop — never by
    catching TypeError at the call site: a TypeError raised INSIDE the
    callback after it already did work would trigger a second invocation
    (recording one sweep twice) and mask the real error.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):  # non-introspectable callable
        return False
    try:
        sig.bind(1, 1, duration_s=0.0)
    except TypeError:
        return False
    return True


def _accepts_kwarg(fn, name):
    """True when *fn* can be called with the keyword *name*.

    Same rule as :func:`_accepts_duration_kwarg`: the call shape is decided
    ONCE, by signature inspection, never by catching a TypeError raised
    inside the call (which would trigger a second, duplicate invocation).
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    try:
        sig.bind(**{name: None})
    except TypeError:
        return False
    return True


def run_daemon_loop(args, stop_event=None, on_sweep=None, app_box=None,
                    timing=None):
    """The collection loop: scan + collect + resolve (+ measure cadence).

    Owned here and imported by the service (move-by-import — never copied).
    ``stop_event`` (a threading.Event) breaks the loop between sweeps and
    cuts the inter-sweep sleep short for prompt shutdown. ``on_sweep`` is
    called once per *completed* sweep as ``on_sweep(eligible, evaluated,
    duration_s)``; the default records the sweep (SSE + snapshot). A sweep
    is one data-collection pass: a scan cycle, or — in --no-scan mode,
    where scans are disabled — a successful collect cycle (with
    evaluated=0, honestly measured, never invented). The first successful
    data cycle of either kind therefore marks readiness downstream.
    ``duration_s`` is the measured seconds the sweep's work took (WS3
    timeliness: scan lag/duration), elapsed on the monotonic clock so a
    wall-clock adjustment cannot distort it. The callback's call shape
    is decided ONCE by signature inspection before the loop: callbacks
    that accept the keyword receive it; older two-argument callbacks are
    called without it. An exception raised inside the callback is a real
    error — reported, never retried.
    ``app_box``, when given,
    receives the App under ``app_box["app"]`` so the owner can close
    sessions after the loop stops.  It may also carry the shared
    ``app_box["timing"]`` timing log (the service owns it so
    ``/scanner-health`` can publish the stage durations and the first-sweep
    state); the ``timing`` keyword below does the same.
    ``timing``, when given, is the shared
    :class:`proto.daemon_timing.TimingLog` (the service owns it so
    ``/scanner-health`` can publish the stage durations and the first-sweep
    state); otherwise the loop creates and owns its own.

    Workstream F guarantees:
    * every stage of the iteration is timed and recorded with its outcome
      (ok / skipped / failed / not_due / partial) — a failure is never
      recorded as a success;
    * the per-stage due-times are PERSISTED (``data/.cadence.json`` next to
      the DB), so a restart does not repeat work that is not due. The file
      is only written when a stage actually completes, so the first boot
      after this change behaves exactly like today;
    * the stop flag is observed BETWEEN stages and BETWEEN per-coin units
      (collector, quant step, measurement cycle), so a SIGTERM drains
      inside the operator's grace;
    * exactly ONE sweep record (one watcher_events row + one
      dashboard_snapshot row) is written per completed iteration: the app's
      own sweep write is suppressed on the daemon path, the CLI path keeps
      its own.
    """
    app = make_app(args)
    if app_box is not None:
        app_box["app"] = app
    if on_sweep is None:
        def on_sweep(eligible, evaluated, duration_s=None, _db=args.db):
            record_completed_sweep(_db, eligible, evaluated)
    # Decide the callback's call shape ONCE by signature inspection: a
    # TypeError from inside the callback must never be mistaken for a
    # legacy signature and trigger a second, duplicate call.
    sweep_takes_duration = _accepts_duration_kwarg(on_sweep)
    # Same for the app's scan entry point: the daemon writes the sweep
    # record itself, so the app must NOT write one too (one row per
    # iteration).
    scan_fn = getattr(app, "scan_once", None)
    scan_takes_flag = (scan_fn is not None
                       and _accepts_kwarg(scan_fn, "record_sweep"))
    if timing is None and app_box is not None:
        # The service hands the shared timing log over through the box, so
        # the embedded loop's call shape never changes.
        timing = app_box.get("timing")
    if timing is None:
        timing = TimingLog(sidecar_path(args.db, TIMING_FILE),
                           db=args.db)
    # Persisted per-stage due-times (WS-F).  A stage with no entry is due
    # at once (first boot); the file is only written when a stage actually
    # completes, so the first restart after this change still runs the work.
    cadence = Cadence(sidecar_path(args.db, CADENCE_FILE), intervals={
        "scan": int(args.interval),
        "collect": int(args.collect_every),
        "resolve": int(args.resolve_every),
        "measure": int(args.measure_every),
    }, db=args.db)
    resolver_pool = ThreadPoolExecutor(max_workers=1,
                                       thread_name_prefix="outcome-resolver")
    resolve_future = None
    resolve_started = 0.0        # monotonic: the measured pass duration
    resolve_started_wall = 0.0    # wall clock: the persisted due-time
    measure_cfg = measuremod.measurement_config(
        args.stake, args.log_threshold,
        getattr(args, "leverage_cap", None))
    # Quant shadow watcher (Task 2): lazy imports only under the flag,
    # constructed once before the loop. A failed init is reported and
    # leaves the watcher disabled — the loop below never depends on it.
    quant_watcher = None
    quant_last_shadow = (0, 0)
    quant_takes_stop = False
    if getattr(args, "quant_shadow", False):
        try:
            from .quant.engine import Watcher as _QuantWatcher
            from .quant.qstore import QuantStore as _QuantStore
            quant_watcher = _QuantWatcher(
                getattr(args, "quant_bars", "data/bars"),
                _QuantStore(getattr(args, "quant_db", "data/quant.db")),
                equity=getattr(args, "quant_equity", 1000.0))
            quant_takes_stop = _accepts_kwarg(quant_watcher.step,
                                              "should_stop")
        except Exception as e:
            print(f"quant init FAILED: {type(e).__name__}: {e}", flush=True)

    def _stopped():
        return stop_event is not None and stop_event.is_set()

    def _run_scan():
        if scan_fn is None:
            return
        if scan_takes_flag:
            scan_fn(record_sweep=False)
        else:
            scan_fn()

    def _fill_missing_stages():
        """Mark the stages an interrupted iteration never reached."""
        done = set(((timing.current or {}).get("stages") or {}))
        for name in STAGES:
            if name not in done:
                timing.record(name, 0.0, "skipped", interrupted=True)

    def _venue_hooks(interrupted):
        """funding + settlements + prices; they ride the collect cadence.

        Each hook is guarded on its own: a failure is reported, never
        raised, and never fails the bars sweep.  The stop flag is observed
        between the hooks so a SIGTERM drains here too.
        """
        if interrupted or _stopped():
            for name in ("funding", "settlements", "prices"):
                timing.record(name, 0.0, "skipped", interrupted=True)
            return True
        # WS4 point-in-time funding: one observation per coin, recorded on
        # the same cadence as the bars cycle (funding rides the bulk ticker
        # row - one extra request).
        t_stage = time.monotonic()
        try:
            fund_counts = fmod.collect_funding()
            print(f"[{time.strftime('%H:%M:%S')}] funding: "
                  f"{len(fund_counts)} coins recorded", flush=True)
            timing.record("funding", time.monotonic() - t_stage, "ok",
                          coins=len(fund_counts))
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] funding FAILED: "
                  f"{type(e).__name__}: {e}", flush=True)
            timing.record("funding", time.monotonic() - t_stage, "failed",
                          error=f"{type(e).__name__}: {e}")
        if _stopped():
            for name in ("settlements", "prices"):
                timing.record(name, 0.0, "skipped", interrupted=True)
            return True
        # WS4 venue-reported settlement history: incremental page-1 pass,
        # throttled to daily inside maybe_collect (3 settles/day/coin make
        # sub-daily polling pure venue load).
        t_stage = time.monotonic()
        try:
            settle_dir = getattr(args, "settlements_dir", None)
            settle_out = smod.maybe_collect(data_dir=settle_dir)
            if settle_out:
                print(f"[{time.strftime('%H:%M:%S')}] settlements: "
                      f"{len(settle_out)} coins "
                      f"{sum(r.get('new', 0) for r in settle_out.values())} "
                      "new rows", flush=True)
                timing.record("settlements", time.monotonic() - t_stage, "ok",
                              coins=len(settle_out))
            else:
                # The daily archive throttle said "not due".
                timing.record("settlements", 0.0, "not_due")
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] settlements FAILED: "
                  f"{type(e).__name__}: {e}", flush=True)
            timing.record("settlements", time.monotonic() - t_stage, "failed",
                          error=f"{type(e).__name__}: {e}")
        if _stopped():
            timing.record("prices", 0.0, "skipped", interrupted=True)
            return True
        # WS4 mark/index price history: incremental recent-window pass,
        # throttled to daily inside maybe_collect.
        t_stage = time.monotonic()
        try:
            price_dir = getattr(args, "prices_dir", None)
            price_out = pmod.maybe_collect(data_dir=price_dir)
            if price_out:
                total = sum(r.get("new", 0)
                            for s in price_out.values()
                            for r in s.values())
                print(f"[{time.strftime('%H:%M:%S')}] prices: "
                      f"{len(price_out)} series {total} new rows",
                      flush=True)
                timing.record("prices", time.monotonic() - t_stage, "ok",
                              series=len(price_out))
            else:
                timing.record("prices", 0.0, "not_due")
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] prices FAILED: "
                  f"{type(e).__name__}: {e}", flush=True)
            timing.record("prices", time.monotonic() - t_stage, "failed",
                          error=f"{type(e).__name__}: {e}")
        return interrupted

    run_id = f"{os.getpid()}-{int(time.time())}"
    n = 0
    v3_tag = " (v3 mode)" if args.use_v3 else ""
    print(f"daemon: stake ${args.stake:g} coins {args.coins}"
          f"{v3_tag} scan {args.interval}s collect {args.collect_every}s "
          f"resolve {args.resolve_every}s", flush=True)
    try:
        while args.iterations is None or n < args.iterations:
            if _stopped():
                break
            # Elapsed-time anchor on the MONOTONIC clock: both the WS3
            # sweep duration and the inter-sweep sleep budget derive from
            # it, so a wall-clock adjustment can distort neither.
            t0 = time.monotonic()
            timing.begin()
            timing.mark_sweep_started()
            interrupted = False
            # Sweep definition: one data-collection pass (scan OR collect).
            # In --no-scan mode scans never run, so a successful collect
            # cycle below completes the sweep for readiness/snapshot/
            # journal purposes (same on_sweep flag scan sweeps set).
            sweep_ok = False
            collect_ok = False
            collect_coins = 0
            if args.no_scan:
                print(f"[{time.strftime('%H:%M:%S')}] no-scan mode: "
                      f"collector + resolver only", flush=True)
                timing.record("scan", 0.0, "skipped")
            elif not cadence.due("scan"):
                # The persisted cadence says the scan is not due yet: the
                # restart does NOT repeat it.
                timing.record("scan", 0.0, "not_due")
            else:
                t_stage = time.monotonic()
                stage_started = time.time()
                try:
                    app.maybe_refresh_universe()
                    _run_scan()
                    st = app.store.stats()
                    print(f"[{time.strftime('%H:%M:%S')}] scan: "
                          f"{len(app.cards)} cards failed {app.failed} "
                          f"rows {st['rows']} outcomes {st.get('outcomes', 0)}",
                          flush=True)
                    sweep_ok = True
                    failed = int(getattr(app, "failed", 0) or 0)
                    timing.record("scan", time.monotonic() - t_stage,
                                  "partial" if failed else "ok",
                                  failed_coins=failed or None,
                                  evaluated=len(app.cards))
                    cadence.mark("scan", stage_started)
                except Exception as e:
                    print(f"[{time.strftime('%H:%M:%S')}] scan FAILED: "
                          f"{type(e).__name__}: {e}", flush=True)
                    timing.record("scan", time.monotonic() - t_stage,
                                  "failed", error=f"{type(e).__name__}: {e}")
            if _stopped():
                interrupted = True
            # --- collect block (bars + funding + settlements + prices) ----
            if not interrupted and not cadence.due("collect"):
                timing.record("collect", 0.0, "not_due")
                # The funding / settlement / price hooks ride the collect
                # cadence: they did not run either.
                timing.record("funding", 0.0, "not_due")
                timing.record("settlements", 0.0, "not_due")
                timing.record("prices", 0.0, "not_due")
            elif not interrupted:
                t_stage = time.monotonic()
                stage_started = time.time()
                counts = None
                try:
                    counts = colmod.collect_full_universe(
                        stop_event=stop_event)
                    collect_coins = len(counts)
                    print(f"[{time.strftime('%H:%M:%S')}] collect: "
                          f"{len(counts)} coins "
                          f"{sum(counts.values())} new bars", flush=True)
                    errors = dict(getattr(counts, "errors", {}) or {})
                    stopped = bool(getattr(counts, "stopped", False))
                    if stopped:
                        outcome = "failed"
                    elif errors and not counts:
                        outcome = "failed"      # total failure: retry next time
                    elif errors:
                        outcome = "partial"
                    else:
                        outcome = "ok"
                    collect_ok = outcome in ("ok", "partial")
                    timing.record("collect", time.monotonic() - t_stage,
                                  outcome, failed_coins=len(errors) or None,
                                  coins=len(counts))
                    if outcome in ("ok", "partial"):
                        # Total failure is NOT marked (it retries on the next
                        # iteration); a partial pass IS marked, exactly like
                        # the settlements/prices archive throttles.
                        cadence.mark("collect", stage_started)
                except Exception as e:
                    print(f"[{time.strftime('%H:%M:%S')}] collect FAILED: "
                          f"{type(e).__name__}: {e}", flush=True)
                    timing.record("collect", time.monotonic() - t_stage,
                                  "failed", error=f"{type(e).__name__}: {e}")
                # The funding / settlement / price hooks ride the collect
                # cadence.  Each one is guarded on its own (a failure must
                # never fail the bars sweep) and the stop flag is observed
                # between them, so a SIGTERM drains here too.
                interrupted = _venue_hooks(interrupted)
            if _stopped():
                interrupted = True
            # --- resolve: a background pass, observed when it finishes ----
            resolve_entry = None
            if resolve_future is not None and resolve_future.done():
                duration_s = time.monotonic() - resolve_started
                try:
                    result = resolve_future.result()
                    print(f"[{time.strftime('%H:%M:%S')}] resolve: "
                          f"{result['outcomes']} new outcomes, "
                          f"{result['plans']} plan rows, "
                          f"{result['episode_outcomes']} episode outcomes",
                          flush=True)
                    resolve_entry = (duration_s, "ok")
                    cadence.mark("resolve", resolve_started_wall)
                except Exception as e:
                    print(f"[{time.strftime('%H:%M:%S')}] resolve FAILED: "
                          f"{type(e).__name__}: {e}", flush=True)
                    resolve_entry = (duration_s, "failed")
                resolve_future = None
            if resolve_entry is None:
                if not cadence.due("resolve"):
                    resolve_entry = (0.0, "not_due")
                elif resolve_future is not None:
                    print(f"[{time.strftime('%H:%M:%S')}] resolve remains "
                          "in progress; not starting a duplicate pass",
                          flush=True)
                    resolve_entry = (0.0, "skipped")
                else:
                    resolve_future = resolver_pool.submit(resolve_batch,
                                                          args.db)
                    resolve_started = time.monotonic()
                    resolve_started_wall = time.time()
                    print(f"[{time.strftime('%H:%M:%S')}] resolve started "
                          "(background)", flush=True)
                    resolve_entry = (0.0, "skipped")
            timing.record("resolve", resolve_entry[0], resolve_entry[1])
            if _stopped():
                interrupted = True
            # Independent all-eligible measurement cycle (Task 3): cadence +
            # calibration over EVERY eligible coin, separate from the
            # interactive scan and never changing its rankings. Episode
            # creation stays disabled until enable_epoch() passes
            # calibration (spec §12, §17); this loop never enables it.
            if not interrupted and not cadence.due("measure"):
                timing.record("measure", 0.0, "not_due")
            elif not interrupted:
                t_stage = time.monotonic()
                stage_started = time.time()
                try:
                    app.maybe_refresh_universe()   # cached 10min, no extra load
                    tk, det = app.tk, app.det
                    eligible = measuremod.measurement_eligible(
                        tk, det, args.stake)
                    refresh_state = (f"ERROR {app.universe_error}"
                                     if app.universe_error else "ok")
                    print(f"[{time.strftime('%H:%M:%S')}] measure inputs: "
                          f"refresh {refresh_state}; "
                          f"tickers {len(tk)} details {len(det)} "
                          f"eligible {len(eligible)}", flush=True)
                    if not eligible:
                        print(f"[{time.strftime('%H:%M:%S')}] measure "
                              "WARNING: zero eligible coins; no measurement "
                              "attempts will be recorded", flush=True)

                    def _measure_progress(done, total):
                        if done % 50 == 0 or done == total:
                            print(f"[{time.strftime('%H:%M:%S')}] measure "
                                  f"scoring {done}/{total}", flush=True)
                        # WS-F: the stop flag is observed between the
                        # per-coin units of the cycle, so a SIGTERM drains
                        # instead of running the whole universe.
                        if _stopped():
                            raise _StopRequested()

                    counts = measuremod.run_cycle(
                        app.store, eligible, config=measure_cfg,
                        now_fn=time.time, tickers=tk,
                        attempt_id_factory=lambda coin, cycle_ts:
                        measuremod.measurement_attempt_id(
                            coin, cycle_ts, run_id),
                        max_workers=MAX_WORKERS,
                        progress_fn=_measure_progress)
                    stats = measuremod.cadence_stats(app.store)
                    print(f"[{time.strftime('%H:%M:%S')}] measure: "
                          f"attempted {counts['attempted']} "
                          f"qual {counts['qualifying']} "
                          f"non-qual {counts['non_qualifying']} "
                          f"unknown {counts['unknown']} "
                          f"(fail {counts['failed']} "
                          f"degraded {counts['degraded']}) "
                          f"sweep {counts['sweep_closed']} · "
                          f"calibration "
                          f"{'PASS' if stats['calibration']['pass'] else 'FAIL'}"
                          f" p95 {stats['gap_p95_s']}s "
                          f"p99 {stats['gap_p99_s']}s", flush=True)
                    failed = int(counts.get("failed", 0) or 0)
                    timing.record("measure", time.monotonic() - t_stage,
                                  "partial" if failed else "ok",
                                  failed_coins=failed or None,
                                  attempted=counts.get("attempted"))
                    # The cycle completed (per-coin failures included): it
                    # is marked, exactly like before this change.
                    cadence.mark("measure", stage_started)
                except _StopRequested:
                    print(f"[{time.strftime('%H:%M:%S')}] measure "
                          "interrupted by stop request", flush=True)
                    timing.record("measure", time.monotonic() - t_stage,
                                  "failed", interrupted=True)
                    interrupted = True
                except Exception as e:
                    print(f"[{time.strftime('%H:%M:%S')}] measure FAILED: "
                          f"{type(e).__name__}: {e}", flush=True)
                    timing.record("measure", time.monotonic() - t_stage,
                                  "failed", error=f"{type(e).__name__}: {e}")
            # Quant shadow step: AFTER the measure block, BEFORE sweep
            # accounting. Guarded on its own — a watcher failure is
            # reported, never raised, and this block never touches
            # sweep_ok/collect_ok/sweep_counts/last_* nor calls on_sweep.
            # When step() reports new emissions, the counts (only) are
            # fanned out as one in-process quant.shadow SSE event via the
            # service ring — inside this same guard, so a notify failure
            # is reported exactly like a watcher failure: never raised.
            if quant_watcher is not None and not interrupted:
                t_stage = time.monotonic()
                try:
                    if quant_takes_stop:
                        _quant_step = quant_watcher.step(
                            should_stop=lambda: stop_event is not None
                            and stop_event.is_set())
                    else:
                        _quant_step = quant_watcher.step()
                    # The quant stage is logged even when it succeeds (it
                    # used to print nothing at all).
                    print(f"[{time.strftime('%H:%M:%S')}] quant: "
                          f"{_quant_step.get('coins', 0)} coins "
                          f"emitted {_quant_step.get('emitted', 0)} "
                          f"resolved {_quant_step.get('resolved', 0)} "
                          f"in {_quant_step.get('seconds', 0.0)}s", flush=True)
                    outcome = "ok" if _quant_step.get("ok", True) else "failed"
                    if _quant_step.get("interrupted"):
                        outcome = "failed"
                    timing.record("quant", time.monotonic() - t_stage,
                                  outcome,
                                  seconds=_quant_step.get("seconds"),
                                  emitted=_quant_step.get("emitted"),
                                  coins=_quant_step.get("coins"))
                    try:
                        from . import service as _service_mod
                    except Exception:
                        _service_mod = None
                    if _service_mod is not None:
                        quant_last_shadow = \
                            _service_mod.emit_quant_shadow(
                                quant_last_shadow, _quant_step)
                except Exception as e:
                    print(f"[{time.strftime('%H:%M:%S')}] quant FAILED: "
                          f"{type(e).__name__}: {e}", flush=True)
                    timing.record("quant", time.monotonic() - t_stage,
                                  "failed", error=f"{type(e).__name__}: {e}")
            n += 1
            sweep_counts = None
            if sweep_ok:
                sweep_counts = (len(app.uni), len(app.cards))
            elif args.no_scan and collect_ok:
                # No-scan deployments never scan: the successful collect
                # above is this iteration's sweep. evaluated is honestly
                # 0 (no scan evaluated anything) — never invented.
                sweep_counts = (collect_coins, 0)
            if sweep_counts is not None:
                # WS3 timeliness: measured seconds this sweep's work took.
                # Elapsed time on the MONOTONIC clock — a wall-clock
                # adjustment (NTP step) must never yield a negative or
                # distorted duration. The call shape was decided once by
                # signature inspection; any exception the callback raises
                # is a real error, reported here and never retried.
                duration_s = time.monotonic() - t0
                # A COMPLETED sweep is the only thing that may move the
                # first-sweep state to "complete" (WS-F): an iteration that
                # failed or was interrupted leaves it at "in_progress".
                timing.mark_sweep_completed(
                    ts=time.time(), duration_s=duration_s,
                    stages=dict((timing.current or {}).get("stages") or {}))
                try:
                    if sweep_takes_duration:
                        on_sweep(*sweep_counts, duration_s=duration_s)
                    else:
                        # Callback predates the duration keyword.
                        on_sweep(*sweep_counts)
                except Exception as e:
                    print(f"[{time.strftime('%H:%M:%S')}] sweep record "
                          f"FAILED: {type(e).__name__}: {e}", flush=True)
            if interrupted:
                _fill_missing_stages()
            iteration_s = time.monotonic() - t0
            timing.end(duration_s=iteration_s)
            print(f"[{time.strftime('%H:%M:%S')}] "
                  f"{format_stages((timing.last() or {}).get('stages') or {})}",
                  flush=True)
            if args.iterations is not None and n >= args.iterations:
                break
            sleep_for = max(5.0, args.interval - (time.monotonic() - t0))
            if stop_event is not None:
                if stop_event.wait(sleep_for):
                    break
            else:
                time.sleep(sleep_for)
    except KeyboardInterrupt:
        pass
    finally:
        resolver_pool.shutdown(wait=False, cancel_futures=True)
        try:
            app.store.close()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
