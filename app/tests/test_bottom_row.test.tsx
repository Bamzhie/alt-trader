// Bottom-row tests (Task 10) — Market Movers, Recent Analysis, Activity
// Log, and the full Dashboard grid. RED first: written against the
// documented contract (spec §4 "Bottom row") before any of the three
// components exist.
//
// House style: the task-brief tests come first (adapted only to await the
// snapshot, exactly as test_dashboard.test.tsx does), then coverage for
// every behavior the brief's Interfaces section specifies.
//
// HARD RULES under test (see app/src/api/types.ts header):
//   * fetch is stubbed (jsonResponse + stubFetch); the socket tripwire in
//     tests/setup.ts guards every egress path.
//   * READ-ONLY — every stubbed call is a GET; the POST stub throws.
//   * Banned words are asserted absent from rendered output.
//   * Nothing is fabricated: a measure the snapshot does not carry renders
//     as an explicit unavailability, never an invented ordering.
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import App from "../src/App";
import { ActivityLog } from "../src/components/ActivityLog";
import { MarketMovers } from "../src/components/MarketMovers";
import { RecentAnalysis } from "../src/components/RecentAnalysis";
import { useDashboardStore } from "../src/store/dashboard";
import { healthzOk, readyzOk, sseStreamResponse } from "./sse_stub";
import type {
  ActivityEntry,
  Envelope,
  Mover,
  RecentAnalysisEntry,
  Snapshot,
} from "../src/api/types";

const DATA_AS_OF = 1791936000;

// --- fixture snapshot (mirrors mock_service.py _snapshot()) ----------------

function makeSnapshot(): Snapshot {
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
        criteria: [{ name: "close reclaims 4H range high", passed: true }],
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
      },
    ],
    comparison_24h: {
      available: false,
      reason: "no dashboard snapshot at or before now-24h — comparison unavailable, not inferred",
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
    ],
    coins_without_setup: [{ coin: "XRP", name: "XRP", blocking_reason: "no setup yet" }],
    market_overview: [
      { coin: "BTC", name: "Bitcoin", price: 112505.0, change_24h_pct: 2.41, last_bar_ts: null },
      { coin: "ETH", name: "Ethereum", price: 4092.4, change_24h_pct: -1.18, last_bar_ts: null },
    ],
    movers: [
      { coin: "SOL", name: "Solana", change_24h_pct: 4.87 },
      { coin: "BTC", name: "Bitcoin", change_24h_pct: 2.41 },
      { coin: "AVAX", name: "Avalanche", change_24h_pct: 1.05 },
      { coin: "LINK", name: "Chainlink", change_24h_pct: -0.74 },
      { coin: "DOGE", name: "Dogecoin", change_24h_pct: -3.62 },
    ],
    // Six entries on purpose: the panel shows the last five, so the
    // oldest (XRP) must never render.
    recent_analysis: [
      { ts: 1791934200, coin: "LINK", direction: "SHORT", text: "LINK rejected the 4H range low." },
      { ts: 1791933900, coin: "ETH", direction: "SHORT", text: "ETH printed a lower high." },
      { ts: 1791928800, coin: "AVAX", direction: "LONG", text: "AVAX reclaimed the range high." },
      { ts: 1791927000, coin: "SOL", direction: "LONG", text: "SOL closed above its swing high." },
      { ts: 1791921600, coin: "DOGE", direction: "SHORT", text: "DOGE rolled into a lower high." },
      { ts: 1791918000, coin: "XRP", direction: "LONG", text: "XRP reclaimed the range high." },
    ],
    // Ten entries on purpose: the journal caps what it displays, so the
    // two oldest must never render.
    activity: [
      {
        ts: 1791935400,
        kind: "sweep-complete",
        coin: null,
        text: "Sweep complete — 132 of 150 eligible perps evaluated, 6 active setups.",
      },
      { ts: 1791934200, kind: "emission", coin: "LINK", text: "LINK setup emitted — range rejection, 4H horizon." },
      { ts: 1791933900, kind: "invalidation", coin: "ARB", text: "ARB setup invalidated — close reclaimed the range high." },
      { ts: 1791930600, kind: "emission", coin: "ETH", text: "ETH setup emitted — range rejection, 4H horizon." },
      { ts: 1791929400, kind: "expiry", coin: "OP", text: "OP setup expired at horizon end with no resolution." },
      { ts: 1791928800, kind: "emission", coin: "AVAX", text: "AVAX setup emitted — range reclaim, 4H horizon." },
      { ts: 1791927000, kind: "emission", coin: "SOL", text: "SOL setup emitted — breakout continuation, 4H horizon." },
      { ts: 1791921600, kind: "emission", coin: "DOGE", text: "DOGE setup emitted — range rejection, 24H horizon." },
      { ts: 1791918060, kind: "emission", coin: "BTC", text: "BTC setup emitted — daily reclaim." },
      { ts: 1791914400, kind: "emission", coin: "AAVE", text: "AAVE setup emitted — daily reclaim." },
    ],
    supported_timeframes: ["5m", "15m", "1H", "4H", "1D"],
    event_kinds: ["setup.emitted", "sweep.completed"],
    unavailable: null,
  };
}

function snapshotEnvelope(snapshot: Snapshot): Envelope<Snapshot> {
  return { ok: true, data: snapshot, error: null };
}

function jsonResponse(status: number, payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

/**
 * Fetch stub: GETs answer with the snapshot envelope; ANY POST throws
 * (the read-only rule) and is recorded on `postCalls`. Bars answer empty
 * and valid so the sparklines keep their honest empty frame.
 */
function stubFetch(snapshot: Snapshot) {
  const postCalls: Array<{ url: string; init: RequestInit | undefined }> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if ((init?.method ?? "GET").toUpperCase() === "POST") {
      postCalls.push({ url, init });
      throw new Error(`POST attempted against read-only dashboard: ${url}`);
    }
    if (url.includes("/bars")) {
      const coin = new URL(url, "http://localhost").searchParams.get("coin") ?? "";
      const tf = new URL(url, "http://localhost").searchParams.get("tf") ?? "15m";
      return jsonResponse(200, {
        ok: true,
        data: { coin, tf, bars: [], closed_only: true, count: 0, data_as_of: DATA_AS_OF, unavailable: null },
        error: null,
      });
    }
    if (url.includes("/metrics")) {
      const coin = new URL(url, "http://localhost").searchParams.get("coin") ?? "";
      return jsonResponse(200, {
        ok: true,
        data: {
          coin,
          ts: DATA_AS_OF,
          rsi: null,
          macd: null,
          ema50: null,
          ema200: null,
          volume_pct: null,
          unavailable: null,
          data_as_of: DATA_AS_OF,
        },
        error: null,
      });
    }
    if (url.includes("/tradeplan/") || url.includes("/setup/")) {
      return jsonResponse(404, {
        ok: false,
        data: null,
        error: { code: "NOT_FOUND", message: "no setup for coin" },
      });
    }
    // Contract answers for the routes the wired Dashboard now opens —
    // see tests/sse_stub.ts (a JSON body on /events would end the stream
    // at once and spin the hook's reconnect loop for the whole test).
    if (url.endsWith("/healthz")) return healthzOk();
    if (url.endsWith("/readyz")) return readyzOk();
    if (url.endsWith("/events")) {
      return sseStreamResponse(init?.signal ?? null).response;
    }
    return jsonResponse(200, snapshotEnvelope(snapshot));
  });
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
    moversTab: "gainers",
    tab: "analysis",
    barTimeframe: "15m",
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
  const stub = stubFetch(snapshot);
  render(<App />);
  await screen.findByText("Market Movers");
  return stub;
}

/** The <section> a panel heading belongs to. */
function panelFor(heading: string): HTMLElement {
  const title = screen.getByText(heading);
  const section = title.closest("section");
  expect(section).not.toBeNull();
  return section as HTMLElement;
}

// --- brief Step 1 (adapted only to await the snapshot) ----------------------

test("bottom row renders all three regions", async () => {
  await renderDashboard();
  expect(screen.getByText("Market Movers")).toBeInTheDocument();
  expect(screen.getByText("Recent Analysis")).toBeInTheDocument();
  expect(screen.getByText("Activity Log")).toBeInTheDocument();
});

test("activity log shows sweep-complete entry", async () => {
  await renderDashboard();
  expect(screen.getByText(/sweep complete/i)).toBeInTheDocument();
});

// --- Market Movers ----------------------------------------------------------

describe("Market Movers", () => {
  test("gainers tab lists only the positive movers, worst-first excluded", async () => {
    await renderDashboard();
    const panel = panelFor("Market Movers");
    // Every gainer renders, no loser does — the assertion has teeth in
    // both directions, not just "a list exists".
    for (const coin of ["SOL", "BTC", "AVAX"]) {
      expect(within(panel).getByText(coin)).toBeInTheDocument();
    }
    for (const coin of ["LINK", "DOGE"]) {
      expect(within(panel).queryByText(coin)).toBeNull();
    }
  });

  test("gainers are ordered by change descending", async () => {
    await renderDashboard();
    const panel = panelFor("Market Movers");
    const tickers = Array.from(panel.querySelectorAll("li"))
      .map((li) => li.textContent ?? "")
      .filter((t) => /SOL|BTC|AVAX/.test(t));
    const order = tickers.map((t) => (t.includes("SOL") ? 0 : t.includes("BTC") ? 1 : 2));
    expect(order).toEqual([0, 1, 2]);
    // The change % is rendered signed, never bare.
    expect(within(panel).getByText("+4.87%")).toBeInTheDocument();
  });

  test("losers tab lists only the negative movers, biggest loss first", async () => {
    await renderDashboard();
    await act(async () => {
      useDashboardStore.getState().setMoversTab("losers");
    });
    const panel = panelFor("Market Movers");
    for (const coin of ["DOGE", "LINK"]) {
      expect(within(panel).getByText(coin)).toBeInTheDocument();
    }
    for (const coin of ["SOL", "BTC", "AVAX"]) {
      expect(within(panel).queryByText(coin)).toBeNull();
    }
    expect(within(panel).getByText("-3.62%")).toBeInTheDocument();
  });

  test("clicking a tab button writes the store, nothing else", async () => {
    const { postCalls } = await renderDashboard();
    fireEvent.click(screen.getByText("Losers"));
    expect(useDashboardStore.getState().moversTab).toBe("losers");
    await waitFor(() => {
      const panel = panelFor("Market Movers");
      expect(within(panel).getByText("DOGE")).toBeInTheDocument();
    });
    expect(postCalls).toHaveLength(0);
  });

  test("volume tab states the unavailability instead of inventing an order", async () => {
    // The /snapshot contract carries no quote-volume measure on `movers`
    // (Mover is coin/name/change_24h_pct only), so the High Volume tab
    // says so outright — a fabricated ranking would be worse than none.
    await renderDashboard();
    await act(async () => {
      useDashboardStore.getState().setMoversTab("volume");
    });
    const panel = panelFor("Market Movers");
    expect(within(panel).getByText(/volume ranking unavailable/i)).toBeInTheDocument();
    // No ticker is claimed as a volume leader.
    for (const coin of ["SOL", "BTC", "AVAX", "LINK", "DOGE"]) {
      expect(within(panel).queryByText(coin)).toBeNull();
    }
  });

  test("empty movers render an honest empty state per tab", () => {
    stubFetch(makeSnapshot());
    render(<MarketMovers movers={[]} tab="gainers" />);
    expect(screen.getByText(/no gainers/i)).toBeInTheDocument();
  });

  test("a mover row click selects the coin and never POSTs", async () => {
    const { postCalls } = stubFetch(makeSnapshot());
    render(<MarketMovers movers={[{ coin: "SOL", name: "Solana", change_24h_pct: 4.87 }]} tab="gainers" />);
    fireEvent.click(screen.getByText("SOL"));
    expect(useDashboardStore.getState().selectedCoin).toBe("SOL");
    expect(postCalls).toHaveLength(0);
  });
});

// --- Recent Analysis --------------------------------------------------------

describe("Recent Analysis", () => {
  const entries: RecentAnalysisEntry[] = [
    { ts: 1791934200, coin: "LINK", direction: "SHORT", text: "LINK rejected the 4H range low." },
    { ts: 1791930600, coin: "ETH", direction: "SHORT", text: "ETH printed a lower high." },
    { ts: 1791928800, coin: "AVAX", direction: "LONG", text: "AVAX reclaimed the range high." },
    { ts: 1791927000, coin: "SOL", direction: "LONG", text: "SOL closed above its swing high." },
    { ts: 1791921600, coin: "DOGE", direction: "SHORT", text: "DOGE rolled into a lower high." },
    { ts: 1791918000, coin: "XRP", direction: "LONG", text: "XRP reclaimed the range high." },
  ];

  test("renders the last five transitions with time ago, coin and direction", async () => {
    await renderDashboard();
    const panel = panelFor("Recent Analysis");
    // 1791936000 (data_as_of) - 1791934200 = 1800s = 30m ago.
    expect(within(panel).getByText("30m ago")).toBeInTheDocument();
    expect(within(panel).getByText("LINK")).toBeInTheDocument();
    expect(within(panel).getAllByText("SHORT").length).toBeGreaterThan(0);
    expect(within(panel).getAllByText("LONG").length).toBeGreaterThan(0);
    // The sixth (oldest) entry is the one dropped by the cap.
    expect(within(panel).queryByText("XRP")).toBeNull();
  });

  test("entries render newest first", async () => {
    await renderDashboard();
    const panel = panelFor("Recent Analysis");
    const coins = Array.from(panel.querySelectorAll("li")).map(
      (li) => li.textContent ?? "",
    );
    const first = coins.findIndex((t) => t.includes("LINK"));
    const last = coins.findIndex((t) => t.includes("DOGE"));
    expect(first).toBeGreaterThanOrEqual(0);
    expect(last).toBeGreaterThan(first);
  });

  test("time ago is measured against the snapshot's data_as_of, not the wall clock", async () => {
    // The store carries no dataAsOf until the snapshot lands, so the
    // relative stamp can never drift with the render time.
    await renderDashboard();
    expect(useDashboardStore.getState().dataAsOf).toBe(DATA_AS_OF);
    const panel = panelFor("Recent Analysis");
    expect(within(panel).getByText("30m ago")).toBeInTheDocument();
  });

  test("no reference stamp renders no relative time rather than a guess", () => {
    render(<RecentAnalysis entries={entries.slice(0, 1)} />);
    // dataAsOf is null in a bare render — "unavailable" (the house word),
    // never "0m ago".
    expect(screen.getByText("unavailable")).toBeInTheDocument();
    expect(screen.queryByText(/ago$/)).toBeNull();
  });

  test("empty entries render an honest empty state", () => {
    render(<RecentAnalysis entries={[]} />);
    expect(screen.getByText(/no transitions yet/i)).toBeInTheDocument();
  });
});

// --- Activity Log -----------------------------------------------------------

describe("Activity Log", () => {
  const entries: ActivityEntry[] = [
    {
      ts: 1791935400,
      kind: "sweep-complete",
      coin: null,
      text: "Sweep complete — 132 of 150 eligible perps evaluated, 6 active setups.",
    },
    { ts: 1791934200, kind: "emission", coin: "LINK", text: "LINK setup emitted — range rejection." },
    { ts: 1791933900, kind: "invalidation", coin: "ARB", text: "ARB setup invalidated." },
    { ts: 1791930600, kind: "emission", coin: "ETH", text: "ETH setup emitted — range rejection." },
    { ts: 1791929400, kind: "expiry", coin: "OP", text: "OP setup expired at horizon end." },
    { ts: 1791928800, kind: "emission", coin: "AVAX", text: "AVAX setup emitted — range reclaim." },
    { ts: 1791927000, kind: "emission", coin: "SOL", text: "SOL setup emitted — breakout." },
    { ts: 1791921600, kind: "emission", coin: "DOGE", text: "DOGE setup emitted — range rejection." },
    { ts: 1791918060, kind: "emission", coin: "BTC", text: "BTC setup emitted — daily reclaim." },
    { ts: 1791914400, kind: "emission", coin: "AAVE", text: "AAVE setup emitted — daily reclaim." },
  ];

  test("renders the journal newest first with its kind label", async () => {
    await renderDashboard();
    const panel = panelFor("Activity Log");
    const rows = Array.from(panel.querySelectorAll("li"));
    // The cap keeps the newest eight; the two oldest never render.
    expect(rows).toHaveLength(8);
    expect(within(panel).getByText(/sweep complete/i)).toBeInTheDocument();
    expect(within(panel).getByText("sweep-complete")).toBeInTheDocument();
    expect(within(panel).getByText("invalidation")).toBeInTheDocument();
    expect(within(panel).getByText("expiry")).toBeInTheDocument();
    expect(
      within(panel).queryByText("BTC setup emitted — daily reclaim."),
    ).toBeNull();
    expect(
      within(panel).queryByText("AAVE setup emitted — daily reclaim."),
    ).toBeNull();
    // The cap is disclosed, never silently truncated.
    expect(within(panel).getByText(/showing 8 of 10/i)).toBeInTheDocument();
  });

  test("newest first even when the feed hands them in out of order", () => {
    render(
      <ActivityLog
        entries={[
          { ts: 1791914400, kind: "emission", coin: "AAVE", text: "AAVE setup emitted." },
          { ts: 1791935400, kind: "sweep-complete", coin: null, text: "Sweep complete." },
        ]}
      />,
    );
    const rows = Array.from(screen.getAllByRole("listitem"));
    expect(rows[0].textContent).toMatch(/sweep complete/i);
    expect(rows[1].textContent).toMatch(/AAVE/);
  });

  test("empty journal renders \"no activity yet\"", () => {
    render(<ActivityLog entries={[]} />);
    expect(screen.getByText("no activity yet")).toBeInTheDocument();
  });

  test("no reference stamp renders no relative time rather than a guess", () => {
    render(<ActivityLog entries={[entries[1]]} />);
    // dataAsOf is null in a bare render — "unavailable" (the house word),
    // never "0m ago".
    expect(screen.getByText("unavailable")).toBeInTheDocument();
  });
});

// --- full page wiring -------------------------------------------------------

describe("full page grid", () => {
  test("every dashboard region renders in the wired page", async () => {
    await renderDashboard();
    // "Dashboard" is both the sidebar nav item and the page heading.
    expect(screen.getAllByText("Dashboard").length).toBeGreaterThan(0);
    expect(screen.getByText(/data as of/i)).toBeInTheDocument();
    expect(screen.getByText("Active Setups")).toBeInTheDocument();
    expect(screen.getByText("Top Signals")).toBeInTheDocument();
    expect(screen.getByText("Market Overview")).toBeInTheDocument();
    expect(screen.getByText(/select a coin/i)).toBeInTheDocument();
    expect(screen.getByText("Market Movers")).toBeInTheDocument();
    expect(screen.getByText("Recent Analysis")).toBeInTheDocument();
    expect(screen.getByText("Activity Log")).toBeInTheDocument();
  });

  test("zero setups still renders every region — silence is valid", async () => {
    const snapshot = makeSnapshot();
    snapshot.setups = [];
    snapshot.active_setups = 0;
    await renderDashboard(snapshot);
    expect(screen.getByText(/silence is valid/i)).toBeInTheDocument();
    expect(screen.getByText("Market Movers")).toBeInTheDocument();
    expect(screen.getByText("Recent Analysis")).toBeInTheDocument();
    expect(screen.getByText("Activity Log")).toBeInTheDocument();
  });

  test("empty bottom-row payloads render honest empty states, never fabricated rows", async () => {
    const snapshot = makeSnapshot();
    snapshot.movers = [];
    snapshot.recent_analysis = [];
    snapshot.activity = [];
    await renderDashboard(snapshot);
    expect(within(panelFor("Market Movers")).getByText(/no gainers/i)).toBeInTheDocument();
    expect(within(panelFor("Recent Analysis")).getByText(/no transitions yet/i)).toBeInTheDocument();
    expect(within(panelFor("Activity Log")).getByText("no activity yet")).toBeInTheDocument();
  });

  test("no banned words anywhere in the rendered page", async () => {
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
      "scanned",
    ]) {
      expect(text).not.toContain(w);
    }
  });
});

// --- standalone component props --------------------------------------------

describe("component props", () => {
  const movers: Mover[] = [
    { coin: "SOL", name: "Solana", change_24h_pct: 4.87 },
    { coin: "BTC", name: "Bitcoin", change_24h_pct: 2.41 },
  ];

  test("MarketMovers renders badge, ticker and change % per row", () => {
    render(<MarketMovers movers={movers} tab="gainers" />);
    for (const coin of ["SOL", "BTC"]) {
      expect(screen.getByText(coin)).toBeInTheDocument();
    }
    expect(screen.getByText("+4.87%")).toBeInTheDocument();
    expect(screen.getByText("+2.41%")).toBeInTheDocument();
  });

  test("MarketMovers renders all three tabs with the active one from the store", () => {
    render(<MarketMovers movers={movers} tab="gainers" />);
    expect(screen.getByText("Gainers")).toBeInTheDocument();
    expect(screen.getByText("Losers")).toBeInTheDocument();
    expect(screen.getByText("High Volume")).toBeInTheDocument();
    expect(screen.getByText("Gainers").getAttribute("aria-pressed")).toBe("true");
  });

  test("MarketMovers issues no request of its own", () => {
    const { fetchMock } = stubFetch(makeSnapshot());
    render(<MarketMovers movers={movers} tab="gainers" />);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
