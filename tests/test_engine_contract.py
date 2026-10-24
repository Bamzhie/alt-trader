"""Tests for proto.engine.contract.BenchmarkContract.

Covers: validation, canonical JSON, hash stability, tamper detection,
save_once refusal to overwrite, and load round-trip.
"""
import hashlib
import json
import math
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _valid_kwargs(**overrides):
    """A minimal valid contract as kwargs."""
    base = dict(
        version="engine-v1",
        leverage=20.0,
        target_roe=1.0,
        stop_roe=2.5,
        horizon_bars=864,
        entry="next_open",
        timeout="fail",
        gap_policy="DATA_GAP_is_fail",
        no_signal="fail",
        pass_threshold=0.75,
        min_history_days=7,
        liquidity_floor_qv24=1e6,
        embargo_bars=864,
        margin_policy="cross",
        funding="excluded",
        cost_model={"fee_per_side": 0.0005,
                     "slip_tiers": [[30e6, 0.00015], [10e6, 0.0003],
                                    [3e6, 0.0006], [1e6, 0.0012]],
                     "min_qv24": 1e6},
        trigger_threshold_tau=0.5,
        decision_grid_bars=12,
        stage_sizes=(20, 50, 100),
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

print("=== Test: canonical JSON is stable and deterministic ===")

from proto.engine.contract import BenchmarkContract, ContractError

c1 = BenchmarkContract(**_valid_kwargs())
c2 = BenchmarkContract(**_valid_kwargs())
j1 = c1.canonical_json()
j2 = c2.canonical_json()
check("same canonical JSON for identical contracts", j1 == j2)

# Field order must not matter in canonical form
c3 = BenchmarkContract(**dict(reversed(list(_valid_kwargs().items()))))
check("canonical JSON independent of field order", c3.canonical_json() == j1)

# No NaN / Infinity in JSON output
check("canonical JSON contains no NaN/Infinity",
      "NaN" not in j1 and "Infinity" not in j1)

print("\n=== Test: hash stability ===")

h1 = c1.sha256()
h2 = c2.sha256()
check("same hash for identical contracts", h1 == h2)
check("hash is 64 hex chars", len(h1) == 64 and all(ch in "0123456789abcdef" for ch in h1))

# Hash is deterministic across repeated calls
check("hash stable on repeated calls", c1.sha256() == c1.sha256())

print("\n=== Test: validation rejects invalid fields ===")

# Non-positive leverage
try:
    BenchmarkContract(**_valid_kwargs(leverage=0))
    check("rejects zero leverage", False, "no error raised")
except ContractError:
    check("rejects zero leverage", True)
except ValueError:
    check("rejects zero leverage", True)

try:
    BenchmarkContract(**_valid_kwargs(leverage=-5))
    check("rejects negative leverage", False)
except (ContractError, ValueError):
    check("rejects negative leverage", True)

# Non-finite leverage
try:
    BenchmarkContract(**_valid_kwargs(leverage=float("nan")))
    check("rejects NaN leverage", False)
except (ContractError, ValueError):
    check("rejects NaN leverage", True)

# Non-positive horizon_bars
try:
    BenchmarkContract(**_valid_kwargs(horizon_bars=0))
    check("rejects zero horizon_bars", False)
except (ContractError, ValueError):
    check("rejects zero horizon_bars", True)

# Non-positive min_history_days
try:
    BenchmarkContract(**_valid_kwargs(min_history_days=0))
    check("rejects zero min_history_days", False)
except (ContractError, ValueError):
    check("rejects zero min_history_days", True)

# Non-positive liquidity_floor_qv24
try:
    BenchmarkContract(**_valid_kwargs(liquidity_floor_qv24=0))
    check("rejects zero liquidity_floor_qv24", False)
except (ContractError, ValueError):
    check("rejects zero liquidity_floor_qv24", True)

# Non-positive embargo_bars
try:
    BenchmarkContract(**_valid_kwargs(embargo_bars=0))
    check("rejects zero embargo_bars", False)
except (ContractError, ValueError):
    check("rejects zero embargo_bars", True)

# embargo_bars must be >= horizon_bars
try:
    BenchmarkContract(**_valid_kwargs(embargo_bars=100, horizon_bars=288))
    check("rejects embargo_bars < horizon_bars", False)
except (ContractError, ValueError):
    check("rejects embargo_bars < horizon_bars", True)

# NaN in stop_roe
try:
    BenchmarkContract(**_valid_kwargs(stop_roe=float("nan")))
    check("rejects NaN stop_roe", False)
except (ContractError, ValueError):
    check("rejects NaN stop_roe", True)

# Infinity in target_roe
try:
    BenchmarkContract(**_valid_kwargs(target_roe=float("inf")))
    check("rejects Infinity target_roe", False)
except (ContractError, ValueError):
    check("rejects Infinity target_roe", True)

print("\n=== Test: load rejects unknown fields ===")

# Build JSON with an unknown field
raw = _valid_kwargs()
raw["bogus_field"] = 42
try:
    BenchmarkContract.from_dict(raw)
    check("rejects unknown field", False)
except ContractError:
    check("rejects unknown field", True)

# Wrong type for a field
try:
    BenchmarkContract.from_dict({**_valid_kwargs(), "leverage": "twenty"})
    check("rejects string leverage", False)
except (ContractError, TypeError):
    check("rejects string leverage", True)

print("\n=== Test: save_once refuses to overwrite ===")

tmpdir = tempfile.mkdtemp(prefix="contract_test_")
c = BenchmarkContract(**_valid_kwargs())

# First save should succeed
path1 = os.path.join(tmpdir, "contract.json")
c.save_once(path1)
check("save_once writes first file", os.path.exists(path1))

# Second save to same path should refuse
try:
    c.save_once(path1)
    check("save_once refuses overwrite", False, "no error raised")
except ContractError:
    check("save_once refuses overwrite", True)
except FileExistsError:
    check("save_once refuses overwrite", True)

# Save under different path with same hash should succeed (it's a different file)
path2 = os.path.join(tmpdir, "contract2.json")
c.save_once(path2)
check("save_once allows different file", os.path.exists(path2))

print("\n=== Test: load round-trip and tamper detection ===")

c_loaded = BenchmarkContract.load(path1)
check("load preserves leverage", c_loaded.leverage == c.leverage)
check("load preserves version", c_loaded.version == c.version)
check("load preserves horizon_bars", c_loaded.horizon_bars == c.horizon_bars)
check("load hash matches original", c_loaded.sha256() == c.sha256())

# Tampered file: change a field in the JSON, hash should no longer match
with open(path1, "r") as fh:
    tampered = json.load(fh)
tampered["leverage"] = 999.0
tampered_path = os.path.join(tmpdir, "tampered.json")
with open(tampered_path, "w") as fh:
    json.dump(tampered, fh)

try:
    c_tampered = BenchmarkContract.load(tampered_path)
    check("load re-validates tampered data", False,
          "no error; hash mismatch not detected")
except ContractError:
    check("load re-validates tampered data", True)

# Corrupt the hash field in a saved file — load should detect the mismatch
obj = json.loads(json.dumps(tampered))
obj["_sha256"] = "0" * 64
corrupt_path = os.path.join(tmpdir, "corrupt.json")
with open(corrupt_path, "w") as fh:
    json.dump(obj, fh)
try:
    BenchmarkContract.load(corrupt_path)
    check("load detects hash mismatch", False)
except ContractError:
    check("load detects hash mismatch", True)

print("\n=== Test: unknown enum fields rejected ===")

# Unknown entry value
try:
    BenchmarkContract(**_valid_kwargs(entry="unknown_entry_type"))
    check("rejects unknown entry", False)
except (ContractError, ValueError):
    check("rejects unknown entry", True)

# Unknown timeout value
try:
    BenchmarkContract(**_valid_kwargs(timeout="unknown_timeout"))
    check("rejects unknown timeout", False)
except (ContractError, ValueError):
    check("rejects unknown timeout", True)

# Unknown gap_policy
try:
    BenchmarkContract(**_valid_kwargs(gap_policy="unknown_policy"))
    check("rejects unknown gap_policy", False)
except (ContractError, ValueError):
    check("rejects unknown gap_policy", True)

# Unknown no_signal
try:
    BenchmarkContract(**_valid_kwargs(no_signal="unknown"))
    check("rejects unknown no_signal", False)
except (ContractError, ValueError):
    check("rejects unknown no_signal", True)

# Unknown funding
try:
    BenchmarkContract(**_valid_kwargs(funding="modeled"))
    check("rejects unknown funding", False)
except (ContractError, ValueError):
    check("rejects unknown funding", True)

# Unknown margin_policy
try:
    BenchmarkContract(**_valid_kwargs(margin_policy="isolated"))
    check("rejects unknown margin_policy", False)
except (ContractError, ValueError):
    check("rejects unknown margin_policy", True)

print("\n=== Test: pass_threshold must be in (0, 1] ===")

try:
    BenchmarkContract(**_valid_kwargs(pass_threshold=0))
    check("rejects zero pass_threshold", False)
except (ContractError, ValueError):
    check("rejects zero pass_threshold", True)

try:
    BenchmarkContract(**_valid_kwargs(pass_threshold=1.5))
    check("rejects pass_threshold > 1", False)
except (ContractError, ValueError):
    check("rejects pass_threshold > 1", True)

print("\n=== Test: cost_model hash is computed ===")

c = BenchmarkContract(**_valid_kwargs())
check("cost_model_hash is 64 hex chars",
      len(c.cost_model_hash) == 64 and all(ch in "0123456789abcdef" for ch in c.cost_model_hash))
check("cost_model_hash differs for different tiers",
      BenchmarkContract(**_valid_kwargs(cost_model={"fee_per_side": 0.001,
        "slip_tiers": [[30e6, 0.00015]], "min_qv24": 1e6})).cost_model_hash
      != c.cost_model_hash)


print("\n=== Test: R2-3 slip tier slip values must be strictly positive ===")


def _kwargs_with_slip_value(slip):
    """Fresh valid kwargs with the first tier's slip value replaced."""
    kw = _valid_kwargs()
    kw["cost_model"]["slip_tiers"][0][1] = slip
    return kw


_err = None
try:
    BenchmarkContract(**_kwargs_with_slip_value(0.0))
    check("rejects zero slip tier slip value", False, "no error raised")
except ContractError as e:
    check("rejects zero slip tier slip value", True)
    _err = e
check("zero slip error names the tier slip field",
      _err is not None and "slip_tier_slip" in str(_err), f"err={_err!r}")

_err = None
try:
    BenchmarkContract(**_kwargs_with_slip_value(-0.0001))
    check("rejects negative slip tier slip value", False, "no error raised")
except ContractError as e:
    check("rejects negative slip tier slip value", True)
    _err = e
check("negative slip error names the tier slip field",
      _err is not None and "slip_tier_slip" in str(_err), f"err={_err!r}")

# A valid positive slip value is still accepted.
try:
    BenchmarkContract(**_kwargs_with_slip_value(0.0001))
    check("accepts positive slip tier slip value", True)
except ContractError as e:
    check("accepts positive slip tier slip value", False, f"unexpected error: {e}")

print("\n=== Test: R2-3 trigger_threshold_tau must be strictly positive ===")

_err = None
try:
    BenchmarkContract(**_valid_kwargs(trigger_threshold_tau=0))
    check("rejects zero trigger_threshold_tau", False, "no error raised")
except ContractError as e:
    check("rejects zero trigger_threshold_tau", True)
    _err = e
check("zero tau error names trigger_threshold_tau",
      _err is not None and "trigger_threshold_tau" in str(_err), f"err={_err!r}")

_err = None
try:
    BenchmarkContract(**_valid_kwargs(trigger_threshold_tau=-0.5))
    check("rejects negative trigger_threshold_tau", False, "no error raised")
except ContractError as e:
    check("rejects negative trigger_threshold_tau", True)
    _err = e
check("negative tau error names trigger_threshold_tau",
      _err is not None and "trigger_threshold_tau" in str(_err), f"err={_err!r}")

# Positive taus are accepted; no upper bound (score scale is a Phase 5 decision).
for _tau in (0.5, 2.0):
    try:
        BenchmarkContract(**_valid_kwargs(trigger_threshold_tau=_tau))
        check(f"accepts trigger_threshold_tau={_tau}", True)
    except ContractError as e:
        check(f"accepts trigger_threshold_tau={_tau}", False, f"unexpected error: {e}")

print("\n=== Test: decision_grid_bars required and validated ===")

# decision_grid_bars is required (no default in required-fields sense)
try:
    BenchmarkContract(**_valid_kwargs())
    # Should succeed since we added it to defaults
    check("decision_grid_bars present by default", True)
except ContractError:
    check("decision_grid_bars present by default", False, "unexpected error")

# Non-positive decision_grid_bars rejected
try:
    BenchmarkContract(**_valid_kwargs(decision_grid_bars=0))
    check("rejects zero decision_grid_bars", False)
except (ContractError, ValueError):
    check("rejects zero decision_grid_bars", True)

try:
    BenchmarkContract(**_valid_kwargs(decision_grid_bars=-5))
    check("rejects negative decision_grid_bars", False)
except (ContractError, ValueError):
    check("rejects negative decision_grid_bars", True)

try:
    BenchmarkContract(**_valid_kwargs(decision_grid_bars="twelve"))
    check("rejects string decision_grid_bars", False)
except (ContractError, ValueError, TypeError):
    check("rejects string decision_grid_bars", True)

print("\n=== Test: stage_sizes required and validated ===")

# stage_sizes must be exactly (20, 50, 100) per plan §4
try:
    BenchmarkContract(**_valid_kwargs())
    check("stage_sizes defaults to (20,50,100)", True)
except ContractError:
    check("stage_sizes defaults to (20,50,100)", False, "unexpected error")

# Wrong tuple rejected
try:
    BenchmarkContract(**_valid_kwargs(stage_sizes=(10, 30)))
    check("rejects wrong stage_sizes", False)
except ContractError:
    check("rejects wrong stage_sizes", True)

# Stage sizes must be strictly increasing positive integers
try:
    BenchmarkContract(**_valid_kwargs(stage_sizes=(100, 50, 20)))
    check("rejects non-increasing stage_sizes", False)
except ContractError:
    check("rejects non-increasing stage_sizes", True)

# Non-integer values rejected
try:
    BenchmarkContract(**_valid_kwargs(stage_sizes=(20.0, 50, 100)))
    check("rejects float stage_sizes", False)
except ContractError:
    check("rejects float stage_sizes", True)

# stage_sizes not a tuple/list rejected
try:
    BenchmarkContract(**_valid_kwargs(stage_sizes=20))
    check("rejects non-tuple stage_sizes", False)
except ContractError:
    check("rejects non-tuple stage_sizes", True)

print("\n=== Test: load requires _sha256 ===")

# Save a valid contract
c_for_hash_test = BenchmarkContract(**_valid_kwargs())
path_valid = os.path.join(tmpdir, "valid_hash.json")
c_for_hash_test.save_once(path_valid)

# Remove the _sha256 field from a saved file — load must reject
with open(path_valid, "r") as fh:
    data_no_hash = json.load(fh)
del data_no_hash["_sha256"]
path_no_hash = os.path.join(tmpdir, "no_hash.json")
with open(path_no_hash, "w") as fh:
    json.dump(data_no_hash, fh)

try:
    BenchmarkContract.load(path_no_hash)
    check("load rejects file missing _sha256", False, "no error raised")
except ContractError:
    check("load rejects file missing _sha256", True)

# _sha256 present but None
data_null_hash = json.loads(json.dumps(data_no_hash))
data_null_hash["_sha256"] = None
path_null_hash = os.path.join(tmpdir, "null_hash.json")
with open(path_null_hash, "w") as fh:
    json.dump(data_null_hash, fh)

try:
    BenchmarkContract.load(path_null_hash)
    check("load rejects file with null _sha256", False, "no error raised")
except ContractError:
    check("load rejects file with null _sha256", True)

print("\n=== Test: cost_model is immutable after construction ===")

c_test = BenchmarkContract(**_valid_kwargs())

# Attempt to mutate the underlying dict — should fail
try:
    c_test.cost_model["fee_per_side"] = 0.999
    check("cost_model dict is immutable", False, "mutation succeeded")
except TypeError:
    check("cost_model dict is immutable", True)
except Exception as e:
    check("cost_model dict is immutable", False, f"unexpected error type: {type(e).__name__}")

# Attempt to add a new key — should fail
try:
    c_test.cost_model["injected_key"] = 123
    check("cost_model rejects new keys", False, "mutation succeeded")
except TypeError:
    check("cost_model rejects new keys", True)
except Exception as e:
    check("cost_model rejects new keys", False, f"unexpected error type: {type(e).__name__}")


print("\n=== Test: cost_model is deep-frozen (nested slip_tiers) ===")

c_deep = BenchmarkContract(**_valid_kwargs())

# Top-level reassignment through the proxy was already refused.
try:
    c_deep.cost_model["slip_tiers"] = [[1e6, 0.5]]
    check("cost_model slip_tiers reassignment refused", False, "mutation succeeded")
except TypeError:
    check("cost_model slip_tiers reassignment refused", True)
except Exception as e:
    check("cost_model slip_tiers reassignment refused", False,
          f"unexpected error type: {type(e).__name__}")

# Nested tier list must be immutable too (deep freeze).
try:
    c_deep.cost_model["slip_tiers"][0] = [99e6, 0.999]
    check("cost_model slip_tiers[0] reassignment refused", False, "mutation succeeded")
except TypeError:
    check("cost_model slip_tiers[0] reassignment refused", True)
except Exception as e:
    check("cost_model slip_tiers[0] reassignment refused", False,
          f"unexpected error type: {type(e).__name__}")

# Inner tier pair must be immutable too (deep freeze).
try:
    c_deep.cost_model["slip_tiers"][0][0] = 1.0
    check("cost_model slip_tiers[0][0] reassignment refused", False, "mutation succeeded")
except TypeError:
    check("cost_model slip_tiers[0][0] reassignment refused", True)
except Exception as e:
    check("cost_model slip_tiers[0][0] reassignment refused", False,
          f"unexpected error type: {type(e).__name__}")

print("\n=== Test: hash coverage over all hashed fields ===")

c_cov = BenchmarkContract(**_valid_kwargs())

# cost_model_hash must equal the sha256 of the canonical JSON of the
# ORIGINAL plain dict (freezing slip_tiers must not change it).
_orig_cm = _valid_kwargs()["cost_model"]
_expected_cm_hash = hashlib.sha256(json.dumps(
    _orig_cm, sort_keys=True, separators=(",", ":"),
    allow_nan=False, ensure_ascii=False).encode("utf-8")).hexdigest()
check("cost_model_hash equals sha256 of canonical original plain dict",
      c_cov.cost_model_hash == _expected_cm_hash,
      f"got {c_cov.cost_model_hash}, expected {_expected_cm_hash}")

# decision_grid_bars feeds the contract hash.
c_grid24 = BenchmarkContract(**_valid_kwargs(decision_grid_bars=24))
check("sha256 changes when decision_grid_bars changes 12 -> 24",
      c_cov.sha256() != c_grid24.sha256())

cj = c_cov.canonical_json()
check("canonical_json contains decision_grid_bars", "decision_grid_bars" in cj)
check("canonical_json contains stage_sizes", "stage_sizes" in cj)

# List/tuple canonical equivalence: a contract built from kwargs (lists) and
# one rebuilt via from_dict(json.loads(canonical_json)) (lists again, after
# tuples serialized as arrays) must share the same sha256.
c_rt = BenchmarkContract.from_dict(json.loads(c_cov.canonical_json()))
check("sha256 identical for kwargs-built vs from_dict(json.loads(canonical_json))",
      c_cov.sha256() == c_rt.sha256())

# save_once -> load round trip: file stores slip_tiers as JSON lists,
# loaded contract freezes them to tuples — sha256 must still match.
path_rt = os.path.join(tmpdir, "roundtrip_frozen.json")
c_cov.save_once(path_rt)
check("save_once/load round trip preserves sha256 with frozen cost_model",
      BenchmarkContract.load(path_rt).sha256() == c_cov.sha256())


print(f"\n=== {'ALL PASS' if not FAILURES else 'FAIL: ' + str(len(FAILURES)) + ' failures'}: {len(FAILURES)} failures ===")
if FAILURES:
    print("Failures:", FAILURES)
    sys.exit(1)
print("All tests passed.")
sys.exit(0)
