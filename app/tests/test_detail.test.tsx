// Detail tests (Task 9) — Market Overview + Coin detail panel + chart.
//
// Written RED first, before MarketOverview / CoinDetail / CandleChart
// exist. The task-brief tests come first (verbatim), then coverage for
// the behaviors the brief's Interfaces section specifies.
//
// HARD RULES under test (see app/src/api/types.ts header):
//   * fetch is stubbed (jsonResponse + stubDetail as in
//     test_dashboard.test.ts); the socket tripwire in tests/setup.ts
//     guards every egress path, and every stubbed call is a GET — any
//     POST attempt throws the read-only rule.
//   * Trade plan carries the literal reference-size line and NEVER a
//     leverage/liquidation field or word in rendered output.
//   * No 1m tab; a null RSI renders "unavailable", never 0.
//   * Timestamps are UTC-seconds ints; bars are closed only.
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { readFileSync } from "node:fs";
import type {
  BarTimeframe,
  Envelope,
  Bar,
  Bars,
  Metrics,
  Setup,
  TradePlan,
} from "../src/api/types";
import { MarketOverview } from "../src/components/MarketOverview";
import { CandleChart } from "../src/components/CandleChart";
import { CoinDetail } from "../src/components/CoinDetail";
import { useDashboardStore } from "../src/store/dashboard";

const DATA_AS_OF = 1791936000;
const SETUP_ID = "setup-btc-1";

function makeBar(i: number, base: number): Bar {
  const ts = DATA_AS_OF - (60 - i) * 300;
  return {
    ts,
    open: base + i * 2,
    high: base + i * 2 + 25,
    low: base + i * 2 - 20,
    close: base + i * 2 + 10,
    volume: 1000 + i * 15,
    closed: true,
  };
}

function makeBars(): Bar[] {
  return Array.from({ length: 60 }, (_, i) => makeBar(i, 105_000));
}

function barsEnvelope(bars: Bar[], coin = "BTC", tf: BarTimeframe = "15m"): Envelope<Bars> {
  return {
    ok: true,
    data: {
      coin,
      tf,
      bars,
      closed_only: true,
      count: bars.length,
      data_as_of: DATA_AS_OF,
      unavailable: null,
    },
    error: null,
  };
}

function makeSetup(overrides: Partial<Setup> = {}): Setup {
  return {
    id: SETUP_ID,
    coin: "BTC",
    direction: "LONG",
    horizon: "4h",
    state: "active",
    emitted_ts: DATA_AS_OF - 3600,
    expires_ts: DATA_AS_OF + 10800,
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
    ...overrides,
  };
}

function makeTradePlan(overrides: Partial<TradePlan> = {}): TradePlan {
  return {
    id: "tp-btc-1",
    setup_id: SETUP_ID,
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
    indicators: [
      { name: "RSI 14", value: 57.4 },
      { name: "MACD", value: 0.0211 },
      { name: "EMA 50", value: 110980.2 },
      { name: "EMA 200", value: 108214.7 },
      { name: "Volume +%", value: 138.5 },
    ],
    valid_until_ts: DATA_AS_OF + 10800,
    next_update_ts: DATA_AS_OF + 14400,
    analysis: ["BTC reclaimed the 4H range high on the 20:00 UTC close."],
    data_as_of: DATA_AS_OF,
    ...overrides,
  };
}

function makeMetrics(overrides: Partial<Metrics> = {}): Metrics {
  return {
    coin: "BTC",
    ts: DATA_AS_OF,
    rsi: 57.4,
    macd: 0.0211,
    ema50: 110980.2,
    ema200: 108214.7,
    volume_pct: 138.5,
    unavailable: null,
    data_as_of: DATA_AS_OF,
    ...overrides,
  };
}

function makeMetricsNullRsi(): Metrics {
  const m = makeMetrics();
  m.rsi = null;
  return m;
}

/**
 * Per-coin fixture overrides so a coin switch is observable: the stub
 * answers `/setup/<coin>` and `/tradeplan/<coin>` with that coin's own
 * name/price/ids (the panel reads both routes by coin symbol).
 */
function coinFixture(coin: string): Partial<Setup> & Partial<TradePlan> & Partial<Metrics> {
  if (coin !== "ETH") return {};
  return {
    coin: "ETH",
    name: "Ethereum",
    id: "tp-eth-1",
    setup_id: "setup-eth-1",
    direction: "SHORT",
    horizon: "4h",
    price: 4092.4,
    change_24h_pct: -1.18,
    rule: "range_reject_short",
    criteria: [{ name: "close rejects 4H range low", passed: true }],
    analysis: ["ETH rejected the 4H range low twice inside the last three bars."],
    tradeplan_id: "tp-eth-1",
    entry_zone: { low: 4080.5, high: 4105.2 },
    stop: 4180.0,
    tp1: 4020.0,
    tp2: 3960.0,
    indicators: [
      { name: "RSI 14", value: 42.8 },
      { name: "MACD", value: -0.0142 },
      { name: "EMA 50", value: 3988.1 },
      { name: "EMA 200", value: 4102.55 },
      { name: "Volume +%", value: 91.2 },
    ],
    rsi: 42.8,
    macd: -0.0142,
    ema50: 3988.1,
    ema200: 4102.55,
    volume_pct: 91.2,
  };
}

function notFoundEnvelope(): Envelope<null> {
  return {
    ok: false,
    data: null,
    error: { code: "NOT_FOUND", message: "no setup for coin" },
  };
}

// Builds a Response like fetch() would: real global Response so headers
// and .json() behave exactly as in production.
function jsonResponse(status: number, payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface DetailStub {
  postCalls: Array<{ url: string; init: RequestInit | undefined }>;
  barsCalls: string[];
  /** Releases every parked request for the deferred coin (no-op if none). */
  releaseCoin: () => void;
}

/**
 * Fetch stub for the detail panel: GETs answer with bars / metrics /
 * setup / tradeplan envelopes; ANY POST throws (the read-only rule).
 * `noSetup` makes every setup/plan lookup 404 (a coin with no setup);
 * `barsFails` makes the /bars fetch reject; `metrics` overrides the
 * metrics payload; `deferCoin` parks every request for that coin so a
 * selection change can be asserted mid-flight (no stale data visible).
 */
function stubDetail(opts: {
  noSetup?: boolean;
  barsFails?: boolean;
  metrics?: () => Metrics;
  deferCoin?: string;
} = {}): DetailStub {
  const postCalls: Array<{ url: string; init: RequestInit | undefined }> = [];
  const barsCalls: string[] = [];
  const deferred: Array<() => void> = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if ((init?.method ?? "GET").toUpperCase() === "POST") {
      postCalls.push({ url, init });
      throw new Error(`POST attempted against read-only dashboard: ${url}`);
    }
    // The deferred coin's reads are parked in flight — the panel must
    // show nothing for it, not the previous coin's data.
    if (opts.deferCoin !== undefined && url.includes(opts.deferCoin)) {
      await new Promise<void>((resolve) => deferred.push(resolve));
    }
    if (url.includes("/bars")) {
      barsCalls.push(url);
      if (opts.barsFails) throw new TypeError("network request failed");
      const coin = new URL(url, "http://localhost").searchParams.get("coin") ?? "BTC";
      const tf = (new URL(url, "http://localhost").searchParams.get("tf") ?? "15m") as BarTimeframe;
      return jsonResponse(200, barsEnvelope(makeBars(), coin, tf));
    }
    if (url.includes("/metrics")) {
      const coin = new URL(url, "http://localhost").searchParams.get("coin") ?? "BTC";
      return jsonResponse(200, {
        ok: true,
        data: opts.metrics
          ? opts.metrics()
          : makeMetrics({ ...coinFixture(coin) }),
        error: null,
      });
    }
    if (url.includes("/tradeplan/") || url.includes("/setup/")) {
      if (opts.noSetup) return jsonResponse(404, notFoundEnvelope());
      // The coin travels in the path (`/setup/<coin>`, `/tradeplan/<id>`);
      // answer with that coin's own fixture so a switch is observable.
      const coin = url.split("/").pop() ?? "BTC";
      const overrides = coinFixture(coin);
      return jsonResponse(200, {
        ok: true,
        data: url.includes("/tradeplan/")
          ? makeTradePlan(overrides)
          : makeSetup(overrides),
        error: null,
      });
    }
    return jsonResponse(404, notFoundEnvelope());
  });
  vi.stubGlobal("fetch", fetchMock);
  return {
    postCalls,
    barsCalls,
    // Releases whatever is currently parked; safe to call with nothing
    // parked (a no-op) and repeatable.
    releaseCoin: () => {
      for (const r of deferred.splice(0)) r();
    },
  };
}

/** Renders CoinDetail for BTC with the store primed. */
async function renderDetail(opts = {}) {
  useDashboardStore.getState().selectCoin("BTC");
  const stub = stubDetail(opts);
  render(<CoinDetail />);
  return stub;
}

beforeEach(() => {
  useDashboardStore.setState({
    selectedCoin: null,
    tab: "analysis",
    barTimeframe: "15m",
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// --- MarketOverview -------------------------------------------------------

describe("MarketOverview", () => {
  const overview = [
    { coin: "BTC", name: "Bitcoin", price: 112505.0, change_24h_pct: 2.41, last_bar_ts: null },
    { coin: "ETH", name: "Ethereum", price: 4092.4, change_24h_pct: -1.18, last_bar_ts: null },
  ];

  test("renders BTC and ETH rows with no TOTAL row", () => {
    render(<MarketOverview overview={overview} />);
    expect(screen.getByText("BTC")).toBeInTheDocument();
    expect(screen.getByText("ETH")).toBeInTheDocument();
    expect(screen.getByText("Bitcoin")).toBeInTheDocument();
    expect(document.body.textContent!.toLowerCase()).not.toContain("total");
  });

  test("clicking a row selects the coin and never POSTs", () => {
    const { postCalls } = stubDetail();
    render(<MarketOverview overview={overview} />);
    screen.getByText("ETH").click();
    expect(useDashboardStore.getState().selectedCoin).toBe("ETH");
    expect(postCalls).toHaveLength(0);
  });
});

// --- brief Step 1 (verbatim) ----------------------------------------------

describe("brief step 1", () => {
  test("detail shows trade plan with reference notional and no leverage", async () => {
    await renderDetail();
    await screen.findByText(/reference size, not a position/i);
    expect(document.body.textContent).not.toMatch(/leverage/i);
  });

  test("no-setup coin shows no setup yet + blocking reason", async () => {
    await renderDetail({ noSetup: true });
    await screen.findByText(/no setup yet/i);
    expect(
      screen.getByText(/no rule criteria met on the last sweep/i),
    ).toBeInTheDocument();
  });

  test("bars unavailable per tab on fetch failure", async () => {
    await renderDetail({ barsFails: true });
    await screen.findByText(/bars unavailable/i);
    expect(screen.getByText("Analysis")).toBeInTheDocument();
  });

  test("null rsi renders unavailable not 0", async () => {
    await renderDetail({ metrics: makeMetricsNullRsi });
    await screen.findByText(/unavailable/i);
    expect(document.body.textContent).not.toContain(">0<");
  });
});

// --- coin detail ----------------------------------------------------------

describe("coin detail", () => {
  test("header shows badge, COIN - Name, price, 24h % and signal pill", async () => {
    await renderDetail();
    expect(await screen.findByText("BTC - Bitcoin")).toBeInTheDocument();
    expect(screen.getByText("112,505")).toBeInTheDocument();
    expect(screen.getByText("+2.41%")).toBeInTheDocument();
    expect(screen.getByText("LONG")).toBeInTheDocument();
  });

  test("timeframe tabs omit 1m and switch the bars timeframe", async () => {
    const { barsCalls } = await renderDetail();
    for (const tf of ["5m", "15m", "1H", "4H", "1D"]) {
      expect(screen.getByText(tf)).toBeInTheDocument();
    }
    expect(screen.queryByText("1m")).toBeNull();
    await waitFor(() => expect(barsCalls.length).toBeGreaterThan(0));
    expect(barsCalls[0]).toContain("tf=15m");
    const { act } = await import("@testing-library/react");
    await act(async () => {
      useDashboardStore.getState().setBarTimeframe("4H");
    });
    await waitFor(() => expect(barsCalls.some((u) => u.includes("tf=4H"))).toBe(true));
  });

  test("trade plan tab shows entry zone, indicators, TP1/TP2/stop and footer", async () => {
    await renderDetail();
    const { act, fireEvent } = await import("@testing-library/react");
    const tab = await screen.findByText("Trade Plan");
    await act(async () => {
      fireEvent.click(tab);
    });
    const pane = screen.getByTestId("tradeplan-pane");
    expect(screen.getByText(/112,223\.7/)).toBeInTheDocument();
    expect(screen.getByText(/112,673\.8/)).toBeInTheDocument();
    expect(screen.getByText(/114,743\.8/)).toBeInTheDocument();
    expect(screen.getByText(/117,848\.8/)).toBeInTheDocument();
    expect(screen.getByText(/111,188\.7/)).toBeInTheDocument();
    expect(within(pane).getByText("RSI 14")).toBeInTheDocument();
    expect(within(pane).getByText(/57\.4/)).toBeInTheDocument();
    expect(screen.getByText(/Signal valid for/i)).toBeInTheDocument();
    expect(screen.getByText(/Next update in/i)).toBeInTheDocument();
    expect(
      within(pane).getByText(/reference size, not a position/i),
    ).toBeInTheDocument();
  });

  test("null rsi renders unavailable not 0", async () => {
    await renderDetail({ metrics: makeMetricsNullRsi });
    await screen.findByText(/unavailable/i);
    expect(document.body.textContent).not.toContain(">0<");
  });

  test("metrics tab renders the indicator values", async () => {
    await renderDetail();
    const { act, fireEvent } = await import("@testing-library/react");
    const tab = await screen.findByText("Metrics");
    await act(async () => {
      fireEvent.click(tab);
    });
    expect(screen.getByText(/0\.0211/)).toBeInTheDocument();
    expect(screen.getByText(/138\.5/)).toBeInTheDocument();
  });

  test("no banned words anywhere in the detail panel", async () => {
    await renderDetail();
    await screen.findByText(/reference size, not a position/i);
    const text = document.body.textContent!.toLowerCase();
    for (const w of [
      "vwap",
      "confidence",
      "probability",
      "leverage",
      "liquidation",
      "conviction",
    ]) {
      expect(text).not.toContain(w);
    }
  });

  test("nothing selected renders an honest empty state", () => {
    useDashboardStore.getState().selectCoin(null);
    stubDetail();
    render(<CoinDetail />);
    expect(screen.getByText(/select a coin/i)).toBeInTheDocument();
  });

  test("switching coins clears the previous coin's data until the new coin lands", async () => {
    // Regression (Important, fix round 1): the reset branch only ran for
    // a null selection, so BTC -> ETH kept setup/plan/metrics/bars on
    // screen while ETH's reads were in flight — the header rendered
    // "ETH - Bitcoin" with BTC's price and the chart showed BTC candles.
    const { releaseCoin } = stubDetail({ deferCoin: "ETH" });
    useDashboardStore.getState().selectCoin("BTC");
    render(<CoinDetail />);
    // BTC's data has landed.
    expect(await screen.findByText("BTC - Bitcoin")).toBeInTheDocument();
    expect(screen.getByText("112,505")).toBeInTheDocument();

    // Switch to ETH; every ETH read is parked in flight.
    await act(async () => {
      useDashboardStore.getState().selectCoin("ETH");
    });
    // Not one trace of BTC survives the switch: no BTC price, no BTC
    // name, no BTC RSI readout, and the chart shows the empty frame
    // (bars were reset, so there are no stale BTC candles either).
    expect(screen.queryByText("112,505")).toBeNull();
    expect(screen.queryByText("BTC - Bitcoin")).toBeNull();
    expect(screen.queryByText("Bitcoin")).toBeNull();
    expect(screen.queryByText(/RSI 14/)).toBeNull();
    expect(screen.getByText("ETH - ETH")).toBeInTheDocument();
    expect(screen.getByText("price unavailable")).toBeInTheDocument();
    expect(screen.getByTestId("candle-chart")).toHaveAttribute(
      "aria-label",
      "no chart data",
    );
    // The panes are loading, never the previous coin's content.
    expect(screen.getByText("loading…")).toBeInTheDocument();

    // ETH's reads land — now its own data renders (its price, its name).
    await act(async () => {
      releaseCoin();
    });
    expect(await screen.findByText("ETH - Ethereum")).toBeInTheDocument();
    expect(screen.getByText("4,092.4")).toBeInTheDocument();
    expect(screen.getByTestId("candle-chart")).toHaveAttribute(
      "aria-label",
      "price chart",
    );
  });
});

// --- chart renders candles + volume only (no client-side EMA) ----------

describe("chart overlays", () => {
  test("the frontend computes no indicator series — candles + volume only", () => {
    // Controller ruling (fix round 1): the spec constraint is explicit
    // ("frontend never computes indicators; indicators come from
    // /metrics"), and the client-side EMA seed disagreed numerically
    // with the backend's EMA (metrics showed EMA 50 110,980.20 while the
    // overlay sat elsewhere, both labeled EMA 50). The overlays were
    // REMOVED: this source guard keeps the computation from creeping
    // back in. The chart renders candles + volume only until the real
    // service provides EMA series via /bars.
    const read = (p: string) =>
      readFileSync(new URL(p, import.meta.url), "utf8");
    const chart = read("../src/components/CandleChart.tsx");
    // No line/EMA overlay series is ever created on the chart: no
    // LineSeries import and no ema identifier anywhere in the module
    // (prose may mention the ruling; code may not).
    expect(chart).not.toMatch(/LineSeries/);
    expect(chart).not.toMatch(/ema50|ema200|emaOverlay|--ema/i);
    const detail = read("../src/components/CoinDetail.tsx");
    // The EMA smoothing constant and its seed are gone from the panel;
    // the only EMA references left are the /metrics scalar readouts.
    expect(detail).not.toMatch(/2 \/ \(period \+ 1\)/);
    expect(detail).toContain('"EMA 50", metrics.ema50');
    expect(detail).toContain('"EMA 200", metrics.ema200');
    // The removed overlay colors no longer exist as theme tokens.
    const theme = read("../src/theme.css");
    expect(theme).not.toMatch(/--ema/i);
  });

  test("CandleChart renders candles and volume with real bars", async () => {
    // The envelope's data is non-null by construction here.
    const data = barsEnvelope(makeBars()).data as Bars;
    render(<CandleChart bars={data} />);
    // jsdom has no canvas, so no chart DOM is created — the frame stays
    // and the aria-label reports real data (not the empty frame).
    expect(screen.getByTestId("candle-chart")).toHaveAttribute(
      "aria-label",
      "price chart",
    );
  });
});
