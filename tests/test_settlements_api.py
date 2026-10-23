"""WS4: GET /settlements read route over the settlement archive.

Self-checking script (stdlib only). Talks to 127.0.0.1 ONLY: an egress
tripwire wraps socket connects and fails any connect outside loopback.

Fixtures use seeded tmp settlement + funding dirs laid out as default
siblings of the tmp bars data dir (no production paths). The 1205-row
coin is written via the REAL settlement writer
(proto.settlements.append_settlements) — never hand-built rows.

Run:  python3 tests/test_settlements_api.py   -> ALL PASS, exit 0
"""

import gzip
import http.client
import json
import os
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


TOKEN = "bearer-secret-settlements-route"

BIG_COIN = "BIG"
BIG_N = 1205
BIG_BASE = 1790000000 - (1790000000 % 28800)
BIG_STEP = 28800  # venue 8h settle grid
EMPTY_COIN = "EMPTYHDR"
CORRUPT_COIN = "CORRUPT"
NEVER_COIN = "NEVERARCHIVED"


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
    from proto import collection

    tmp = tempfile.mkdtemp(prefix="svc-settlements-route-")
    try:
        db_path = os.path.join(tmp, "signals.db")
        bars_dir = os.path.join(tmp, "bars")
        os.makedirs(bars_dir, exist_ok=True)
        st_dir = os.path.join(os.path.dirname(bars_dir),
                              "funding_settlements")
        os.makedirs(st_dir, exist_ok=True)
        funding_dir = os.path.join(os.path.dirname(bars_dir), "funding")
        os.makedirs(funding_dir, exist_ok=True)

        # -- fixture: 1205-row coin via the REAL settlement writer --------
        big_rows = [{"settle_ts": BIG_BASE + i * BIG_STEP,
                     "settled_rate": 0.0001 + i * 1e-8,
                     "cycle_hours": 8} for i in range(BIG_N)]
        check("fixture writes all 1205 rows via the real writer",
              smod.append_settlements(st_dir, BIG_COIN, big_rows) == BIG_N)
        archived = smod.read_settlements(st_dir, BIG_COIN)
        check("fixture reads back 1205 rows",
              len(archived) == BIG_N, str(len(archived)))

        # -- fixture: header-only coin (zero data rows) -------------------
        with gzip.open(os.path.join(st_dir, f"{EMPTY_COIN}.csv.gz"),
                       "wt", newline="") as f:
            f.write("settle_ts,settled_rate,cycle_hours\n")

        # -- fixture: corrupt gzip (present but unreadable) ---------------
        with open(os.path.join(st_dir, f"{CORRUPT_COIN}.csv.gz"),
                   "wb") as f:
            f.write(b"not a gzip at all")

        # -- separation fixture: same coin name, different archive --------
        fmod.append_funding(funding_dir, BIG_COIN,
                            [{"ts": 1000, "rate": 0.5, "cap": 0.002}])

        server = svc.serve(0, TOKEN, collection.CollectionState(
                               bars_dir, funding_dir, st_dir,
                               os.path.join(tmp, "prices"), 300),
                           data_dir=bars_dir, settlements_dir=st_dir)
        port = server.server_address[1]
        time.sleep(0.15)
        try:
            # -- auth gate --------------------------------------------------
            st, body = get(port, f"/settlements?coin={BIG_COIN}&limit=2",
                           token=None)
            check("settlements without token -> 401", st == 401, f"got {st}")
            check_envelope("settlements 401 envelope", body, False)
            st, body = get(port, f"/settlements?coin={BIG_COIN}&limit=2",
                           token="wrong-token")
            check("settlements wrong token -> 401", st == 401, f"got {st}")

            # -- 200 shape, order, count, data_as_of == last_ts ------------
            st, body = get(port, f"/settlements?coin={BIG_COIN}&limit=2")
            check("settlements limit=2 -> 200", st == 200, f"got {st}")
            check_envelope("settlements 200 envelope", body, True)
            data = (body.get("data") or {}) if isinstance(body, dict) else {}
            check("settlements data has exactly the contract keys",
                  set(data) == {"coin", "rows", "count",
                                "data_as_of", "last_ts"},
                  str(sorted(data))[:160])
            rows = data.get("rows") or []
            check("settlement rows carry exactly "
                  "{settle_ts, settled_rate, cycle_hours}",
                  all(set(r) == {"settle_ts", "settled_rate", "cycle_hours"}
                      for r in rows),
                  str(rows)[:160])
            check("settlement rows ascend by settle_ts",
                  all(b["settle_ts"] < a["settle_ts"]
                      for b, a in zip(rows, rows[1:])),
                  str([r["settle_ts"] for r in rows])[:160])
            check("settlements count == len(rows)",
                  data.get("count") == len(rows) == 2, str(data)[:160])
            check("settlements data_as_of == last_ts == last archived ts",
                  data.get("data_as_of") == data.get("last_ts")
                  == archived[-1]["settle_ts"],
                  str({k: data.get(k) for k in ("data_as_of", "last_ts")}))
            check("settled rates are venue values, never zero-filled",
                  all(r["settled_rate"] != 0 for r in rows),
                  str(rows)[:160])

            # -- real default-1000 slice over the 1205-row coin ------------
            st, body = get(port, f"/settlements?coin={BIG_COIN}")
            data = (body.get("data") or {}) if isinstance(body, dict) else {}
            rows = data.get("rows") or []
            check("default limit returns exactly the last 1000 rows",
                  st == 200 and data.get("count") == 1000
                  and rows[0]["settle_ts"] == archived[205]["settle_ts"]
                  and rows[-1]["settle_ts"] == archived[-1]["settle_ts"],
                  f"{st} {data.get('count')}")

            # -- limit validation: reject, never clamp ----------------------
            for bad, why in (("0", "zero"), ("-5", "negative"),
                             ("5001", "over max"), ("abc", "non-integer")):
                st, body = get(port,
                               f"/settlements?coin={BIG_COIN}&limit={bad}")
                check(f"settlements limit={bad} -> 400 ({why})",
                      st == 400, f"got {st}")
                check_envelope(f"settlements limit={bad} envelope",
                               body, False)

            # -- coin validation ---------------------------------------------
            st, body = get(port, "/settlements")
            check("settlements missing coin -> 400", st == 400, f"got {st}")
            check_envelope("settlements missing-coin envelope", body, False)
            st, body = get(port, "/settlements?coin=")
            check("settlements empty coin -> 400", st == 400, f"got {st}")
            st, body = get(port, f"/settlements?coin={NEVER_COIN}")
            check("settlements unarchived coin -> 404", st == 404,
                  f"got {st}")
            check_envelope("settlements 404 envelope", body, False)

            # -- corrupt archive -> 503 ---------------------------------------
            st, body = get(port, f"/settlements?coin={CORRUPT_COIN}")
            check("settlements corrupt file -> 503", st == 503, f"got {st}")
            check_envelope("settlements 503 envelope", body, False)

            # -- empty archive -> 200 with explicit nulls ----------------------
            st, body = get(port, f"/settlements?coin={EMPTY_COIN}")
            data = (body.get("data") or {}) if isinstance(body, dict) else {}
            check("settlements empty archive -> 200 explicit emptiness",
                  st == 200 and data.get("rows") == []
                  and data.get("count") == 0
                  and data.get("data_as_of") is None
                  and data.get("last_ts") is None,
                  f"{st} {str(data)[:160]}")

            # -- traversal + filesystem tripwire ---------------------------------
            before = snapshot_tree(tmp)
            for evil in ("..", "a/b", "%2e%2e", "%2f", "..%2f..%2fetc"):
                st, body = get(port, f"/settlements?coin={evil}")
                check(f"settlements traversal {evil!r} -> 400",
                      st == 400, f"got {st}")
                check_envelope(f"settlements traversal {evil!r} envelope",
                               body, False)
            check("filesystem tripwire: tmp tree unchanged",
                  snapshot_tree(tmp) == before,
                  str(snapshot_tree(tmp))[:200])

            # -- separation from /funding ---------------------------------------
            st, body = get(port, f"/funding?coin={BIG_COIN}")
            fdata = (body.get("data") or {}) if isinstance(body, dict) else {}
            frows = fdata.get("rows") or []
            check("funding route reads its own archive, not settlements",
                  st == 200 and len(frows) == 1
                  and frows[0]["ts"] == 1000
                  and set(frows[0]) == {"ts", "rate", "cap"},
                  f"{st} {str(frows)[:160]}")
            st, body = get(port, f"/settlements?coin={BIG_COIN}&limit=1")
            sdata = (body.get("data") or {}) if isinstance(body, dict) else {}
            srows = sdata.get("rows") or []
            check("settlements route reads its own archive, not funding",
                  st == 200 and len(srows) == 1
                  and set(srows[0]) == {"settle_ts", "settled_rate",
                                        "cycle_hours"},
                  f"{st} {str(srows)[:160]}")
        finally:
            server.shutdown()
            server.server_close()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAILURES:
        print(f"FAIL  tests/test_settlements_api.py  "
              f"({len(FAILURES)}/{CHECKS} failed: {FAILURES})")
        sys.exit(1)
    print(f"ALL PASS  tests/test_settlements_api.py  ({CHECKS} checks)")


if __name__ == "__main__":
    main()
