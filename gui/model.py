"""Pure display logic for the ALT RADAR desktop GUI.

Contract (task G1 brief): NO tkinter import, NO network, NO threads — every
function in this module must be unit-testable without a display.

Nothing here re-derives scanner semantics:
  - sorting reuses proto.app.SORT_KEYS (score / early / lean / move / vol),
  - trade plans are rendered by proto.planner.format_plan,
  - the WATCH rule mirrors the TUI display rule (min_notional > stake).
Scoring, vetoes and the stake-aware flag gate live in proto.scorer /
proto.app and are NEVER forked in this module.
"""

from proto import planner as pl
from proto.app import SORT_KEYS  # sorting semantics shared with the TUI
from proto.scorer import Scorecard, Veto

# Direction filters accepted by filter_cards / shown in the toolbar combo.
DIR_FILTERS = ("both", "long", "short")

# Outcome horizons, in display order (proto.outcomes.HORIZON_BARS keys).
HORIZONS = ("1h", "4h", "24h", "7d")

# Operator input bounds (brief: stake > 0, threshold 0-100, coins 10-581,
# interval >= 15s).
MIN_COINS, MAX_COINS = 10, 581
MIN_INTERVAL = 15


def direction_arrow(direction):
    """▲ / ▼ / • for a scorecard direction."""
    return {"LONG": "▲", "SHORT": "▼"}.get(direction, "•")


def _to_float(v):
    """float(v), or None for missing / garbage / non-finite venue values."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def format_price(p):
    """Price text: 8 significant digits, fixed decimals for sub-1e-4 prices.

    None / garbage / non-finite -> "n/a" (venue fields are untrusted text and
    a bad price must never crash a render).
    """
    f = _to_float(p)
    if f is None:
        return "n/a"
    if f == 0:
        return "0"
    if abs(f) < 1e-4:
        # Tiny prices must not render as scientific notation ("1.2e-07").
        return f"{f:.12f}".rstrip("0").rstrip(".")
    return f"{f:.8g}"


def format_oi(pct, notional=None):
    """OI cell: percent plus the dollars behind it.

    "+68.3% (~$12K)" — the notional (approx USDT open interest) is what
    separates real money arriving from a tiny base printing a big percent.
    "n/a" when the percent is missing (MEXC-only / unavailable).
    """
    f = _to_float(pct)
    if f is None:
        return "n/a"
    base = f"{f:+.1f}%"
    n = _to_float(notional)
    if n is None:
        return base
    return f"{base} (~{format_vol(n)})"


def format_vol(v):
    """24h quote volume text: $X.XM at >= 1M, $XK at >= 1K, $N below 1K."""
    f = _to_float(v)
    if f is None:
        return "n/a"
    if f >= 1e6:
        return f"${f / 1e6:.1f}M"
    if f >= 1e3:
        return f"${f / 1e3:.0f}K"
    return f"${f:.0f}"


def format_flags(card, stake):
    """Flags column text for one row, space separated ('' when nothing).

      WATCH        - venue minimum notional is above the operator's stake
                     (same rule the TUI flags column shows);
      UNVALIDATED  - Tier-2 marker for MEXC rows (no outcome history yet).
    """
    flags = []
    min_not = getattr(card, "min_notional", None)
    f_min = _to_float(min_not)
    f_stake = _to_float(stake)
    if f_min is not None and f_stake is not None and f_min > f_stake:
        flags.append("WATCH")
    notes = getattr(card, "notes", None) or ()
    tier2 = any(isinstance(n, str) and "UNVALIDATED" in n for n in notes)
    if getattr(card, "venue", None) == "MEXC" or tier2:
        flags.append("UNVALIDATED")
    return " ".join(flags)


def filter_cards(cards, dir_filter):
    """Direction filter: 'both' keeps everything, 'long'/'short' selects.

    Case-insensitive; unknown filters raise ValueError (operator inputs are
    validated, never silently ignored).
    """
    key = str(dir_filter).strip().lower()
    if key == "both":
        return list(cards)
    if key in ("long", "short"):
        want = key.upper()
        return [c for c in cards if c.direction == want]
    raise ValueError(f"unknown direction filter: {dir_filter!r}")


def sort_cards(cards, key):
    """Sort a list with proto.app.SORT_KEYS semantics.

    Keys: score / early / lean / move / vol — the SAME lambdas the TUI and
    headless loop use (no fork). Unknown keys raise ValueError.
    """
    try:
        keyfunc = SORT_KEYS[key]
    except KeyError:
        raise ValueError(f"unknown sort key: {key!r}") from None
    return sorted(cards, key=keyfunc)


def split_vetoed(cards):
    """(ranked, vetoed): cards without vetoes / cards carrying vetoes.

    Input order is preserved — callers sort afterwards via sort_cards.
    """
    ranked, vetoed = [], []
    for c in cards:
        (vetoed if getattr(c, "vetoes", None) else ranked).append(c)
    return ranked, vetoed


def snapshot_cards(rows):
    """Rebuild scorecards from Store.latest_rows() dicts (instant launch).

    Restores every display field the table and detail pane need (parts,
    vetoes with codes, OI percent + notional, economics). Per-signal notes
    (vol/price detail dicts) were never logged and stay absent — the detail
    pane shows the numbers, not the prose. Saved veto reasons read as the
    code itself (e.g. "late_move"), since only codes are logged.

    Rows arrive newest-scan-first by score; a coin appearing twice (two
    cycles inside the window) keeps its highest-scored row — the table
    addresses rows by coin and duplicates would collide.
    """
    cards = []
    seen = set()
    for r in rows:
        if r.get("coin") in seen:
            continue
        seen.add(r.get("coin"))
        sc = Scorecard(
            coin=r.get("coin", "?"), venue=r.get("venue") or "MEXC",
            score=r.get("score") or 0.0, lean=r.get("lean") or 0.0,
            direction=r.get("direction") or "NEUTRAL",
            earlyness=r.get("earlyness") or 0.0,
            funding_rate=r.get("funding_rate") or 0.0,
            oi_change_pct=r.get("oi_change_pct"),
            oi_notional=r.get("oi_notional"),
            price=r.get("price") or 0.0,
            change_24h_pct=r.get("change_24h_pct") or 0.0,
            quote_vol_24h=r.get("quote_vol_24h") or 0.0,
            spread_pct=r.get("spread_pct") or 0.0,
            min_notional=r.get("min_notional"))
        sc.magnitude_parts = {"VOL": r.get("mag_vol"), "BOOK": r.get("mag_book"),
                              "OI": r.get("mag_oi")}
        sc.lean_parts = {"VOL": r.get("lean_vol"), "BOOK": r.get("lean_book"),
                         "OI": r.get("lean_oi")}
        sc.tradeable = bool(r.get("tradeable", True))
        codes = [c for c in str(r.get("veto_codes") or "").split(",") if c]
        sc.vetoes = [Veto(code=c, reason=c) for c in codes]
        cards.append(sc)
    return cards


def snapshot_label(rows):
    """'saved 14:02 (150 coins)' or '' when there is nothing saved."""
    if not rows:
        return ""
    import time
    n_coins = len({r.get("coin") for r in rows})
    saved_at = max((r.get("ts") or 0) for r in rows)
    return "saved %s (%d coins)" % (
        time.strftime("%H:%M", time.localtime(saved_at)), n_coins)


def resolve_launch_snapshot(db_path):
    """("file"|"db"|"empty", cards, plans, label) for instant launch.

    File first (exact last screen, cached plans included), then the DB's
    latest scan cycle (no plans — they embed live structure), then blank.
    Only local reads; fully unit-testable.
    """
    from proto import snapshot as snap
    cards, plans, saved_at = snap.load(db_path)
    if cards:
        import time as _t
        label = "saved %s (%d coins)" % (
            _t.strftime("%H:%M", _t.localtime(saved_at)), len(cards))
        return "file", cards, plans, label
    try:
        from proto.store import Store
        store = Store(db_path)
        try:
            rows = store.latest_rows()
        finally:
            try:
                store.close()
            except Exception:
                pass
    except Exception:
        return "empty", [], {}, ""
    if not rows:
        return "empty", [], {}, ""
    return "db", snapshot_cards(rows), {}, snapshot_label(rows)


def plan_text(card, plan):
    """Full trade-plan text via proto.planner.format_plan (never reimplemented)."""
    if plan is None:
        return "no plan available"
    return pl.format_plan(plan, card)


def outcome_summary(store):
    """Outcome-log summary for the outcomes panel.

      {"counts": {"1h": n, "4h": n, "24h": n, "7d": n},
       "total": n,
       "direction": {"LONG":  {"count", "avg_return", "hit_rate"},
                     "SHORT": {...}, ...}}

    avg_return = mean signed return (%) of resolved rows for that direction;
    hit_rate   = share of those resolved rows with a positive (in-favour)
    signed return, 0..1. `store=None` (or a store with no rows) yields the
    zero shape so the panel can render before any resolve has run.
    """
    zero = {"count": 0, "avg_return": 0.0, "hit_rate": 0.0}
    summary = {
        "counts": {h: 0 for h in HORIZONS},
        "total": 0,
        "direction": {"LONG": dict(zero), "SHORT": dict(zero)},
    }
    if store is None:
        return summary

    rows = store.conn.execute(
        "SELECT o.horizon, o.return_pct, s.direction"
        " FROM outcome_log o JOIN signal_log s ON s.id = o.signal_id"
    ).fetchall()
    rets_by_dir = {}
    for horizon, ret, direction in rows:
        summary["total"] += 1
        if horizon in summary["counts"]:
            summary["counts"][horizon] += 1
        ret = _to_float(ret)
        if ret is None:
            continue
        rets_by_dir.setdefault(direction, []).append(ret)

    for direction, rets in rets_by_dir.items():
        if not rets:
            continue
        wins = sum(1 for r in rets if r > 0)
        summary["direction"].setdefault(direction, dict(zero)).update(
            {"count": len(rets),
             "avg_return": sum(rets) / len(rets),
             "hit_rate": wins / len(rets)})
    return summary


def detail_text(card, plan=None, plan_err=None, stake=None, last_error=None):
    """Full detail-pane text for one scorecard (read-only, review-only).

    Mirrors the TUI detail view: score components (VOL/BOOK/OI mag+lean),
    earlyness / 24h / vol / spread / funding / OI lines, Tier-2 + funding-only
    notes, vetoes, counter-trend warnings, the last per-coin error, and the
    full trade plan rendered by pl.format_plan (which carries plan warnings).
    """
    out = []
    arrow = direction_arrow(card.direction)
    out.append(f"{card.coin}  {arrow} {card.direction}"
               f"   score {card.score:.1f}"
               f"   lean {card.lean:+.2f}"
               f"   earlyness {card.earlyness:.2f}")
    out.append(f"venue {card.venue} · tier 2 · READ-ONLY — this app places no orders")
    out.append("")

    out.append("SCORE COMPONENTS                 MAG      LEAN")
    for k in ("VOL", "BOOK", "OI"):
        mv = _to_float(card.magnitude_parts.get(k))
        lv = _to_float(card.lean_parts.get(k))
        mag = f"{mv * 100:.1f}" if mv is not None else "n/a"
        lean = f"{lv:+.2f}" if lv is not None else "n/a"
        out.append(f"  {k:<26} {mag:>10} {lean:>10}")
    out.append("")

    oi = _to_float(card.oi_change_pct)
    oi_n = _to_float(getattr(card, "oi_notional", None))
    out.append(f"  earlyness {card.earlyness:.2f}"
               f"   ·   24h {card.change_24h_pct:+.1f}%"
               f"   ·   vol24 {format_vol(card.quote_vol_24h)}"
               f"   ·   spread {card.spread_pct:.2f}%"
               f"   ·   funding {card.funding_rate * 100:+.4f}%"
               f"   ·   OIΔ {format_oi(oi, oi_n)}")
    if oi_n is not None:
        out.append(f"      (~{format_vol(oi_n)} open interest behind the move — "
                   f"judge the percent against this base)")
    if oi is None:
        out.append("  ⚠ OI unavailable on MEXC — OI/FUNDING signal is running on "
                   "funding alone")

    f_min = _to_float(card.min_notional)
    f_stake = _to_float(stake)
    if f_stake is not None:
        if f_min is None:
            out.append("  ⚠ minimum notional unknown — fails closed, this coin "
                       "never flags at any stake")
        elif f_min > f_stake:
            out.append(f"  ⚠ min notional ${f_min:.4f} > stake ${f_stake:.2f} — "
                       "WATCH only until stake grows")
        else:
            out.append(f"  min notional ${f_min:.4f} ≤ stake ${f_stake:.2f} — "
                       "fits stake")

    notes = card.notes or ()
    if any(isinstance(n, str) and "UNVALIDATED" in n for n in notes):
        out.append("  ⚠ TIER 2 · UNVALIDATED — no outcome history exists for "
                   "this score yet; treat as experimental")
    if getattr(card, "venue", None) == "MEXC" and not any(
            isinstance(n, str) and "UNVALIDATED" in n for n in notes):
        out.append("  ⚠ TIER 2 · UNVALIDATED — MEXC row, no outcome history "
                   "for this score yet; treat as experimental")
    if last_error:
        out.append(f"  ⚠ last error: {last_error}")
    out.append("")

    if card.vetoes:
        out.append("VETOES")
        for v in card.vetoes:
            out.append(f"  ⨯ {v.code}: {v.reason}")
        out.append("")

    counters = [n for n in notes
                if isinstance(n, str) and n.startswith("counter-trend:")]
    if counters:
        out.append("WARNINGS")
        for n in counters:
            out.append(f"  ⚠ {n}")
        out.append("")

    out.append("TRADE PLAN (review only — this app places no orders)")
    if plan is not None:
        out.append(plan_text(card, plan))
    elif plan_err:
        out.append(f"  no plan: {plan_err}")
    else:
        out.append("  no plan available (fetching…)")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Operator input validation (brief: invalid input -> a status-bar message,
# never a crash). Each returns the parsed value or raises ValueError with a
# message that can be shown verbatim in the status bar.
# ---------------------------------------------------------------------------

def validate_stake(value):
    f = _to_float(value)
    if f is None or f <= 0:
        raise ValueError(f"stake must be a number > 0, got {value!r}")
    return f


def validate_threshold(value):
    f = _to_float(value)
    if f is None or not 0 <= f <= 100:
        raise ValueError(f"log threshold must be between 0 and 100, got {value!r}")
    return f


def validate_coins(value):
    f = _to_float(value)
    if f is None or f != int(f):
        raise ValueError(f"coins must be a whole number, got {value!r}")
    v = int(f)
    if not MIN_COINS <= v <= MAX_COINS:
        raise ValueError(f"coins must be between {MIN_COINS} and {MAX_COINS}, got {value!r}")
    return v


def validate_interval(value):
    f = _to_float(value)
    if f is None or f != int(f):
        raise ValueError(f"interval must be a whole number of seconds, got {value!r}")
    v = int(f)
    if v < MIN_INTERVAL:
        raise ValueError(f"interval must be at least {MIN_INTERVAL}s, got {value!r}")
    return v
