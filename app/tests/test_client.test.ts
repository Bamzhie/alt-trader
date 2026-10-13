// Typed API client tests — RED/GREEN via mocked fetch only.
//
// The socket tripwire in tests/setup.ts blocks all real network egress, so
// every test stubs global fetch (vi.stubGlobal) and asserts on the real
// client behaviour: envelope unwrap, ApiError mapping, request construction.
import { afterEach, describe, expect, test, vi } from "vitest";
import { createClient, ApiError } from "../src/api/client";
import type {
  Envelope,
  JournalEntry,
  Mover,
  Setup,
} from "../src/api/types";

const BASE_URL = "http://127.0.0.1:18741";

// A snapshot envelope as the mock service would actually emit it, built from
// the documented contract (app/dev/mock_service.py _snapshot()).
function snapshotEnvelope(): Envelope<Record<string, unknown>> {
  const setup: Setup = {
    id: "setup-btc-1",
    coin: "BTC",
    name: "Bitcoin",
    direction: "LONG",
    horizon: "4h",
    state: "active",
    emitted_ts: 1791932400,
    expires_ts: 1791946800,
    price: 112505.0,
    change_24h_pct: 2.41,
    rule: "range_reclaim_long",
    criteria: [
      { name: "close reclaims 4H range high", passed: true },
      { name: "EMA50 above EMA200", passed: true },
    ],
    inputs: {
      rsi: 57.4,
      macd: 0.0211,
      ema50: 110980.2,
      ema200: 108214.7,
      volume_pct: 138.5,
    },
    unavailable_inputs: [],
    analysis: ["BTC reclaimed the 4H range high on the 20:00 UTC close."],
    tradeplan_id: "tp-btc-1",
  };
  const movers: Mover[] = [
    { coin: "SOL", name: "Solana", change_24h_pct: 4.87 },
    { coin: "BTC", name: "Bitcoin", change_24h_pct: 2.41 },
  ];
  const journal: JournalEntry[] = [
    {
      ts: 1791934200,
      kind: "emission",
      coin: "LINK",
      text: "LINK setup emitted — range rejection, 4H horizon.",
    },
  ];
  return {
    ok: true,
    data: {
      snapshot_event_id: "000012",
      ts: 1791936000,
      data_as_of: 1791936000,
      eligible_count: 150,
      evaluated_count: 132,
      active_setups: 6,
      monitoring_coverage: 0.984,
      new_setups_24h: 6,
      setups: [setup],
      comparison_24h: {
        available: true,
        reason: null,
        prior_ts: 1791849600,
        delta_active_setups: 1,
        delta_new_setups_24h: 1,
      },
      snapshot_history: [
        {
          ts: 1791936000,
          eligible_count: 150,
          evaluated_count: 132,
          active_setups: 6,
          new_setups_24h: 6,
          monitoring_coverage: 0.984,
          covers_24h: false,
        },
      ],
      coins_without_setup: [
        {
          coin: "XRP",
          name: "XRP",
          blocking_reason: "no setup yet — no rule criteria met on the last sweep",
        },
      ],
      market_overview: [
        {
          coin: "BTC",
          name: "Bitcoin",
          price: 112505.0,
          change_24h_pct: 2.41,
          last_bar_ts: null,
        },
      ],
      movers,
      recent_analysis: [
        {
          ts: 1791934200,
          coin: "LINK",
          direction: "SHORT",
          text: "LINK rejected the 4H range low with EMA50 below EMA200.",
        },
      ],
      activity: journal,
      supported_timeframes: ["5m", "15m", "1H", "4H", "1D"],
      event_kinds: [
        "setup.emitted",
        "setup.invalidated",
        "setup.expired",
        "setup.resolved",
        "monitoring.unavailable",
        "monitoring.ok",
        "sweep.completed",
      ],
      unavailable: null,
    },
    error: null,
  };
}

// Builds a Response like fetch() would: real global Response so headers and
// .json() behave exactly as in production.
function jsonResponse(status: number, payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// A fetch stub that answers any request with the given response, recording
// the (url, init) of every call for request-construction assertions.
function stubFetch(response: Response | (() => never)) {
  const calls: Array<{ url: string; init: RequestInit | undefined }> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init });
    if (typeof response === "function") {
      return response();
    }
    return response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("createClient — envelope unwrap", () => {
  test("snapshot returns data on ok envelope", async () => {
    stubFetch(jsonResponse(200, snapshotEnvelope()));
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    const snap = await client.snapshot();

    expect(snap.eligible_count).toBe(150);
    expect(snap.active_setups).toBe(6);
    expect(typeof snap.snapshot_event_id).toBe("string");
    expect(snap.snapshot_event_id).toBe("000012");
    // Section-level unavailable stays explicit null — never a guess.
    expect(snap.unavailable).toBeNull();
  });

  test("unauthorized throws ApiError UNAUTH", async () => {
    stubFetch(
      jsonResponse(401, {
        ok: false,
        data: null,
        error: { code: "UNAUTH", message: "missing or invalid bearer token" },
      }),
    );
    const bad = createClient({ baseUrl: BASE_URL, token: "nope" });

    await expect(bad.snapshot()).rejects.toMatchObject({ code: "UNAUTH" });
    await expect(bad.snapshot()).rejects.toBeInstanceOf(ApiError);
  });

  test("not found throws ApiError NOT_FOUND", async () => {
    stubFetch(
      jsonResponse(404, {
        ok: false,
        data: null,
        error: { code: "NOT_FOUND", message: "no bars for coin 'DOESNOTEXIST'" },
      }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await expect(client.bars("DOESNOTEXIST", "5m")).rejects.toMatchObject({
      code: "NOT_FOUND",
    });
  });

  test("bad request throws ApiError BAD_REQUEST with the server message", async () => {
    stubFetch(
      jsonResponse(400, {
        ok: false,
        data: null,
        error: {
          code: "BAD_REQUEST",
          message: "unsupported timeframe '1m'; supported: 5m, 15m, 1H, 4H, 1D",
        },
      }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await expect(client.bars("BTC", "1m" as never)).rejects.toMatchObject({
      code: "BAD_REQUEST",
      message: "unsupported timeframe '1m'; supported: 5m, 15m, 1H, 4H, 1D",
    });
  });

  test("unavailable throws ApiError UNAVAILABLE", async () => {
    stubFetch(
      jsonResponse(503, {
        ok: false,
        data: null,
        error: { code: "UNAVAILABLE", message: "not ready — first sweep in progress" },
      }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await expect(client.snapshot()).rejects.toMatchObject({ code: "UNAVAILABLE" });
  });

  test("rate limited throws ApiError RATE_LIMITED", async () => {
    stubFetch(
      jsonResponse(429, {
        ok: false,
        data: null,
        error: { code: "RATE_LIMITED", message: "slow down" },
      }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await expect(client.metrics("BTC")).rejects.toMatchObject({
      code: "RATE_LIMITED",
    });
  });

  test("network failure throws ApiError NETWORK", async () => {
    stubFetch(() => {
      throw new TypeError("fetch failed");
    });
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await expect(client.snapshot()).rejects.toMatchObject({ code: "NETWORK" });
  });

  test("ok:false envelope without an error object throws NETWORK", async () => {
    stubFetch(jsonResponse(200, { ok: false, data: null, error: null }));
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await expect(client.snapshot()).rejects.toMatchObject({ code: "NETWORK" });
  });

  test("unknown error codes pass through on the ApiError", async () => {
    stubFetch(
      jsonResponse(500, {
        ok: false,
        data: null,
        error: { code: "SOMETHING_NEW", message: "unmapped server code" },
      }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await expect(client.snapshot()).rejects.toMatchObject({
      code: "SOMETHING_NEW",
      message: "unmapped server code",
    });
  });
});

describe("createClient — request construction", () => {
  test("every request carries the bearer token", async () => {
    const { calls } = stubFetch(jsonResponse(200, snapshotEnvelope()));
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await client.snapshot();

    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(`${BASE_URL}/snapshot`);
    const headers = new Headers(calls[0].init?.headers);
    expect(headers.get("Authorization")).toBe("Bearer dev-token");
  });

  test("setups builds query params from the SetupsQuery", async () => {
    const { calls } = stubFetch(
      jsonResponse(200, {
        ok: true,
        data: { setups: [], next_cursor: null, limit: 10, state: "active", total_returned: 0 },
        error: null,
      }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    const page = await client.setups({ state: "active", limit: 10, cursor: "o20" });

    expect(page.nextCursor).toBeNull();
    const url = new URL(calls[0].url);
    expect(url.pathname).toBe("/setups");
    expect(url.searchParams.get("state")).toBe("active");
    expect(url.searchParams.get("limit")).toBe("10");
    expect(url.searchParams.get("cursor")).toBe("o20");
  });

  test("setups with an empty query sends no query string", async () => {
    const { calls } = stubFetch(
      jsonResponse(200, {
        ok: true,
        data: { setups: [], next_cursor: "o100", limit: 100, state: null, total_returned: 0 },
        error: null,
      }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    const page = await client.setups({});

    expect(page.nextCursor).toBe("o100");
    expect(calls[0].url).toBe(`${BASE_URL}/setups`);
  });

  test("bars passes coin and tf through as query params", async () => {
    const { calls } = stubFetch(
      jsonResponse(200, {
        ok: true,
        data: {
          coin: "BTC",
          tf: "4H",
          bars: [
            { ts: 1791935400, open: 112014.9, high: 112673.5, low: 111961.9, close: 112480.5, volume: 1556.96, closed: true },
          ],
          closed_only: true,
          count: 1,
          data_as_of: 1791936000,
          unavailable: null,
        },
        error: null,
      }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    const bars = await client.bars("BTC", "4H");

    expect(bars.tf).toBe("4H");
    expect(bars.bars).toHaveLength(1);
    expect(bars.unavailable).toBeNull();
    const url = new URL(calls[0].url);
    expect(url.pathname).toBe("/bars");
    expect(url.searchParams.get("coin")).toBe("BTC");
    expect(url.searchParams.get("tf")).toBe("4H");
  });

  test("setup(id) hits /setup/:id", async () => {
    // Wire fidelity: _setup_route() in mock_service.py attaches only
    // tradeplan_id — no `name`. The fixture deliberately omits it.
    const setup: Setup = {
      id: "setup-eth-1",
      coin: "ETH",
      direction: "SHORT",
      horizon: "4h",
      state: "active",
      emitted_ts: 1791930600,
      expires_ts: 1791945000,
      price: 4092.4,
      change_24h_pct: -1.18,
      rule: "range_reject_short",
      criteria: [{ name: "close rejects 4H range low", passed: true }],
      inputs: {
        rsi: 42.8,
        macd: -0.0142,
        ema50: 3988.1,
        ema200: 4102.55,
        volume_pct: 91.2,
      },
      unavailable_inputs: [],
      analysis: ["ETH rejected the 4H range low twice inside the last three bars."],
      tradeplan_id: "tp-eth-1",
    };
    const { calls } = stubFetch(
      jsonResponse(200, { ok: true, data: setup, error: null }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    const got = await client.setup("setup-eth-1");

    // Wire fidelity: /setup/:id never attaches `name` (only /snapshot does) —
    // see mock_service.py _setup_route(). The type must reflect that.
    expect(got.coin).toBe("ETH");
    expect(got.direction).toBe("SHORT");
    expect("name" in got).toBe(false);
    expect(got.name).toBeUndefined();
    expect(calls[0].url).toBe(`${BASE_URL}/setup/setup-eth-1`);
  });

  test("tradeplan(id) hits /tradeplan/:id", async () => {
    const { calls } = stubFetch(
      jsonResponse(200, {
        ok: true,
        data: {
          id: "tp-btc-1",
          setup_id: "setup-btc-1",
          coin: "BTC",
          name: "Bitcoin",
          direction: "LONG",
          horizon: "4h",
          entry_zone: { low: 112223.7, high: 112673.8 },
          stop: 111188.7,
          tp1: 114743.8,
          tp2: 117848.8,
          reference_notional_usdt: 100,
          reference_stop_risk_usdt: 0.92,
          reference_size_note: "100 USDT reference notional — reference size, not a position",
          indicators: [{ name: "RSI 14", value: 57.4 }],
          valid_until_ts: 1791946800,
          next_update_ts: 1791950400,
          analysis: ["BTC reclaimed the 4H range high."],
          data_as_of: 1791936000,
        },
        error: null,
      }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    const plan = await client.tradeplan("tp-btc-1");

    expect(plan.stop).toBe(111188.7);
    expect(calls[0].url).toBe(`${BASE_URL}/tradeplan/tp-btc-1`);
  });

  test("metrics returns the metrics row", async () => {
    stubFetch(
      jsonResponse(200, {
        ok: true,
        data: {
          coin: "AVAX",
          ts: 1791936000,
          rsi: null,
          macd: 0.0009,
          ema50: 38.31,
          ema200: 37.92,
          volume_pct: 88.6,
          unavailable: {
            reason: "indicator input not returned by the last sweep",
            missing: ["rsi"],
          },
          data_as_of: 1791936000,
        },
        error: null,
      }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    const m = await client.metrics("AVAX");

    // Unavailable indicators are explicit nulls with a section-level reason.
    expect(m.rsi).toBeNull();
    expect(m.unavailable?.reason).toContain("indicator input not returned");
  });
});

describe("createClient — health endpoints", () => {
  test("healthz returns true when alive", async () => {
    stubFetch(jsonResponse(200, { ok: true, alive: true }));
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await expect(client.healthz()).resolves.toBe(true);
  });

  test("healthz returns false when the liveness probe fails", async () => {
    stubFetch(jsonResponse(503, { ok: false, alive: false }));
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await expect(client.healthz()).resolves.toBe(false);
  });

  test("readyz returns true when ready", async () => {
    stubFetch(
      jsonResponse(200, {
        ok: true,
        data: { ready: true, data_as_of: 1791936000 },
        error: null,
      }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await expect(client.readyz()).resolves.toBe(true);
  });

  test("readyz returns false when the first sweep is in progress", async () => {
    stubFetch(
      jsonResponse(503, {
        ok: false,
        data: null,
        error: { code: "UNAVAILABLE", message: "not ready — first sweep in progress" },
      }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await expect(client.readyz()).resolves.toBe(false);
  });

  test("health endpoints skip the bearer token (contract: auth-free probes)", async () => {
    const { calls } = stubFetch(jsonResponse(200, { ok: true, alive: true }));
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await client.healthz();

    expect(calls[0].url).toBe(`${BASE_URL}/healthz`);
    const headers = new Headers(calls[0].init?.headers);
    expect(headers.get("Authorization")).toBeNull();
  });
});

describe("createClient — idempotent commands", () => {
  test("pause posts to /commands/pause and resolves void", async () => {
    const { calls } = stubFetch(
      jsonResponse(200, {
        ok: true,
        data: { command: "pause", idempotent: true, paused: true },
        error: null,
      }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await expect(client.pause()).resolves.toBeUndefined();
    expect(calls[0].url).toBe(`${BASE_URL}/commands/pause`);
    expect(calls[0].init?.method).toBe("POST");
  });

  test("resume posts to /commands/resume and resolves void", async () => {
    const { calls } = stubFetch(
      jsonResponse(200, {
        ok: true,
        data: { command: "resume", idempotent: true, paused: false },
        error: null,
      }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await expect(client.resume()).resolves.toBeUndefined();
    expect(calls[0].url).toBe(`${BASE_URL}/commands/resume`);
    expect(calls[0].init?.method).toBe("POST");
  });

  test("disableTrigger posts the rule in the JSON body", async () => {
    const { calls } = stubFetch(
      jsonResponse(200, {
        ok: true,
        data: {
          command: "disable-trigger",
          rule: "range_reclaim_long",
          idempotent: true,
          enabled: false,
          disabled_triggers: ["range_reclaim_long"],
        },
        error: null,
      }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await expect(
      client.disableTrigger("range_reclaim_long"),
    ).resolves.toBeUndefined();
    expect(calls[0].url).toBe(`${BASE_URL}/commands/disable-trigger`);
    expect(calls[0].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      rule: "range_reclaim_long",
    });
  });

  test("enableTrigger posts the rule in the JSON body", async () => {
    const { calls } = stubFetch(
      jsonResponse(200, {
        ok: true,
        data: {
          command: "enable-trigger",
          rule: "range_reclaim_long",
          idempotent: true,
          enabled: true,
          disabled_triggers: [],
        },
        error: null,
      }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await expect(client.enableTrigger("range_reclaim_long")).resolves.toBeUndefined();
    expect(calls[0].url).toBe(`${BASE_URL}/commands/enable-trigger`);
    expect(calls[0].init?.method).toBe("POST");
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({
      rule: "range_reclaim_long",
    });
  });

  test("command failures surface as ApiError", async () => {
    stubFetch(
      jsonResponse(400, {
        ok: false,
        data: null,
        error: {
          code: "BAD_REQUEST",
          message: "unknown trigger rule; known: range_reclaim_long, range_reject_short, breakout_continuation_long",
        },
      }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await expect(client.disableTrigger("made_up_rule")).rejects.toMatchObject({
      code: "BAD_REQUEST",
    });
  });
});
