"""
Episode-based measurement infrastructure.

Forward-only measurement of flagged opportunities with versioned rules,
transactional episode lifecycle, and all-eligible-coin cadence.

Does not modify scoring, vetoes, flags, or interactive ranking.
Legacy signal_log, plan_log, plan_outcome, outcome_log remain unchanged.

Episode lifecycle (Task 2):
- One episode per (coin, config_hash, direction) while OPEN/REARMING.
- QUALIFYING opens/refreshes; same-direction within re-arm stays one episode.
- Reversal (opposite direction QUALIFYING) closes and immediately restarts.
- NON_QUALIFYING accrues re-arm time; 60 observed minutes -> REARM_CONFIRMED.
- UNKNOWN never refreshes, never re-arms, links only to an open episode.
- Late observations (obs_ts <= cursor) never rewind the cursor or mutate state.
- A gap > max_gap_s closes an episode as COVERAGE_LOST (via sweep_coverage).
"""

import hashlib
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from enum import IntEnum
from typing import Optional, NamedTuple, Any

# --------------------------------------------------------------------------
# Version constants (all immutable rules that affect episode metrics)
# --------------------------------------------------------------------------

EPISODE_RULE_VERSION = "v2.0.0"   # re-arm 60m, max gap 15m, observation classes
PLAN_RULE_VERSION = "v1.0.0"      # entry validity 60m, stop-first resolution
OUTCOME_RULE_VERSION = "v2.0.0"   # adverse-edge fill, 6-day recovery, horizons
COST_MODEL_VERSION = "v0.1.0-unstable"  # FEE 0.05% taker/side, slippage unset

# Code revision stamped on rows written by the measurement path (spec §4).
# A scorer/planner change bumps this, which changes the cohort identity.
CODE_REV = "2026-10-09-episode-measurement"

# Default values
DEFAULT_REARM_MINUTES = 60
DEFAULT_ENTRY_VALIDITY_MINUTES = 60  # independent of re-arm
DEFAULT_MAX_OBS_GAP_MINUTES = 15
DEFAULT_HOLD_HORIZON_BARS = 288  # 24 hours of 5m bars
DEFAULT_FEE_PER_SIDE_PCT = 0.05  # 0.05% taker
DEFAULT_SLIPPAGE_BPS_PER_SIDE = None  # unset until decided

# Calibration thresholds (spec §12 / acceptance 17): the measurement
# scheduler targets every eligible coin once per 5-minute cycle; the epoch
# may only be enabled when the per-coin successful-observation gaps hold
# p95 <= 10 minutes and p99 <= 15 minutes.
CALIBRATION_P95_MAX_GAP_S = 600
CALIBRATION_P99_MAX_GAP_S = 900

# meta key holding the measurement epoch (spec §12).
EPOCH_KEY = "episode_epoch_ts"


class ObservationClass(IntEnum):
    """Class of observation for episode lifecycle decisions."""
    QUALIFYING = 1      # flagged, direction-set, data_quality=OK
    NON_QUALIFYING = 2  # unflagged, but data_quality=OK
    UNKNOWN = 3         # failed fetch, degraded, or not selected


class EpisodeState(IntEnum):
    """Episode lifecycle states."""
    OPEN = 1           # actively being refreshed
    REARMING = 2       # post-closure, waiting for re-arm timer
    CLOSED = 3         # final state (terminal or coverage lost)


class CloseReason(IntEnum):
    """Why an episode closed."""
    REARM_CONFIRMED = 1    # 60 observed minutes post-open
    REVERSAL = 2           # opposite direction qualifying observation
    COVERAGE_LOST = 3      # gap > MAX_OBS_GAP


class PlanStatus(IntEnum):
    """Episode plan status."""
    PLANNED = 1      # valid plan frozen from first observation
    NO_PLAN = 2      # no valid plan at first qualifying observation


# --------------------------------------------------------------------------
# Config serialization and hashing
# --------------------------------------------------------------------------

CONFIG_FIELDS = [
    "stake",
    "log_threshold",
    "leverage_cap",
    "universe_budget",
    "universe_policy",
]


def config_snapshot(
    stake: float,
    log_threshold: float,
    leverage_cap: Optional[int],
    universe_budget: int,
    universe_policy: str = "mexc_full",
) -> tuple[str, str]:
    """
    Create a stable config hash and JSON for an observation/cohort.

    Returns (config_hash, config_json) where:
    - config_hash: SHA256 hash of canonical JSON (for DB indexing)
    - config_json: the JSON string for storage

    The hash is deterministic: same values -> same hash.
    Different values -> different hash ensures episodes don't merge.
    """
    config = {
        "stake": float(stake),
        "log_threshold": float(log_threshold),
        "leverage_cap": int(leverage_cap) if leverage_cap is not None else None,
        "universe_budget": int(universe_budget),
        "universe_policy": str(universe_policy),
        "version": EPISODE_RULE_VERSION,
    }
    config_json = json.dumps(config, sort_keys=True, separators=(",", ":"))
    config_hash = hashlib.sha256(config_json.encode("utf-8")).hexdigest()[:16]
    return config_hash, config_json


# --------------------------------------------------------------------------
# Observation event structure
# --------------------------------------------------------------------------

class ObservationEvent(NamedTuple):
    """One measurement attempt for one coin with one config."""
    coin: str
    venue: str
    config_hash: str
    obs_ts: float           # wall-clock time when result known
    observation_class: ObservationClass
    signal_id: Optional[int] = None
    degraded_reasons: Optional[str] = None
    coverage: Optional[float] = None  # optional coverage indicator
    attempt_id: Optional[str] = None   # unique ID for deduplication

    @classmethod
    def qualifying(cls, coin, venue, config_hash, obs_ts, signal_id,
                   attempt_id=None):
        """Create a QUALIFYING observation."""
        return cls(coin, venue, config_hash, obs_ts, ObservationClass.QUALIFYING,
                   signal_id, None, None, attempt_id)

    @classmethod
    def non_qualifying(cls, coin, venue, config_hash, obs_ts):
        """Create a NON_QUALIFYING observation."""
        return cls(coin, venue, config_hash, obs_ts,
                   ObservationClass.NON_QUALIFYING)

    @classmethod
    def unknown(cls, coin, venue, config_hash, obs_ts, reasons=None,
                attempt_id=None):
        """Create an UNKNOWN observation (failed/degraded)."""
        return cls(coin, venue, config_hash, obs_ts, ObservationClass.UNKNOWN,
                   None, reasons, None, attempt_id)


# --------------------------------------------------------------------------
# Episode lifecycle transitions (Task 2)
# --------------------------------------------------------------------------

_REARM_S = DEFAULT_REARM_MINUTES * 60          # 3600
_MAX_GAP_S = DEFAULT_MAX_OBS_GAP_MINUTES * 60  # 900

_STATE_NAME = {
    EpisodeState.OPEN: "OPEN",
    EpisodeState.REARMING: "REARMING",
    EpisodeState.CLOSED: "CLOSED",
}
_CLOSE_NAME = {
    CloseReason.REARM_CONFIRMED: "REARM_CONFIRMED",
    CloseReason.REVERSAL: "REVERSAL",
    CloseReason.COVERAGE_LOST: "COVERAGE_LOST",
}

_EP_COLS = (
    "coin, venue, direction, first_signal_id, start_ts, end_ts, state, "
    "close_reason, after_gap, last_valid_ts, last_qualifying_ts, "
    "rearm_start_ts, close_ts, plan_status, no_plan_reason, qualifying_obs, "
    "non_qualifying_obs, unknown_obs, late_obs, config_hash, config_json, "
    "flag_rule_version, plan_rule_version, episode_rule_version, "
    "outcome_rule_version, cost_model_version"
)


def _episode_row(conn, episode_id):
    """Load one episode row as a dict keyed by column name."""
    cur = conn.execute(f"SELECT {_EP_COLS} FROM signal_episode WHERE id=?",
                       (episode_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([c.strip() for c in _EP_COLS.split(",")], row))


def _open_episode(conn, coin, config_hash):
    """The single non-closed episode for this (coin, config_hash), or None."""
    return conn.execute(
        "SELECT id, direction, state, start_ts, last_valid_ts,"
        " last_qualifying_ts, rearm_start_ts FROM signal_episode"
        " WHERE coin=? AND config_hash=? AND state != 'CLOSED'"
        " ORDER BY id DESC LIMIT 1",
        (coin, config_hash)).fetchone()


def _cursor(conn, coin, config_hash):
    row = conn.execute(
        "SELECT last_processed_obs_ts FROM episode_cursor"
        " WHERE coin=? AND config_hash=?", (coin, config_hash)).fetchone()
    return row[0] if row else None


def _set_cursor(conn, coin, config_hash, ts):
    conn.execute(
        "INSERT INTO episode_cursor (coin, config_hash, last_processed_obs_ts)"
        " VALUES (?,?,?) ON CONFLICT(coin, config_hash) DO UPDATE SET"
        " last_processed_obs_ts=excluded.last_processed_obs_ts"
        " WHERE excluded.last_processed_obs_ts > episode_cursor.last_processed_obs_ts",
        (coin, config_hash, int(ts)))


def _insert_episode(conn, coin, venue, direction, signal_id, start_ts,
                    after_gap, config_hash, config_json, versions, plan_status,
                    no_plan_reason):
    """Insert a fresh OPEN episode. Caller holds the write transaction."""
    marks = ", ".join("?" for _ in _EP_COLS.split(","))
    cur = conn.execute(
        f"INSERT INTO signal_episode ({_EP_COLS}) VALUES ({marks})",
        (coin, venue, direction, signal_id, int(start_ts), None, "OPEN", None,
         int(after_gap), int(start_ts), int(start_ts), None, None,
         plan_status, no_plan_reason,
         1, 0, 0, 0, config_hash, config_json,
         versions.get("flag_rule"), versions.get("plan_rule"),
         versions.get("episode_rule", EPISODE_RULE_VERSION),
         versions.get("outcome_rule", OUTCOME_RULE_VERSION),
         versions.get("cost_model", COST_MODEL_VERSION)))
    return cur.lastrowid


def _freeze_episode_plan(conn, episode_id, signal_id, plan, frozen_at):
    """Persist the first valid plan in the same transaction as its episode."""
    if plan is None or not _plan_status(plan)[0] == "PLANNED":
        return
    values = {name: getattr(plan, name, None) for name in
              ("entry_low", "entry_high", "stop", "tp1", "tp2",
               "leverage", "notional", "max_loss")}
    warnings = getattr(plan, "warnings", None) or []
    conn.execute(
        """INSERT INTO episode_plan
           (episode_id, signal_id, direction, entry_low, entry_high, stop,
            tp1, tp2, leverage, notional, max_loss, warnings, frozen_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (episode_id, signal_id, getattr(plan, "direction", None),
         values["entry_low"], values["entry_high"], values["stop"],
         values["tp1"], values["tp2"], values["leverage"],
         values["notional"], values["max_loss"],
         json.dumps(warnings, separators=(",", ":")), int(frozen_at)))


def _after_coverage_gap(conn, coin, config_hash, obs_ts):
    """Whether the previous episode lost coverage without observed quiet."""
    row = conn.execute(
        """SELECT close_ts FROM signal_episode
           WHERE coin=? AND config_hash=? AND close_reason='COVERAGE_LOST'
           ORDER BY close_ts DESC, id DESC LIMIT 1""",
        (coin, config_hash)).fetchone()
    if row is None or row[0] is None:
        return 0
    quiet = conn.execute(
        """SELECT 1 FROM episode_observation
           WHERE coin=? AND config_hash=? AND obs_class=?
             AND obs_ts>? AND obs_ts<? LIMIT 1""",
        (coin, config_hash, int(ObservationClass.NON_QUALIFYING),
         int(row[0]), int(obs_ts))).fetchone()
    return int(quiet is None)


def _close_episode(conn, episode_id, reason: CloseReason, close_ts):
    """Close an episode. close_ts is the design's semantic close time
    (last_valid_ts for COVERAGE_LOST, the confirming obs_ts otherwise),
    which is NOT necessarily `now`."""
    conn.execute(
        "UPDATE signal_episode SET state='CLOSED', close_reason=?,"
        " close_ts=?, end_ts=? WHERE id=?",
        (_CLOSE_NAME[reason], int(close_ts), int(close_ts), episode_id))


def _insert_observation(conn, event, episode_id, late):
    conn.execute(
        "INSERT INTO episode_observation (episode_id, signal_id, coin,"
        " config_hash, obs_ts, obs_class, late, degraded_reasons, attempt_id,"
        " coverage) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (episode_id, event.signal_id, event.coin, event.config_hash,
         int(event.obs_ts), int(event.observation_class), 1 if late else 0,
         event.degraded_reasons, event.attempt_id, event.coverage))


def _bump(conn, episode_id, column):
    conn.execute(
        f"UPDATE signal_episode SET {column} = {column} + 1 WHERE id=?",
        (episode_id,))


def _advance_valid(conn, episode_id, ts, qualifying):
    """Advance the coverage clock. Only QUALIFYING/NON_QUALIFYING do this.

    An UNKNOWN fetch is not evidence the episode is still alive, so it must
    never move last_valid_ts or cancel a re-arm.
    """
    conn.execute(
        "UPDATE signal_episode SET"
        " last_valid_ts = MAX(COALESCE(last_valid_ts, -9223372036854775808), ?)"
        " WHERE id=?",
        (int(ts), episode_id))
    if qualifying:
        conn.execute(
            "UPDATE signal_episode SET"
            " last_qualifying_ts = MAX(COALESCE(last_qualifying_ts,"
            " -9223372036854775808), ?) WHERE id=?",
            (int(ts), episode_id))


def _result(episode_id, state_change=None, close_reason=None, late=False):
    return {
        "episode_id": episode_id,
        "state_change": state_change,
        "close_reason": close_reason,
        "late": late,
    }


def process_observation(store, event: ObservationEvent, plan=None,
                        versions=None, config_json=None, card=None) -> dict:
    """
    Process one observation event for an episode. Transactional.

    Implements spec 6.2 in order, inside one BEGIN IMMEDIATE transaction:
    1. Late/stale (obs_ts <= cursor, or older than MAX_OBS_GAP on arrival):
       record, never mutate, never lower the watermark.
    2. Gap: only a QUALIFYING or NON_QUALIFYING observation can close an
       episode as COVERAGE_LOST. UNKNOWN never closes an episode by itself.
    3. Class-specific transition, including the OPEN/REARMING distinction.

    Returns dict with:
    - episode_id: linked episode (or None)
    - late: bool
    - state_change: 'OPEN'/'REARMING'/'CLOSED' or None
    - close_reason: 'REARM_CONFIRMED'/'REVERSAL'/'COVERAGE_LOST' or None
    """
    versions = versions or {}
    conn = store.conn
    now = event.obs_ts

    # NOTE: the epoch gate (spec §12) is enforced by the production entry
    # point run_cycle(), which only feeds QUALIFYING observations at or after
    # the epoch into this function. It deliberately does NOT live here: this
    # is the lifecycle primitive, and gating it would make the lifecycle
    # untestable offline and silently drop observations the calibration
    # journal needs.

    # BEGIN IMMEDIATE: serialise concurrent writers for this coin/config so
    # two processes cannot both create an OPEN episode and both pass the
    # unique index (the loser must be forced to see the winner's row).
    conn.execute("BEGIN IMMEDIATE")
    try:
        ep = _open_episode(conn, event.coin, event.config_hash)

        # --- 1. late / out-of-order handling -----------------------------
        # Spec 6.2 step 1: an observation is late/stale when its obs_ts is at
        # or before the watermark. A large obs_ts - last_valid_ts distance is
        # NOT lateness - it is the COVERAGE_LOST trigger handled in step 2.
        cursor_ts = _cursor(conn, event.coin, event.config_hash)
        late = cursor_ts is not None and now <= cursor_ts
        if late:
            ep_id = ep[0] if ep is not None else None
            _insert_observation(conn, event, ep_id, True)
            if ep_id is not None:
                _bump(conn, ep_id, "late_obs")
            conn.commit()
            return _result(ep_id, late=True)

        cls = event.observation_class
        valid = cls in (ObservationClass.QUALIFYING,
                        ObservationClass.NON_QUALIFYING)

        # --- 2. coverage gap (valid observations only) --------------------
        # UNKNOWN must never close an episode by itself (spec 6.2), so the
        # gap test lives here rather than before the class branch.
        after_gap = 0
        if ep is not None and valid:
            last_valid = ep[4] if ep[4] is not None else ep[3]
            if last_valid is not None and now - last_valid > _MAX_GAP_S:
                _close_episode(conn, ep[0], CloseReason.COVERAGE_LOST,
                               last_valid)
                _set_cursor(conn, event.coin, event.config_hash, now)
                after_gap = 1
                ep = None

        # --- 3. class-specific transition ---------------------------------
        if cls == ObservationClass.UNKNOWN:
            # link only: never refreshes, never re-arms, never closes.
            if ep is not None:
                _bump(conn, ep[0], "unknown_obs")
            _insert_observation(conn, event, ep[0] if ep else None, False)
            _set_cursor(conn, event.coin, event.config_hash, now)
            conn.commit()
            return _result(ep[0] if ep else None)

        if cls == ObservationClass.NON_QUALIFYING:
            if ep is None:
                _set_cursor(conn, event.coin, event.config_hash, now)
                conn.commit()
                return _result(None)
            ep_id, ep_state = ep[0], ep[2]
            _bump(conn, ep_id, "non_qualifying_obs")
            _insert_observation(conn, event, ep_id, False)
            _advance_valid(conn, ep_id, now, qualifying=False)
            if ep_state == "OPEN":
                # First quiet observation after an open episode starts re-arm.
                conn.execute(
                    "UPDATE signal_episode SET state='REARMING',"
                    " rearm_start_ts=? WHERE id=?",
                    (int(now), ep_id))
                _set_cursor(conn, event.coin, event.config_hash, now)
                conn.commit()
                return _result(ep_id, state_change="REARMING")
            # REARMING: the timer advances only through observed, valid,
            # closely-spaced non-qualifying observations (guaranteed here by
            # the gap check above), so obs_ts - rearm_start_ts is observed
            # quiet time and unobserved time can never count toward 60m.
            row = _episode_row(conn, ep_id)
            rearm_start = row["rearm_start_ts"]
            if rearm_start is not None and now - rearm_start >= _REARM_S:
                _close_episode(conn, ep_id, CloseReason.REARM_CONFIRMED, now)
                _set_cursor(conn, event.coin, event.config_hash, now)
                conn.commit()
                return _result(ep_id, state_change="CLOSED",
                               close_reason="REARM_CONFIRMED")
            _set_cursor(conn, event.coin, event.config_hash, now)
            conn.commit()
            return _result(ep_id, state_change="REARMING")

        # QUALIFYING
        direction = _direction_from_plan(plan, card)
        if ep is None:
            after_gap = _after_coverage_gap(
                conn, event.coin, event.config_hash, now)
            plan_status, no_plan_reason = _plan_status(plan)
            ep_id = _insert_episode(
                conn, event.coin, event.venue, direction, event.signal_id,
                now, after_gap, event.config_hash, config_json, versions,
                plan_status, no_plan_reason)
            _freeze_episode_plan(conn, ep_id, event.signal_id, plan, now)
            _bump(conn, ep_id, "qualifying_obs")
            _insert_observation(conn, event, ep_id, False)
            _advance_valid(conn, ep_id, now, qualifying=True)
            _set_cursor(conn, event.coin, event.config_hash, now)
            conn.commit()
            return _result(ep_id)

        ep_id, ep_dir, ep_state = ep[0], ep[1], ep[2]
        if direction and ep_dir and direction != ep_dir:
            # Reversal: close the old episode, start a new one immediately.
            _close_episode(conn, ep_id, CloseReason.REVERSAL, now)
            plan_status, no_plan_reason = _plan_status(plan)
            new_id = _insert_episode(
                conn, event.coin, event.venue, direction, event.signal_id,
                now, 0, event.config_hash, config_json, versions,
                plan_status, no_plan_reason)
            _freeze_episode_plan(conn, new_id, event.signal_id, plan, now)
            _bump(conn, new_id, "qualifying_obs")
            _insert_observation(conn, event, new_id, False)
            _advance_valid(conn, new_id, now, qualifying=True)
            _set_cursor(conn, event.coin, event.config_hash, now)
            conn.commit()
            return _result(new_id, state_change="OPEN",
                           close_reason="REVERSAL")

        # Same direction. A QUALIFYING observation cancels an in-flight
        # re-arm and returns the episode to OPEN.
        _bump(conn, ep_id, "qualifying_obs")
        _insert_observation(conn, event, ep_id, False)
        _advance_valid(conn, ep_id, now, qualifying=True)
        if ep_state == "REARMING":
            conn.execute(
                "UPDATE signal_episode SET state='OPEN', rearm_start_ts=NULL"
                " WHERE id=?", (ep_id,))
            _set_cursor(conn, event.coin, event.config_hash, now)
            conn.commit()
            return _result(ep_id, state_change="OPEN")
        _set_cursor(conn, event.coin, event.config_hash, now)
        conn.commit()
        return _result(ep_id, state_change="OPEN")

    except Exception:
        conn.rollback()
        raise


def sweep_coverage(store, now: float, *, max_gap_s: int = 900) -> int:
    """
    Close non-closed episodes with more than max_gap_s since the last valid
    observation.

    The clock is last_valid_ts, advanced only by QUALIFYING/NON_QUALIFYING
    observations: the cursor is NOT the clock because it also advances on
    UNKNOWN fetches, which would hide a coverage gap. COVERAGE_LOST closes at
    last_valid_ts (spec 6.3), not at `now`.

    Idempotent: a second sweep with the same (or later) `now` finds no stale
    episodes and closes nothing. Advances the cursor for closed episodes so a
    late row cannot rewind it.

    Returns the count of episodes closed as COVERAGE_LOST.
    """
    conn = store.conn
    conn.execute("BEGIN IMMEDIATE")
    try:
        now_i = int(now)
        stale = store.stale_open_episodes(now_i, max_gap_s)
        closed = 0
        for ep_id, coin, cfg, last_touch in stale:
            _close_episode(conn, ep_id, CloseReason.COVERAGE_LOST, last_touch)
            _set_cursor(conn, coin, cfg, max(last_touch or 0, now_i))
            closed += 1
        conn.commit()
        return closed
    except Exception:
        conn.rollback()
        raise


def _direction_from_plan(plan, card=None):
    """Direction of a QUALIFYING observation.

    Prefers the frozen plan's direction; falls back to the scorecard (`card`,
    or `plan.card` when the caller passes a plan wrapping one) so a
    QUALIFYING observation with no trade plan still gets a real direction.
    signal_episode.direction is NOT NULL, so an unresolvable direction is a
    hard error rather than a NULL write.
    """
    for source in (plan, card):
        if source is None:
            continue
        d = getattr(source, "direction", None)
        if d and d != "NEUTRAL":
            return d
    if plan is not None:
        d = getattr(getattr(plan, "card", None), "direction", None)
        if d and d != "NEUTRAL":
            return d
    raise ValueError(
        "QUALIFYING observation has no direction: neither the plan nor the "
        "scorecard supplies one")


def _plan_status(plan):
    """(plan_status, no_plan_reason) for a new episode's first observation."""
    if plan is None or not bool(getattr(plan, "valid", False)):
        return "NO_PLAN", "no plan at first qualifying observation"
    direction = getattr(plan, "direction", None)
    levels = [getattr(plan, name, None) for name in
              ("entry_low", "entry_high", "stop", "tp1", "tp2")]
    try:
        if direction not in ("LONG", "SHORT") or not all(
                math.isfinite(float(v)) and float(v) > 0 for v in levels):
            return "NO_PLAN", "first plan has invalid or incomplete levels"
        lo, hi, stop, tp1, tp2 = map(float, levels)
    except (TypeError, ValueError):
        return "NO_PLAN", "first plan has invalid or incomplete levels"
    if direction == "LONG" and not stop < lo < hi < tp1 < tp2:
        return "NO_PLAN", "first LONG plan levels are out of order"
    if direction == "SHORT" and not stop > hi > lo > tp1 > tp2:
        return "NO_PLAN", "first SHORT plan levels are out of order"
    return "PLANNED", None


# --------------------------------------------------------------------------
# Measurement scheduler (Task 3)
# --------------------------------------------------------------------------

# Degraded-data markers on a scorecard (design §3.3): a transient failure
# that affected the score inputs makes the attempt UNKNOWN even when the
# score is high. Structural absences (MTF history, MEXC-only coin) are
# informational and stay eligible.
_DEGRADED_NOTE_PREFIXES = ("DATA ",)


def _card_is_degraded(card) -> bool:
    """True when a scored card carries a transient-data degradation note."""
    for note in getattr(card, "notes", None) or ():
        if isinstance(note, str) and note.startswith(_DEGRADED_NOTE_PREFIXES):
            return True
    return False


def _card_flagged(card, config) -> bool:
    """The existing stake-aware flag rule (spec SS4 / app.scan_once).

    Read-only: it decides the observation CLASS, never the ranking.
    """
    stake = config.get("stake")
    threshold = config.get("log_threshold")
    if stake is None or threshold is None:
        return False
    try:
        return (card.is_actionable(stake) and float(card.score) >= float(threshold))
    except (TypeError, ValueError):
        return False


def epoch_ts(store):
    """The measurement epoch, or None when episode creation is disabled."""
    raw = store.get_meta(EPOCH_KEY)
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def run_cycle(store, eligible, *, config, now_fn=None, score_one=None,
              attempt_id_factory=None, tickers=None, max_workers=1,
              progress_fn=None):
    """
    One measurement cycle: a coverage sweep, then exactly one attempt per
    eligible coin.

    Every attempt is journaled in episode_observation, including failures
    (UNKNOWN with the reason) and degraded cards (UNKNOWN, design §3.2/§3.3).
    Coins that score cleanly are QUALIFYING or NON_QUALIFYING under the
    existing stake-aware flag rule.

    Episode creation is gated on the measurement epoch: until
    enable_epoch() has passed calibration, the cycle only records attempts,
    so interactive rankings, signal_log and coin_state are never touched
    (spec §12, §17). From the epoch onward a qualifying observation is
    logged as a signal row and fed through the episode lifecycle with the
    frozen plan attached.

    Returns per-cycle counts plus sweep_closed. Never raises on a single
    coin's failure: one bad coin must not abort the cycle.
    """
    if score_one is None:
        from .scan import analyse_one
        feed = tickers or {}
        score_one = lambda sym, coin, detail: analyse_one(
            sym, coin, detail, feed.get(sym, {}), config.get("stake"),
            attach_plans=True)

    now_fn = now_fn or (lambda: time.time())
    cfg_hash = config["config_hash"]
    cfg_json = config.get("config_json")
    stake = config.get("stake")
    threshold = config.get("log_threshold")
    venue = config.get("venue", "MEXC")

    # 1. Coverage sweep first (spec §6.3): a coin that stopped answering must
    #    not hold an episode open into this cycle.
    cycle_ts = now_fn()
    sweep_closed = sweep_coverage(store, cycle_ts)

    counts = {"attempted": 0, "qualifying": 0, "non_qualifying": 0,
              "unknown": 0, "failed": 0, "degraded": 0, "sweep_closed":
              sweep_closed}
    epoch = epoch_ts(store)
    active_epoch = epoch is not None
    versions = {"flag_rule": 2, "plan_rule": PLAN_RULE_VERSION,
                "episode_rule": EPISODE_RULE_VERSION,
                "outcome_rule": OUTCOME_RULE_VERSION,
                "cost_model": COST_MODEL_VERSION}

    eligible = list(eligible)
    # The daemon measures the entire venue universe. Score fetches are
    # independent and use the scan module's shared rate limiter, so run them
    # concurrently while keeping journal/lifecycle writes serialized below.
    # Preserve eligible order for deterministic episode cursor updates.
    executor = (ThreadPoolExecutor(max_workers=max_workers)
                if max_workers > 1 and eligible else None)
    futures = ([executor.submit(score_one, sym, coin, detail)
                for sym, coin, detail in eligible]
               if executor else None)

    try:
        for index, (sym, coin, detail) in enumerate(eligible):
            attempt_id = (attempt_id_factory(coin, cycle_ts)
                          if attempt_id_factory is not None else None)
            try:
                card = (futures[index].result() if futures is not None
                        else score_one(sym, coin, detail))
            except Exception as e:
                # A failed fetch is UNKNOWN, never NON_QUALIFYING and never a
                # silent drop: the attempt is journaled with its reason.
                log_attempt(store, coin, cfg_hash, now_fn(),
                            ObservationClass.UNKNOWN,
                            degraded_reasons=f"{type(e).__name__}: {e}",
                            attempt_id=attempt_id)
                counts["attempted"] += 1
                counts["failed"] += 1
                counts["unknown"] += 1
                if progress_fn is not None:
                    progress_fn(index + 1, len(eligible))
                continue

            if card is None:
                log_attempt(store, coin, cfg_hash, now_fn(),
                            ObservationClass.UNKNOWN,
                            degraded_reasons="no scorecard (fetch failed)",
                            attempt_id=attempt_id)
                counts["attempted"] += 1
                counts["failed"] += 1
                counts["unknown"] += 1
                if progress_fn is not None:
                    progress_fn(index + 1, len(eligible))
                continue

            # obs_ts is captured after scoring completes (spec §3.1): the
            # earliest moment the system could have acted on this observation.
            obs_ts = now_fn()
            degraded = _card_is_degraded(card)
            flagged = _card_flagged(card, config)
            degraded_reasons = None

            if degraded:
                # A degraded card is UNKNOWN even when flagged: it can neither
                # start an episode nor refresh one (spec §3.3).
                obs_class = ObservationClass.UNKNOWN
                degraded_reasons = "degraded: " + "; ".join(
                    n for n in (getattr(card, "notes", None) or [])
                    if isinstance(n, str)
                    and n.startswith(_DEGRADED_NOTE_PREFIXES))
                counts["degraded"] += 1
                counts["unknown"] += 1
            elif flagged and card.direction in ("LONG", "SHORT"):
                obs_class = ObservationClass.QUALIFYING
                counts["qualifying"] += 1
            else:
                obs_class = ObservationClass.NON_QUALIFYING
                counts["non_qualifying"] += 1
            counts["attempted"] += 1

            if (active_epoch and obs_class == ObservationClass.QUALIFYING
                    and int(obs_ts) >= epoch):
                # Persist the score row with its measurement metadata, then run
                # the transactional lifecycle. The frozen plan rides the event.
                signal_id = store.log_signal(card, flagged=True, tier=2)
                store.record_measurement_observation(
                    signal_id, obs_ts=obs_ts, data_quality="OK",
                    degraded_reasons=None,
                    last_bar_ts=getattr(card, "last_bar_ts", None),
                    ticker_ts=getattr(card, "ticker_ts", None),
                    stake=stake, log_threshold=threshold,
                    leverage_cap=config.get("leverage_cap"),
                    config_hash=cfg_hash, code_rev=CODE_REV)
                event = ObservationEvent.qualifying(coin, venue, cfg_hash, obs_ts,
                                                    signal_id, attempt_id)
                process_observation(store, event, getattr(card, "plan", None),
                                    versions, cfg_json, card=card)
            else:
                log_attempt(store, coin, cfg_hash, obs_ts, obs_class,
                            degraded_reasons=degraded_reasons,
                            attempt_id=attempt_id)
            if progress_fn is not None:
                progress_fn(index + 1, len(eligible))
    finally:
        if executor is not None:
            executor.shutdown(wait=True)
    return counts


def log_attempt(store, coin, config_hash, obs_ts, obs_class, *,
                signal_id=None, degraded_reasons=None, attempt_id=None):
    """Journal one attempt; episode linking happens through the lifecycle."""
    return store.log_measurement_attempt(
        coin, config_hash, obs_ts, obs_class, signal_id=signal_id,
        degraded_reasons=degraded_reasons, attempt_id=attempt_id)


def measurement_config(stake, log_threshold, leverage_cap=None,
                       universe_budget=0, venue="MEXC"):
    """config_snapshot for the measurement scheduler.

    Uses the measurement-universe policy version, NOT the interactive
    UNIVERSE_BUDGET: the measurement cohort is independent of the top-N
    rotation, so `universe_budget` is deliberately 0 (uncapped) here.
    """
    from .scan import MEASUREMENT_UNIVERSE_POLICY
    config_hash, config_json = config_snapshot(
        stake=stake, log_threshold=log_threshold,
        leverage_cap=leverage_cap, universe_budget=universe_budget,
        universe_policy=MEASUREMENT_UNIVERSE_POLICY)
    return {"config_hash": config_hash, "config_json": config_json,
            "stake": stake, "log_threshold": log_threshold,
            "leverage_cap": leverage_cap, "venue": venue}


def measurement_eligible(tickers, details, stake=None):
    """The all-eligible measurement universe: [(sym, coin, detail_row)]."""
    from .scan import build_measurement_universe
    return build_measurement_universe(tickers, details, stake=stake)


def measurement_attempt_id(coin, cycle_ts, run_id):
    """Unique attempt id so simultaneous GUI/daemon attempts never collide
    (spec §5.3): coin + cycle timestamp + the writer's run id."""
    digest = hashlib.sha256(
        f"{coin}:{cycle_ts}:{run_id}".encode()).hexdigest()
    return f"{int(cycle_ts)}-{coin}-{digest[:12]}"


def _pctile(sorted_vals, pct):
    """Nearest-rank percentile over a pre-sorted list of numbers."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = max(1, math.ceil(pct / 100.0 * len(sorted_vals)))
    return sorted_vals[min(rank, len(sorted_vals)) - 1]


def cadence_stats(store, start_ts=None, end_ts=None) -> dict:
    """
    Cadence and calibration statistics over the measurement attempts.

    Successful-observation gaps are per-coin, between consecutive completed
    observations (QUALIFYING/NON_QUALIFYING) only: a failed or degraded
    attempt is UNKNOWN and cannot vouch for liveness, so it must not close a
    gap (spec §3.1, §12). Calibration passes when the p95 gap is at most 10
    minutes and the p99 gap at most 15 minutes; below that the scheduler or
    eligible universe must be fixed before the epoch may be enabled.
    """
    sql = ("SELECT coin, obs_ts, obs_class, degraded_reasons FROM"
           " episode_observation")
    params = []
    where = []
    if start_ts is not None:
        where.append("obs_ts >= ?")
        params.append(int(start_ts))
    if end_ts is not None:
        where.append("obs_ts <= ?")
        params.append(int(end_ts))
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY coin, obs_ts"

    per_coin = {}
    totals = {"attempts": 0, "qualifying": 0, "non_qualifying": 0,
              "unknown": 0, "failures": 0, "degraded": 0}
    for coin, obs_ts, obs_class, reasons in store.conn.execute(sql, params):
        totals["attempts"] += 1
        cls = int(obs_class or 0)
        if cls == int(ObservationClass.QUALIFYING):
            totals["qualifying"] += 1
        elif cls == int(ObservationClass.NON_QUALIFYING):
            totals["non_qualifying"] += 1
        else:
            totals["unknown"] += 1
            text = reasons or ""
            if text.startswith("degraded:"):
                totals["degraded"] += 1
            else:
                totals["failures"] += 1
            continue                      # never closes a gap
        per_coin.setdefault(coin, []).append(int(obs_ts))

    gaps = []
    coin_gaps = {}
    for coin, ts_list in per_coin.items():
        coin_gaps[coin] = sorted(b - a for a, b in zip(ts_list, ts_list[1:]))
        gaps.extend(coin_gaps[coin])
    gaps.sort()

    p50 = _pctile(gaps, 50)
    p95 = _pctile(gaps, 95)
    p99 = _pctile(gaps, 99)
    p95_ok = p95 is not None and p95 <= CALIBRATION_P95_MAX_GAP_S
    p99_ok = p99 is not None and p99 <= CALIBRATION_P99_MAX_GAP_S
    return {
        "coins": len(per_coin),
        "observations": totals["qualifying"] + totals["non_qualifying"],
        "attempts": totals["attempts"],
        "completions": totals["qualifying"] + totals["non_qualifying"],
        "qualifying": totals["qualifying"],
        "non_qualifying": totals["non_qualifying"],
        "unknown": totals["unknown"],
        "failures": totals["failures"],
        "degraded": totals["degraded"],
        "gaps": len(gaps),
        "gap_p50_s": p50,
        "gap_p95_s": p95,
        "gap_p99_s": p99,
        "gap_p50_min": None if p50 is None else round(p50 / 60.0, 2),
        "gap_p95_min": None if p95 is None else round(p95 / 60.0, 2),
        "gap_p99_min": None if p99 is None else round(p99 / 60.0, 2),
        "per_coin_gaps_s": coin_gaps,
        "calibration": {
            "pass": bool(p95_ok and p99_ok),
            "p95_ok": bool(p95_ok),
            "p99_ok": bool(p99_ok),
            "p95_limit_s": CALIBRATION_P95_MAX_GAP_S,
            "p99_limit_s": CALIBRATION_P99_MAX_GAP_S,
        },
    }


def enable_epoch(store, now) -> bool:
    """
    Enable episode creation when calibration passes.

    The epoch is an explicit operator action, never automatic (spec §12):
    it is refused while the cadence calibration fails (p95 > 10 minutes or
    p99 > 15 minutes) or is not yet established, and it is written once, so
    a later call can never move the cohort boundary.
    """
    if epoch_ts(store) is not None:
        return False
    stats = cadence_stats(store)
    if not stats["calibration"]["pass"]:
        return False
    store.set_meta("episode_epoch_ts", int(now))
    return True


# --------------------------------------------------------------------------
# Episode outcome resolver (Task 4) — stub until implemented
# --------------------------------------------------------------------------

def fill_and_bar_helpers():
    raise NotImplementedError("Implement in Task 4: Episode outcome resolver")


# --------------------------------------------------------------------------
# Episode reporting (Task 5) — stubs until implemented
# --------------------------------------------------------------------------

def summary(store, *, config_hash=None, rule_versions=None, now=None) -> dict:
    raise NotImplementedError("Implement in Task 5: Episode reporting")


def breakdowns(store, *, dimension, min_episodes=20, min_coins=10) -> list:
    raise NotImplementedError("Implement in Task 5: Episode reporting")
