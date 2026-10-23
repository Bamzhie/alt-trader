"""Collection-only service: neutral routes work, legacy routes are gone."""

import gzip
import http.client
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from proto import collection, service  # noqa: E402

TOKEN = "test-token"


def get(port, path, token=TOKEN):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    conn.request("GET", path, headers=headers)
    r = conn.getresponse()
    body = r.read()
    conn.close()
    try:
        payload = json.loads(body)
    except ValueError:
        payload = None
    return r.status, payload


def main():
    with tempfile.TemporaryDirectory() as td:
        data = os.path.join(td, "bars")
        os.makedirs(data)
        with gzip.open(os.path.join(data, "BTC.csv.gz"), "wt", newline="") as f:
            f.write("ts,o,h,l,c,vol,amount\n")
            now = int(__import__("time").time())
            f.write(f"{now - 600},1,2,0.5,1.5,10,15\n")

        state = collection.CollectionState(
            data, os.path.join(td, "funding"),
            os.path.join(td, "funding_settlements"),
            os.path.join(td, "prices"), every_s=300)
        server, port = service.serve_with_fallback(
            0, TOKEN, state, attempts=1, data_dir=data,
            funding_dir=state.funding_dir,
            settlements_dir=state.settlements_dir,
            prices_dir=state.prices_dir)
        try:
            st, body = get(port, "/healthz", token=None)
            assert st == 200 and body["alive"] is True, (st, body)

            st, body = get(port, "/readyz")
            assert st == 503 and body["error"]["code"] == "UNAVAILABLE", (
                st, body)

            st, body = get(port, "/bars?coin=BTC&tf=5m")
            assert st == 200 and body["data"]["count"] == 1, (st, body)
            assert body["data"]["closed_only"] is True

            st, body = get(port, "/bars?coin=../etc&tf=5m")
            assert st == 400, (st, body)

            for legacy in ("/snapshot", "/setups", "/events", "/quant-status",
                           "/quant-research", "/quant-signals", "/metrics",
                           "/tradeplan/1", "/scanner-health"):
                st, body = get(port, legacy)
                assert st == 404 and body["error"]["code"] == "NOT_FOUND", (
                    legacy, st, body)

            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("POST", "/commands/pause",
                         headers={"Authorization": f"Bearer {TOKEN}"})
            r = conn.getresponse()
            assert r.status == 404, r.status
            conn.close()

            state.record(
                status="healthy", duration_s=0.1,
                bars={"coins": 1, "new_bars": 1, "failed_coins": 0,
                      "stopped": False, "errors": []},
                funding={"ok": True, "result": {"coins": 1, "new_rows": 1}},
                settlements={"ok": True, "result": {"coins": 0, "new_rows": 0,
                                                    "errors": 0}},
                prices={"ok": True, "result": {"series": 0, "new_rows": 0,
                                               "errors": 0}})
            st, body = get(port, "/readyz")
            assert st == 200 and body["data"]["ready"] is True, (st, body)
            st, body = get(port, "/collector-health")
            assert st == 200 and body["data"]["status"] == "healthy", (
                st, body)
            assert body["data"]["bars"]["coins"] == 1
        finally:
            server.shutdown()
            server.server_close()

    print("ALL PASS collection-only service")


if __name__ == "__main__":
    main()
