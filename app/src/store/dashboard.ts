// Zustand dashboard store — pure client UI state, NO network access.
//
// HARD RULES (see app/src/api/types.ts header):
//   * READ-ONLY frontend — this store never issues fetches; tests assert zero
//     `fetch` invocations from every action.
//   * No confidence / probability / tier field anywhere.
//   * No 1m timeframe — barTimeframe is BarTimeframe (5m|15m|1H|4H|1D) only.
//   * No leverage or liquidation field anywhere.
//   * Timestamps are UTC-seconds integers.
//
// Zustand without middleware: vanilla create() only. The store is importable
// in tests via `useDashboardStore.getState()`.
import { create } from "zustand";
import type { BarTimeframe } from "../api/types";

/** Market segment of the setups table. */
export type MarketFilter = "all" | "top20" | "tail";
/** Horizon shown by the analysis view. */
export type HorizonFilter = "4h" | "24h";
/** Direction narrowing for the setups table. */
export type DirectionFilter = "all" | "long" | "short";
/** Sort order of the setups table. */
export type SortKey = "newest" | "expiry" | "change24h";
/** Detail tab rendered for the selected coin. */
export type Tab = "analysis" | "tradeplan" | "metrics";
/** Sub-tab of the movers panel. */
export type MoversTab = "gainers" | "losers" | "volume";
/** SSE / snapshot connection lifecycle. */
export type ConnectionState = "starting" | "connected" | "error";

export interface DashboardState {
  /** Coin symbol selected by a row click; null = nothing selected. */
  selectedCoin: string | null;
  marketFilter: MarketFilter;
  horizonFilter: HorizonFilter;
  directionFilter: DirectionFilter;
  sort: SortKey;
  tab: Tab;
  barTimeframe: BarTimeframe;
  moversTab: MoversTab;
  connection: ConnectionState;
  /** UTC-seconds timestamp of the latest data; null until first snapshot. */
  dataAsOf: number | null;
}

export interface DashboardActions {
  selectCoin(c: string | null): void;
  setMarketFilter(m: MarketFilter): void;
  setHorizonFilter(h: HorizonFilter): void;
  setDirectionFilter(d: DirectionFilter): void;
  setSort(s: SortKey): void;
  setTab(t: Tab): void;
  setBarTimeframe(tf: BarTimeframe): void;
  setMoversTab(m: MoversTab): void;
  setConnection(c: ConnectionState): void;
  setDataAsOf(ts: number | null): void;
}

export type DashboardStore = DashboardState & DashboardActions;

export const useDashboardStore = create<DashboardStore>((set) => ({
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

  // Pure setters — no side effects, no fetch, no other state touched.
  selectCoin: (c) => set({ selectedCoin: c }),
  setMarketFilter: (m) => set({ marketFilter: m }),
  setHorizonFilter: (h) => set({ horizonFilter: h }),
  setDirectionFilter: (d) => set({ directionFilter: d }),
  setSort: (s) => set({ sort: s }),
  setTab: (t) => set({ tab: t }),
  setBarTimeframe: (tf) => set({ barTimeframe: tf }),
  setMoversTab: (m) => set({ moversTab: m }),
  setConnection: (c) => set({ connection: c }),
  setDataAsOf: (ts) => set({ dataAsOf: ts }),
}));
