"""Tests for proto.engine CLI --out path protection (in-process).

The --out argument must be validated BEFORE any data access or file open, and
must reject paths that fall under protected data directories (data/bars,
data/prices, data/funding*, data/research/) or that name protected database
files (signals.db, quant.db — sidecar prefixes included — and the research DB
name altradar-research.db).

Tests run `proto.engine.__main__.main(argv)` IN PROCESS, capturing output with
io.StringIO + contextlib.redirect_stdout/redirect_stderr: `sys.executable` is
polluted in this environment (it reports the ZCode AppImage), so tests must
never spawn subprocesses.  All fixtures are tiny synthetic temp trees — the
real data/ tree is never touched.
"""
import contextlib
import gzip
import io
import json
import os
import sys
import tempfile
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

FAILURES = []


def check(name, cond, detail=""):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)


from proto.engine import __main__ as engine_main
from proto.research import store


def _run_cli(argv):
    """Run main(argv) in-process.  Returns (returncode, stdout, stderr); an
    uncaught exception is recorded as returncode None with its traceback in
    stderr (a refusal path must return cleanly, never raise)."""
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = engine_main.main(argv)
    except SystemExit as exc:  # argparse errors etc.
        err.write(f"SystemExit: {exc.code}\n")
        return exc.code if isinstance(exc.code, int) else None, out.getvalue(), err.getvalue()
    except Exception:
        err.write(traceback.format_exc())
        return None, out.getvalue(), err.getvalue()
    return code, out.getvalue(), err.getvalue()


def _write_fixture_bars(bars_dir, coins=("BTC", "ETH", "SOL")):
    """Tiny synthetic bar archive: 3 coins x 300 bars (n=0 rows are fine)."""
    os.makedirs(bars_dir, exist_ok=True)
    header = "ts,o,h,l,c,vol,amount\n"
    start = 1700000000
    step = 300  # BAR_S
    for coin in coins:
        rows = [header]
        price = 50000.0 if coin == "BTC" else 100.0
        for i in range(300):
            ts = start + i * step
            rows.append(f"{ts},{price},{price*1.001},{price*0.999},{price},"
                        f"100,{price * 100}\n")
        path = os.path.join(bars_dir, f"{coin}.csv.gz")
        with gzip.open(path, "wt") as fh:
            fh.write("".join(rows))


def _baseline_argv(data_dir, out_path):
    return ["baseline", "--data", data_dir, "--leverage", "10",
            "--horizon-days", "1", "--out", out_path]


def _refusal_case(name, argv, target):
    """One protected --out case: exit 1, refusal named in stderr, no stdout
    payload, and the target must NOT exist afterwards (no fixture mutation)."""
    code, out, err = _run_cli(argv)
    check(f"{name}: exits 1", code == 1,
          f"code={code}, stderr tail: {err.strip()[-300:]}")
    check(f"{name}: refusal named in stderr", "refusing" in err,
          f"stderr tail: {err.strip()[-300:]}")
    check(f"{name}: no stdout payload before refusal", out.strip() == "",
          f"stdout starts: {out.strip()[:120]}")
    check(f"{name}: target not created", not os.path.exists(target),
          f"{target} exists after the run")


print("=== Test: --out refused for protected paths (in-process) ===")

tmp = tempfile.mkdtemp(prefix="cli_test_")
try:
    data_root = os.path.join(tmp, "data")
    bars_dir = os.path.join(data_root, "bars")
    _write_fixture_bars(bars_dir)

    prices_dir = os.path.join(data_root, "prices")
    os.makedirs(prices_dir, exist_ok=True)
    funding_dir = os.path.join(data_root, "funding_2026")
    os.makedirs(funding_dir, exist_ok=True)

    # --out under data/bars
    target = os.path.join(bars_dir, "malicious.json")
    _refusal_case("--out under data/bars", _baseline_argv(bars_dir, target), target)

    # --out under data/prices
    target = os.path.join(prices_dir, "output.json")
    _refusal_case("--out under data/prices", _baseline_argv(bars_dir, target), target)

    # --out under data/funding_2026
    target = os.path.join(funding_dir, "output.json")
    _refusal_case("--out under data/funding_2026", _baseline_argv(bars_dir, target),
                  target)

    # --out named signals.db
    target = os.path.join(tmp, "signals.db")
    _refusal_case("--out named signals.db", _baseline_argv(bars_dir, target), target)

    # --out named quant.db
    target = os.path.join(tmp, "quant.db")
    _refusal_case("--out named quant.db", _baseline_argv(bars_dir, target), target)

    # --out named quant.db-wal (sidecar prefix)
    target = os.path.join(tmp, "quant.db-wal")
    _refusal_case("--out named quant.db-wal (sidecar prefix)",
                  _baseline_argv(bars_dir, target), target)

    # --out under data/research/ (the research DB directory)
    research_dir = os.path.join(data_root, "research")
    target = os.path.join(research_dir, "output.json")
    _refusal_case("--out under data/research/", _baseline_argv(bars_dir, target),
                  target)

    # --out named altradar-research.db (the research DB name)
    target = os.path.join(tmp, "altradar-research.db")
    _refusal_case("--out named altradar-research.db",
                  _baseline_argv(bars_dir, target), target)

    # --- R2-1: ANY --out destination under a data/ root is refused, at any
    # depth, even when no protected subdirectory (bars/prices/funding*/research)
    # is involved.  A path is "under a data root" when a component named
    # `data` is followed by at least one more component.
    target = os.path.join(data_root, "output.json")
    _refusal_case("--out directly under a data/ root (no protected subdir)",
                  _baseline_argv(bars_dir, target), target)

    # Ordering proof for the data-root refusal itself: same --out with a
    # NONEXISTENT --data dir must still return 1 with the --out refusal, NOT a
    # data error (the controller proved this exact case currently slips past
    # the check and proceeds to BTC access).
    missing_data = os.path.join(tmp, "no_such_bars")
    target = os.path.join(data_root, "output.json")
    code, out, err = _run_cli(_baseline_argv(missing_data, target))
    check("ordering (data root): protected --out + nonexistent --data exits 1",
          code == 1, f"code={code}, stderr tail: {err.strip()[-300:]}")
    check("ordering (data root): the refusal is the --out one, not a data error",
          "refusing" in err and "under the data root" in err and "BTC" not in err,
          f"stderr tail: {err.strip()[-300:]}")
    check("ordering (data root): nothing was created", not os.path.exists(target))

    # Deeper nesting under the data root is refused too.
    target = os.path.join(data_root, "sub", "dir", "out.json")
    _refusal_case("--out deeply nested under a data/ root",
                  _baseline_argv(bars_dir, target), target)

    # A symlink whose TARGET is under a data/ root is refused (realpath).
    sneak2 = os.path.join(tmp, "sneak2.json")
    if os.path.exists(sneak2):
        os.remove(sneak2)  # fixture hygiene in /tmp only
    os.symlink(os.path.join(data_root, "output.json"), sneak2)
    _refusal_case("--out symlink whose target is under a data/ root",
                  _baseline_argv(bars_dir, sneak2),
                  os.path.join(data_root, "output.json"))

    # A path merely NAMED `data` (last component, not inside a data dir) is
    # NOT under a data root: no data-root refusal may fire for it.  With a
    # nonexistent --data dir the run fails later on BTC — that is the expected
    # behavior; do NOT add a rejection for this shape.
    plain_parent = os.path.join(tmp, "plain")
    os.makedirs(plain_parent, exist_ok=True)
    target_named_data = os.path.join(plain_parent, "data")
    code, out, err = _run_cli(_baseline_argv(missing_data, target_named_data))
    check("a file merely named 'data' is not refused as a data root",
          code == 1 and "refusing" not in err and "BTC" in err,
          f"code={code}, stderr tail: {err.strip()[-300:]}")
    check("file named 'data' was not created", not os.path.exists(target_named_data))

    # --- ordering proof: the --out check precedes ANY data access.  A
    # protected --out together with a NONEXISTENT --data directory must still
    # return 1 with the --out refusal (not a data error), creating nothing.
    target = os.path.join(tmp, "quant.db")
    code, out, err = _run_cli(_baseline_argv(missing_data, target))
    check("ordering: protected --out + nonexistent --data exits 1", code == 1,
          f"code={code}, stderr tail: {err.strip()[-300:]}")
    check("ordering: the refusal is the --out one, not a data error",
          "refusing" in err and "BTC" not in err,
          f"stderr tail: {err.strip()[-300:]}")
    check("ordering: nothing was created", not os.path.exists(target))

    # --- symlink whose target is inside data/bars is refused (the guard
    # realpaths before checking).
    symlink_path = os.path.join(tmp, "sneak.json")
    symlink_target = os.path.join(bars_dir, "inside.json")
    if os.path.exists(symlink_path):
        os.remove(symlink_path)  # fixture hygiene in /tmp only
    os.symlink(symlink_target, symlink_path)
    _refusal_case("--out symlink into data/bars", _baseline_argv(bars_dir, symlink_path),
                  symlink_target)

    # --- safe path: outside any data/ tree the run succeeds and writes the
    # JSON; nothing appears under the fixture data/ tree except its own bars
    # files.
    safe_out = os.path.join(tmp, "safe_output.json")
    before = set()
    for dirpath, _dirs, files in os.walk(data_root):
        for f in files:
            before.add(os.path.join(dirpath, f))

    code, out, err = _run_cli(_baseline_argv(bars_dir, safe_out))
    check("safe path: exits 0", code == 0,
          f"code={code}, stderr tail: {err.strip()[-300:]}")
    check("safe path: JSON file exists", os.path.exists(safe_out))
    if os.path.exists(safe_out):
        with open(safe_out) as fh:
            payload = json.load(fh)
        check("safe path: JSON parses, command=baseline",
              payload.get("command") == "baseline")
        check("safe path: result records the pre-holdout window",
              payload.get("window") == "pre-holdout")
        check("safe path: result carries window_bounds dict",
              isinstance(payload.get("window_bounds"), dict))
        check("safe path: result carries the sampling label",
              "stride-grid" in str(payload.get("sampling", "")),
              f"sampling={payload.get('sampling')!r}")
        check("safe path: result carries horizons_bars",
              payload.get("horizons_bars") == [288],
              f"got {payload.get('horizons_bars')!r}")
    after = set()
    for dirpath, _dirs, files in os.walk(data_root):
        for f in files:
            after.add(os.path.join(dirpath, f))
    check("safe path: fixture data/ tree unchanged (only its own bars files)",
          before == after,
          f"created: {sorted(after - before)[:5]}, removed: {sorted(before - after)[:5]}")
    check("safe path: table shows the stride-grid title (not 'random-entry')",
          "deterministic stride-grid entries" in err and "random-entry" not in err,
          f"stderr head: {err.strip()[:200]}")
    check("safe path: stale 'assumed round-trip' cost line is gone",
          "assumed round-trip" not in err)
    check("safe path: table shows the pre-holdout window line with the "
          "short-archive note", "holdout starts day 79" in err
          and "no holdout bars exist yet" in err,
          f"stderr head: {err.strip()[:400]}")

    print("\n=== Test: CLI defaults and help text ===")

    def _capture_help(argv):
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                engine_main.main(argv)
        except SystemExit:
            pass
        return out.getvalue() + err.getvalue()

    help_text = _capture_help(["baseline", "--help"])
    check("baseline help describes deterministic stride-grid calibration",
          "deterministic stride-grid baseline calibration" in help_text.lower(),
          help_text[:200])
    check("--stride help mentions the global grid phase",
          "global grid phase" in help_text or "global grid" in help_text)
    check("--max-coins help documents the full eligible universe default",
          "full eligible universe" in help_text)
    check("--window help marks full as including the sealed holdout",
          "sealed holdout" in help_text)
    check("baseline description states the default window ends before the "
          "holdout (--window full is calibration only)",
          "default window ends before the holdout"
          in " ".join(help_text.lower().split()),
          help_text[:300])
    check("--data help does not contain the bare 'data/bars' substring",
          "data/bars" not in help_text,
          [ln for ln in help_text.splitlines() if "data/bars" in ln][:2])

    args = engine_main._parser().parse_args(
        ["baseline", "--data", "x"])
    check("--max-coins default is None (no cap)", args.max_coins is None,
          f"got {args.max_coins!r}")
    check("--window default remains pre-holdout", args.window == "pre-holdout",
          f"got {args.window!r}")

finally:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)


print(f"\n=== {'ALL PASS' if not FAILURES else 'FAIL: ' + str(len(FAILURES)) + ' failures'}: {len(FAILURES)} failures ===")
if FAILURES:
    print("Failures:", FAILURES)
    sys.exit(1)
print("All tests passed.")
sys.exit(0)
