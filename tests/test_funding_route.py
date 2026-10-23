"""WS4e: GET /funding read route over the observation archive.

Self-checking script (stdlib only). Talks to 127.0.0.1 ONLY: an egress
tripwire wraps socket connects and fails any connect outside loopback.

Fixtures use a seeded tmp funding dir laid out as the default sibling of
the tmp bars data dir (no production paths). The 1205-row coin is written
via the REAL funding writer (proto.funding.append_funding) — never
hand-built rows.

Run:  python3 tests/test_funding_route.py   -> ALL PASS, exit 0
"""

import csv
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

# ---- egress tripwire: loopback-only (allows 127.0.0.1 / localhost / ::1) --
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


TOKEN = "bearer-secret-funding-route"

BIG_COIN = "BIG"
BIG_N = 1205
BIG_BASE = 1700000000
BIG_STEP = 3600
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
    """Full relative file listing under root (tripwire evidence)."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            out.append(os.path.relpath(os.path.join(dirpath, name), root))
    return out


def check_envelope(name, body, ok):
    """The typed {ok, data, error} envelope holds on every path."""
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
    from proto import collection

    tmp = tempfile.mkdtemp(prefix="svc-funding-route-")
    try:
        db_path = os.path.join(tmp, "signals.db")
        bars_dir = os.path.join(tmp, "bars")
        os.makedirs(bars_dir, exist_ok=True)
        # Default sibling of the bars dir (what the route resolves when no
        # explicit funding dir is injected): data/funding beside data/bars.
        funding_dir = os.path.join(os.path.dirname(bars_dir), "funding")
        os.makedirs(funding_dir, exist_ok=True)

        # -- fixture: 1205-row coin via the REAL archive writer -------------
        big_rows = [{"ts": BIG_BASE + i * BIG_STEP,
                     "rate": 0.0001 + i * 1e-7,
                     "cap": 0.0018} for i in range(BIG_N)]
        check("fixture writes all 1205 rows via the real writer",
              fmod.append_funding(funding_dir, BIG_COIN, big_rows) == BIG_N)
        archived = fmod.read_funding(funding_dir, BIG_COIN)
        check("fixture reads back 1205 rows ascending",
              len(archived) == BIG_N
              and all(b["ts"] < a["ts"]
                      for b, a in zip(archived, archived[1:])),
              f"got {len(archived)}")

        # -- fixture: header-only coin (zero data rows, explicit empty) -----
        with gzip.open(os.path.join(funding_dir, f"{EMPTY_COIN}.csv.gz"),
                       "wt", newline="") as f:
            f.write("ts,rate,cap\n")

        # -- fixture: corrupt gzip (present but unreadable) -----------------
        with open(os.path.join(funding_dir, f"{CORRUPT_COIN}.csv.gz"),
                   "wb") as f:
            f.write(b"not a gzip at all")

        server = svc.serve(0, TOKEN, collection.CollectionState(
                               bars_dir, funding_dir,
                               os.path.join(tmp, "funding_settlements"),
                               os.path.join(tmp, "prices"), 300),
                           data_dir=bars_dir)
        port = server.server_address[1]
        time.sleep(0.15)
        try:
            # -- auth gate --------------------------------------------------
            st, body = get(port, f"/funding?coin={BIG_COIN}&limit=2",
                           token=None)
            check("funding without token -> 401", st == 401, f"got {st}")
            check_envelope("funding 401 envelope", body, False)
            st, body = get(port, f"/funding?coin={BIG_COIN}&limit=2",
                           token="wrong-token")
            check("funding wrong token -> 401", st == 401, f"got {st}")

            # -- 200 shape, order, count, data_as_of == last_ts ------------
            st, body = get(port, f"/funding?coin={BIG_COIN}&limit=2")
            check("funding limit=2 -> 200", st == 200, f"got {st}")
            check_envelope("funding 200 envelope", body, True)
            data = (body.get("data") or {}) if isinstance(body, dict) else {}
            check("funding data has exactly the contract keys",
                  set(data) == {"coin", "rows", "count",
                                "data_as_of", "last_ts"},
                  str(sorted(data))[:160])
            rows = data.get("rows") or []
            check("funding rows carry exactly {ts, rate, cap}",
                  all(set(r) == {"ts", "rate", "cap"} for r in rows),
                  str(rows)[:160])
            check("funding rows ascend by ts",
                  all(b["ts"] < a["ts"] for b, a in zip(rows, rows[1:])),
                  str([r["ts"] for r in rows])[:160])
            check("funding count == len(rows)",
                  data.get("count") == len(rows) == 2, str(data)[:160])
            check("funding data_as_of == last_ts == last archived ts",
                  data.get("data_as_of") == data.get("last_ts")
                  == archived[-1]["ts"], str(data)[:160])
            check("funding limit=2 returns the last 2 archived rows",
                  [r["ts"] for r in rows]
                  == [archived[-2]["ts"], archived[-1]["ts"]],
                  str([r["ts"] for r in rows])[:160])

            # -- default-1000 slice over the 1205-row coin ------------------
            st, body = get(port, f"/funding?coin={BIG_COIN}")
            data = (body.get("data") or {}) if isinstance(body, dict) else {}
            drows = data.get("rows") or []
            check("funding default slice -> 200 with exactly 1000 rows",
                  st == 200 and data.get("count") == 1000
                  and len(drows) == 1000,
                  f"got {st} count={data.get('count')}")
            check("funding default slice starts at the 206th archived ts",
                  drows and drows[0]["ts"] == archived[205]["ts"]
                  and drows[-1]["ts"] == archived[-1]["ts"],
                  str((drows[0]["ts"] if drows else None,
                       archived[205]["ts"]))[:160])

            # -- invalid limits: reject, never clamp ------------------------
            for raw in ("0", "-5", "5001", "abc"):
                st, body = get(port, f"/funding?coin={BIG_COIN}&limit={raw}")
                want = ("limit must be an integer" if raw == "abc"
                        else "limit must be between 1 and 5000")
                check(f"funding limit={raw} -> 400 ({want})",
                      st == 400 and isinstance(body, dict)
                      and (body.get("error") or {}).get("message") == want,
                      f"got {st} {str(body)[:160]}")
                check_envelope(f"funding limit={raw} envelope", body, False)

            # -- coin required ----------------------------------------------
            st, body = get(port, "/funding?limit=2")
            check("funding missing coin -> 400 coin is required",
                  st == 400 and isinstance(body, dict)
                  and (body.get("error") or {}).get("message")
                  == "coin is required", f"got {st} {str(body)[:160]}")
            st, body = get(port, "/funding?coin=&limit=2")
            check("funding empty coin -> 400 coin is required",
                  st == 400 and isinstance(body, dict)
                  and (body.get("error") or {}).get("message")
                  == "coin is required", f"got {st} {str(body)[:160]}")

            # -- well-formed but unarchived coin -> 404 ----------------------
            st, body = get(port, f"/funding?coin={NEVER_COIN}")
            check("funding unarchived coin -> 404",
                  st == 404, f"got {st}")
            check("funding 404 message names the coin",
                  isinstance(body, dict)
                  and (body.get("error") or {}).get("message")
                  == f"no funding for {NEVER_COIN}",
                  str(body)[:160])
            check_envelope("funding 404 envelope", body, False)

            # -- corrupt file -> 503, never a stack trace --------------------
            st, body = get(port, f"/funding?coin={CORRUPT_COIN}")
            check("funding corrupt file -> 503 funding unavailable",
                  st == 503 and isinstance(body, dict)
                  and (body.get("error") or {}).get("message")
                  == "funding unavailable",
                  f"got {st} {str(body)[:160]}")
            check_envelope("funding 503 envelope", body, False)

            # -- empty archive -> 200 with nulls, never an error -------------
            st, body = get(port, f"/funding?coin={EMPTY_COIN}")
            check("funding empty archive -> 200", st == 200, f"got {st}")
            edata = (body.get("data") or {}) if isinstance(body, dict) else {}
            check("funding empty archive is explicit emptiness",
                  edata.get("rows") == [] and edata.get("count") == 0
                  and edata.get("data_as_of") is None
                  and edata.get("last_ts") is None, str(edata)[:160])
            check_envelope("funding empty 200 envelope", body, True)

            # -- traversal names -> 400 + filesystem tripwire ----------------
            before = snapshot_tree(tmp)
            for raw in ("..", "a/b", "%2e%2e", "%2f"):
                st, body = get(port, f"/funding?coin={raw}")
                check(f"funding traversal {raw} -> 400 invalid coin name",
                      st == 400 and isinstance(body, dict)
                      and (body.get("error") or {}).get("message")
                      == "invalid coin name",
                      f"got {st} {str(body)[:160]}")
            check("traversal requests escape neither read nor write",
                  snapshot_tree(tmp) == before,
                  f"{before} -> {snapshot_tree(tmp)}")
        finally:
            server.shutdown()
            server.server_close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # -- tripwire self-check: non-loopback connect must raise -------------
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2)
        s.connect(("192.0.2.1", 9))  # TEST-NET-1, never routed
        s.close()
        check("tripwire blocks non-loopback connect", False,
              "connect succeeded")
    except NetworkTripwire:
        check("tripwire blocks non-loopback connect", True)
    except Exception as e:  # pragma: no cover — must never reach the network
        check("tripwire blocks non-loopback connect", False, repr(e))

    print()
    if FAILURES:
        print(f"FAIL  tests/test_funding_route.py  "
              f"({len(FAILURES)}/{CHECKS} failed: {FAILURES})")
        sys.exit(1)
    print(f"ALL PASS  tests/test_funding_route.py  ({CHECKS} checks)")


if __name__ == "__main__":
    main()
