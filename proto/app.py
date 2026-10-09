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
from . import scan as scanmod
from . import indicators as ind
from . import planner as pl
from .scan import build_universe, score_universe, venue_health, UNIVERSE_BUDGET
from .scorer import fits_stake
from .store import Store

SORT_KEYS = {
    "score": lambda c: -c.score,
    "early": lambda c: -c.earlyness,
    "lean": lambda c: -abs(c.lean),
    "move": lambda c: -abs(c.change_24h_pct),
    "vol": lambda c: -c.quote_vol_24h,
}

# Universe feed cache (spec SS3): tickers+details+Bybit map refresh at most
# every 10 minutes in memory. Excluded from the 90s scan gate - only the
# per-coin fetch+score in scan_once is inside that budget.
UNIVERSE_TTL = 180


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
        self.failed = 0              # per-coin failures, last scan_once
        self.last_errors = {}        # {coin: reason} from the last scan_once
        self.log_errors = 0          # store write failures, last scan_once
        self.universe_error = None   # last universe refresh failure, if any
        self._uni_at = 0.0           # when self.uni was last refreshed

    # ---------- data ----------
    def refresh_universe(self):
        # store passed -> rotation pointer read/advanced each refresh, so the
        # 30-coin rotation window sweeps forward and survives restarts.
        # `universe_budget` is an OPTIONAL front-end hook (the GUI ties it to
        # its --coins control); absent, the guaranteed 150-coin budget keeps
        # applying exactly as before.
        budget = getattr(self.args, "universe_budget", UNIVERSE_BUDGET)
        self.uni = build_universe(self.args.stake, budget=budget,
                                  store=self.store)
        self.det = mexc.details()
        self.tk = mexc.tickers()
        self._uni_at = time.time()

    def maybe_refresh_universe(self):
        """Refresh the universe feed only when the 10-minute cache expired.

        A failed refresh keeps the last good universe and is surfaced in
        status - never a silent crash of the loop.
        """
        if time.time() - self._uni_at <= UNIVERSE_TTL:
            return
        try:
            self.refresh_universe()
            self.universe_error = None
        except Exception as e:
            self.universe_error = f"{type(e).__name__}: {e}"
            if not self.uni:
                self.status = f"universe unavailable: {self.universe_error}"

    def degraded_text(self):
        """Header marker for venues whose last universe fetch failed (SS6)."""
        bad = [v.upper() for v, st in venue_health().items() if not st["ok"]]
        return "".join(f" · ⚠ {v} DEGRADED" for v in bad)

    def scan_once(self):
        # The 90s gate covers the per-coin fetch+score ONLY; the universe
        # feed above is cached 10 minutes and excluded from that budget.
        self.maybe_refresh_universe()
        t0 = time.time()
        ranked = sorted(
            self.uni,
            key=lambda s: scanmod._fnum(self.tk.get(s[0], {}).get("amount24")),
            reverse=True)[: self.args.coins]

        errors = {}
        cards = score_universe(ranked, self.det, self.tk, self.args.stake,
                               errors=errors,
                               stagger=scanmod.STAGGER_S,
                               max_workers=scanmod.MAX_WORKERS,
                               attach_plans=True,
                               max_leverage=getattr(
                                   self.args, "leverage_cap",
                                   pl.MAX_LEVERAGE))

        # Log: flagged rows AND shadow rows (vetoed / sub-threshold). SS6.1a.
        # Flagged = stake-aware actionable AND above threshold (selective,
        # not permissive): a None min_notional never flags (fail-closed).
        # Flagged rows also freeze their trade plan (levels at score time)
        # so stop/TP1/TP2 hits become measurable facts, not estimates.
        # Log write failures are counted too - never swallowed silently.
        log_errors = 0
        if self.args.write_logs:
            for sc in cards:
                flagged = (sc.is_actionable(self.args.stake)
                           and sc.score >= self.args.log_threshold)
                try:
                    sid = self.store.log_signal(sc, flagged=flagged, tier=2)
                    plan = getattr(sc, "plan", None)
                    if flagged and plan is not None and getattr(
                            plan, "valid", False):
                        self.store.log_plan(sid, plan)
                    # Board upkeep: the coin's single current row follows
                    # every scan (journal above keeps the full history).
                    self.store.upsert_current(sc, flagged, sid, tier=2)
                except Exception:
                    log_errors += 1

        self.failed = len(errors)
        self.last_errors = errors
        self.log_errors = log_errors
        self.cards = cards
        self.sel = 0
        self.last_scan = time.time()
        self.status = (f"scanned {len(cards)} in {time.time()-t0:.0f}s · "
                       f"universe {len(self.uni)} · "
                       f"failed {len(errors)}/{len(ranked)}"
                       + (f" · log errors {log_errors}" if log_errors else "")
                       + (f" · universe ERROR {self.universe_error}"
                          if self.universe_error else "")
                       + self.degraded_text())

    def visible(self):
        """Ranked rows: non-vetoed only. Vetoes get their own section (SS6)."""
        out = [c for c in self.cards if not c.vetoes]
        if self.dir_filter == "long":
            out = [c for c in out if c.direction == "LONG"]
        elif self.dir_filter == "short":
            out = [c for c in out if c.direction == "SHORT"]
        return sorted(out, key=SORT_KEYS[self.sort_key])

    def vetoed(self):
        """Vetoed rows, excluded from ranking but still visible + logged."""
        out = [c for c in self.cards if c.vetoes]
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
            return pl.build_plan(card, stake=self.args.stake, swing_ref=swing,
                                 funding_rate=card.funding_rate,
                                 max_leverage=getattr(
                                     self.args, "leverage_cap",
                                     pl.MAX_LEVERAGE)), bars
        except Exception as e:
            return None, str(e)

    # ---------- rendering ----------
    def draw_table(self, stdscr, h, w):
        rows = self.visible()
        cw = stdscr
        cw.erase()
        cw.border(0)

        paused = " PAUSED" if self.paused else ""
        cw.addstr(0, 2, f" ALT RADAR · MEXC perp scanner · live · read-only"
                        f" · TIER-2 UNVALIDATED{paused} "[:w - 4])
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

        vet = self.vetoed()
        # Reserve room for the vetoed section (header + up to 3 rows) so the
        # ranked list never pushes it off-screen.
        veto_lines = (2 + min(len(vet), 3)) if vet else 0
        body_h = max(1, h - 9 - veto_lines)
        start = 0
        if self.sel >= body_h:
            start = self.sel - body_h + 1
        for i, c in enumerate(rows[start:start + body_h]):
            y = 6 + (i - start)
            arrow = {"LONG": "▲", "SHORT": "▼"}.get(c.direction, "•")
            vol = c.quote_vol_24h
            vol_s = f"${vol/1e6:.1f}M" if vol >= 1e6 else f"${vol/1e3:.0f}K"
            flags = []
            if c.min_notional and not fits_stake(c.min_notional,
                                                 self.args.stake):
                flags.append("WATCH")
            attr = curses.A_REVERSE if (i + start) == self.sel else curses.A_NORMAL
            line = (f"{i+start+1:<3}{arrow:<5}{c.coin:<15}{c.price:>13.8g}"
                    f"{c.change_24h_pct:>7.1f}%{vol_s:>10}{c.lean:>7.2f}"
                    f"{c.earlyness:>7.2f}{c.score:>7.1f}  {' '.join(flags)}")
            try:
                cw.addstr(y, 2, line[:w - 4], attr)
            except curses.error:
                pass

        # Vetoed section: excluded from ranking, still visible + shadow-logged
        # (spec SS5/SS6) - a vetoed coin that later pumps must leave a trace.
        y = 6 + body_h
        if vet and y < h - 3:
            try:
                cw.addstr(y, 2, (f"── VETOED · {len(vet)} excluded from "
                                 f"ranking, shadow-logged ──")[:w - 4],
                          curses.A_DIM)
            except curses.error:
                pass
            y += 1
        for c in vet[:max(0, h - 3 - y)]:
            arrow = {"LONG": "▲", "SHORT": "▼"}.get(c.direction, "•")
            codes = ",".join(v.code for v in c.vetoes)
            line = f"   {arrow} {c.coin:<15}{c.score:>7.1f}  veto:{codes}"
            try:
                cw.addstr(y, 2, line[:w - 4], curses.A_DIM)
            except curses.error:
                pass
            y += 1

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
        _oi = card.oi_change_pct
        _oin = getattr(card, "oi_notional", None)
        if _oi is None:
            put("  ⚠ OI unavailable on MEXC — OI/FUNDING signal is running on "
                "funding alone", curses.A_DIM)
        elif _oin is not None:
            put(f"  OIΔ {_oi:+.1f}% on ~${_oin:,.0f} open interest — judge the "
                f"percent against this base", curses.A_DIM)
        if any(isinstance(n, str) and "UNVALIDATED" in n for n in card.notes):
            put("  ⚠ TIER 2 · UNVALIDATED — no outcome history exists for this "
                "score yet; treat as experimental", curses.A_DIM)
        if card.coin in self.last_errors:
            put(f"  ⚠ last error: {self.last_errors[card.coin]}", curses.A_DIM)
        # Counter-trend labels are surfaced, never swallowed (spec SS4) —
        # they reach plan warnings when a plan exists; show them here too so
        # an unplannable coin still carries the risk label.
        for n in card.notes:
            if isinstance(n, str) and (n.startswith("counter-trend:") or
                                       n.startswith("MTF ") or
                                       n.startswith("DATA ")):
                put(f"  ⚠ {n}", curses.A_BOLD)
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
        try:
            from .snapshot import load as _load
            saved, _, _ = _load(self.args.db)
            if saved:
                self.cards = list(saved)
                self.status = (f"showing saved ({len(saved)} coins) — "
                               f"live scan running…")
        except Exception:
            pass
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
                from . import snapshot as _snap
                _snap.save(self.args.db, self.cards)
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
            # scan_once refreshes the universe itself when the 10-minute
            # cache expires, so every cycle stays inside the same contract.
            app.scan_once()
            rows = app.visible()
            st = app.store.stats()

            print(f"\n{'='*100}")
            print(f" ALT RADAR · {time.strftime('%Y-%m-%d %H:%M:%S')} · MEXC · "
                  f"stake ${app.args.stake:.2f} · read-only · TIER-2 UNVALIDATED")
            print(f" universe {len(app.uni)} · shown {len(rows)} · "
                  f"vetoed {len(app.vetoed())} · "
                  f"sort {app.sort_key} · dir {app.dir_filter}")
            print(f" logs {st['rows']} rows / {st['coins']} coins · "
                  f"flagged {st['flagged']} · L {st['longs']} / S {st['shorts']}"
                  f" · failed {app.failed}{app.degraded_text()}"
                  + (f" · universe ERROR {app.universe_error}"
                     if app.universe_error else ""))
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
                if c.min_notional and not fits_stake(c.min_notional,
                                                     app.args.stake):
                    flags.append("WATCH")
                print(f"{shown+1:<3}{arrow:<5}{c.coin:<15}{c.price:>13.8g}"
                      f"{c.change_24h_pct:>7.1f}%{vol_s:>10}{c.lean:>7.2f}"
                      f"{c.earlyness:>7.2f}{c.score:>7.1f}  {' '.join(flags)}")
                shown += 1

            # Vetoed: excluded from ranking, still visible + shadow-logged.
            vet = app.vetoed()
            if vet:
                print("-" * 100)
                print(f" VETOED · {len(vet)} excluded from ranking, "
                      f"shadow-logged (top 5 by {app.sort_key})")
                for c in vet[:5]:
                    arrow = {"LONG": "▲", "SHORT": "▼"}.get(c.direction, "•")
                    codes = ",".join(v.code for v in c.vetoes)
                    print(f"   {arrow} {c.coin:<15}{c.score:>7.1f}  veto:{codes}")

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
        from . import snapshot as _snap
        _snap.save(app.args.db, app.cards)
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
