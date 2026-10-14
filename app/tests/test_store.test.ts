import { beforeEach, describe, expect, test, vi } from "vitest";
import { useDashboardStore } from "../src/store/dashboard";

beforeEach(() => {
  useDashboardStore.setState({
    selectedCoin: null,
    marketFilter: "all",
    horizonFilter: "4h",
    directionFilter: "all",
    sort: "newest",
    tab: "analysis",
    barTimeframe: "15m",
    moversTab: "gainers",
    connection: "starting",
    dataAsOf: null,
  });
});

// --- brief Step 1 (verbatim) ---------------------------------------------

test("row click selects coin only", () => {
  const spy = vi.fn();
  vi.stubGlobal("fetch", spy);
  useDashboardStore.getState().selectCoin("BTC");
  expect(useDashboardStore.getState().selectedCoin).toBe("BTC");
  expect(spy).not.toHaveBeenCalled();
});

test("defaults: horizon 4h, sort newest, direction all", () => {
  const s = useDashboardStore.getState();
  expect(s.horizonFilter).toBe("4h");
  expect(s.sort).toBe("newest");
  expect(s.directionFilter).toBe("all");
});

// --- additional defaults ---------------------------------------------------

test("defaults: market all, tab analysis, timeframe 15m, movers gainers, connection starting, dataAsOf null, no selection", () => {
  const s = useDashboardStore.getState();
  expect(s.marketFilter).toBe("all");
  expect(s.tab).toBe("analysis");
  expect(s.barTimeframe).toBe("15m");
  expect(s.moversTab).toBe("gainers");
  expect(s.connection).toBe("starting");
  expect(s.dataAsOf).toBe(null);
  expect(s.selectedCoin).toBe(null);
});

test("barTimeframe default is never 1m", () => {
  expect(["5m", "15m", "1H", "4H", "1D"]).toContain(
    useDashboardStore.getState().barTimeframe,
  );
});

// --- real guard on the store's INITIAL defaults ----------------------------
// The tests above run after the beforeEach reset fixture, so they only prove
// the fixture's values round-trip — a regression of the defaults in
// src/store/dashboard.ts would slip through. This block re-imports the module
// fresh (no beforeEach reset ran against it) and asserts the state as the
// store creates it, before any setState call.

describe("initial defaults (fresh module, no reset fixture)", () => {
  test("getState() before any setState matches the store's initial defaults", async () => {
    vi.resetModules();
    const { useDashboardStore: freshStore } = await import(
      "../src/store/dashboard"
    );
    const s = freshStore.getState();
    expect(s.horizonFilter).toBe("4h");
    expect(s.sort).toBe("newest");
    expect(s.directionFilter).toBe("all");
    expect(s.marketFilter).toBe("all");
    expect(s.tab).toBe("analysis");
    expect(s.barTimeframe).toBe("15m");
    expect(s.moversTab).toBe("gainers");
    expect(s.connection).toBe("starting");
    expect(s.dataAsOf).toBe(null);
    expect(s.selectedCoin).toBe(null);
  });
});

// --- setters ---------------------------------------------------------------

test("selectCoin(null) clears the selection", () => {
  useDashboardStore.getState().selectCoin("ETH");
  useDashboardStore.getState().selectCoin(null);
  expect(useDashboardStore.getState().selectedCoin).toBe(null);
});

test("setMarketFilter writes marketFilter", () => {
  useDashboardStore.getState().setMarketFilter("top20");
  expect(useDashboardStore.getState().marketFilter).toBe("top20");
  useDashboardStore.getState().setMarketFilter("tail");
  expect(useDashboardStore.getState().marketFilter).toBe("tail");
  useDashboardStore.getState().setMarketFilter("all");
  expect(useDashboardStore.getState().marketFilter).toBe("all");
});

test("setHorizonFilter writes horizonFilter", () => {
  useDashboardStore.getState().setHorizonFilter("24h");
  expect(useDashboardStore.getState().horizonFilter).toBe("24h");
});

test("setDirectionFilter writes directionFilter", () => {
  useDashboardStore.getState().setDirectionFilter("long");
  expect(useDashboardStore.getState().directionFilter).toBe("long");
  useDashboardStore.getState().setDirectionFilter("short");
  expect(useDashboardStore.getState().directionFilter).toBe("short");
});

test("setSort writes sort", () => {
  useDashboardStore.getState().setSort("expiry");
  expect(useDashboardStore.getState().sort).toBe("expiry");
  useDashboardStore.getState().setSort("change24h");
  expect(useDashboardStore.getState().sort).toBe("change24h");
});

test("setTab writes tab", () => {
  useDashboardStore.getState().setTab("tradeplan");
  expect(useDashboardStore.getState().tab).toBe("tradeplan");
  useDashboardStore.getState().setTab("metrics");
  expect(useDashboardStore.getState().tab).toBe("metrics");
});

test("setBarTimeframe writes barTimeframe", () => {
  useDashboardStore.getState().setBarTimeframe("4H");
  expect(useDashboardStore.getState().barTimeframe).toBe("4H");
  useDashboardStore.getState().setBarTimeframe("1D");
  expect(useDashboardStore.getState().barTimeframe).toBe("1D");
  useDashboardStore.getState().setBarTimeframe("5m");
  expect(useDashboardStore.getState().barTimeframe).toBe("5m");
});

test("setMoversTab writes moversTab", () => {
  useDashboardStore.getState().setMoversTab("losers");
  expect(useDashboardStore.getState().moversTab).toBe("losers");
  useDashboardStore.getState().setMoversTab("volume");
  expect(useDashboardStore.getState().moversTab).toBe("volume");
});

test("setConnection writes connection", () => {
  useDashboardStore.getState().setConnection("connected");
  expect(useDashboardStore.getState().connection).toBe("connected");
  useDashboardStore.getState().setConnection("error");
  expect(useDashboardStore.getState().connection).toBe("error");
});

test("setDataAsOf writes a UTC-seconds integer", () => {
  useDashboardStore.getState().setDataAsOf(1_764_158_400);
  expect(useDashboardStore.getState().dataAsOf).toBe(1_764_158_400);
});

test("every action is a no-op for the network", () => {
  const spy = vi.fn();
  vi.stubGlobal("fetch", spy);
  const s = useDashboardStore.getState();
  s.selectCoin("SOL");
  s.setMarketFilter("tail");
  s.setHorizonFilter("24h");
  s.setDirectionFilter("short");
  s.setSort("change24h");
  s.setTab("metrics");
  s.setBarTimeframe("1H");
  s.setMoversTab("volume");
  s.setConnection("connected");
  s.setDataAsOf(1_764_158_400);
  expect(spy).not.toHaveBeenCalled();
});

test("unrelated state is untouched when one field changes", () => {
  useDashboardStore.getState().selectCoin("BTC");
  useDashboardStore.getState().setTab("tradeplan");
  const s = useDashboardStore.getState();
  expect(s.selectedCoin).toBe("BTC");
  expect(s.tab).toBe("tradeplan");
  expect(s.horizonFilter).toBe("4h");
  expect(s.sort).toBe("newest");
  expect(s.directionFilter).toBe("all");
  expect(s.marketFilter).toBe("all");
  expect(s.barTimeframe).toBe("15m");
  expect(s.moversTab).toBe("gainers");
  expect(s.connection).toBe("starting");
  expect(s.dataAsOf).toBe(null);
});

describe("forbidden content", () => {
  test("state has no confidence/probability/tier/leverage/liquidation keys", () => {
    const s = useDashboardStore.getState() as unknown as Record<string, unknown>;
    for (const key of Object.keys(s)) {
      expect(key).not.toMatch(/confidence|probability|tier|leverage|liquidation/i);
    }
  });
});
