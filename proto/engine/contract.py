"""Frozen benchmark contract: the definition of "a coin passes."

A `BenchmarkContract` is the immutable set of rules that decide, for one coin
and one signal, whether the outcome is a pass (TP) or a fail (SL, TIMEOUT,
NO_SIGNAL, DATA_GAP).  Once frozen, its SHA-256 hash is the only identity a
run ever needs: changing any field changes the hash and therefore the
contract.

The engine NEVER reads `data/bars/` directly; it reads bars through the
research snapshot DB.  This module only defines the contract; it does not
open databases or touch the archive.

Fields (plan §4):
  version             str    contract schema version
  leverage            float  L
  target_roe          float  +1.0 (gross target on margin)
  stop_roe            float  -2.5 (gross stop on margin, as positive)
  horizon_bars        int    T (time barrier)
  entry               str    "next_open" (fixed; frozen)
  timeout             str    "fail"
  gap_policy          str    "DATA_GAP_is_fail"
  no_signal           str    "fail"
  pass_threshold      float  0.75
  min_history_days    int    warmup eligibility
  liquidity_floor_qv24 float  trailing-24h quote volume floor
  embargo_bars        int    >= horizon_bars
  margin_policy       str    "cross"
  funding             str    "excluded"
  cost_model          dict   frozen copy of fee/slippage tiers
  cost_model_hash     str    sha256 of canonical cost_model
  trigger_threshold_tau float  score >= tau is the signal
  decision_grid_bars  int    hourly grid stride (12 = 1h at 5-min bars)
  stage_sizes         tuple  disjoint cohort sizes (20, 50, 100) per plan §4
"""
import hashlib
import json
import math
import os
from dataclasses import dataclass, field, fields
from types import MappingProxyType


CONTRACT_VERSION = "engine-v1"

_VALID_ENTRIES = {"next_open"}
_VALID_TIMEOUTS = {"fail"}
_VALID_GAP_POLICIES = {"DATA_GAP_is_fail"}
_VALID_NO_SIGNALS = {"fail"}
_VALID_MARGIN_POLICIES = {"cross"}
_VALID_FUNDING = {"excluded"}

# Fields that must be present (no default) vs may have defaults.
_REQUIRED_FIELDS = (
    "version", "leverage", "horizon_bars", "min_history_days",
    "liquidity_floor_qv24", "embargo_bars", "margin_policy",
    "cost_model", "trigger_threshold_tau", "decision_grid_bars",
    "stage_sizes",
)

# Required stage_sizes tuple per plan §4 (disjoint cohorts V20, V50, V100+).
_REQUIRED_STAGE_SIZES = (20, 50, 100)


class ContractError(Exception):
    """Raised on validation, load, or save failures."""


def _canonical(obj):
    """Canonical JSON: sorted keys, compact separators, reject NaN/Inf."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, ensure_ascii=False)


def _check_finite(name, value):
    """Reject NaN, Infinity, -Infinity."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ContractError(f"{name} must be a number, got {type(value).__name__}")
    fval = float(value)
    if math.isnan(fval):
        raise ContractError(f"{name} is NaN")
    if math.isinf(fval):
        raise ContractError(f"{name} is infinite")
    return fval


def _check_positive(name, value):
    fval = _check_finite(name, value)
    if fval <= 0:
        raise ContractError(f"{name} must be positive, got {fval}")
    return fval


def _sha256(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class BenchmarkContract:
    """Frozen benchmark contract. Immutable after construction."""
    version: str
    leverage: float
    horizon_bars: int
    min_history_days: int
    liquidity_floor_qv24: float
    embargo_bars: int
    margin_policy: str
    cost_model: dict
    trigger_threshold_tau: float
    target_roe: float = 1.0
    stop_roe: float = 2.5
    entry: str = "next_open"
    timeout: str = "fail"
    gap_policy: str = "DATA_GAP_is_fail"
    no_signal: str = "fail"
    pass_threshold: float = 0.75
    funding: str = "excluded"
    cost_model_hash: str = field(default="")
    decision_grid_bars: int = field(default=12)
    stage_sizes: tuple = field(default=(20, 50, 100))

    def __post_init__(self):
        self._validate()

    def _validate(self):
        # --- required fields ---
        for name in _REQUIRED_FIELDS:
            val = getattr(self, name)
            if val is None:
                raise ContractError(f"{name} is required")

        # --- numeric fields ---
        object.__setattr__(self, "leverage",
                           _check_positive("leverage", self.leverage))
        object.__setattr__(self, "horizon_bars",
                           self._check_int_positive("horizon_bars", self.horizon_bars))
        object.__setattr__(self, "min_history_days",
                           self._check_int_positive("min_history_days", self.min_history_days))
        object.__setattr__(self, "liquidity_floor_qv24",
                           _check_positive("liquidity_floor_qv24", self.liquidity_floor_qv24))
        object.__setattr__(self, "embargo_bars",
                           self._check_int_positive("embargo_bars", self.embargo_bars))
        object.__setattr__(self, "trigger_threshold_tau",
                           _check_positive("trigger_threshold_tau", self.trigger_threshold_tau))
        object.__setattr__(self, "target_roe",
                           _check_positive("target_roe", self.target_roe))
        object.__setattr__(self, "stop_roe",
                           _check_positive("stop_roe", self.stop_roe))
        object.__setattr__(self, "pass_threshold",
                           _check_finite("pass_threshold", self.pass_threshold))

        # decision_grid_bars: positive integer
        object.__setattr__(self, "decision_grid_bars",
                           self._check_int_positive("decision_grid_bars",
                                                    self.decision_grid_bars))

        # stage_sizes: must be exactly (20, 50, 100) per plan §4
        if not isinstance(self.stage_sizes, (tuple, list)):
            raise ContractError(f"stage_sizes must be a tuple/list, got {type(self.stage_sizes).__name__}")
        ss = tuple(self.stage_sizes)
        if ss != _REQUIRED_STAGE_SIZES:
            raise ContractError(f"stage_sizes must be {tuple(_REQUIRED_STAGE_SIZES)}, got {ss}")
        for v in ss:
            if not isinstance(v, int) or isinstance(v, bool):
                raise ContractError(f"stage_sizes entries must be integers, got {v!r}")
            if v <= 0:
                raise ContractError(f"stage_sizes entries must be positive, got {v}")
        # Must be strictly increasing
        for i in range(len(ss) - 1):
            if ss[i] >= ss[i + 1]:
                raise ContractError(f"stage_sizes must be strictly increasing, got {ss}")
        if len(ss) < 2:
            raise ContractError("stage_sizes must have at least 2 entries")
        object.__setattr__(self, "stage_sizes", ss)

        # pass_threshold range
        if not (0 < self.pass_threshold <= 1.0):
            raise ContractError(f"pass_threshold must be in (0, 1], got {self.pass_threshold}")

        # embargo >= horizon
        if self.embargo_bars < self.horizon_bars:
            raise ContractError(
                f"embargo_bars ({self.embargo_bars}) must be >= horizon_bars ({self.horizon_bars})")

        # --- enum fields ---
        if self.version != CONTRACT_VERSION:
            raise ContractError(f"version must be {CONTRACT_VERSION!r}, got {self.version!r}")
        if self.entry not in _VALID_ENTRIES:
            raise ContractError(f"unknown entry: {self.entry!r}")
        if self.timeout not in _VALID_TIMEOUTS:
            raise ContractError(f"unknown timeout: {self.timeout!r}")
        if self.gap_policy not in _VALID_GAP_POLICIES:
            raise ContractError(f"unknown gap_policy: {self.gap_policy!r}")
        if self.no_signal not in _VALID_NO_SIGNALS:
            raise ContractError(f"unknown no_signal: {self.no_signal!r}")
        if self.margin_policy not in _VALID_MARGIN_POLICIES:
            raise ContractError(f"unknown margin_policy: {self.margin_policy!r}")
        if self.funding not in _VALID_FUNDING:
            raise ContractError(f"unknown funding: {self.funding!r}")

         # --- cost_model ---
        if not isinstance(self.cost_model, dict):
            raise ContractError("cost_model must be a JSON object")
        _VALID_COST_KEYS = {"fee_per_side", "slip_tiers", "min_qv24"}
        extra = set(self.cost_model) - _VALID_COST_KEYS
        if extra:
            raise ContractError(f"cost_model has unknown fields: {extra}")
        for k in ("fee_per_side", "min_qv24"):
            if k in self.cost_model:
                _check_positive(k, self.cost_model[k])
        tiers = self.cost_model.get("slip_tiers")
        if not isinstance(tiers, (list, tuple)) or not tiers:
            raise ContractError("cost_model.slip_tiers must be a non-empty list")
        for t in tiers:
            if not (isinstance(t, (list, tuple)) and len(t) == 2):
                raise ContractError(f"slip tier must be [qv24, slip], got {t!r}")
            _check_positive("slip_tier_min_qv24", t[0])
            _check_positive("slip_tier_slip", t[1])

        # --- cost_model_hash (computed before freezing, on the original dict) ---
        hash_val = _sha256(_canonical(self.cost_model))
        object.__setattr__(self, "cost_model_hash", hash_val)

        # Freeze cost_model into an immutable mapping so it cannot be
        # mutated after construction (frozen=True on the dataclass only
        # prevents reassignment of the attribute, not mutation of the dict).
        # slip_tiers is deep-frozen to a tuple of tuples so nested values
        # are immutable too. Validation above already guaranteed slip_tiers
        # is a non-empty sequence of 2-element sequences.
        # ponytail: freezing exactly two levels deep is sufficient because
        # every leaf under the validated cost_model schema is a float.
        cm = dict(self.cost_model)
        cm["slip_tiers"] = tuple(tuple(t) for t in cm["slip_tiers"])
        frozen_cm = MappingProxyType(cm)
        object.__setattr__(self, "cost_model", frozen_cm)

    @staticmethod
    def _check_int_positive(name, value):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ContractError(f"{name} must be an integer, got {type(value).__name__}")
        if value <= 0:
            raise ContractError(f"{name} must be positive, got {value}")
        return value

    # -- serialization --------------------------------------------------

    def to_dict(self):
        """Serialize to a plain dict (canonical field order)."""
        d = {}
        for f in fields(self):
            val = getattr(self, f.name)
            # Convert MappingProxyType back to a plain dict for JSON serialization.
            if isinstance(val, MappingProxyType):
                val = dict(val)
            d[f.name] = val
        return d

    def canonical_json(self) -> str:
        """Stable JSON with sorted keys, no NaN/Inf, ASCII."""
        return _canonical(self.to_dict())

    def sha256(self) -> str:
        """SHA-256 of the canonical JSON."""
        return _sha256(self.canonical_json())

    @classmethod
    def from_dict(cls, d):
        """Construct a contract from a dict, rejecting unknown fields."""
        if not isinstance(d, dict):
            raise ContractError("contract must be a JSON object")
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ContractError(f"unknown contract fields: {sorted(unknown)}")
        return cls(**{k: v for k, v in d.items() if k in known})

    @classmethod
    def load(cls, path) -> "BenchmarkContract":
        """Load a contract from a JSON file and verify its hash.

        Requires that the file contains a valid `_sha256` field that matches
        the recomputed hash. Unsigned files (missing or null _sha256) are
        rejected.
        """
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ContractError("contract file must contain a JSON object")
        if "_sha256" not in data:
            raise ContractError("contract file missing _sha256 field")
        recorded_hash = data.pop("_sha256", None)
        if recorded_hash is None:
            raise ContractError("contract file has null _sha256 field")
        c = cls.from_dict(data)
        actual_hash = c.sha256()
        if recorded_hash != actual_hash:
            raise ContractError(
                f"contract hash mismatch: file says {recorded_hash}, "
                f"recomputed {actual_hash}")
        return c

    def save_once(self, path) -> None:
        """Write the contract JSON with its hash. Refuse to overwrite."""
        if os.path.exists(path):
            raise ContractError(f"refusing to overwrite existing contract: {path}")
        obj = self.to_dict()
        obj["_sha256"] = self.sha256()
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        # Atomic create: O_EXCL ensures we don't clobber an existing file.
        # No cleanup on failure: a partial file indicates a programming error
        # (e.g. disk full), not an engine boundary violation. The caller
        # retries with a fresh path.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, sort_keys=True, indent=2, ensure_ascii=False)
            fh.write("\n")
