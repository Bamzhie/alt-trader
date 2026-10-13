// Component tests for the five shared UI components.
//
// House style: the task-brief tests come first (verbatim), then additional
// coverage for the behaviors the brief's Interfaces section specifies.
//
// HARD RULES under test (see app/src/api/types.ts header):
//   * null renders as explicit "unavailable" — never 0, never inferred.
//   * No numeric confidence/probability/tier anywhere — EvidenceBar is
//     criteria chips only.
//   * All colors come from theme tokens (Tailwind token classes), never
//     raw hexes.
//   * Components are pure renders: no fetch, no network (the socket
//     tripwire in tests/setup.ts guards every egress path).
import { afterEach, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { EvidenceBar } from "../src/components/EvidenceBar";
import { LetterBadge } from "../src/components/LetterBadge";
import { SignalPill } from "../src/components/SignalPill";
import { Sparkline } from "../src/components/Sparkline";
import { StatCard } from "../src/components/StatCard";

afterEach(cleanup);

// --- brief Step 1 (verbatim) ---------------------------------------------

test("EvidenceBar: null input renders unavailable, not 0", () => {
  render(
    <EvidenceBar
      criteria={[{ name: "hh", passed: true }]}
      inputs={{ rsi: null }}
    />,
  );
  expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
  expect(screen.queryByText("0")).not.toBeInTheDocument();
});

test("StatCard: unavailable delta shows unavailable", () => {
  render(
    <StatCard
      label="New Setups"
      value="0"
      delta={{ value: null, available: false }}
    />,
  );
  expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
});

test("Sparkline empty values does not crash", () => {
  expect(() => render(<Sparkline values={[]} up />)).not.toThrow();
});

test("SignalPill: LONG uses the long token color", () => {
  render(<SignalPill direction="LONG" />);
  const pill = screen.getByText("LONG");
  expect(pill.className).toContain("text-long");
});

// --- LetterBadge ----------------------------------------------------------

test("LetterBadge renders the uppercase first letter of the symbol", () => {
  render(<LetterBadge symbol="BTC" />);
  expect(screen.getByText("B")).toBeInTheDocument();
});

test("LetterBadge falls back to a glyph for an empty symbol", () => {
  expect(() => render(<LetterBadge symbol="" />)).not.toThrow();
  expect(screen.getByText("?")).toBeInTheDocument();
});

test("LetterBadge size prop drives the box dimensions", () => {
  const { container } = render(<LetterBadge symbol="ETH" size={40} />);
  const badge = container.querySelector("span") as HTMLElement;
  expect(badge.style.width).toBe("40px");
  expect(badge.style.height).toBe("40px");
});

// --- Sparkline ------------------------------------------------------------

test("Sparkline draws a polyline with one point per value", () => {
  const { container } = render(<Sparkline values={[1, 2, 3, 2, 5]} up />);
  const svg = container.querySelector("svg");
  expect(svg).not.toBeNull();
  const points = svg?.querySelector("polyline")?.getAttribute("points");
  expect(points?.trim().split(/\s+/)).toHaveLength(5);
});

test("Sparkline colors by direction: up = long token, down = short token", () => {
  const { container: upContainer } = render(
    <Sparkline values={[1, 2, 3]} up />,
  );
  expect(
    (upContainer.querySelector("svg") as SVGElement).getAttribute("class"),
  ).toContain("text-long");
  cleanup();
  const { container: downContainer } = render(
    <Sparkline values={[3, 2, 1]} up={false} />,
  );
  expect(
    (downContainer.querySelector("svg") as SVGElement).getAttribute("class"),
  ).toContain("text-short");
});

test("Sparkline single value renders without dividing by zero", () => {
  expect(() => render(<Sparkline values={[42]} up />)).not.toThrow();
});

// --- EvidenceBar ----------------------------------------------------------

test("EvidenceBar: passed criteria chips use the long token, failed use muted", () => {
  render(
    <EvidenceBar
      criteria={[
        { name: "hh", passed: true },
        { name: "ll", passed: false },
      ]}
      inputs={{}}
    />,
  );
  expect(screen.getByText("hh").className).toContain("text-long");
  expect(screen.getByText("ll").className).toContain("text-muted");
});

test("EvidenceBar: available inputs render their value, null renders unavailable", () => {
  render(
    <EvidenceBar criteria={[]} inputs={{ rsi: 57.3, macd: null }} />,
  );
  expect(screen.getByText("57.3")).toBeInTheDocument();
  expect(screen.getByText(/unavailable/i)).toBeInTheDocument();
});

test("EvidenceBar renders no confidence/probability language", () => {
  render(
    <EvidenceBar
      criteria={[{ name: "higher high", passed: true }]}
      inputs={{ rsi: 55, volume_pct: 120 }}
    />,
  );
  const text = document.body.textContent ?? "";
  expect(text).not.toMatch(/confidence|probability|conviction/i);
});

// --- StatCard -------------------------------------------------------------

test("StatCard renders label, value and footnote", () => {
  render(
    <StatCard
      label="Active Setups"
      value="7"
      footnote="data as of 12:00 UTC"
    />,
  );
  expect(screen.getByText("Active Setups")).toBeInTheDocument();
  expect(screen.getByText("7")).toBeInTheDocument();
  expect(screen.getByText("data as of 12:00 UTC")).toBeInTheDocument();
  expect(screen.queryByText(/unavailable/i)).toBeNull();
});

test("StatCard available delta renders the delta string, not unavailable", () => {
  render(
    <StatCard
      label="Active Setups"
      value="7"
      delta={{ value: "+3", available: true }}
    />,
  );
  expect(screen.getByText("+3")).toBeInTheDocument();
  expect(screen.queryByText(/unavailable/i)).toBeNull();
});

test("StatCard without a delta prop renders no delta row", () => {
  render(<StatCard label="Monitoring Coverage" value="82%" />);
  expect(screen.queryByText(/unavailable/i)).toBeNull();
});

// --- SignalPill -----------------------------------------------------------

test("SignalPill: SHORT uses the short token color", () => {
  render(<SignalPill direction="SHORT" />);
  const pill = screen.getByText("SHORT");
  expect(pill.className).toContain("text-short");
});

// --- cross-component guards ------------------------------------------------

test("all five components render purely: no fetch on mount", () => {
  const spy = vi.fn();
  vi.stubGlobal("fetch", spy);
  render(<LetterBadge symbol="BTC" />);
  render(<Sparkline values={[1, 2]} up />);
  render(
    <EvidenceBar
      criteria={[{ name: "hh", passed: true }]}
      inputs={{ rsi: null }}
    />,
  );
  render(<StatCard label="New Setups" value="0" />);
  render(<SignalPill direction="LONG" />);
  expect(spy).not.toHaveBeenCalled();
});
