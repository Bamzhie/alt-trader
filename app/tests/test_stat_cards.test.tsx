// StatCards tests (Task 8) — the four stat cards and their delta handling.
//
// HARD RULES under test (see app/src/api/types.ts header):
//   * A 24h delta with no covering snapshot renders "unavailable" — never
//     an inferred or carry-over number.
//   * Universe wording is "eligible"/"evaluated" — never "scanned".
//   * StatCards is a pure render of the snapshot: fetch is stubbed and only
//     GETs are allowed; the socket tripwire guards every egress path.
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { StatCards } from "../src/components/StatCards";
import type { Snapshot } from "../src/api/types";

const DATA_AS_OF = 1791936000;

function baseSnapshot(): Snapshot {
  return {
    snapshot_event_id: "000012",
    ts: DATA_AS_OF,
    data_as_of: DATA_AS_OF,
    eligible_count: 150,
    evaluated_count: 132,
    active_setups: 6,
    monitoring_coverage: 0.984,
    new_setups_24h: 6,
    setups: [],
    comparison_24h: {
      available: true,
      reason: null,
      prior_ts: DATA_AS_OF - 86400,
      delta_active_setups: 2,
      delta_new_setups_24h: 1,
    },
    snapshot_history: [],
    coins_without_setup: [],
    market_overview: [],
    movers: [],
    recent_analysis: [],
    activity: [],
    supported_timeframes: ["5m", "15m", "1H", "4H", "1D"],
    event_kinds: [],
    unavailable: null,
  };
}

beforeEach(() => {
  const fetchMock = vi.fn(() => {
    throw new Error("StatCards must be pure — no network");
  });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

// --- brief Step 1 (verbatim) ----------------------------------------------

test("stat delta unavailable without covering snapshot", () => {
  const snap = baseSnapshot();
  snap.comparison_24h = {
    available: false,
    reason: "no covering snapshot",
    prior_ts: null,
    delta_active_setups: null,
    delta_new_setups_24h: null,
  };
  render(<StatCards snapshot={snap} />);
  expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
});

test("eligible/evaluated shown separately, never 'scanned'", () => {
  render(<StatCards snapshot={baseSnapshot()} />);
  expect(screen.getAllByText(/150/).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/132/).length).toBeGreaterThan(0);
  expect(document.body.textContent!.toLowerCase()).not.toContain("scanned");
});

// --- Active Setups card ----------------------------------------------------

test("Active Setups renders the count and a signed delta when available", () => {
  render(<StatCards snapshot={baseSnapshot()} />);
  const card = screen.getByText("Active Setups").parentElement!;
  expect(card.textContent).toContain("6");
  expect(card.textContent).toContain("+2");
});

test("Active Setups delta is unavailable when the comparison is unavailable", () => {
  const snap = baseSnapshot();
  snap.comparison_24h.available = false;
  snap.comparison_24h.delta_active_setups = null;
  render(<StatCards snapshot={snap} />);
  const card = screen.getByText("Active Setups").parentElement!;
  // The count still renders; no delta row and no inferred number.
  expect(card.textContent).toContain("6");
  expect(card.textContent).not.toMatch(/[+-]\d/);
});

test("negative delta renders with an explicit minus sign", () => {
  const snap = baseSnapshot();
  snap.comparison_24h.delta_active_setups = -3;
  render(<StatCards snapshot={snap} />);
  const card = screen.getByText("Active Setups").parentElement!;
  expect(card.textContent).toContain("-3");
});

test("zero delta renders an explicit sign so it never renders as bare green", () => {
  // Task 6 hand-off: StatCard colors a delta by a leading "-", so a bare
  // "0" would render long-green. A neutral delta must carry an explicit
  // "+" prefix.
  const snap = baseSnapshot();
  snap.comparison_24h.delta_active_setups = 0;
  render(<StatCards snapshot={snap} />);
  const card = screen.getByText("Active Setups").parentElement!;
  expect(card.textContent).toContain("+0");
});

// --- Eligible Perps card ---------------------------------------------------

test("Eligible Perps shows evaluated of eligible on one line", () => {
  render(<StatCards snapshot={baseSnapshot()} />);
  const card = screen.getByText("Eligible Perps").parentElement!;
  expect(card.textContent).toContain("132 of 150");
  expect(card.textContent).toMatch(/evaluated/i);
  expect(card.textContent).toMatch(/eligible/i);
});

// --- Monitoring Coverage card ----------------------------------------------

test("Monitoring Coverage renders the decimal as a percentage", () => {
  render(<StatCards snapshot={baseSnapshot()} />);
  expect(screen.getByText("Monitoring Coverage")).toBeInTheDocument();
  expect(screen.getByText("98.4%")).toBeInTheDocument();
});

test("Monitoring Coverage renders unavailable when null", () => {
  const snap = baseSnapshot();
  snap.monitoring_coverage = null;
  render(<StatCards snapshot={snap} />);
  const card = screen.getByText("Monitoring Coverage").parentElement!;
  expect(card.textContent).toMatch(/unavailable/i);
});

// --- New Setups 24h card ---------------------------------------------------

test("New Setups 24h renders the count with the 24h delta", () => {
  render(<StatCards snapshot={baseSnapshot()} />);
  const card = screen.getByText("New Setups 24h").parentElement!;
  expect(card.textContent).toContain("6");
  expect(card.textContent).toContain("+1");
});

test("New Setups 24h delta is unavailable without a covering snapshot", () => {
  const snap = baseSnapshot();
  snap.comparison_24h.available = false;
  snap.comparison_24h.delta_new_setups_24h = null;
  render(<StatCards snapshot={snap} />);
  const card = screen.getByText("New Setups 24h").parentElement!;
  expect(card.textContent).toMatch(/unavailable/i);
});

// --- guards ----------------------------------------------------------------

test("StatCards renders no banned words", () => {
  render(<StatCards snapshot={baseSnapshot()} />);
  const text = document.body.textContent ?? "";
  expect(text).not.toMatch(/vwap|confidence|probability|conviction/i);
  expect(text.toLowerCase()).not.toContain("leverage");
  expect(text.toLowerCase()).not.toContain("liquidation");
  expect(text.toLowerCase()).not.toContain("scanned");
});
