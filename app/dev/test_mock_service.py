"""Contract tests for the stdlib mock service (app/dev/mock_service.py).

The server binds 127.0.0.1 and these tests talk to it over 127.0.0.1 only —
loopback, no network egress, no orders, no keys. The JS socket tripwire in
app/tests/setup.ts deliberately does not cover these files.

Run:  cd app && python3 dev/test_mock_service.py
"""

import http.client
import importlib.util
import json
import os
import socketserver
import urllib.error
import urllib.request
import unittest
from typing import Any, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
BASE_HOST = "127.0.0.1"
BASE_PORT = 18741
BASE = f"http://{BASE_HOST}:{BASE_PORT}"
TOKEN = "dev-token"

SERVER: Optional[Any] = None


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "mock_service", os.path.join(HERE, "mock_service.py")
    )
    if spec is None or spec.loader is None:
        raise ImportError("cannot load mock_service.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def get(path: str, token: Optional[str] = TOKEN):
    """GET with an optional bearer. token=None sends no Authorization header."""
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(BASE + path, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def post(path, body=None, token=TOKEN):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body or {}).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def sse_frames(path, last_event_id=None, want=4, timeout=6):
    """Read up to `want` data-bearing SSE frames, then close.

    A read timeout is NOT an error — it just means the stream went quiet
    (nothing newer than the cursor). Whatever arrived is returned.
    """
    headers = {"Authorization": f"Bearer {TOKEN}", "Accept": "text/event-stream"}
    if last_event_id is not None:
        headers["Last-Event-ID"] = last_event_id
    conn = http.client.HTTPConnection(BASE_HOST, BASE_PORT, timeout=timeout)
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    frames, cur = [], {}
    try:
        while len(frames) < want:
            raw = resp.readline()
            if not raw:
                break
            line = raw.decode("utf-8").rstrip("\r\n")
            if line == "":
                if cur.get("data") is not None:
                    frames.append(cur)
                cur = {}
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "data":
                try:
                    cur["data"] = json.loads(value)
                except ValueError:
                    cur["data"] = value
            else:
                cur[field] = value
    except (TimeoutError, OSError):
        pass  # stream idle — nothing newer to replay
    return frames, conn


def setUpModule():
    global SERVER
    mock_service = _load_module()
    SERVER = mock_service.start_server(
        host=BASE_HOST,
        port=BASE_PORT,
        token=TOKEN,
        fixtures_path=os.path.join(HERE, "fixtures.json"),
    )


def tearDownModule():
    if SERVER is not None:
        SERVER.shutdown()
        SERVER.server_close()


BANNED = (
    "confidence",
    "probability",
    "vwap",
    "conviction",
    "leverage",
    "liquidation",
    "margin call",
    "scanned",
    "guaranteed",
    "will profit",
)


class TestMockService(unittest.TestCase):
    """The five checks pinned verbatim by the plan."""

    def test_healthz_no_auth(self):
        status, body = get("/healthz", token=None)
        self.assertEqual(status, 200)
        self.assertTrue(body["alive"])
        self.assertTrue(body["ok"])

    def test_auth_matrix(self):
        self.assertEqual(get("/snapshot", token="wrong")[0], 401)
        self.assertEqual(get("/snapshot", token=None)[0], 401)
        status, body = get("/snapshot", token=None)
        self.assertEqual(body["error"]["code"], "UNAUTH")
        # /dev/* is the only auth-free route besides /healthz.
        self.assertEqual(get("/dev/toggle-ready", token=None)[0], 200)
        get("/dev/toggle-ready")  # flip it back

    def test_bars_reject_1m(self):
        status, body = get("/bars?coin=BTC&tf=1m")
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "BAD_REQUEST")
        self.assertIsNone(body["data"])
        self.assertFalse(body["ok"])

    def test_metrics_explicit_null(self):
        status, body = get("/metrics?coin=METRICS_NULL")
        self.assertEqual(status, 200)
        self.assertIsNone(body["data"]["rsi"])
        self.assertIn("rsi", body["data"])  # key present, value null — never dropped
        for key in ("macd", "ema50", "ema200", "volume_pct"):
            self.assertIn(key, body["data"])

    def test_tradeplan_no_leverage(self):
        status, body = get("/tradeplan/tp-btc-1")
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["reference_notional_usdt"], 100)
        self.assertNotIn("leverage", body["data"])
        for key in ("entry_zone", "stop", "tp1", "tp2"):
            self.assertIn(key, body["data"])


class TestReadiness(unittest.TestCase):
    def test_readyz_200_then_503_on_toggle(self):
        status, body = get("/readyz")
        self.assertEqual(status, 200)
        self.assertTrue(body["data"]["ready"])
        status, _ = get("/dev/toggle-ready")
        self.assertEqual(status, 200)
        status, body = get("/readyz")
        self.assertEqual(status, 503)
        self.assertEqual(body["error"]["code"], "UNAVAILABLE")
        self.assertFalse(body["ok"])
        self.assertIn("sweep", body["error"]["message"].lower())
        get("/dev/toggle-ready")  # restore ready for the rest of the suite

    def test_readyz_requires_auth(self):
        self.assertEqual(get("/readyz", token=None)[0], 401)


class TestSnapshot(unittest.TestCase):
    def test_snapshot_carries_documented_fields(self):
        status, body = get("/snapshot")
        self.assertEqual(status, 200)
        data = body["data"]
        for key in (
            "snapshot_event_id",
            "eligible_count",
            "evaluated_count",
            "active_setups",
            "monitoring_coverage",
            "new_setups_24h",
            "data_as_of",
            "setups",
        ):
            self.assertIn(key, data)
        self.assertIsInstance(data["snapshot_event_id"], str)
        self.assertIsInstance(data["data_as_of"], int)
        self.assertEqual(data["eligible_count"], 150)
        self.assertLessEqual(data["evaluated_count"], data["eligible_count"])
        self.assertEqual(data["active_setups"], len(data["setups"]))
        self.assertEqual(len(data["setups"]), 6)
        for setup in data["setups"]:
            self.assertIn(setup["direction"], ("LONG", "SHORT"))
            self.assertIsInstance(setup["coin"], str)

    def test_snapshot_24h_comparison_unavailable(self):
        _, body = get("/snapshot")
        comparison = body["data"]["comparison_24h"]
        self.assertFalse(comparison["available"])
        self.assertIsInstance(comparison["reason"], str)
        self.assertIsNone(comparison["delta_active_setups"])

    def test_snapshot_history_has_non_covering_pair(self):
        _, body = get("/snapshot")
        history = body["data"]["snapshot_history"]
        self.assertGreaterEqual(len(history), 2)
        covering = [h for h in history if h["covers_24h"]]
        self.assertEqual(covering, [])  # the pair deliberately does not cover 24h


class TestSetups(unittest.TestCase):
    def test_defaults_and_cursor(self):
        status, body = get("/setups")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["data"]["setups"]), 6)
        self.assertIsNone(body["data"]["next_cursor"])

    def test_limit_and_cursor_pagination(self):
        _, body = get("/setups?limit=2")
        self.assertEqual(len(body["data"]["setups"]), 2)
        cursor = body["data"]["next_cursor"]
        self.assertIsInstance(cursor, str)
        _, body = get(f"/setups?limit=2&cursor={cursor}")
        self.assertEqual(len(body["data"]["setups"]), 2)
        self.assertEqual(len(get("/setups?limit=2")[1]["data"]["setups"]), 2)

    def test_limit_max_500(self):
        _, body = get("/setups?limit=5000")
        self.assertEqual(len(body["data"]["setups"]), 6)
        _, body = get("/setups?limit=abc")
        self.assertEqual(body["error"]["code"], "BAD_REQUEST")

    def test_state_filter(self):
        _, body = get("/setups?state=expired")
        self.assertEqual(body["data"]["setups"], [])
        _, body = get("/setups?state=bogus")
        self.assertEqual(body["error"]["code"], "BAD_REQUEST")

    def test_setup_by_id(self):
        status, body = get("/setup/BTC")
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["coin"], "BTC")
        status, body = get("/setup/NOPE")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "NOT_FOUND")


class TestNoSetupCoin(unittest.TestCase):
    def test_no_setup_coin_reports_blocking_reason(self):
        status, body = get("/snapshot")
        nosetup = body["data"]["coins_without_setup"]
        self.assertGreaterEqual(len(nosetup), 1)
        entry = nosetup[0]
        self.assertIn("coin", entry)
        self.assertIn("blocking_reason", entry)
        self.assertIsInstance(entry["blocking_reason"], str)
        self.assertTrue(entry["blocking_reason"].strip())


class TestBars(unittest.TestCase):
    def test_bars_closed_only(self):
        status, body = get("/bars?coin=BTC&tf=4H")
        self.assertEqual(status, 200)
        bars = body["data"]["bars"]
        self.assertGreaterEqual(len(bars), 40)
        ts = [b["ts"] for b in bars]
        self.assertEqual(ts, sorted(ts))  # ascending
        self.assertTrue(all(isinstance(t, int) for t in ts))
        self.assertEqual(body["data"]["data_as_of"], 1791936000)
        self.assertLessEqual(ts[-1], body["data"]["data_as_of"])
        self.assertLessEqual(bars[-1]["ts"], body["data"]["data_as_of"] - 1)
        self.assertIsNone(body["data"]["unavailable"])

    def test_all_supported_timeframes(self):
        for tf in ("5m", "15m", "1H", "4H", "1D"):
            status, body = get(f"/bars?coin=BTC&tf={tf}")
            self.assertEqual(status, 200, tf)
            self.assertGreater(len(body["data"]["bars"]), 0, tf)

    def test_unknown_coin_404(self):
        status, body = get("/bars?coin=NOPE&tf=4H")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "NOT_FOUND")

    def test_missing_params_400(self):
        self.assertEqual(get("/bars?tf=4H")[1]["error"]["code"], "BAD_REQUEST")
        self.assertEqual(get("/bars?coin=BTC")[1]["error"]["code"], "BAD_REQUEST")


class TestMetrics(unittest.TestCase):
    def test_metrics_all_five_keys(self):
        status, body = get("/metrics?coin=BTC")
        self.assertEqual(status, 200)
        for key in ("rsi", "macd", "ema50", "ema200", "volume_pct"):
            self.assertIn(key, body["data"])
            self.assertIsNotNone(body["data"][key])
        self.assertIsNone(body["data"]["unavailable"])

    def test_metrics_unknown_coin_404(self):
        self.assertEqual(get("/metrics?coin=NOPE")[0], 404)

    def test_metrics_missing_coin_400(self):
        self.assertEqual(get("/metrics")[1]["error"]["code"], "BAD_REQUEST")


class TestTradePlan(unittest.TestCase):
    def test_tradeplan_shape(self):
        _, body = get("/tradeplan/tp-btc-1")
        data = body["data"]
        self.assertEqual(data["coin"], "BTC")
        self.assertEqual(data["direction"], "LONG")
        self.assertEqual(set(data["entry_zone"]), {"low", "high"})
        self.assertLess(data["entry_zone"]["low"], data["entry_zone"]["high"])
        self.assertLess(data["stop"], data["entry_zone"]["low"])
        self.assertGreater(data["tp1"], data["entry_zone"]["high"])
        self.assertGreater(data["tp2"], data["tp1"])
        self.assertIsInstance(data["reference_notional_usdt"], int)
        self.assertIn("indicators", data)

    def test_tradeplan_unknown_id_404(self):
        status, body = get("/tradeplan/nope")
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "NOT_FOUND")

    def test_tradeplan_short_is_direction_symmetric(self):
        status, body = get("/tradeplan/tp-eth-1")
        self.assertEqual(status, 200)
        data = body["data"]
        self.assertEqual(data["direction"], "SHORT")
        # A short mirrors the long exactly: stop above the zone, targets below.
        self.assertGreater(data["stop"], data["entry_zone"]["high"])
        self.assertLess(data["tp1"], data["entry_zone"]["low"])
        self.assertLess(data["tp2"], data["tp1"])
        self.assertEqual(data["reference_notional_usdt"], 100)
        self.assertNotIn("leverage", data)

    def test_tradeplan_lookup_by_coin(self):
        status, body = get("/tradeplan/BTC")
        self.assertEqual(status, 200)
        self.assertEqual(body["data"]["coin"], "BTC")


class TestCommands(unittest.TestCase):
    def test_pause_resume_idempotent(self):
        for _ in range(3):
            status, body = post("/commands/pause", {})
            self.assertEqual(status, 200)
            self.assertTrue(body["data"]["idempotent"])
            self.assertEqual(body["data"]["command"], "pause")
        status, body = post("/commands/pause", {})
        self.assertEqual(status, 200)
        status, _ = post("/commands/resume", {})
        self.assertEqual(status, 200)

    def test_trigger_commands_validate_rule(self):
        status, body = post("/commands/disable-trigger", {"rule": "no_such_rule"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "BAD_REQUEST")
        # A real rule from the fixture set — the same names /setups carries.
        status, body = post("/commands/disable-trigger", {"rule": "range_reclaim_long"})
        self.assertEqual(status, 200)
        self.assertFalse(body["data"]["enabled"])
        status, _ = post("/commands/disable-trigger", {"rule": "range_reclaim_long"})
        self.assertEqual(status, 200)  # idempotent
        status, body = post("/commands/enable-trigger", {"rule": "range_reclaim_long"})
        self.assertEqual(status, 200)
        self.assertTrue(body["data"]["enabled"])

    def test_missing_rule_body_400(self):
        status, body = post("/commands/disable-trigger", {})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["code"], "BAD_REQUEST")

    def test_unknown_command_404(self):
        status, body = post("/commands/nuke", {})
        self.assertEqual(status, 404)
        self.assertEqual(body["error"]["code"], "NOT_FOUND")

    def test_commands_require_auth(self):
        self.assertEqual(post("/commands/pause", {}, token=None)[0], 401)

    def test_get_route_404(self):
        self.assertEqual(get("/nope")[0], 404)


class TestSSE(unittest.TestCase):
    def test_events_replay_monotonic_ids(self):
        frames, conn = sse_frames("/events", want=3)
        try:
            self.assertGreaterEqual(len(frames), 3)
            ids = [int(f["id"]) for f in frames]
            self.assertEqual(ids, sorted(ids))
            self.assertEqual(len(set(ids)), len(ids))
            kinds = {f["event"] for f in frames}
            self.assertTrue(kinds <= {
                "setup.emitted",
                "setup.invalidated",
                "setup.expired",
                "setup.resolved",
                "monitoring.unavailable",
                "monitoring.ok",
                "sweep.completed",
                "resync",
            })
            for f in frames:
                self.assertIn("kind", f["data"])
                self.assertIn("id", f["data"])
                self.assertIsInstance(f["data"]["ts"], int)
        finally:
            conn.close()

    def test_inject_event_lands_in_stream(self):
        _, body = get("/dev/inject-event?kind=monitoring.unavailable&coin=BTC")
        injected_id = body["data"]["id"]
        self.assertEqual(body["data"]["kind"], "monitoring.unavailable")
        # Replay from the frame just before the injection: exactly one new
        # frame should arrive, and it must be the one we just pushed.
        cursor = f"{int(injected_id) - 1:06d}"
        frames, conn = sse_frames("/events", last_event_id=cursor, want=1)
        try:
            self.assertEqual(len(frames), 1)
            self.assertEqual(frames[0]["event"], "monitoring.unavailable")
            self.assertEqual(frames[0]["data"]["id"], injected_id)
            self.assertEqual(frames[0]["data"]["coin"], "BTC")
        finally:
            conn.close()

    def test_last_event_id_replays_only_newer(self):
        _, first = get("/dev/inject-event?kind=sweep.completed")
        cursor = first["data"]["id"]
        _, second = get("/dev/inject-event?kind=monitoring.ok&coin=ETH")
        newer_id = second["data"]["id"]
        frames, conn = sse_frames("/events", last_event_id=cursor, want=1)
        try:
            self.assertEqual(len(frames), 1)
            self.assertEqual(frames[0]["data"]["id"], newer_id)
            for f in frames:
                self.assertGreater(int(f["id"]), int(cursor))
        finally:
            conn.close()

    def test_stale_cursor_emits_resync(self):
        frames, conn = sse_frames("/events", last_event_id="000000", want=2)
        try:
            self.assertEqual(frames[0]["event"], "resync")
            self.assertEqual(frames[0]["data"]["kind"], "resync")
        finally:
            conn.close()

    def test_events_require_auth(self):
        headers = {"Accept": "text/event-stream"}
        conn = http.client.HTTPConnection(BASE_HOST, BASE_PORT, timeout=5)
        conn.request("GET", "/events", headers=headers)
        resp = conn.getresponse()
        resp.read()
        conn.close()
        self.assertEqual(resp.status, 401)


class TestGlobalConstraints(unittest.TestCase):
    def test_no_banned_words_in_fixtures_or_responses(self):
        routes = [
            "/snapshot",
            "/setups",
            "/setups?limit=2",
            "/setup/BTC",
            "/bars?coin=BTC&tf=4H",
            "/metrics?coin=METRICS_NULL",
            "/metrics?coin=BTC",
            "/tradeplan/tp-btc-1",
            "/readyz",
        ]
        blobs = [json.dumps(get(r)[1]) for r in routes]
        with open(os.path.join(HERE, "fixtures.json")) as f:
            blobs.append(f.read())
        for blob in blobs:
            low = blob.lower()
            for word in BANNED:
                self.assertNotIn(word, low, f"banned word {word!r} in {blob[:120]}")

    def test_no_1m_timeframe_anywhere(self):
        with open(os.path.join(HERE, "fixtures.json")) as f:
            text = f.read()
        self.assertNotIn('"1m"', text)
        _, body = get("/snapshot")
        self.assertNotIn("1m", [tf for tf in body["data"]["supported_timeframes"]])
        self.assertEqual(
            body["data"]["supported_timeframes"], ["5m", "15m", "1H", "4H", "1D"]
        )

    def test_no_total_row(self):
        _, body = get("/snapshot")
        overview = body["data"]["market_overview"]
        self.assertEqual([r["coin"] for r in overview], ["BTC", "ETH"])
        self.assertNotIn("TOTAL", json.dumps(overview).upper().replace("TOTALS", ""))

    def test_movers_recent_analysis_activity_present(self):
        _, body = get("/snapshot")
        self.assertEqual(len(body["data"]["movers"]), 5)
        self.assertEqual(len(body["data"]["recent_analysis"]), 5)
        kinds = {e["kind"] for e in body["data"]["activity"]}
        self.assertIn("sweep-complete", kinds)
        ts = [e["ts"] for e in body["data"]["activity"]]
        self.assertEqual(ts, sorted(ts, reverse=True))  # newest first

    def test_request_log_never_contains_authorization(self):
        get("/snapshot")  # authorized traffic
        get("/healthz")
        log = "\n".join(SERVER.service.request_log)
        self.assertNotIn(TOKEN, log)
        self.assertNotIn("authorization", log.lower())
        self.assertNotIn("bearer", log.lower())
        self.assertTrue(log.strip())

    def test_wording_eligible_evaluated_never_scanned(self):
        _, body = get("/snapshot")
        text = json.dumps(body["data"]).lower()
        self.assertIn("eligible_count", text)
        self.assertIn("evaluated_count", text)
        self.assertNotIn("scanned", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
