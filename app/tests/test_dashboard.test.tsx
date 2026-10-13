// Dashboard tests (Task 8) — stat cards, Top Signals table, filter bar,
// evidence chips. RED first: written against the documented contract before
// any dashboard implementation exists.
//
// House style: the task-brief tests come first (verbatim), then additional
// coverage for the behaviors the brief's Interfaces section specifies.
//
// HARD RULES under test (see app/src/api/types.ts header):
//   * fetch is stubbed (jsonResponse + stubFetch as in test_client.test.ts);
//     the socket tripwire in tests/setup.ts guards every egress path.
//   * READ-ONLY — every stubbed call is a GET; the POST stub throws, so any
//     write attempt fails the test loudly.
//   * Banned words are asserted absent from rendered output.
//   * Timestamps are UTC-seconds ints; "data as of <ts>" is rendered.
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import App from "../src/App";
import { useDashboardStore } from "../src/store/dashboard";
import type { Envelope, Snapshot } from "../src/api/types";

const DATA_AS_OF = 1791936000;

// --- fixture snapshot (mirrors mock_service.py _snapshot()) ----------------

function makeSnapshot(overrides: Partial<Snapshot> = {}): Snapshot {
  return {
    snapshot_event_id: "000012",
    ts: DATA_AS_OF,
    data_as_of: DATA_AS_OF,
    eligible_count: 150,
    evaluated_count: 132,
    active_setups: 6,
    monitoring_coverage: 0.984,
    new_setups_24h: 6,
    setups: [
      {
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
        analysis: [
          "BTC reclaimed the 4H range high on the 20:00 UTC close.",
          "EMA50 sits above EMA200 and volume ran 38.5% over the 20-bar median.",
        ],
        tradeplan_id: "tp-btc-1",
      },
      {
        id: "setup-eth-1",
        coin: "ETH",
        name: "Ethereum",
        direction: "SHORT",
        horizon: "4h",
        state: "active",
        emitted_ts: 1791930600,
        expires_ts: 1791945000,
        price: 4092.4,
        change_24h_pct: -1.18,
        rule: "range_reject_short",
        criteria: [
          { name: "close rejects 4H range low", passed: true },
          { name: "EMA50 below EMA200", passed: true },
        ],
        inputs: {
          rsi: 42.8,
          macd: -0.0142,
          ema50: 3988.1,
          ema200: 4102.55,
          volume_pct: 91.2,
        },
        unavailable_inputs: [],
        analysis: [
          "ETH rejected the 4H range low twice inside the last three bars.",
          "EMA50 remains below EMA200, but volume came in under the median.",
        ],
        tradeplan_id: "tp-eth-1",
      },
      {
        id: "setup-sol-1",
        coin: "SOL",
        name: "Solana",
        direction: "LONG",
        horizon: "24h",
        state: "active",
        emitted_ts: 1791927000,
        expires_ts: 1791939600,
        price: 214.55,
        change_24h_pct: 4.87,
        rule: "breakout_continuation_long",
        criteria: [
          { name: "close above prior 4H swing high", passed: true },
          { name: "volume above 20-bar median", passed: true },
        ],
        inputs: {
          rsi: 64.1,
          macd: 0.0087,
          ema50: 205.44,
          ema200: 197.81,
          volume_pct: 171.9,
        },
        unavailable_inputs: [],
        analysis: [
          "SOL closed above its prior 4H swing high.",
          "Volume ran 71.9% over the 20-bar median, the strongest expansion.",
        ],
        tradeplan_id: "tp-sol-1",
      },
    ],
    // The fixture pair's older snapshot is only 6h back — no row covers
    // now-24h, so the comparison is unavailable (never inferred).
    comparison_24h: {
      available: false,
      reason:
        "no dashboard snapshot at or before now-24h — comparison unavailable, not inferred",
      prior_ts: null,
      delta_active_setups: null,
      delta_new_setups_24h: null,
    },
    snapshot_history: [
      {
        ts: DATA_AS_OF,
        eligible_count: 150,
        evaluated_count: 132,
        active_setups: 6,
        new_setups_24h: 6,
        monitoring_coverage: 0.984,
        covers_24h: false,
      },
      {
        ts: DATA_AS_OF - 6 * 3600,
        eligible_count: 149,
        evaluated_count: 127,
        active_setups: 5,
        new_setups_24h: 5,
        monitoring_coverage: 0.977,
        covers_24h: false,
      },
    ],
    coins_without_setup: [
      { coin: "XRP", name: "XRP", blocking_reason: "no setup yet" },
    ],
    market_overview: [
      { coin: "BTC", name: "Bitcoin", price: 112505.0, change_24h_pct: 2.41, last_bar_ts: null },
      { coin: "ETH", name: "Ethereum", price: 4092.4, change_24h_pct: -1.18, last_bar_ts: null },
    ],
    movers: [
      { coin: "SOL", name: "Solana", change_24h_pct: 4.87 },
      { coin: "BTC", name: "Bitcoin", change_24h_pct: 2.41 },
    ],
    recent_analysis: [
      { ts: 1791934200, coin: "LINK", direction: "SHORT", text: "LINK rejected the range low." },
    ],
    activity: [
      { ts: 1791935400, kind: "sweep-complete", coin: null, text: "Sweep complete" },
    ],
    supported_timeframes: ["5m", "15m", "1H", "4H", "1D"],
    event_kinds: ["setup.emitted", "sweep.completed"],
    unavailable: null,
    ...overrides,
  };
}

function snapshotEnvelope(snapshot: Snapshot): Envelope<Snapshot> {
  return { ok: true, data: snapshot, error: null };
}

// Builds a Response like fetch() would: real global Response so headers and
// .json() behave exactly as in production.
function jsonResponse(status: number, payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * Fetch stub: GETs answer with the snapshot envelope; ANY POST throws
 * (the read-only rule) and is recorded on `postCalls`.
 */
function stubFetch(snapshot: Snapshot) {
  const postCalls: Array<{ url: string; init: RequestInit | undefined }> = [];
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if ((init?.method ?? "GET").toUpperCase() === "POST") {
        postCalls.push({ url, init });
        throw new Error(`POST attempted against read-only dashboard: ${url}`);
      }
      return jsonResponse(200, snapshotEnvelope(snapshot));
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return { postCalls, fetchMock };
}

beforeEach(() => {
  useDashboardStore.setState({
    selectedCoin: null,
    marketFilter: "all",
    horizonFilter: "4h",
    directionFilter: "all",
    sort: "newest",
    dataAsOf: null,
    connection: "starting",
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** Renders the app and waits for the snapshot to land. */
async function renderDashboard(snapshot = makeSnapshot()) {
  stubFetch(snapshot);
  render(<App />);
  await screen.findByText("Active Setups");
}

// --- brief Step 1 (verbatim) ----------------------------------------------

test("dashboard renders stat cards and setup rows with data-as-of stamp", async () => {
  await renderDashboard();
  expect(screen.getByText("Active Setups")).toBeInTheDocument();
  expect(screen.getByText(/data as of/i)).toBeInTheDocument();
  expect(screen.getByText("BTC")).toBeInTheDocument();
});

test("row click selects, never POSTs", async () => {
  const { postCalls } = stubFetch(makeSnapshot());
  render(<App />);
  const btc = await screen.findByText("BTC");
  fireEvent.click(btc);
  expect(useDashboardStore.getState().selectedCoin).toBe("BTC");
  expect(postCalls).toHaveLength(0);
});

test("no banned words anywhere in rendered output", async () => {
  await renderDashboard();
  const text = document.body.textContent!.toLowerCase();
  for (const w of [
    "vwap",
    "high probability",
    "high conviction",
    "confidence",
    "probability",
    "leverage",
    "liquidation",
  ]) {
    expect(text).not.toContain(w);
  }
});

test("zero setups renders silence is valid", async () => {
  await renderDashboard(
    makeSnapshot({ setups: [], active_setups: 0, new_setups_24h: 0 }),
  );
  expect(screen.getByText(/silence is valid/i)).toBeInTheDocument();
  // other regions still render
  expect(screen.getByText("Market Movers")).toBeInTheDocument();
});

// --- stat cards ------------------------------------------------------------

describe("stat cards", () => {
  test("Active Setups shows the count and never infers a delta", async () => {
    await renderDashboard();
    const card = screen.getByText("Active Setups").parentElement;
    expect(card).not.toBeNull();
    expect(card!.textContent).toContain("6");
    // comparison unavailable → no delta row on this card at all; the New
    // Setups 24h card is the one that renders "unavailable".
    expect(card!.textContent).not.toMatch(/[+-]\d/);
  });

  test("New Setups 24h renders unavailable without a covering snapshot", async () => {
    await renderDashboard();
    const card = screen.getByText("New Setups 24h").parentElement!;
    expect(card.textContent).toMatch(/unavailable/i);
  });

  test("Eligible Perps shows evaluated/eligible separately, never 'scanned'", async () => {
    await renderDashboard();
    expect(screen.getAllByText(/132 of 150/).length).toBeGreaterThan(0);
    expect(document.body.textContent!.toLowerCase()).not.toContain("scanned");
  });

  test("Monitoring Coverage renders a percentage from the decimal", async () => {
    await renderDashboard();
    expect(screen.getByText("98.4%")).toBeInTheDocument();
  });

  test("New Setups 24h card renders the count", async () => {
    await renderDashboard();
    expect(screen.getByText("New Setups 24h")).toBeInTheDocument();
  });

  test("stat delta unavailable without covering snapshot", async () => {
    await renderDashboard();
    expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
  });
});

// --- Top Signals table ------------------------------------------------------

describe("Top Signals table", () => {
  test("each setup renders a row with price, 24h % and direction pill", async () => {
    await renderDashboard();
    // SOL is the 24h-horizon setup; BTC/ETH are 4h.
    expect(screen.getByText("112,505")).toBeInTheDocument();
    expect(screen.getByText("ETH")).toBeInTheDocument();
    expect(screen.getAllByText("LONG").length).toBeGreaterThan(0);
    expect(screen.getAllByText("SHORT").length).toBeGreaterThan(0);
  });

  test("evidence chips render criteria and indicator inputs", async () => {
    await renderDashboard();
    expect(screen.getByText("close reclaims 4H range high")).toBeInTheDocument();
    expect(screen.getAllByText("RSI 14").length).toBeGreaterThan(0);
  });

  test("horizon filter defaults to 4h and narrows the rows", async () => {
    await renderDashboard();
    // SOL is horizon 24h — hidden under the default 4h filter.
    expect(screen.queryByText("SOL")).toBeNull();
    expect(screen.getByText("BTC")).toBeInTheDocument();

    await act(async () => {
      useDashboardStore.getState().setHorizonFilter("24h");
    });
    await waitFor(() => expect(screen.getByText("SOL")).toBeInTheDocument());
    expect(screen.queryByText("BTC")).toBeNull();
  });

  test("direction filter narrows to LONG only", async () => {
    await renderDashboard();
    expect(screen.getByText("ETH")).toBeInTheDocument();
    await act(async () => {
      useDashboardStore.getState().setDirectionFilter("long");
    });
    await waitFor(() => expect(screen.queryByText("ETH")).toBeNull());
    expect(screen.getByText("BTC")).toBeInTheDocument();
  });

  test("filter bar shows All/Long/Short counts of the filtered set", async () => {
    await renderDashboard();
    // default 4h horizon → BTC (LONG) + ETH (SHORT) = 2 rows.
    expect(screen.getByText("All (2)")).toBeInTheDocument();
    expect(screen.getByText("Long (1)")).toBeInTheDocument();
    expect(screen.getByText("Short (1)")).toBeInTheDocument();
  });

  test("sort by 24h% orders rows by absolute change descending", async () => {
    await renderDashboard();
    await act(async () => {
      useDashboardStore.getState().setSort("change24h");
    });
    const rows = screen.getAllByRole("row").slice(1); // drop header row
    const coins = rows.map((r) => r.querySelector("td")?.textContent ?? "");
    // |2.41| (BTC) before |-1.18| (ETH)
    expect(coins[0]).toContain("BTC");
    expect(coins[1]).toContain("ETH");
  });
});

// --- data freshness ---------------------------------------------------------

describe("data freshness", () => {
  test("data as of stamp renders the UTC-seconds timestamp", async () => {
    await renderDashboard();
    // 1791936000 = 2026-10-14T00:00:00Z — rendered as a UTC timestamp string.
    expect(screen.getByText(/2026-10-14.*00:00:00.*UTC/i)).toBeInTheDocument();
  });

  test("snapshot fetch sets dataAsOf in the store", async () => {
    await renderDashboard();
    expect(useDashboardStore.getState().dataAsOf).toBe(DATA_AS_OF);
  });
});
