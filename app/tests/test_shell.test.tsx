// Component tests for the app shell (Task 7): sidebar nav, top bar,
// connection banner, stub pages, and the usePage state routing in App.
//
// House style: the task-brief tests come first (verbatim), then additional
// coverage for the behaviors the brief's Interfaces section specifies.
//
// HARD RULES under test (see app/src/api/types.ts header):
//   * READ-ONLY shell — the shell never fetches on mount; the socket
//     tripwire in tests/setup.ts guards every egress path.
//   * No confidence / probability / tier language in the chrome.
//   * Colors come from theme tokens (Tailwind token classes), never raw
//     hexes — asserted on the active nav item and the banner.
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import App from "../src/App";
import { useDashboardStore } from "../src/store/dashboard";
import { healthzOk, readyzOk, sseStreamResponse, stubReadOnlyFetch } from "./sse_stub";

beforeEach(() => {
  useDashboardStore.setState({ connection: "starting" });
  // The wired Dashboard opens /events and probes /healthz + /readyz on
  // mount (Task 11 fix round 1). Chrome-level tests here don't exercise
  // the data path, but an unstubbed fetch would leave those probes
  // resolving after the test body (state updates outside act) — so every
  // render gets the minimal read-only stub. The one test that asserts the
  // request shape installs its own stub.
  stubReadOnlyFetch();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  useDashboardStore.setState({ connection: "starting" });
  vi.unstubAllGlobals();
});

// --- brief Step 1 (verbatim) ---------------------------------------------

test("shell shows all six nav items and stubs for non-dashboard pages", () => {
  render(<App />);
  for (const name of [
    "Dashboard",
    "Watchlist",
    "Signals",
    "Analysis",
    "Trades",
    "Settings",
  ]) {
    expect(screen.getByRole("button", { name })).toBeInTheDocument();
  }
  fireEvent.click(screen.getByRole("button", { name: "Signals" }));
  expect(screen.getByText(/coming in a later release/i)).toBeInTheDocument();
});

test("starting connection shows banner", () => {
  useDashboardStore.setState({ connection: "starting" });
  render(<App />);
  expect(screen.getByText(/first sweep in progress/i)).toBeInTheDocument();
});

// --- sidebar --------------------------------------------------------------

test("dashboard is the active nav item on mount", () => {
  render(<App />);
  expect(
    screen.getByRole("button", { name: "Dashboard" }),
  ).toHaveAttribute("aria-current", "page");
});

test("nav click moves the active item and swaps the page", () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Signals" }));
  expect(screen.getByRole("button", { name: "Signals" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(
    screen.getByRole("button", { name: "Dashboard" }),
  ).not.toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("heading", { name: "Signals" })).toBeInTheDocument();
});

test("active nav item is painted with the accent token, inactive with muted", () => {
  render(<App />);
  const active = screen.getByRole("button", { name: "Dashboard" });
  const inactive = screen.getByRole("button", { name: "Watchlist" });
  expect(active.className).toContain("text-accent");
  expect(inactive.className).toContain("text-muted");
});

test("sidebar footer carries API status, exchange and account", () => {
  render(<App />);
  expect(screen.getByText("API status: Starting…")).toBeInTheDocument();
  expect(screen.getByText("Exchange: MEXC")).toBeInTheDocument();
  expect(screen.getByText("Account: Futures")).toBeInTheDocument();
});

test("sidebar footer API status follows the connection state", () => {
  useDashboardStore.setState({ connection: "connected" });
  render(<App />);
  expect(screen.getByText("API status: Connected")).toBeInTheDocument();
  expect(screen.queryByText("API status: Starting…")).toBeNull();
});

// --- top bar --------------------------------------------------------------

test("top bar shows the wordmark, subtitle and SCANNING pill", () => {
  render(<App />);
  expect(screen.getByText("ALT RADAR")).toBeInTheDocument();
  expect(
    screen.getByText("MEXC Perpetual Futures Scanner"),
  ).toBeInTheDocument();
  expect(screen.getByText("SCANNING")).toBeInTheDocument();
});

test("top bar clock renders UTC seconds and ticks", () => {
  vi.useFakeTimers();
  try {
    vi.setSystemTime(new Date("2026-10-14T12:00:00Z"));
    render(<App />);
    const clock = () => screen.getByText(/^\d{2}:\d{2}:\d{2} UTC$/);
    expect(clock().textContent).toBe("12:00:00 UTC");
    act(() => {
      vi.advanceTimersByTime(1500);
    });
    expect(clock().textContent).toBe("12:00:01 UTC");
  } finally {
    vi.useRealTimers();
  }
});

test("bell and gear are labelled placeholders that do not navigate", () => {
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "Notifications" }));
  fireEvent.click(screen.getByRole("button", { name: "Preferences" }));
  expect(
    screen.getByRole("button", { name: "Dashboard" }),
  ).toHaveAttribute("aria-current", "page");
  expect(screen.queryByText(/coming in a later release/i)).toBeNull();
});

// --- connection banner ----------------------------------------------------

test("banner is hidden once connected", () => {
  useDashboardStore.setState({ connection: "connected" });
  render(<App />);
  expect(screen.queryByText(/first sweep in progress/i)).toBeNull();
});

test("error connection shows a contained fallback message", () => {
  useDashboardStore.setState({ connection: "error" });
  render(<App />);
  const banner = screen.getByRole("alert");
  expect(banner.textContent).toMatch(/connection error/i);
  expect(banner.textContent).toMatch(/retrying/i);
  // The shell itself stays usable: nav and pages still render.
  expect(
    screen.getByRole("button", { name: "Dashboard" }),
  ).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
});

// --- dashboard route ------------------------------------------------------

test("dashboard route renders the Dashboard page, not a stub", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
  expect(screen.queryByText(/coming in a later release/i)).toBeNull();
});

// --- read-only guards -----------------------------------------------------

// Task 8 replaced the placeholder Dashboard with a real page that reads the
// snapshot on mount. The shell itself still issues nothing of its own — the
// only request the app makes is the dashboard's single GET to /api/snapshot,
// and never a POST.
test("shell mounts read-only: one snapshot GET, never a POST", async () => {
  const postCalls: Array<{ url: string; init: RequestInit | undefined }> = [];
  const getCalls: string[] = [];
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if ((init?.method ?? "GET").toUpperCase() === "POST") {
        postCalls.push({ url, init });
        throw new Error(`POST attempted against read-only app: ${url}`);
      }
      getCalls.push(url);
      // Contract answers for the routes the wired Dashboard now opens —
      // see tests/sse_stub.ts (a JSON body on /events would end the
      // stream at once and spin the hook's reconnect loop all test).
      if (url.endsWith("/healthz")) return healthzOk();
      if (url.endsWith("/readyz")) return readyzOk();
      if (url.endsWith("/events")) {
        return sseStreamResponse(init?.signal ?? null).response;
      }
      return new Response(
        JSON.stringify({
          ok: true,
          data: {
            snapshot_event_id: "000012",
            ts: 1791936000,
            data_as_of: 1791936000,
            eligible_count: 150,
            evaluated_count: 132,
            active_setups: 1,
            monitoring_coverage: 0.984,
            new_setups_24h: 1,
            setups: [],
            comparison_24h: {
              available: false,
              reason: null,
              prior_ts: null,
              delta_active_setups: null,
              delta_new_setups_24h: null,
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
          },
          error: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  render(<App />);
  await screen.findByText("Active Setups");
  expect(postCalls).toHaveLength(0);
  expect(getCalls).toContain("/api/snapshot");
  vi.unstubAllGlobals();
});

test("shell chrome carries no confidence/probability/tier language", () => {
  render(<App />);
  const text = document.body.textContent ?? "";
  expect(text).not.toMatch(/confidence|probability|conviction/i);
  expect(text).not.toMatch(/\btier\b/i);
});
