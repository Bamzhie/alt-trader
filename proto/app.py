"""
ALT RADAR - live scanner.

Continuous refresh against MEXC with a curses TUI: ranked LONG/SHORT table,
detail view with the full scoring breakdown and trade plan, and veto visibility.

    python3 -m proto.app --stake 0.10 --coins 150 --interval 60

Keys: arrows select | ENTER detail | d long/short/both | s sort | p pause |
      w write logs | q quit

Read-only. Places no orders, holds no keys.
"""

import argparse
import curses
import sys
import time

from . import mexc
from . import indicators as ind
from . import planner as pl
from .scan import build_universe, analyse_one
from .store import Store
from concurrent.futures import ThreadPoolExecutor, as_completed

SORT_KEYS = {
    "score": lambda c: -c.score,
    "early": lambda c: -c.earlyness,
    "lean": lambda c: -abs(c.lean),
    "move": lambda c: -abs(c.change_24h_pct),
    "vol": lambda c: -c.quote_vol_24h,
}


class App:
    def __init__(self, args):
        self.args = args
        self.cards = []
        self.sel = 0
        self.detail = False
        self.dir_filter = "both"     # both | long | short
        self.sort_key = "score"
        self.paused = False
        self.status = "starting..."
        self.last_scan = 0.0
        self.store = Store(args.db)
        self.uni = []
        self.det = {}
        self.tk = {}

    # ---------- data ----------
    def refresh_universe(self):
        # store passed -> rotation pointer read/advanced each refresh, so the
        # 30-coin rotation window sweeps forward and survives restarts.
        self.uni = build_universe(self.args.stake, store=self.store)
        self.det = mexc.details()
        self.tk = mexc.tickers()

    def scan_once(self):
        t0 = time.time()
        ranked = sorted(
            self.uni,
            key=lambda s: float(self.tk.get(s[0], {}).get("amount24") or 0),
            reverse=True)[: self.args.coins]

        cards = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(analyse_one, sym, coin, self.det.get(sym, {}),
                              self.tk.get(sym, {}), self.args.stake): coin
                    for sym, coin in ranked}
            for f in as_completed(futs):
                sc = f.result()
                if sc:
                    cards.append(sc)

        # Log: flagged rows AND shadow rows (vetoed / sub-threshold). SS6.1a.
        # Flagged = actionable AND above threshold (selective, not permissive).
        if self.args.write_logs:
            for sc in cards:
                flagged = bool(sc.actionable) and sc.score >= self.args.log_threshold
                try:
                    self.store.log_signal(sc, flagged=flagged, tier=2)
                except Exception:
                    pass

        self.cards = cards
        self.sel = 0
        self.last_scan = time.time()
        self.status = (f"scanned {len(cards)} in {time.time()-t0:.0f}s · "
                       f"universe {len(self.uni)}")

    def visible(self):
        out = self.cards
        if self.dir_filter == "long":
            out = [c for c in out if c.direction == "LONG"]
        elif self.dir_filter == "short":
            out = [c for c in out if c.direction == "SHORT"]
        return sorted(out, key=SORT_KEYS[self.sort_key])

    def plan_for(self, card):
        sym_lookup = dict((cn, sy) for sy, cn in self.uni)
        if card.coin not in sym_lookup:
            return None, None
        try:
            bars = mexc.klines(sym_lookup[card.coin], "5m", limit=200)
            swing = (ind.swing_low(bars) if card.direction == "LONG"
                     else ind.swing_high(bars))
            if swing is None:
                swing = bars[-1]["l"] if card.direction == "LONG" else bars[-1]["h"]
            return pl.build_plan(card, stake=self.args.stake, swing_ref=swing), bars
        except Exception as e:
            return None, str(e)

    # ---------- rendering ----------
    def draw_table(self, stdscr, h, w):
        rows = self.visible()
        cw = stdscr
        cw.erase()
        cw.border(0)

        paused = " PAUSED" if self.paused else ""
        cw.addstr(0, 2, f" ALT RADAR · MEXC perp scanner · live · read-only{paused} ".ljust(w - 4))
        st = self.store.stats()
        cw.addstr(1, 2, f" universe {len(self.uni)} · shown {len(rows)} · "
                        f"sort {self.sort_key} · dir {self.dir_filter} · "
                        f"stake ${self.args.stake:.2f}".ljust(w - 4))
        cw.addstr(2, 2, f" logs: {st['rows']} rows / {st['coins']} coins · "
                        f"flagged {st['flagged']} · L {st['longs']} / S {st['shorts']} · "
                        f"{self.status}".ljust(w - 4))

        header = (f"{'#':<3}{'DIR':<5}{'COIN':<15}{'PRICE':>13}{'24H%':>8}"
                  f"{'VOL24':>10}{'LEAN':>7}{'EARLY':>7}{'SCORE':>7}  FLAGS")
        cw.addstr(4, 2, header[:w - 4], curses.A_BOLD)
        cw.hline(5, 2, curses.ACS_HLINE, w - 4)

        body_h = h - 9
        start = 0
        if self.sel >= body_h:
            start = self.sel - body_h + 1
        for i, c in enumerate(rows[start:start + body_h]):
            y = 6 + (i - start)
            arrow = {"LONG": "▲", "SHORT": "▼"}.get(c.direction, "•")
            vol = c.quote_vol_24h
            vol_s = f"${vol/1e6:.1f}M" if vol >= 1e6 else f"${vol/1e3:.0f}K"
            flags = []
            if c.vetoes:
                flags.append("veto:" + ",".join(v.code for v in c.vetoes))
            if c.min_notional and c.min_notional > self.args.stake:
                flags.append("WATCH")
            attr = curses.A_REVERSE if (i + start) == self.sel else curses.A_NORMAL
            line = (f"{i+start+1:<3}{arrow:<5}{c.coin:<15}{c.price:>13.8g}"
                    f"{c.change_24h_pct:>7.1f}%{vol_s:>10}{c.lean:>7.2f}"
                    f"{c.earlyness:>7.2f}{c.score:>7.1f}  {' '.join(flags)}")
            try:
                cw.addstr(y, 2, line[:w - 4], attr)
            except curses.error:
                pass

        help_txt = ("↑↓ select  ENTER detail  d long/short/both  s sort  "
                    "p pause  w logs  q quit")
        cw.addstr(h - 2, 2, help_txt[:w - 4])
        cw.refresh()

    def draw_detail(self, stdscr, h, w):
        rows = self.visible()
        if not rows:
            return
        card = rows[min(self.sel, len(rows) - 1)]
        plan, err = self.plan_for(card)

        cw = stdscr
        cw.erase()
        cw.border(0)
        arrow = {"LONG": "▲", "SHORT": "▼"}.get(card.direction, "•")
        cw.addstr(0, 2, (f" {card.coin} {arrow} {card.direction} · score {card.score} "
                         f"· lean {card.lean:+.2f} · earlyness {card.earlyness:.2f} "
                         f"[ENTER back]").ljust(w - 4), curses.A_BOLD)
        cw.hline(1, 2, curses.ACS_HLINE, w - 4)

        y = 3
        def put(s, attr=curses.A_NORMAL):
            nonlocal y
            if y < h - 2:
                try:
                    cw.addstr(y, 2, s[:w - 4], attr)
                except curses.error:
                    pass
                y += 1

        put("SCORE COMPONENTS          MAGNITUDE   LEAN")
        for k in ("VOL", "BOOK", "OI"):
            mv = card.magnitude_parts.get(k)
            lv = card.lean_parts.get(k)
            put(f"  {k:<22} {('%.1f' % (mv*100)) if mv is not None else '  n/a':>8}"
                f"   {(('%+.2f' % lv) if lv is not None else 'n/a'):>7}")
        put("")
        put(f"  earlyness {card.earlyness:.2f}   ·   24h {card.change_24h_pct:+.1f}%"
            f"   ·   vol24 ${card.quote_vol_24h:,.0f}"
            f"   ·   spread {card.spread_pct:.2f}%"
            f"   ·   funding {card.funding_rate*100:+.4f}%")
        if card.oi_change_pct is None:
            put("  ⚠ OI unavailable on MEXC — OI/FUNDING signal is running on "
                "funding alone", curses.A_DIM)
        put("")

        if card.vetoes:
            put("VETOES", curses.A_BOLD)
            for v in card.vetoes:
                put(f"  ⨯ {v.code}: {v.reason}")
            put("")

        if plan:
            put("TRADE PLAN (review only — this app places no orders)", curses.A_BOLD)
            for line in pl.format_plan(plan, card).splitlines():
                attr = curses.A_BOLD if line.strip().startswith("⚠") else curses.A_NORMAL
                put(line, attr)
        elif err:
            put(f"no plan: {err}")
        else:
            put("no plan available")

        cw.addstr(h - 2, 2, "ENTER back   q quit"[:w - 4])
        cw.refresh()

    # ---------- loop ----------
    def loop(self, stdscr):
        curses.curs_set(0)
        stdscr.nodelay(True)
        self.refresh_universe()

        while True:
            if not self.paused and (time.time() - self.last_scan >
                                    self.args.interval):
                self.scan_once()

            h, w = stdscr.getmaxyx()
            if self.detail:
                self.draw_detail(stdscr, h, w)
            else:
                self.draw_table(stdscr, h, w)

            ch = stdscr.getch()
            if ch == -1:
                time.sleep(0.4)
                continue
            if ch in (ord("q"), 27):
                return
            if ch == ord("p"):
                self.paused = not self.paused
                self.status = "paused" if self.paused else "resumed"
            elif ch == ord("d"):
                self.dir_filter = {"both": "long", "long": "short",
                                   "short": "both"}[self.dir_filter]
                self.sel = 0
            elif ch == ord("s"):
                order = list(SORT_KEYS)
                self.sort_key = order[(order.index(self.sort_key) + 1) % len(order)]
                self.sel = 0
            elif ch == ord("w"):
                self.args.write_logs = not self.args.write_logs
                self.status = ("logging ON (flagged + shadow)" if self.args.write_logs
                               else "logging OFF")
            elif ch in (ord("\n"), ord("j")) and not self.detail:
                self.detail = True
            elif ch == ord("\n") and self.detail:
                self.detail = False
            elif ch == curses.KEY_DOWN:
                self.sel = min(self.sel + 1, max(0, len(self.visible()) - 1))
            elif ch == curses.KEY_UP:
                self.sel = max(0, self.sel - 1)


def run_headless(app, iterations=None):
    """
    Non-curses loop for environments without a real TTY (cron, systemd, CI,
    piping to a file). Prints a table per cycle and keeps logging.

    The TUI is the primary surface, but the scanner must not be hostage to a
    terminal: curses.wrapper() raises if stdin is not a tty, which is exactly
    the situation under a service manager or a piped invocation.
    """
    n = 0
    try:
        while iterations is None or n < iterations:
            app.refresh_universe()
            app.scan_once()
            rows = app.visible()
            st = app.store.stats()

            print(f"\n{'='*100}")
            print(f" ALT RADAR · {time.strftime('%Y-%m-%d %H:%M:%S')} · MEXC · "
                  f"stake ${app.args.stake:.2f} · read-only")
            print(f" universe {len(app.uni)} · shown {len(rows)} · "
                  f"sort {app.sort_key} · dir {app.dir_filter}")
            print(f" logs {st['rows']} rows / {st['coins']} coins · "
                  f"flagged {st['flagged']} · L {st['longs']} / S {st['shorts']}")
            print("=" * 100)
            print(f"{'#':<3}{'DIR':<5}{'COIN':<15}{'PRICE':>13}{'24H%':>8}"
                  f"{'VOL24':>10}{'LEAN':>7}{'EARLY':>7}{'SCORE':>7}  FLAGS")
            print("-" * 100)

            shown = 0
            for c in rows:
                if shown >= 15:
                    break
                arrow = {"LONG": "▲", "SHORT": "▼"}.get(c.direction, "•")
                vol = c.quote_vol_24h
                vol_s = f"${vol/1e6:.1f}M" if vol >= 1e6 else f"${vol/1e3:.0f}K"
                flags = []
                if c.vetoes:
                    flags.append("veto:" + ",".join(v.code for v in c.vetoes))
                if c.min_notional and c.min_notional > app.args.stake:
                    flags.append("WATCH")
                print(f"{shown+1:<3}{arrow:<5}{c.coin:<15}{c.price:>13.8g}"
                      f"{c.change_24h_pct:>7.1f}%{vol_s:>10}{c.lean:>7.2f}"
                      f"{c.earlyness:>7.2f}{c.score:>7.1f}  {' '.join(flags)}")
                shown += 1

            top = next((c for c in rows if c.actionable), None)
            if top:
                plan, err = app.plan_for(top)
                print("-" * 100)
                print(f" TRADE PLAN · {top.coin} {top.direction} (review only)")
                if plan:
                    print(pl.format_plan(plan, top))
                else:
                    print(f"  no plan: {err}")

            sys.stdout.flush()
            n += 1
            if iterations is not None and n >= iterations:
                break
            time.sleep(app.args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        app.store.close()
    return n


def main():
    ap = argparse.ArgumentParser(description="ALT RADAR live scanner")
    ap.add_argument("--stake", type=float, default=0.10)
    ap.add_argument("--coins", type=int, default=150)
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--db", default="data/signals.db")
    ap.add_argument("--log-threshold", type=float, default=24.0)
    ap.add_argument("--write-logs", action="store_true", default=True)
    ap.add_argument("--no-logs", dest="write_logs", action="store_false")
    ap.add_argument("--headless", action="store_true",
                    help="print-only loop, no curses (for cron/systemd/pipes)")
    ap.add_argument("--iterations", type=int, default=None,
                    help="headless only: stop after N cycles")
    args = ap.parse_args()

    app = App(args)

    if args.headless:
        run_headless(app, args.iterations)
        return

    # Fall back to headless rather than dying if there is no usable terminal.
    if not sys.stdout.isatty() or not sys.stdin.isatty():
        print("no interactive TTY detected — running headless "
              "(use --headless to be explicit)", file=sys.stderr)
        run_headless(app, args.iterations)
        return

    try:
        curses.wrapper(app.loop)
    except curses.error as e:
        print(f"curses unavailable ({e}) — falling back to headless",
              file=sys.stderr)
        run_headless(app, args.iterations)

if __name__ == "__main__":
    main()
