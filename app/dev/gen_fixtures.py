"""Deterministic fixture generator for app/dev/fixtures.json.

Not imported at runtime — it just writes the JSON the mock service serves, so
the fixture file can be regenerated without hand-editing hundreds of candles.

Run:  python3 app/dev/gen_fixtures.py
"""

import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "fixtures.json")

# Fixed clock so every fixture is reproducible: 2026-10-14T00:00:00Z.
DATA_AS_OF = 1791936000
NOW = DATA_AS_OF
H24 = DATA_AS_OF - 86400
H48 = DATA_AS_OF - 2 * 86400
CUTOFF_24H = DATA_AS_OF - 86400  # the comparison point: nearest snapshot at or before this

TF_SECS = {"5m": 300, "15m": 900, "1H": 3600, "4H": 14400, "1D": 86400}
SUPPORTED_TF = ["5m", "15m", "1H", "4H", "1D"]


def rnd(i, salt=0):
    """Deterministic pseudo-noise in [-1, 1] — no random module, no seeding."""
    x = math.sin((i + 1) * 12.9898 + salt * 78.233) * 43758.5453
    return 2.0 * (x - math.floor(x)) - 1.0


def bars(coin, tf, count, end_price, drift, vol, salt=0):
    """Closed bars ending at the last complete boundary at or before DATA_AS_OF.

    Closes are walked BACKWARD from `end_price` so the newest closed bar lands
    exactly on the fixture price the snapshot/indicators quote — no drift gap
    between "price" in the table and the last candle's close.
    """
    step = TF_SECS[tf]
    last_close_ts = (DATA_AS_OF // step) * step
    # A bar with ts == DATA_AS_OF is still forming; closed bars end strictly before.
    if last_close_ts == DATA_AS_OF:
        last_close_ts -= step
    dp = _dp(coin)
    closes = [0.0] * count
    closes[count - 1] = end_price
    for idx in range(count - 2, -1, -1):
        wave = math.sin((idx + 1) / 6.0 + salt) * vol * 0.55 + rnd(idx, salt) * vol * 0.45
        closes[idx] = round(closes[idx + 1] / (1.0 + drift + wave), dp)
    out = []
    for i in range(count):
        ts = last_close_ts - (count - 1 - i) * step
        open_ = closes[i - 1] if i > 0 else round(closes[0] / (1.0 + drift), dp)
        close = closes[i]
        high = round(max(open_, close) * (1.0 + abs(rnd(i, salt + 1)) * vol * 0.30), dp)
        low = round(min(open_, close) * (1.0 - abs(rnd(i, salt + 2)) * vol * 0.30), dp)
        volume = round(1200.0 + abs(rnd(i, salt + 3)) * 900.0, 2)
        out.append(
            {
                "ts": ts,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "closed": True,
            }
        )
    return out


def _dp(coin):
    return 4 if coin in ("DOGE", "LINK") else (2 if coin in ("SOL", "AVAX") else 1)


COINS = {
    "BTC": {"name": "Bitcoin", "px": 112480.5, "chg": 2.41},
    "ETH": {"name": "Ethereum", "px": 4118.72, "chg": -1.18},
    "SOL": {"name": "Solana", "px": 214.36, "chg": 4.87},
    "DOGE": {"name": "Dogecoin", "px": 0.2481, "chg": -3.62},
    "AVAX": {"name": "Avalanche", "px": 38.94, "chg": 1.05},
    "LINK": {"name": "Chainlink", "px": 22.87, "chg": -0.74},
}

# The last closed 5m close per coin, used to keep indicators/levels coherent.
LAST_5M = {
    "BTC": 112505.0,
    "ETH": 4092.40,
    "SOL": 214.55,
    "DOGE": 0.2472,
    "AVAX": 39.02,
    "LINK": 22.79,
}

SETUPS = [
    {
        "id": "setup-btc-1",
        "coin": "BTC",
        "direction": "LONG",
        "horizon": "4h",
        "state": "active",
        "emitted_ts": NOW - 3600,
        "expires_ts": NOW + 3 * 3600,
        "price": LAST_5M["BTC"],
        "change_24h_pct": 2.41,
        "rule": "range_reclaim_long",
        "criteria": [
            {"name": "close reclaims 4H range high", "passed": True},
            {"name": "EMA50 above EMA200", "passed": True},
            {"name": "volume above 20-bar median", "passed": True},
            {"name": "higher-low structure intact", "passed": True},
        ],
        "inputs": {"rsi": 57.4, "macd": 0.0211, "ema50": 110980.2, "ema200": 108214.7,
                   "volume_pct": 138.5},
        "unavailable_inputs": [],
        "analysis": [
            "BTC reclaimed the 4H range high on the 20:00 UTC close, holding above the "
            "prior session's high for two consecutive bars.",
            "EMA50 sits above EMA200 and volume ran 38.5% over the 20-bar median, so the "
            "structure is expanding rather than reversing.",
        ],
    },
    {
        "id": "setup-eth-1",
        "coin": "ETH",
        "direction": "SHORT",
        "horizon": "4h",
        "state": "active",
        "emitted_ts": NOW - 5400,
        "expires_ts": NOW + 2 * 3600 + 1800,
        "price": LAST_5M["ETH"],
        "change_24h_pct": -1.18,
        "rule": "range_reject_short",
        "criteria": [
            {"name": "close rejects 4H range low", "passed": True},
            {"name": "EMA50 below EMA200", "passed": True},
            {"name": "lower-high sequence intact", "passed": True},
            {"name": "volume above 20-bar median", "passed": False},
        ],
        "inputs": {"rsi": 42.8, "macd": -0.0142, "ema50": 3988.10, "ema200": 4102.55,
                   "volume_pct": 91.2},
        "unavailable_inputs": [],
        "analysis": [
            "ETH rejected the 4H range low twice inside the last three bars and printed a "
            "lower high on the 19:00 UTC close.",
            "EMA50 remains below EMA200, but volume came in under the 20-bar median, so the "
            "expansion is unconfirmed.",
        ],
    },
    {
        "id": "setup-sol-1",
        "coin": "SOL",
        "direction": "LONG",
        "horizon": "4h",
        "state": "active",
        "emitted_ts": NOW - 9000,
        "expires_ts": NOW + 3600,
        "price": LAST_5M["SOL"],
        "change_24h_pct": 4.87,
        "rule": "breakout_continuation_long",
        "criteria": [
            {"name": "close above prior 4H swing high", "passed": True},
            {"name": "EMA50 above EMA200", "passed": True},
            {"name": "volume above 20-bar median", "passed": True},
        ],
        "inputs": {"rsi": 64.1, "macd": 0.0087, "ema50": 205.44, "ema200": 197.81,
                   "volume_pct": 171.9},
        "unavailable_inputs": [],
        "analysis": [
            "SOL closed above its prior 4H swing high, extending the continuation it started "
            "six hours earlier.",
            "Volume ran 71.9% over the 20-bar median, the strongest expansion on the board.",
        ],
    },
    {
        "id": "setup-doge-1",
        "coin": "DOGE",
        "direction": "SHORT",
        "horizon": "24h",
        "state": "active",
        "emitted_ts": NOW - 14400,
        "expires_ts": NOW + 6 * 3600,
        "price": LAST_5M["DOGE"],
        "change_24h_pct": -3.62,
        "rule": "range_reject_short",
        "criteria": [
            {"name": "close rejects 4H range low", "passed": True},
            {"name": "lower-high sequence intact", "passed": True},
            {"name": "volume above 20-bar median", "passed": True},
        ],
        "inputs": {"rsi": 38.5, "macd": -0.0003, "ema50": 0.2511, "ema200": 0.2498,
                   "volume_pct": 122.4},
        "unavailable_inputs": [],
        "analysis": [
            "DOGE rejected the 4H range low and rolled into a lower high on the daily "
            "horizon.",
            "Volume is 22.4% over the 20-bar median while RSI holds under 40.",
        ],
    },
    {
        "id": "setup-avax-1",
        "coin": "AVAX",
        "direction": "LONG",
        "horizon": "4h",
        "state": "active",
        "emitted_ts": NOW - 7200,
        "expires_ts": NOW + 4 * 3600,
        "price": LAST_5M["AVAX"],
        "change_24h_pct": 1.05,
        "rule": "range_reclaim_long",
        "criteria": [
            {"name": "close reclaims 4H range high", "passed": True},
            {"name": "EMA50 above EMA200", "passed": True},
            {"name": "volume above 20-bar median", "passed": False},
        ],
        "inputs": {"rsi": None, "macd": 0.0009, "ema50": 38.31, "ema200": 37.92,
                   "volume_pct": 88.6},
        "unavailable_inputs": ["rsi"],
        "analysis": [
            "AVAX reclaimed the 4H range high, but the RSI input did not arrive for this "
            "sweep, so the momentum criterion is unconfirmed.",
            "Volume also came in under the 20-bar median — the setup is on structure alone.",
        ],
    },
    {
        "id": "setup-link-1",
        "coin": "LINK",
        "direction": "SHORT",
        "horizon": "4h",
        "state": "active",
        "emitted_ts": NOW - 1800,
        "expires_ts": NOW + 5 * 3600,
        "price": LAST_5M["LINK"],
        "change_24h_pct": -0.74,
        "rule": "range_reject_short",
        "criteria": [
            {"name": "close rejects 4H range low", "passed": True},
            {"name": "EMA50 below EMA200", "passed": True},
            {"name": "lower-high sequence intact", "passed": True},
            {"name": "volume above 20-bar median", "passed": True},
        ],
        "inputs": {"rsi": 45.2, "macd": -0.0011, "ema50": 22.94, "ema200": 23.11,
                   "volume_pct": 134.0},
        "unavailable_inputs": [],
        "analysis": [
            "LINK rejected the 4H range low with EMA50 below EMA200 on the newest bar.",
            "Volume is 34.0% over the 20-bar median, so the rejection has participation.",
        ],
    },
]


def tradeplans():
    plans = []
    for s in SETUPS:
        coin = s["coin"]
        px = LAST_5M[coin]
        dp = _dp(coin)
        atr = round(px * 0.0092, dp)
        if s["direction"] == "LONG":
            entry = (round(px * 0.9975, dp), round(px * 1.0015, dp))
            stop = round(entry[0] - atr, dp)
            tp1 = round(entry[1] + 2 * atr, dp)
            tp2 = round(entry[1] + 5 * atr, dp)
        else:
            entry = (round(px * 0.9985, dp), round(px * 1.0025, dp))
            stop = round(entry[1] + atr, dp)
            tp1 = round(entry[0] - 2 * atr, dp)
            tp2 = round(entry[0] - 5 * atr, dp)
        plans.append(
            {
                "id": "tp-" + coin.lower() + "-1",
                "setup_id": s["id"],
                "coin": coin,
                "name": COINS[coin]["name"],
                "direction": s["direction"],
                "horizon": s["horizon"],
                "entry_zone": {"low": entry[0], "high": entry[1]},
                "stop": stop,
                "tp1": tp1,
                "tp2": tp2,
                "reference_notional_usdt": 100,
                "reference_stop_risk_usdt": round(
                    abs(entry[0] - stop) / entry[0] * 100.0, 2
                ),
                "reference_size_note": "100 USDT reference notional — reference size, not a position",
                "indicators": [
                    {"name": "RSI 14", "value": s["inputs"]["rsi"]},
                    {"name": "MACD", "value": s["inputs"]["macd"]},
                    {"name": "EMA 50", "value": s["inputs"]["ema50"]},
                    {"name": "EMA 200", "value": s["inputs"]["ema200"]},
                    {"name": "Volume +%", "value": s["inputs"]["volume_pct"]},
                ],
                "valid_until_ts": s["expires_ts"],
                "next_update_ts": s["expires_ts"] + 3600,
                "analysis": s["analysis"],
                "data_as_of": DATA_AS_OF,
            }
        )
    return plans


def metrics():
    out = {}
    for s in SETUPS:
        out[s["coin"]] = {
            "coin": s["coin"],
            "ts": DATA_AS_OF,
            "rsi": s["inputs"]["rsi"],
            "macd": s["inputs"]["macd"],
            "ema50": s["inputs"]["ema50"],
            "ema200": s["inputs"]["ema200"],
            "volume_pct": s["inputs"]["volume_pct"],
            "unavailable": None if not s["unavailable_inputs"] else {
                "reason": "indicator input not returned by the last sweep",
                "missing": list(s["unavailable_inputs"]),
            },
            "data_as_of": DATA_AS_OF,
        }
    # A coin whose RSI input is missing — exercised by test_metrics_explicit_null.
    out["METRICS_NULL"] = {
        "coin": "METRICS_NULL",
        "ts": DATA_AS_OF,
        "rsi": None,
        "macd": None,
        "ema50": 41.20,
        "ema200": 40.88,
        "volume_pct": 96.3,
        "unavailable": {
            "reason": "indicator input not returned by the last sweep",
            "missing": ["rsi", "macd"],
        },
        "data_as_of": DATA_AS_OF,
    }
    return out


ACTIVITY = [
    {"ts": NOW - 600, "kind": "sweep-complete", "coin": None,
     "text": "Sweep complete — 132 of 150 eligible perps evaluated, 6 active setups."},
    {"ts": NOW - 1800, "kind": "emission", "coin": "LINK",
     "text": "LINK setup emitted — range rejection, 4H horizon."},
    {"ts": NOW - 2100, "kind": "invalidation", "coin": "ARB",
     "text": "ARB setup invalidated — close reclaimed the range high."},
    {"ts": NOW - 5400, "kind": "emission", "coin": "ETH",
     "text": "ETH setup emitted — range rejection, 4H horizon."},
    {"ts": NOW - 6600, "kind": "expiry", "coin": "OP",
     "text": "OP setup expired at horizon end with no resolution."},
    {"ts": NOW - 7200, "kind": "emission", "coin": "AVAX",
     "text": "AVAX setup emitted — range reclaim, 4H horizon."},
    {"ts": NOW - 9000, "kind": "emission", "coin": "SOL",
     "text": "SOL setup emitted — breakout continuation, 4H horizon."},
    {"ts": NOW - 14400, "kind": "emission", "coin": "DOGE",
     "text": "DOGE setup emitted — range rejection, 24H horizon."},
    {"ts": NOW - 36000, "kind": "monitoring", "coin": "ETH",
     "text": "Monitoring resumed for ETH — exchange feed answering again."},
    {"ts": NOW - 14400 - 3540, "kind": "emission", "coin": "BTC",
     "text": "BTC setup emitted — range reclaim, 4H horizon."},
]

SEED_EVENTS = [
    # All seed transitions sit inside the 30-minute ring window so the demo
    # stream survives startup (the prune is a true 30-minute window now).
    {"kind": "setup.emitted", "ts": NOW - 1740, "coin": "BTC"},
    {"kind": "setup.emitted", "ts": NOW - 1680, "coin": "DOGE"},
    {"kind": "setup.emitted", "ts": NOW - 1500, "coin": "SOL"},
    {"kind": "setup.emitted", "ts": NOW - 1320, "coin": "AVAX"},
    {"kind": "setup.emitted", "ts": NOW - 1140, "coin": "ETH"},
    {"kind": "setup.invalidated", "ts": NOW - 900, "coin": "ARB"},
    {"kind": "setup.expired", "ts": NOW - 840, "coin": "OP"},
    {"kind": "monitoring.unavailable", "ts": NOW - 720, "coin": "ETH"},
    {"kind": "monitoring.ok", "ts": NOW - 660, "coin": "ETH"},
    {"kind": "setup.emitted", "ts": NOW - 600, "coin": "LINK"},
    {"kind": "sweep.completed", "ts": NOW - 540, "coin": None},
]

EVENT_KINDS = [
    "setup.emitted",
    "setup.invalidated",
    "setup.expired",
    "setup.resolved",
    "monitoring.unavailable",
    "monitoring.ok",
    "sweep.completed",
]


def main():
    fixtures = {
        "meta": {
            "data_as_of": DATA_AS_OF,
            "generated_for": "app/dev mock service (dev-only fixtures, not live data)",
            "supported_timeframes": SUPPORTED_TF,
            "event_kinds": EVENT_KINDS,
        },
        "snapshots": [
            {
                "ts": NOW,
                "eligible_count": 150,
                "evaluated_count": 132,
                "active_setups": len(SETUPS),
                "monitoring_coverage": 0.984,
                "new_setups_24h": 6,
                "covers_24h": True,
            },
            # Deliberate: the two prior snapshots are INSIDE the last 24 hours,
            # so no row sits at or before now-24h. The 24h delta must render
            # "unavailable", never an inferred number.
            {
                "ts": NOW - 6 * 3600,
                "eligible_count": 149,
                "evaluated_count": 127,
                "active_setups": 5,
                "monitoring_coverage": 0.977,
                "new_setups_24h": 5,
                "covers_24h": False,
            },
            {
                "ts": NOW - 12 * 3600,
                "eligible_count": 148,
                "evaluated_count": 121,
                "active_setups": 4,
                "monitoring_coverage": 0.971,
                "new_setups_24h": 4,
                "covers_24h": False,
            },
        ],
        "setups": SETUPS,
        "coins": COINS,
        "market_overview": [
            {
                "coin": "BTC",
                "name": COINS["BTC"]["name"],
                "price": LAST_5M["BTC"],
                "change_24h_pct": COINS["BTC"]["chg"],
                "last_bar_ts": None,
            },
            {
                "coin": "ETH",
                "name": COINS["ETH"]["name"],
                "price": LAST_5M["ETH"],
                "change_24h_pct": COINS["ETH"]["chg"],
                "last_bar_ts": None,
            },
        ],
        "coins_without_setup": [
            {
                "coin": "XRP",
                "name": "XRP",
                "blocking_reason": "no setup yet — no rule criteria met on the last sweep",
            },
            {
                "coin": "ADA",
                "name": "Cardano",
                "blocking_reason": "no setup yet — 4H range not yet resolved",
            },
        ],
        "movers": [
            {"coin": "SOL", "name": "Solana", "change_24h_pct": 4.87},
            {"coin": "BTC", "name": "Bitcoin", "change_24h_pct": 2.41},
            {"coin": "AVAX", "name": "Avalanche", "change_24h_pct": 1.05},
            {"coin": "LINK", "name": "Chainlink", "change_24h_pct": -0.74},
            {"coin": "DOGE", "name": "Dogecoin", "change_24h_pct": -3.62},
        ],
        "recent_analysis": [
            {"ts": NOW - 1800, "coin": "LINK", "direction": "SHORT",
             "text": "LINK rejected the 4H range low with EMA50 below EMA200."},
            {"ts": NOW - 5400, "coin": "ETH", "direction": "SHORT",
             "text": "ETH rejected the 4H range low and printed a lower high."},
            {"ts": NOW - 7200, "coin": "AVAX", "direction": "LONG",
             "text": "AVAX reclaimed the 4H range high; RSI input unavailable."},
            {"ts": NOW - 9000, "coin": "SOL", "direction": "LONG",
             "text": "SOL closed above its prior 4H swing high."},
            {"ts": NOW - 14400, "coin": "DOGE", "direction": "SHORT",
             "text": "DOGE rolled into a lower high on the daily horizon."},
        ],
        "activity": sorted(ACTIVITY, key=lambda e: -e["ts"]),
        "seed_events": SEED_EVENTS,
        "metrics": metrics(),
        "tradeplans": tradeplans(),
        "bars": {},
    }

    # ~40 closed 4H bars for BTC, plus shorter series for every other tf so the
    # chart tabs and the sparkline all have real closed-bar data. Each series is
    # anchored at its own last closed bar: the 5m close matches the setup price,
    # longer timeframes sit near it (a real close-time price is tf-dependent).
    for coin, meta in COINS.items():
        end_price = LAST_5M[coin]
        drift = 0.0006 if meta["chg"] > 0 else -0.0004
        vol = 0.006 if coin == "BTC" else 0.011
        fixtures["bars"][coin] = {
            "5m": bars(coin, "5m", 48, end_price, drift, vol, 1),
            "15m": bars(coin, "15m", 48, end_price, drift, vol, 2),
            "1H": bars(coin, "1H", 48, end_price, drift, vol, 3),
            "4H": bars(coin, "4H", 40, end_price, drift, vol, 4),
            "1D": bars(coin, "1D", 30, end_price, drift, vol, 5),
        }

    with open(OUT, "w") as f:
        json.dump(fixtures, f, indent=1, sort_keys=False)
        f.write("\n")
    print(f"wrote {OUT} ({os.path.getsize(OUT)} bytes)")


if __name__ == "__main__":
    main()
