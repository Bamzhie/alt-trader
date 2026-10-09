# ALT RADAR — Desktop GUI User Manual

**ALT RADAR** is a READ-ONLY MEXC perpetual-futures scanner. This manual
covers the tkinter desktop app (`python3 -m gui`).

> **READ-ONLY by design.** The window carries a permanent ` READ-ONLY `
> badge: the app places no orders, holds no API keys, and moves no funds.
> Every plan it shows is for review only.

Tested with Python 3.14 / tkinter 8.6 (Linux, X11/XWayland). Stdlib only —
nothing to `pip install`.

---

## 1. Requirements & installation

| Requirement | Notes |
|---|---|
| Python 3 | Developed and tested on Python 3.14 |
| tkinter | Debian/Ubuntu: `sudo apt install python3-tk` · Fedora: `sudo dnf install python3-tkinter` · Windows/macOS python.org installers include Tk |
| Network | Needed for scans, plans, collector and resolver; the app starts and stays usable offline (it keeps the last good table) |
| Display | An X/Wayland display (or WSLg, or `ssh -X`). See §12 for headless errors |

There is no build step and no virtualenv is required. Run everything from
the repository root (the `gui` and `proto` packages must be importable).

## 2. Launching the app

```
python3 -m gui
```

### CLI flags (all optional)

| Flag | Default | Bounds / validation | Meaning |
|---|---|---|---|
| `--stake STAKE` | `0.10` | must be > 0 | USDT stake per trade. Drives WATCH flags, min-notional checks and plan sizing |
| `--coins COINS` | `150` | 10–581 | Universe budget **and** scan size (how many coins are scored each scan) |
| `--interval INTERVAL` | `60` | ≥ 15 (seconds) | Auto-scan period |
| `--db DB` | `data/signals.db` | any writable path | SQLite signal/outcome database |
| `--log-threshold THRESHOLD` | `24.0` | 0–100 | Score at or above which a scanned row is logged as **FLAGGED** |
| `--no-auto-scan` | off | — | Start with auto-scan paused (the **Scan now** button still works) |
| `-h`, `--help` | — | — | Print usage and exit |

Invalid CLI input never opens a window — the app prints an error to stderr
and exits with code **2**:

```
$ python3 -m gui --stake 0
error: stake must be a number > 0, got 0.0      (exit 2)
$ python3 -m gui --coins 5
error: coins must be between 10 and 581, got 5  (exit 2)
$ python3 -m gui --interval 5
error: interval must be at least 15s, got 5     (exit 2)
$ python3 -m gui --log-threshold 101
error: log threshold must be between 0 and 100, got 101.0  (exit 2)
```

### Launch examples

```bash
python3 -m gui                                   # everything default
python3 -m gui --stake 0.5 --coins 60            # bigger stake, small universe
python3 -m gui --no-auto-scan                    # manual control only
python3 -m gui --db /path/to/other.db --log-threshold 30
python3 -m gui --interval 300                    # rescan every 5 minutes
```

On start (auto-scan on, the default) the first scan fires within about half
a second; subsequent scans run every `--interval` seconds.

## 3. Window layout

```
┌──────────────────────────────────────────────────────────────────────┐
│ Header: title · READ-ONLY · TIER-2 UNVALIDATED · summary line        │
├──────────────────────────────────────────────────────────────────────┤
│ Toolbar: Stake | Flag threshold | Coins | Interval | Dir | Sort |     │
│          Scan now | auto-scan | Collect bars | Resolve | Refresh      │
├─────────────────────────────────────┬────────────────────────────────┤
│ Signals table (ranked rows)         │ Detail — selected coin         │
│                                     │ (full breakdown, trade plan)   │
│ VETOED — excluded, shadow-logged    ├────────────────────────────────┤
│                                     │ Outcomes (hit rates)           │
├─────────────────────────────────────┴────────────────────────────────┤
│ Status bar: activity · failed x/y · message                          │
└──────────────────────────────────────────────────────────────────────┘
```

Window title: `ALT RADAR — READ-ONLY MEXC scanner`; default size 1360×860,
minimum 1100×640.

## 4. Header line

`ALT RADAR · MEXC perp scanner` ` READ-ONLY ` `TIER-2 UNVALIDATED`
followed by the live summary:

```
universe 150 · shown 12 · vetoed 3 · stake $0.10 · MEXC ok · Bybit ok ·
last scan 14:03:22 · logs 4821 rows / 577 coins · flagged 214 · outcomes 96
```

| Segment | Meaning |
|---|---|
| `universe N` | Coins in the current universe build (0 before the first scan) |
| `shown N` | Rows currently visible in the Signals table (after direction filter, excluding vetoed) |
| `vetoed N` | Rows excluded from ranking by vetoes (shown in the VETOED section) |
| `stake $X.XX` | The stake every WATCH/min-notional check currently uses |
| `MEXC` / `Bybit` | Venue health: `ok` · `DEGRADED` (last fetch failed) · `–` (not yet attempted) |
| `last scan` | Wall-clock time of the last scan (`never` before the first) |
| `logs N rows / N coins` | Rows / distinct coins recorded in the SQLite signal log |
| `flagged N` | Logged rows that met the flag rule (see §7) |
| `outcomes N` | Resolved outcome rows |

## 5. Toolbar — every control

Controls commit as you use them; every accepted change is announced in the
status bar, every rejected one shows a red message and restores the previous
value. Nothing here ever crashes the app.

| Control | How it works | Status message |
|---|---|---|
| **Stake $** entry + **Apply** | Type a number > 0, click **Apply** (or press **Enter** in the field). Applies immediately: WATCH flags in the FLAGS column are re-derived, and any cached trade plans (priced at the old stake) are dropped and refetched for the selected coin | `stake $0.25 applied` or red `stake must be a number > 0, got '0'` |
| **Flag threshold** entry + **Apply** | 0–100. Enter or **Apply**. Controls which scanned rows are logged as FLAGGED (score ≥ threshold); it does not hide anything from the table | `flag threshold 30 applied` or a red range error |
| **Coins** spinbox | Arrows, or type and press **Enter** / leave the field. 10–581. Sets the universe budget **and** how many coins each scan scores — takes effect on the next scan | `coins 60 applied (universe budget + scan size)` |
| **Interval s** spinbox | 15–3600. Auto-scan period; live immediately for the scheduler | `auto-scan interval 300s applied` |
| **Dir** combo | Read-only list: `Both` / `Long` / `Short`. Filters the table instantly — no rescan, no network | — |
| **Sort** combo | Read-only list: `score`, `early`, `lean`, `move`, `vol` — all descending, identical semantics to the TUI/headless sort keys: highest score, highest earlyness, largest absolute lean, largest absolute 24h move, highest 24h quote volume | — |
| **Scan now** | Queue one scan immediately (works even while paused) | scan status, e.g. `scanned 150 in 34s · universe 150 · failed 0/150` |
| **Pause auto-scan / Resume auto-scan** | Toggles the scheduler. The button label always shows the action you can take next | `auto-scan paused` / `auto-scan resumed (60s)` |
| **Collect bars** | Downloads 5m bars for the **full** universe into `data/bars/*.csv.gz` (deduplicated). Feeds the 7-day outcome horizon, which cannot be resolved from REST alone | `collected 5m bars for N coins (+M new bars)` |
| **Resolve outcomes** | Resolves pending signal outcomes (REST bars for 1h/4h/24h, collector bars for 7d) and refreshes the Outcomes panel | `resolved N pending outcome(s)` |
| **Refresh stats** | Re-reads DB statistics and the outcome summary into the header and Outcomes panel | — |
| **★ Top 10** | Opens the picks modal: current scan's top-10 actionable coins (above threshold, fits stake) — the trade-now list | `no picks yet — run a scan first` if empty |
| **👁 Watch** | Opens the watch modal: best coins blocked only by stake (min notional > stake, or unknown) — tradable as stake compounds | `watch list empty` if none blocked |
| **+ New** | Opens the new-listings modal: coins first logged within 7 days, by latest score | `no new listings in the last 7 days` if none |

Double-click (or Enter) a modal row to jump it into the main view, which
fetches its trade plan. The three lists refresh with every scan and live
in the toolbar (not the crowded first page) as modal buttons by design.

Notes:

* Only one job of each kind runs at a time; clicking again shows
  `scan already in progress` (or `collect already in progress`, …) instead
  of stacking work.
* All long work (scans, plan fetches, collection, resolution, SQLite) runs
  on a single background thread — the window never freezes.
* While a scan is running the status bar shows `activity: scanning`
  (likewise `collecting`, `resolving`, `refreshing stats`, `idle`).

## 6. The Signals table

Columns, left to right:

| Column | Content |
|---|---|
| `#` | Rank after filter + sort |
| `DIR` | `▲` LONG · `▼` SHORT · `•` NEUTRAL |
| `COIN` | Base coin symbol (also the row's identity) |
| `PRICE` | Last price: 8 significant digits, fixed decimals for sub-1e-4 prices (no scientific notation), `n/a` if the venue sent garbage |
| `24h%` | 24h price change, signed (`+3.2%`) |
| `VOL24` | 24h quote volume: `$4.3M` ≥ 1M · `$600K` ≥ 1K · `$80` below |
| `FUND%` | Funding rate in percent, signed, 4 decimals |
| `OIΔ%` | Open-interest change, signed, or `n/a` when the venue doesn't provide it |
| `LEAN` | Directional lean, −1.00 … +1.00 (5m-driven) |
| `EARLY` | Earlyness 0.00 … 1.00 — how early the move is |
| `SCORE` | Signal quality 0.0 … 100.0 |
| `FLAGS` | `WATCH` / `UNVALIDATED` (see §7), space-separated; empty when clean |

Row colours: **LONG** rows light green, **SHORT** rows light red, rows with
a `WATCH` flag render their text in orange-red, and vetoed rows are grey
(their own section). Rows are clickable; only ranked (non-vetoed) coins
appear here.

The table shows every scored coin of the last scan — the flag threshold
only affects what gets *logged*, never what you see.

## 7. Flags glossary

| Flag | Meaning |
|---|---|
| **WATCH** | The venue's minimum order notional is **above your stake** (`min_notional > stake`): at this stake the trade cannot be placed on that venue. The row still ranks and logs — raise the stake (or wait for the venue) until it fits. Shown exactly at the boundary only when strictly greater: `min_notional == stake` is not a watch. |
| **UNVALIDATED** | Tier-2 marker: this score has **no outcome history** yet — either the row is on MEXC (the venue this scoring version started tracking) or it carries the Tier-2 note. Treat flagged rows of this kind as experimental until the outcomes panel shows resolved history. |

Related detail-pane lines (shown when you select a row):

* `⚠ min notional $5.0000 > stake $0.10 — WATCH only until stake grows`
* `min notional $0.0500 ≤ stake $0.10 — fits stake`
* `⚠ minimum notional unknown — fails closed, this coin never flags at any stake`
  (unknown min-notional can never be flagged as actionable, at any stake)

**Flag rule (what gets logged as FLAGGED):** a row is logged flagged when it
is actionable (directional, no vetoes, tradeable) **and** its known
min-notional fits the stake **and** `score ≥ flag threshold`. Everything
else — vetoed, sub-threshold, unknown min-notional — is still written as a
*shadow row*, because a log containing only winners has no denominator.

## 8. Detail pane walkthrough

Click any row (in Signals or in VETOED) to populate **Detail — selected
coin (review only, no orders)**. Selections are mutually exclusive: picking
a row in one list clears the other. Before any selection the pane reads
`select a row for the full breakdown`.

Top to bottom, a detail view contains:

1. **Header** — `AAA  ▲ LONG   score 70.0   lean +0.50   earlyness 0.50`
2. **Venue line** — `venue MEXC · tier 2 · READ-ONLY — this app places no orders`
3. **SCORE COMPONENTS** — VOL / BOOK / OI magnitudes (as %) and their leans —
   the three ingredients of the score.
4. **Metrics line** — `earlyness · 24h · vol24 · spread · funding · OIΔ`.
   If OI is unavailable: `⚠ OI unavailable on MEXC — OI/FUNDING signal is
   running on funding alone`.
5. **Min-notional verdict** — the three WATCH/fits/fails-closed lines from §7.
6. **Tier-2 warning** — `⚠ TIER 2 · UNVALIDATED …` for unvalidated rows.
7. **Last error** — `⚠ last error: <reason>` if that coin failed its most
   recent per-coin fetch (the scan still succeeded overall).
8. **VETOES** — `⨯ <code>: <reason>` per hard disqualifier (see §9).
9. **WARNINGS** — counter-trend notes such as
   `⚠ counter-trend: 5m LONG vs 4H SHORT — elevated risk` (a label only,
   never a score penalty).
10. **TRADE PLAN (review only — this app places no orders)** — the full
    plan: direction, entry band (limit, inside spread), stop, TP1/TP2 with
    R-multiples, leverage (with the operator band), position size/margin,
    max loss, costs, break-even, reward:risk, funding note, and any plan
    warnings.

Plan states you may see:

| Text | Meaning |
|---|---|
| `no plan available (fetching…)` | Plan request is queued or in flight |
| `no plan: <error>` | Fetch/plan failed — the reason is shown verbatim; selecting again later retries |
| `<COIN> is no longer in the last scan` | The coin dropped out of the table before the detail rendered |

Plans are cached per coin and fetched in the background — the window stays
responsive. A new scan or a stake change clears the cache (plans embed the
stake), and the selected row is refetched automatically.

## 9. Vetoed section & veto codes

Under the table: **`VETOED — excluded from ranking, shadow-logged`** with
columns **DIR · COIN · SCORE · VETO CODES** (codes comma-separated, e.g.
`late_move,thin_book`). Vetoed coins never appear in the ranked table, but
they are still scored, visible here, and written to the log as shadow rows.
Selecting a vetoed row opens its full detail, including the veto reasons.

Veto codes produced by the scorer:

| Code | Meaning |
|---|---|
| `low_volume` | 24h quote volume below the liquidity floor |
| `wide_spread` | Spread too wide to enter/exit realistically |
| `late_move` | Already moved ≥ the late-move threshold in 1h (or vertically in 24h) — chasing, not an entry |
| `thin_history` | Fewer than 30 bars of history — insufficient warm-up |

## 10. Outcomes panel & hit rate

The **Outcomes** panel (bottom-right) summarizes the outcome log:

```
1h 41 · 4h 28 · 24h 19 · 7d 8   (total 96 resolved)
LONG: n=64 · avg signed return +1.42% · hit rate 58%
SHORT: n=32 · avg signed return -0.31% · hit rate 47%
```

* **Per-horizon counts** — resolved rows per horizon. Horizons are fixed:
  `1h` = 12 · `4h` = 48 · `24h` = 288 · `7d` = 2016 five-minute bars.
  A horizon only appears once enough forward bars exist — never
  zero-filled early.
* **hit rate** — the share of *resolved* rows for that direction whose
  signed return was positive, i.e. the move went in the signal's favour.
  Returns are signed by direction, so a SHORT profits when price falls.
  Displayed 0–100%.
* **avg signed return** — mean signed return (%) of those resolved rows.
* A direction with no resolved rows shows `no resolved outcomes yet`.
* `Resolve now` runs the resolver without waiting for anything else.
* **24h flagged line** — `24h flagged: N signals · R resolved · won W /
  lost L · P% won · avg ±X%`: the rolling hit-rate over flagged signals
  logged in the last 24 hours. This is the number to watch through the
  data-collection week.

Bars come from REST for the short horizons and from the collector's
`data/bars/*.csv.gz` files for `7d` (REST caps at 2000 bars, short of the
2016 needed) — that is why **Collect bars** matters for 7d coverage.

### The 3–7 day review: daily top-20

The week's analysis focuses on **daily top-20 performance**:

```bash
python3 -m proto.report --top20 --days 7
```

Each UTC day contributes its top-20 flagged signals by score, with
per-horizon resolved/won/% for whatever has matured so far. Read it as:
which days' picks held up at 4h/24h, and whether any score band or
direction separates from coin-flip after costs. Unresolved horizons show
0 (pending) — a young day's 24h column fills in the next day's run.

## 11. Status bar & messages

Three slots, left to right:

| Slot | Content |
|---|---|
| `activity: …` | `idle` · `scanning` · `collecting` · `resolving` · `refreshing stats` |
| `failed x/y of last scan` | Per-coin fetch failures / attempted rows of the last scan |
| message | The last status or error. Errors render **red** and take priority; otherwise the latest scan/apply/resolve status |

Frequently seen messages:

| Message (colour) | Meaning |
|---|---|
| `ready` | Background worker initialised, DB open |
| `scanned N in Xs · universe U · failed f/r …` | Successful scan (appends `· universe ERROR …` / `· ⚠ MEXC DEGRADED` when relevant) |
| `scan failed — keeping previous table (<reason>)` + red `scan failed: <reason>` | Scan failed; the previous table is intact |
| red `scan failed: universe unavailable (…) — keeping previous table` | Universe refresh failed and there was nothing new to score — old table kept |
| red `universe refresh failed: … (scoring the cached universe)` | Refresh failed but cached coins were still scored |
| red `<field> must be …` | Input rejected; the field shows its previous value |
| `<cmd> already in progress` | That job is still running |
| `ready failed: …` (red) | The worker could not open the database — see §12 |
| `internal error: …` (red) | A malformed internal message was swallowed — the app keeps running |

## 12. Troubleshooting

**App won't start: `_tkinter.TclError: no display name and no $DISPLAY environment variable`**
You are on a machine without a display. Options: run it on a desktop
session; `ssh -X` into the machine; on WSL use WSLg (or set
`DISPLAY=:0`); on Wayland ensure XWayland is available. To confirm a
display exists: `echo $DISPLAY` and `xdpyinfo`. If you only need the data,
use the headless scanner instead (§13) — it needs no display.

**`ModuleNotFoundError: No module named 'tkinter'`**
Install the Tk bindings for your interpreter (§1).

**Everything is red / nothing scans: no network**
The status bar shows `scan failed — keeping previous table (…)`, and the
header may read `MEXC DEGRADED · Bybit DEGRADED`. The app never wipes a
good table on a failed scan — the rows you still see are the last good
ones. Fix connectivity and press **Scan now**; venue health returns to
`ok` on the next successful fetch.

**Table is empty**
Work through, in order:

1. Header `universe 0` → the universe refresh never succeeded; check the
   red status message and network.
2. Status `scanned 0 in …` → no coin passed scoring for this universe.
3. Header `shown 0` but `vetoed N` → everything was vetoed; check the
   VETOED section and its codes (§9).
4. `Dir` set to `Long`/`Short` → switch back to `Both`.

**Detail pane says `no plan: …`**
The plan fetch (network) failed or the coin left the scan. Re-select the
row or scan again; old plans are never shown as if they were fresh.

**My input keeps snapping back**
The field's value was out of bounds — stake > 0, threshold 0–100, coins
10–581, interval ≥ 15s — and the red status line shows the exact rule.

**`ready failed: …` at startup**
The background worker could not open `--db` (permissions, corrupt file,
locked by another process). Point `--db` at a writable path, or move the
existing file aside.

**Buttons seem dead**
Long jobs run in the background: watch `activity:` in the status bar and
`… already in progress` if you clicked twice. The UI never blocks on the
network.

**Two instances at once**
Both open the same SQLite file (WAL mode, so reads never block), but two
*scanning* instances double the log rows. Run one scanner; a second
instance is fine for viewing (`--no-auto-scan`).

## 13. Scheduling: collector, resolver & headless scans (daemon)

The GUI collects and resolves on demand (**Collect bars** /
**Resolve outcomes**) and scans on its own interval *while it is open*.
For unattended operation (overnight, or while the GUI is closed), use the
daemon — one process running the full loop against the same database
(`data/signals.db` by default):

```bash
# full loop: scan 60s + collect 5m + resolve hourly (default cadence)
python3 -m proto.daemon

# alongside an open GUI: collector + resolver only (no double-logged scans)
python3 -m proto.daemon --no-scan

# flags: --stake --coins --interval --db --log-threshold --collect-every
#        --resolve-every (seconds) --iterations N --no-scan
```

A systemd user unit is shipped for this host (`~/.config/systemd/user/
altradar.service`, `--no-scan` mode):

```bash
systemctl --user daemon-reload
systemctl --user enable --now altradar.service   # start now + on login
systemctl --user status altradar.service
journalctl --user -u altradar.service -f          # follow its log
```

(The resolver example that used to live here as a cron one-liner is
superseded by the daemon; the cron lines below remain valid alternatives
where no systemd user session exists.)

```cron
# scan + log once per minute (headless, no display needed)
* * * * * cd /path/to/alt-radar && /usr/bin/python3 -m proto.app --headless --iterations 1 >> logs/scan.log 2>&1

# collector: 5m bars for the FULL universe every 5 minutes (7d horizon feed)
*/5 * * * * cd /path/to/alt-radar && /usr/bin/python3 -c "from proto import collector; c = collector.collect_full_universe(); print(len(c), 'coins')" >> logs/collector.log 2>&1

# resolver: resolve pending outcomes every 10 minutes (needs the venue symbol map)
*/10 * * * * cd /path/to/alt-radar && /usr/bin/python3 -c "
from types import SimpleNamespace
from proto.app import App
from proto import outcomes
app = App(SimpleNamespace(stake=0.10, coins=150, interval=60,
                          db='data/signals.db', log_threshold=24.0,
                          write_logs=True))
app.refresh_universe()
n = outcomes.resolve_pending(app.store, {coin: sym for sym, coin in app.uni})
print('resolved', n)
app.store.close()" >> logs/resolver.log 2>&1
```

Guidance:

* **Collector every 5 minutes** matches the bar interval the horizons are
  counted in; missing a slot only delays resolution, bars are appended and
  deduplicated by timestamp, so catch-up is automatic.
* **Resolver after the scanner** (or every 10 minutes): it can only resolve
  horizons for which enough forward bars exist, so more frequent runs just
  resolve rows a little earlier.
* The headless CLI honours the same flags as the GUI where they overlap
  (`--stake`, `--coins`, `--interval`, `--db`, `--log-threshold`);
  `--coins` there is the scan size, while in the GUI it is also the
  universe budget.
* Keep the GUI open alongside the daemon or cron if you like — the
  database is shared; just don't run two SCANNERS writing logs
  simultaneously (GUI auto-scan + daemon default mode both scan). Use
  daemon `--no-scan` mode next to an open GUI, or pause the GUI (p).

## 14. Keyboard shortcuts & mouse

| Input | Effect |
|---|---|
| `Ctrl+Q` | Quit |
| `Enter` | Commit the Stake / Flag-threshold entries and the Coins / Interval spinboxes |
| `Tab` / `Shift+Tab` | Move between controls (standard Tk navigation) |
| Leave a spinbox field | Also commits it (focus-out) |
| Click a row | Select it → detail pane updates; clicking in the other list moves the selection |
| Mouse wheel over a list or the detail pane | Scroll it |

There are no other hidden shortcuts; every action is also a visible button.

## 14b. Instant launch: the close-time snapshot

Closing the app (window close button, `Ctrl+Q`, TUI `q`/`Esc`, headless
end/`Ctrl+C`) writes exactly what was on screen — cards plus any fetched
trade plans — to `.last_entries` next to the database. The next launch
renders that file immediately (no API wait) and the first live scan
replaces it wholesale. Cached plans show instantly on click and refresh
quietly underneath.

Order: close-file first, then the DB's latest scan cycle, then blank.
Stale files (over 7 days), corrupt files, and version mismatches are
ignored silently. The TUI saves on `q`/`Esc`; the GUI on close/`Ctrl+Q`.

## 15. FAQ

**Does it trade?** No. Read-only: no order code path, no API keys, no
funds — the ` READ-ONLY ` badge is permanent.

**Why is everything labelled TIER-2 UNVALIDATED?** The current scoring
version has no proven outcome history yet. The outcomes panel fills up as
the resolver runs; until then treat signals as experimental.

**Is the score different from the TUI?** No. The GUI imports the same
scorer, veto rules, sort keys, planner and flag rule as the TUI/headless
scanner — one code path, three front ends.

**What exactly is a "flagged" row?** See §7. In short: actionable (no
vetoes, directional, fits your stake) **and** score ≥ flag threshold.
Everything else is still shadow-logged so the record keeps a denominator.

**Why does the header say `universe 150` but show 12 rows?** Each scan
scores the top N coins by volume (`Coins`), the direction filter and the
veto split then reduce what's displayed — `shown` counts ranked rows only,
`vetoed` counts the rest.

**Where does my data live?**

| Path | Contents |
|---|---|
| `data/signals.db` | Signal log (ranked + shadow rows) and outcome log (WAL mode) |
| `data/bars/<COIN>.csv.gz` | Collector 5m bars (feeds the 7d horizon) |
| `logs/` | Log files from cron examples above |

**How often should I scan?** The default 60s suits a watched desktop; for
backgrounded use 300s is friendlier to the venues. Scans are staggered and
rate-limited against the venue (12 workers, venue-wide request limiter)
rather than fired as one burst.

**Can I trust the table after a failed scan?** Yes — a failed scan keeps
the previous table on screen (the status bar says so in red) rather than
clearing it.
