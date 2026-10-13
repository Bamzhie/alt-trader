"""Task 4: rule-evidence copy with banned-wording guard.

Offline only: no network, no I/O. Pins the Task 4 interface contract:

- BANNED_WORDS pins the exact banned tuple (case-insensitive guard).
- assert_clean(text) raises on any banned wording; clean copy passes.
- summarize_setup(setup, inputs) -> (line1, line2): rule criteria +
  computed inputs only; shorter honest copy when inputs missing.
- blocking_reason(coin, ctx) -> str: "no setup yet" reason (veto /
  cooldown / missing data / recently invalidated) + last-eval ts.
- badge_letters(coin) -> 1-2 letter disc-badge glyph.

Run: python3 tests/test_setup_copy.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


print("=== banned-words tuple pinned ===")
from proto.setup_copy import (  # noqa: E402
    BANNED_WORDS,
    assert_clean,
    badge_letters,
    blocking_reason,
    summarize_setup,
)

check("BANNED_WORDS exact tuple",
      BANNED_WORDS == ("vwap", "high probability", "high conviction",
                       "confidence", "probability", "confirmed profit",
                       "fill", "executed", "leverage"),
      str(BANNED_WORDS))


def assert_clean_raises(text):
    try:
        assert_clean(text)
    except ValueError:
        return True
    return False


# --------------------------------------------------------------------------
print("\n=== banned words rejected ===")


def test_banned_words_rejected():
    for w in ("VWAP support", "High conviction long", "88% confidence"):
        check(f"assert_clean raises on {w!r}", assert_clean_raises(w))


test_banned_words_rejected()

for w in ("probability of success", "CONFIRMED PROFIT here",
          "order fill expected", "trade executed", "use 5x leverage",
          "High Probability breakout", "vWaP line"):
    check(f"assert_clean raises on {w!r}", assert_clean_raises(w))

clean_ok = True
try:
    assert_clean("LONG setup via t1-break-v1: close above range high; "
                 "data as of 1234")
except ValueError:
    clean_ok = False
check("assert_clean passes honest copy", clean_ok)


# --------------------------------------------------------------------------
print("\n=== summarize_setup ===")
setup = {
    "rule_id": "t1-break-v1",
    "coin": "BTC",
    "direction": "LONG",
    "criteria": ["close above range high", "volume above median"],
    "confirmations": ["rsi above 50"],
}


def test_missing_inputs_shorten():
    line1, line2 = summarize_setup(setup, {"oi": None})
    check("missing inputs -> 'unavailable' in line2",
          "unavailable" in line2.lower(), repr(line2))
    check("line1 still cites rule", "t1-break-v1" in line1, repr(line1))


test_missing_inputs_shorten()

full1, full2 = summarize_setup(
    setup, {"rsi": 62.5, "oi": 123.0, "data_as_of": 1700000000})
check("line1 cites rule", "t1-break-v1" in full1, repr(full1))
check("line1 cites direction", "LONG" in full1, repr(full1))
check("line1 cites passed criteria", "range high" in full1, repr(full1))
check("line2 cites computed input", "62.5" in full2, repr(full2))
check("line2 cites staleness stamp", "1700000000" in str(full2),
      repr(full2))
short1, short2 = summarize_setup(setup, {"oi": None})
check("missing-inputs copy is shorter",
      len(short1) + len(short2) < len(full1) + len(full2),
      f"full={len(full1) + len(full2)} short={len(short1) + len(short2)}")
check("empty inputs also honest",
      "unavailable" in summarize_setup(setup, {})[1].lower())

for label, text in (("full line1", full1), ("full line2", full2),
                    ("short line1", short1), ("short line2", short2)):
    try:
        assert_clean(text)
        check(f"assert_clean passes {label}", True)
    except ValueError as e:
        check(f"assert_clean passes {label}", False, str(e))


# --------------------------------------------------------------------------
print("\n=== blocking_reason ===")
r_veto = blocking_reason("BTC", {"veto": "funding gate",
                                 "last_eval_ts": 1700000000})
check("veto reason cites veto", "funding gate" in r_veto, repr(r_veto))
check("veto reason has last-eval ts", "1700000000" in r_veto, repr(r_veto))
r_cd = blocking_reason("ETH", {"cooldown_until": 1800000000,
                               "now": 1700000000,
                               "last_eval_ts": 1700000000})
check("cooldown reason cites cooldown",
      "cooldown" in r_cd.lower(), repr(r_cd))
r_md = blocking_reason("SOL", {"missing": ["oi", "funding"],
                               "last_eval_ts": 1700000000})
check("missing-data reason cites missing inputs",
      "oi" in r_md and "unavailable" in r_md.lower(), repr(r_md))
r_inv = blocking_reason("DOGE", {"invalidated_ts": 1699999999,
                                 "last_eval_ts": 1700000000})
check("invalidation reason cites invalidation",
      "invalidated" in r_inv.lower(), repr(r_inv))
r_dflt = blocking_reason("XRP", {"last_eval_ts": 1700000000})
check("default reason carries last-eval ts",
      "1700000000" in r_dflt, repr(r_dflt))
for label, text in (("veto", r_veto), ("cooldown", r_cd),
                    ("missing", r_md), ("invalidated", r_inv),
                    ("default", r_dflt)):
    check(f"blocking_reason mentions no-setup-yet ({label})",
          "no setup yet" in text.lower(), repr(text))
    try:
        assert_clean(text)
        check(f"assert_clean passes blocking_reason ({label})", True)
    except ValueError as e:
        check(f"assert_clean passes blocking_reason ({label})", False,
              str(e))


# --------------------------------------------------------------------------
print("\n=== badge_letters ===")
check("BTC -> 2 uppercase letters",
      badge_letters("BTC") == "BT", repr(badge_letters("BTC")))
check("single-char coin -> 1 letter",
      badge_letters("X") == "X", repr(badge_letters("X")))
check("empty coin -> placeholder",
      len(badge_letters("")) == 1, repr(badge_letters("")))
check("lowercase normalized",
      badge_letters("eth") == "ET", repr(badge_letters("eth")))
check("badge letters are 1-2 chars",
      all(1 <= len(badge_letters(c)) <= 2
          for c in ("BTC", "ETHUSDT", "a", "")))

print("\n" + ("ALL PASS" if not FAILURES
              else f"{len(FAILURES)} FAILED: {FAILURES}"))
sys.exit(1 if FAILURES else 0)
