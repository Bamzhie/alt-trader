"""Unattended data-collection daemon: scan + bars + outcomes, forever.

Runs the full measurement loop without the GUI: every --interval seconds a
scan cycle (signals AND shadow rows logged, same as the app), every
--collect-every seconds the full-universe 5m bar collector, every
--resolve-every seconds the outcome resolver. Designed for systemd
(see altradar.service) or nohup. Read-only: places no orders, holds no keys.

If the GUI runs with auto-scan ON at the same time, both log signals and
rows double up (harmless for stats denominators, but noisy) — pause the
GUI (p) or close it while the daemon owns collection.
"""

import argparse
import os
import sys
import time
from types import SimpleNamespace

from .app import App
from . import collector as colmod
from . import measurement as measuremod
from . import outcomes as outmod
from .scan import MAX_WORKERS, build_universe


def full_symbol_map():
    """{coin: symbol} for the whole venue (not the scan rotation)."""
    try:
        return {cn: sy for sy, cn in build_universe(float("inf"), budget=None)}
    except Exception as e:
        print(f"symbol map refresh failed: {type(e).__name__}: {e}",
              file=sys.stderr, flush=True)
        return {}


def main():
    ap = argparse.ArgumentParser(description="ALT RADAR collection daemon")
    ap.add_argument("--stake", type=float, default=0.10)
    ap.add_argument("--coins", type=int, default=150)
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--db", default="data/signals.db")
    ap.add_argument("--log-threshold", type=float, default=24.0)
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
    args = ap.parse_args()

    app_args = SimpleNamespace(
        stake=args.stake, coins=args.coins, interval=args.interval,
        universe_budget=args.coins, log_threshold=args.log_threshold,
        db=args.db, write_logs=True)
    app = App(app_args)
    sym_map = {}
    last_collect = 0.0
    last_resolve = 0.0
    last_measure = 0.0
    measure_cfg = measuremod.measurement_config(
        args.stake, args.log_threshold,
        getattr(args, "leverage_cap", None))

    # --- read-only status / explicit epoch activation (Task 6) ----------
    # Both run before the daemon loop and exit. Epoch activation is an
    # explicit operator action only: it is refused while calibration fails
    # and can never move an epoch that is already set (spec §12).
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
        return

    run_id = f"{os.getpid()}-{int(time.time())}"
    n = 0
    print(f"daemon: stake ${args.stake:g} coins {args.coins} "
          f"scan {args.interval}s collect {args.collect_every}s "
          f"resolve {args.resolve_every}s", flush=True)
    try:
        while args.iterations is None or n < args.iterations:
            t0 = time.time()
            if args.no_scan:
                print(f"[{time.strftime('%H:%M:%S')}] no-scan mode: "
                      f"collector + resolver only", flush=True)
            else:
                try:
                    app.maybe_refresh_universe()
                    app.scan_once()
                    st = app.store.stats()
                    print(f"[{time.strftime('%H:%M:%S')}] scan: "
                          f"{len(app.cards)} cards failed {app.failed} "
                          f"rows {st['rows']} outcomes {st.get('outcomes', 0)}",
                          flush=True)
                except Exception as e:
                    print(f"[{time.strftime('%H:%M:%S')}] scan FAILED: "
                          f"{type(e).__name__}: {e}", flush=True)
            now = time.time()
            if now - last_collect >= args.collect_every:
                try:
                    counts = colmod.collect_full_universe()
                    last_collect = now
                    print(f"[{time.strftime('%H:%M:%S')}] collect: "
                          f"{len(counts)} coins "
                          f"{sum(counts.values())} new bars", flush=True)
                except Exception as e:
                    print(f"[{time.strftime('%H:%M:%S')}] collect FAILED: "
                          f"{type(e).__name__}: {e}", flush=True)
            if now - last_resolve >= args.resolve_every:
                try:
                    if not sym_map:
                        sym_map = full_symbol_map()
                    done = outmod.resolve_pending(app.store, sym_map)
                    pdone = outmod.resolve_plans(app.store, sym_map)
                    episode_result = outmod.resolve_episode_plans(
                        app.store, sym_map, now=now)
                    last_resolve = now
                    print(f"[{time.strftime('%H:%M:%S')}] resolve: "
                          f"{done} new outcomes, {pdone} plan rows, "
                          f"{episode_result['resolved']} episode outcomes",
                          flush=True)
                except Exception as e:
                    print(f"[{time.strftime('%H:%M:%S')}] resolve FAILED: "
                          f"{type(e).__name__}: {e}", flush=True)
            # Independent all-eligible measurement cycle (Task 3): cadence +
            # calibration over EVERY eligible coin, separate from the
            # interactive scan and never changing its rankings. Episode
            # creation stays disabled until enable_epoch() passes
            # calibration (spec §12, §17); this loop never enables it.
            if now - last_measure >= args.measure_every:
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

                    counts = measuremod.run_cycle(
                        app.store, eligible, config=measure_cfg,
                        now_fn=time.time, tickers=tk,
                        attempt_id_factory=lambda coin, cycle_ts:
                        measuremod.measurement_attempt_id(
                            coin, cycle_ts, run_id),
                        max_workers=MAX_WORKERS,
                        progress_fn=_measure_progress)
                    last_measure = now
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
                except Exception as e:
                    print(f"[{time.strftime('%H:%M:%S')}] measure FAILED: "
                          f"{type(e).__name__}: {e}", flush=True)
            n += 1
            if args.iterations is not None and n >= args.iterations:
                break
            sleep_for = max(5.0, args.interval - (time.time() - t0))
            time.sleep(sleep_for)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            app.store.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
