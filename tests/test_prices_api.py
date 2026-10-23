"""WS4: GET /prices read route over the mark/index price archive.

Self-checking script (stdlib only). Talks to 127.0.0.1 ONLY: an egress
tripwire wraps socket connects and fails any connect outside loopback.

Fixtures use seeded tmp price/mark + price/index dirs laid out as default
siblings of the tmp bars data dir (no production paths). Rows are written
via the REAL writer (proto.prices.append_prices) — never hand-built bytes.

Run:  python3 tests/test_prices_api.py   -> ALL PASS, exit 0
"""

import gzip
import http.client
import json
import os
import shutil
import socket
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


class NetworkTripwire(AssertionError):
    """A test tried to connect outside loopback — instant failure."""


_RealSocket = socket.socket


class _GuardedSocket(_RealSocket):
    def connect(self, address, *args, **kw):
        host = address[0] if isinstance(address, tuple) else address
        if host not in LOOPBACK_HOSTS:
            raise NetworkTripwire(
                f"NETWORK TRIPWIRE: connect to non-loopback {host!r} blocked")
        return super().connect(address, *args, **kw)


_RealCreateConnection = socket.create_connection


def _guarded_create_connection(address, *args, **kw):
    host = address[0] if isinstance(address, tuple) else address
    if host not in LOOPBACK_HOSTS:
        raise NetworkTripwire(
            f"NETWORK TRIPWIRE: create_connection to non-loopback {host!r}")
    return _RealCreateConnection(address, *args, **kw)


socket.socket = _GuardedSocket
socket.create_connection = _guarded_create_connection

FAILURES = []
CHECKS = 0


def check(name, cond, detail=""):
    global CHECKS
    CHECKS += 1
    cond = bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}"
          + ("" if cond else f"  {detail}"))
    if not cond:
        FAILURES.append(name)
    return cond


TOKEN = "bearer-secret-prices-route"

BIG_COIN = "BIG"
BIG_N = 1205
BIG_STEP = 3600  # hourly grid
BIG_BASE = 1790000000 - (1790000000 % 3600)
EMPTY_COIN = "EMPTYHDR"
CORRUPT_COIN = "CORRUPT"
NEVER_COIN = "NEVERARCHIVED"


def mark_row(ts):
    return {"ts": ts, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5}


def index_row(ts):
    return {"ts": ts, "o": 200.0, "h": 201.0, "l": 199.0, "c": 200.5}


def get(port, path, token=TOKEN):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    return resp.status, payload


def snapshot_tree(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            out.append(os.path.relpath(os.path.join(dirpath, name), root))
    return out


def check_envelope(name, body, ok):
    if not isinstance(body, dict):
        return check(name, False, f"not a dict: {body!r}"[:160])
    if ok:
        return check(name, body.get("ok") is True
                     and isinstance(body.get("data"), dict)
                     and body.get("error") is None, str(body)[:160])
    err = body.get("error")
    return check(name, body.get("ok") is False
                 and body.get("data") is None
                 and isinstance(err, dict)
                 and isinstance(err.get("code"), str)
                 and isinstance(err.get("message"), str), str(body)[:160])


def main():
    from proto import service as svc
    from proto import funding as fmod
    from proto import settlements as smod
    from proto import prices as pmod
    from proto import collection

    tmp = tempfile.mkdtemp(prefix="svc-prices-route-")
    try:
        db_path = os.path.join(tmp, "signals.db")
        bars_dir = os.path.join(tmp, "bars")
        os.makedirs(bars_dir, exist_ok=True)
        prices_root = os.path.join(os.path.dirname(bars_dir), "prices")
        funding_dir = os.path.join(os.path.dirname(bars_dir), "funding")
        st_dir = os.path.join(os.path.dirname(bars_dir),
                              "funding_settlements")
        os.makedirs(funding_dir, exist_ok=True)
        os.makedirs(st_dir, exist_ok=True)

        # -- fixture: 1205-row mark + index coins via the REAL writer -----
        big_mark = [mark_row(BIG_BASE + i * BIG_STEP) for i in range(BIG_N)]
        big_index = [index_row(BIG_BASE + i * BIG_STEP) for i in range(BIG_N)]
        check("fixture writes all 1205 mark rows via the real writer",
              pmod.append_prices(prices_root, "mark", BIG_COIN,
                                 big_mark) == BIG_N)
        check("fixture writes all 1205 index rows via the real writer",
              pmod.append_prices(prices_root, "index", BIG_COIN,
                                 big_index) == BIG_N)
        archived = pmod.read_prices(prices_root, "mark", BIG_COIN)
        check("fixture reads back 1205 mark rows",
              len(archived) == BIG_N, str(len(archived)))

        # -- fixture: header-only coin (zero data rows) -------------------
        os.makedirs(os.path.join(prices_root, "mark"), exist_ok=True)
        with gzip.open(os.path.join(prices_root, "mark",
                                    f"{EMPTY_COIN}.csv.gz"),
                       "wt", newline="") as f:
            f.write("ts,o,h,l,c\n")

        # -- fixture: corrupt gzip (present but unreadable) ---------------
        with open(os.path.join(prices_root, "mark", f"{CORRUPT_COIN}.csv.gz"),
                  "wb") as f:
            f.write(b"not a gzip at all")

        # -- separation fixtures: same coin name in other archives ---------
        fmod.append_funding(funding_dir, BIG_COIN,
                            [{"ts": 1000, "rate": 0.5, "cap": 0.002}])
        smod.append_settlements(st_dir, BIG_COIN,
                                [{"settle_ts": 28800, "settled_rate": 0.0001,
                                  "cycle_hours": 8}])

        server = svc.serve(0, TOKEN, collection.CollectionState(
                               bars_dir, funding_dir, st_dir, prices_root,
                               300), data_dir=bars_dir)
        port = server.server_address[1]
        time.sleep(0.15)
        try:
            # -- auth gate --------------------------------------------------
            st, body = get(port, f"/prices?series=mark&coin={BIG_COIN}&limit=2",
                           token=None)
            check("prices without token -> 401", st == 401, f"got {st}")
            check_envelope("prices 401 envelope", body, False)
            st, body = get(port, f"/prices?series=mark&coin={BIG_COIN}&limit=2",
                           token="wrong-token")
            check("prices wrong token -> 401", st == 401, f"got {st}")

            # -- 200 shape, order, count, data_as_of == last_ts ------------
            st, body = get(port, f"/prices?series=mark&coin={BIG_COIN}&limit=2")
            check("prices limit=2 -> 200", st == 200, f"got {st}")
            check_envelope("prices 200 envelope", body, True)
            data = (body.get("data") or {}) if isinstance(body, dict) else {}
            check("prices data has exactly the contract keys",
                  set(data) == {"series", "coin", "rows", "count",
                                "data_as_of", "last_ts"},
                  str(sorted(data))[:160])
            check("prices echoes the requested series",
                  data.get("series") == "mark" and data.get("coin") == BIG_COIN,
                  str({k: data.get(k) for k in ("series", "coin")}))
            rows = data.get("rows") or []
            check("price rows carry exactly {ts, o, h, l, c}",
                  all(set(r) == {"ts", "o", "h", "l", "c"} for r in rows),
                  str(rows)[:160])
            check("price rows ascend by ts",
                  all(b["ts"] < a["ts"] for b, a in zip(rows, rows[1:])),
                  str([r["ts"] for r in rows])[:160])
            check("prices count == len(rows)",
                  data.get("count") == len(rows) == 2, str(data)[:160])
            check("prices data_as_of == last_ts == last archived ts",
                  data.get("data_as_of") == data.get("last_ts")
                  == archived[-1]["ts"],
                  str({k: data.get(k) for k in ("data_as_of", "last_ts")}))
            check("price values are venue values, never zero-filled",
                  all(r["o"] == 100.0 and r["c"] == 100.5 for r in rows),
                  str(rows)[:160])

            # -- real default-1000 slice over the 1205-row coin ------------
            st, body = get(port, f"/prices?series=mark&coin={BIG_COIN}")
            data = (body.get("data") or {}) if isinstance(body, dict) else {}
            rows = data.get("rows") or []
            check("default limit returns exactly the last 1000 rows",
                  st == 200 and data.get("count") == 1000
                  and rows[0]["ts"] == archived[205]["ts"]
                  and rows[-1]["ts"] == archived[-1]["ts"],
                  f"{st} {data.get('count')}")

            # -- mark vs index separation -----------------------------------
            st, body = get(port, f"/prices?series=index&coin={BIG_COIN}&limit=2")
            idata = (body.get("data") or {}) if isinstance(body, dict) else {}
            irows = idata.get("rows") or []
            check("index series reads its own archive, not mark",
                  st == 200 and idata.get("series") == "index"
                  and all(r["o"] == 200.0 for r in irows),
                  f"{st} {str(irows)[:160]}")

            # -- series validation -------------------------------------------
            st, body = get(port, f"/prices?coin={BIG_COIN}")
            check("prices missing series -> 400", st == 400, f"got {st}")
            check_envelope("prices missing-series envelope", body, False)
            st, body = get(port, "/prices?series=")
            check("prices empty series -> 400", st == 400, f"got {st}")
            st, body = get(port, f"/prices?series=bogus&coin={BIG_COIN}")
            check("prices invalid series -> 400", st == 400, f"got {st}")
            check_envelope("prices invalid-series envelope", body, False)

            # -- limit validation: reject, never clamp ----------------------
            for bad, why in (("0", "zero"), ("-5", "negative"),
                             ("5001", "over max"), ("abc", "non-integer")):
                st, body = get(port,
                               f"/prices?series=mark&coin={BIG_COIN}"
                               f"&limit={bad}")
                check(f"prices limit={bad} -> 400 ({why})",
                      st == 400, f"got {st}")
                check_envelope(f"prices limit={bad} envelope", body, False)

            # -- coin validation ---------------------------------------------
            st, body = get(port, "/prices?series=mark")
            check("prices missing coin -> 400", st == 400, f"got {st}")
            check_envelope("prices missing-coin envelope", body, False)
            st, body = get(port, "/prices?series=mark&coin=")
            check("prices empty coin -> 400", st == 400, f"got {st}")
            st, body = get(port, f"/prices?series=mark&coin={NEVER_COIN}")
            check("prices unarchived coin -> 404", st == 404, f"got {st}")
            check_envelope("prices 404 envelope", body, False)

            # -- corrupt archive -> 503 ---------------------------------------
            st, body = get(port, f"/prices?series=mark&coin={CORRUPT_COIN}")
            check("prices corrupt file -> 503", st == 503, f"got {st}")
            check_envelope("prices 503 envelope", body, False)

            # -- empty archive -> 200 with explicit nulls ----------------------
            st, body = get(port, f"/prices?series=mark&coin={EMPTY_COIN}")
            data = (body.get("data") or {}) if isinstance(body, dict) else {}
            check("prices empty archive -> 200 explicit emptiness",
                  st == 200 and data.get("rows") == []
                  and data.get("count") == 0
                  and data.get("data_as_of") is None
                  and data.get("last_ts") is None,
                  f"{st} {str(data)[:160]}")

            # -- traversal + filesystem tripwire ---------------------------------
            before = snapshot_tree(tmp)
            for evil in ("..", "a/b", "%2e%2e", "%2f", "..%2f..%2fetc"):
                st, body = get(port, f"/prices?series=mark&coin={evil}")
                check(f"prices traversal {evil!r} -> 400",
                      st == 400, f"got {st}")
                check_envelope(f"prices traversal {evil!r} envelope",
                               body, False)
            check("filesystem tripwire: tmp tree unchanged",
                  snapshot_tree(tmp) == before,
                  str(snapshot_tree(tmp))[:200])

            # -- separation from /funding and /settlements ----------------------
            st, body = get(port, f"/funding?coin={BIG_COIN}")
            fdata = (body.get("data") or {}) if isinstance(body, dict) else {}
            frows = fdata.get("rows") or []
            check("funding route reads its own archive, not prices",
                  st == 200 and len(frows) == 1
                  and frows[0]["ts"] == 1000
                  and set(frows[0]) == {"ts", "rate", "cap"},
                  f"{st} {str(frows)[:160]}")
            st, body = get(port, f"/settlements?coin={BIG_COIN}")
            sdata = (body.get("data") or {}) if isinstance(body, dict) else {}
            srows = sdata.get("rows") or []
            check("settlements route reads its own archive, not prices",
                  st == 200 and len(srows) == 1
                  and set(srows[0]) == {"settle_ts", "settled_rate",
                                        "cycle_hours"},
                  f"{st} {str(srows)[:160]}")
            st, body = get(port, f"/prices?series=mark&coin={BIG_COIN}&limit=1")
            pdata = (body.get("data") or {}) if isinstance(body, dict) else {}
            prows = pdata.get("rows") or []
            check("prices route reads its own archive, not funding/settlements",
                  st == 200 and len(prows) == 1
                  and set(prows[0]) == {"ts", "o", "h", "l", "c"},
                  f"{st} {str(prows)[:160]}")
        finally:
            server.shutdown()
            server.server_close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"FAIL  tests/test_prices_api.py  "
              f"({len(FAILURES)}/{CHECKS} failed: {FAILURES})")
        sys.exit(1)
    print(f"ALL PASS  tests/test_prices_api.py  ({CHECKS} checks)")


if __name__ == "__main__":
    main()
