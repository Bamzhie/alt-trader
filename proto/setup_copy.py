"""Rule-evidence copy for the trader dashboard (Task 4).

Pure stdlib, no I/O. Every string produced here cites only the
triggering rule, direction, passed criteria, supporting inputs, and
input availability/staleness. This module never computes or displays
model outputs, tiers, or probability-style claims.

Downstream UI tests (Tasks 5-7) run :func:`assert_clean` over every
rendered text path; all copy below is written to pass that guard.
"""

__all__ = ["BANNED_WORDS", "assert_clean", "summarize_setup",
           "blocking_reason", "badge_letters"]

BANNED_WORDS = ("vwap", "high probability", "high conviction",
                "confidence", "probability", "confirmed profit",
                "fill", "executed", "leverage")


def assert_clean(text):
    """Raise ValueError if *text* contains any banned wording.

    Matching is case-insensitive substring matching against
    :data:`BANNED_WORDS`. Returns True when the text is clean so it
    can be used inline in tests.
    """
    lowered = str(text).lower()
    for word in BANNED_WORDS:
        if word.lower() in lowered:
            raise ValueError(f"banned wording in copy: {word!r}")
    return True


# Keys in the ``inputs`` mapping that describe staleness rather than an
# indicator reading. They are rendered as a "data as of" stamp.
_STALENESS_KEYS = ("data_as_of", "as_of", "last_bar_ts", "last_eval_ts")


def _fmt_value(value):
    if isinstance(value, float):
        return f"{value:g}"
    return str(value)


def summarize_setup(setup, inputs):
    """Return 2-line rule-evidence copy for an active watcher setup.

    ``setup`` is a watcher_setup row as a dict (keys ``rule_id``,
    ``direction``, ``criteria``/``passed``/``matched``,
    ``confirmations``). ``inputs`` maps computed indicator labels to
    values (``None`` = unavailable); staleness stamps use the
    ``data_as_of``/``as_of``/``last_bar_ts``/``last_eval_ts`` keys.

    Line 1 cites rule + direction + passed criteria. Line 2 lists the
    available computed inputs plus staleness, or shorter honest copy
    naming what is unavailable when inputs are missing.
    """
    setup = dict(setup or {})
    inputs = dict(inputs or {})
    rule_id = str(setup.get("rule_id", "unknown rule"))
    direction = str(setup.get("direction", "")).upper()
    criteria = (setup.get("criteria") or setup.get("passed")
                or setup.get("matched") or [])
    confirmations = setup.get("confirmations") or []

    head = f"{direction} setup via {rule_id}" if direction \
        else f"Setup via {rule_id}"
    if criteria:
        line1 = f"{head}: " + "; ".join(str(c) for c in criteria)
    else:
        line1 = f"{head}: rule criteria recorded at emission"
    if confirmations:
        line1 += " | supporting: " + "; ".join(str(c) for c in confirmations)

    readings = {k: v for k, v in inputs.items()
                if k not in _STALENESS_KEYS}
    stamp = None
    for key in _STALENESS_KEYS:
        if inputs.get(key) is not None:
            stamp = inputs[key]
            break
    if inputs.get("stale"):
        stamp = None

    available = [f"{k} {_fmt_value(v)}" for k, v in readings.items()
                 if v is not None]
    missing = [str(k) for k, v in readings.items() if v is None]

    parts = list(available)
    if missing:
        if available:
            parts.append(f"{', '.join(missing)} unavailable")
        else:
            line2 = f"{', '.join(missing)} unavailable; criteria only."
            assert_clean(line1)
            assert_clean(line2)
            return line1, line2
    if not parts:
        line2 = "Inputs unavailable; rule criteria only."
        assert_clean(line1)
        assert_clean(line2)
        return line1, line2
    if stamp is not None:
        parts.append(f"data as of {stamp}")
    elif inputs.get("stale"):
        parts.append("stale feed; treated as unavailable")
    line2 = "; ".join(parts)
    assert_clean(line1)
    assert_clean(line2)
    return line1, line2


def blocking_reason(coin, ctx):
    """Return the "no setup yet" reason line for *coin*.

    ``ctx`` keys (all optional): ``veto`` (rule-veto label),
    ``cooldown_until`` + ``now`` (epoch seconds), ``missing`` (list of
    unavailable input labels), ``invalidated_ts``, ``last_eval_ts``.
    Priority: veto > active cooldown > missing data > recently
    invalidated > default. The last-evaluation stamp is always
    included when known.
    """
    ctx = dict(ctx or {})
    coin = str(coin)
    last_eval = ctx.get("last_eval_ts")
    tail = (f"last evaluated {last_eval}."
            if last_eval is not None
            else "last evaluation time unavailable.")

    veto = ctx.get("veto")
    if veto:
        return f"No setup yet \u2014 {coin}: rule veto ({veto}); {tail}"

    cooldown_until = ctx.get("cooldown_until")
    now = ctx.get("now")
    if (cooldown_until is not None and now is not None
            and now < cooldown_until):
        return (f"No setup yet \u2014 {coin}: cooling down after a recent "
                f"emission (cooldown until {cooldown_until}); {tail}")

    missing = ctx.get("missing") or []
    if missing:
        names = ", ".join(str(m) for m in missing)
        return (f"No setup yet \u2014 {coin}: input data unavailable "
                f"({names}); {tail}")

    invalidated_ts = ctx.get("invalidated_ts")
    if invalidated_ts is not None:
        return (f"No setup yet \u2014 {coin}: most recent setup "
                f"invalidated at {invalidated_ts}; {tail}")

    return f"No setup yet \u2014 {coin}: no rule has triggered; {tail}"


def badge_letters(coin):
    """Return 1-2 uppercase letters for a drawn disc badge."""
    letters = "".join(ch for ch in str(coin).upper() if ch.isalpha())
    if not letters:
        return "?"
    return letters[:2]
