"""Deterministic, frozen universe sampling.

The universe file is the predeclared definition of the coin population. This
slice deliberately infers no eligibility from bars, liquidity, or any other
source: a coin is in the universe because the file says so, and a sample is
reproducible from (definition, seed, n, method) alone. That is what lets a
later run prove it sampled from a definition fixed before any result was seen.
"""

import hashlib
import json
import re
import time

from . import store

SAMPLE_METHOD = "sha256-rank-v1"

_COIN_RE = re.compile(r"[A-Z0-9]+")

class SamplingError(Exception):
    """Raised when a universe definition or sample request is invalid."""


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(obj):
    try:
        return json.dumps(
            obj, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except ValueError as exc:
        raise SamplingError(f"universe is not canonical JSON: {exc}") from exc


def _read_universe(universe_file):
    """Return the validated definition object from ``universe_file``."""
    try:
        with open(universe_file, encoding="utf-8") as fh:
            definition = json.load(fh)
    except (OSError, ValueError) as exc:
        raise SamplingError(f"cannot read universe file {universe_file}: {exc}") from exc
    if not isinstance(definition, dict):
        raise SamplingError("universe must be a JSON object")
    name = definition.get("name")
    if not isinstance(name, str) or not name:
        raise SamplingError("universe name must be a non-empty string")
    if "description" in definition and not isinstance(definition["description"], str):
        raise SamplingError("universe description must be a string")
    coins = definition.get("coins")
    if not isinstance(coins, list) or not coins:
        raise SamplingError("universe coins must be a non-empty list")
    for coin in coins:
        if not isinstance(coin, str) or not _COIN_RE.fullmatch(coin):
            raise SamplingError(f"invalid coin: {coin!r}")
    if len(set(coins)) != len(coins):
        raise SamplingError("universe coins must be unique")
    return definition


def _draw(coins, seed, n):
    """Rank coins by sha256(f"{seed}|{coin}") and take the first ``n``."""
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise SamplingError(f"n must be a positive integer: {n!r}")
    if n > len(coins):
        raise SamplingError(f"n={n} exceeds universe size {len(coins)}")
    ranked = sorted(coins, key=lambda coin: (_sha256(f"{seed}|{coin}"), coin))
    return ranked[:n]


def record_sample(db_path, universe_file, seed, n, *, label=None):
    """Record or replay one frozen sample; never redraws a stored sample."""
    definition = _read_universe(universe_file)
    canonical = _canonical(definition)
    definition_sha256 = _sha256(canonical)
    universe_id = "uni-" + definition_sha256[:20]
    seed = str(seed)
    coins = _draw(definition["coins"], seed, n)
    sample_id = "smp-" + _sha256(
        f"{universe_id}|{seed}|{n}|{SAMPLE_METHOD}")[:20]
    coins_json = json.dumps(coins, separators=(",", ":"))
    coins_sha256 = _sha256(coins_json)

    conn = store.connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT OR IGNORE INTO universe (universe_id, name, "
                "definition_json, definition_sha256, created_ts) "
                "VALUES (?,?,?,?,?)",
                (universe_id, definition["name"], canonical,
                 definition_sha256, int(time.time())))
            stored_definition = conn.execute(
                "SELECT definition_json, definition_sha256 FROM universe "
                "WHERE universe_id = ?", (universe_id,)).fetchone()
            if stored_definition != (canonical, definition_sha256):
                raise SamplingError(
                    f"universe id {universe_id} already maps to another definition")
            conn.execute(
                "INSERT OR IGNORE INTO sample (sample_id, universe_id, seed, n, "
                "method, label, coins_json, coins_sha256, created_ts) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (sample_id, universe_id, seed, n, SAMPLE_METHOD, label,
                 coins_json, coins_sha256, int(time.time())))
            stored_coins = conn.execute(
                "SELECT coins_json FROM sample WHERE sample_id = ?",
                (sample_id,)).fetchone()
            if stored_coins is None or stored_coins[0] != coins_json:
                raise SamplingError(
                    f"sample id {sample_id} already maps to different coins")
            conn.executemany(
                "INSERT OR IGNORE INTO sample_coin (sample_id, rank, coin) "
                "VALUES (?,?,?)",
                [(sample_id, rank, coin)
                 for rank, coin in enumerate(coins, 1)])
            stored_ranks = conn.execute(
                "SELECT rank, coin FROM sample_coin WHERE sample_id = ? "
                "ORDER BY rank", (sample_id,)).fetchall()
            if stored_ranks != list(enumerate(coins, 1)):
                raise SamplingError(
                    f"sample id {sample_id} already maps to different ranks")
    finally:
        conn.close()
    return load_sample(db_path, sample_id)


def load_sample(db_path, sample_id):
    """Read one frozen sample; opens the database read-only."""
    conn = store.connect(db_path, readonly=True)
    try:
        row = conn.execute(
            "SELECT sample_id, universe_id, seed, n, method, label, coins_json, "
            "coins_sha256 FROM sample WHERE sample_id = ?",
            (sample_id,)).fetchone()
        if row is None:
            raise SamplingError(f"unknown sample: {sample_id}")
        coins = [r[0] for r in conn.execute(
            "SELECT coin FROM sample_coin WHERE sample_id = ? ORDER BY rank",
            (sample_id,))]
    finally:
        conn.close()
    return {
        "sample_id": row[0],
        "universe_id": row[1],
        "seed": row[2],
        "n": row[3],
        "method": row[4],
        "coins": coins,
        "label": row[5],
    }
