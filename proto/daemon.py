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
from .scan import build_universe


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
                    last_resolve = now
                    print(f"[{time.strftime('%H:%M:%S')}] resolve: "
                          f"{done} new outcomes, {pdone} plan rows",
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
                    counts = measuremod.run_cycle(
                        app.store, eligible, config=measure_cfg,
                        now_fn=time.time, tickers=tk,
                        attempt_id_factory=lambda coin, cycle_ts:
                        measuremod.measurement_attempt_id(
                            coin, cycle_ts, run_id))
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
