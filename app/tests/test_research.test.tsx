// Research view tests (Task 4) — mocked fetch only.
//
// The socket tripwire in tests/setup.ts blocks all real network egress, so
// every test stubs global fetch (vi.stubGlobal): /quant-status answers with
// the Task 3 contract shape, /events answers with an open SSE stream from
// tests/sse_stub.ts (a JSON body there would end the stream at once and spin
// the hook's reconnect loop).
//
// HARD RULES under test:
//   * SHADOW labeling only: allowed labels are `SHADOW`, `not actionable`,
//     `insufficient evidence`, `measured range (lower–upper)` — plus the
//     static caption `range of measured outcomes, not a prediction`.
//   * Banned copy: confidence / probability / tier / TOTAL / 1m / leverage /
//     liquidation / scanned must not appear in research copy.
//   * Research output is never rendered as setups: no setup-table components
//     (no <table> at all) and no mixing into Dashboard tables.
//   * 503/empty renders insufficient-evidence — never blank.
import { afterEach, describe, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { createClient } from "../src/api/client";
import { Research } from "../src/pages/Research";
import { sseStreamResponse } from "./sse_stub";

const BASE_URL = "http://127.0.0.1:18741";

// A /quant-status body shaped like proto/service.py _handle_quant_status:
// mode/status/paused/pause_reason/config_hash/event_counts/
// suppression_top/promotions/reason.
function populatedStatus(): Record<string, unknown> {
  return {
    mode: "shadow",
    status: "healthy",
    paused: false,
    pause_reason: null,
    config_hash: "abc123",
    event_counts: { total: 9, emitted: 7, resolved: 2 },
    suppression_top: [
      {
        rule_id: "range_reclaim_long",
        reason: "holdout CI lower bound -0.0123 not above zero",
        n: 4,
      },
    ],
    promotions: [
      {
        rule_id: "breakout_continuation_long",
        config_hash: "abc123",
        passed: true,
        reasons: ["holdout CI lower bound above zero"],
        holdout: { ci_lo: 0.0123, ci_hi: 0.0456 },
        expected_net_r: 0.0123,
      },
    ],
    reason: null,
  };
}

function emptyStatus(): Record<string, unknown> {
  return {
    mode: "shadow",
    status: "unseen",
    paused: false,
    pause_reason: null,
    config_hash: "abc123",
    event_counts: { total: 0, emitted: 0, resolved: 0 },
    suppression_top: [],
    promotions: [],
    reason: "insufficient-evidence",
  };
}

function jsonResponse(status: number, payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

// Answers /quant-status via `handler`, /events via an open SSE stream.
function stubQuantFetch(handler: (url: string) => Response): string[] {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(url);
      if (url.endsWith("/events")) {
        return sseStreamResponse(init?.signal ?? null).response;
      }
      if (url.endsWith("/quant-signals")) {
        // The manual-review candidate read: an empty candidate list, so the
        // block renders insufficient evidence rather than a candidate.
        return new Response(
          JSON.stringify({
            ok: true,
            data: {
              version: "signals-v1",
              generated_ts: 1_700_000_000,
              count: 0,
              counts: { long: 0, short: 0 },
              signals: [],
              note: "no candidates for review",
            },
            error: null,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      return handler(url);
    },
  );
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/** Every banned wording from the Task 4 copy discipline. */
const BANNED_PATTERNS: RegExp[] = [
  /confidence/i,
  /probability/i,
  /\btier\b/i,
  /TOTAL/,
  /1m/,
  /leverage/i,
  /liquidation/i,
  /scanned/i,
];

function researchText(): string {
  const section = document.querySelector("section");
  return section?.textContent ?? "";
}

describe("createClient.quantStatus", () => {
  test("hits /quant-status with the bearer token and returns the status", async () => {
    const seen: Array<{ url: string; init: RequestInit | undefined }> = [];
    vi.stubGlobal(
      "fetch",
      async (input: RequestInfo | URL, init?: RequestInit) => {
        seen.push({ url: String(input), init });
        return jsonResponse(200, {
          ok: true,
          data: populatedStatus(),
          error: null,
        });
      },
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    const status = await client.quantStatus();

    expect(seen).toHaveLength(1);
    expect(seen[0].url).toBe(`${BASE_URL}/quant-status`);
    expect(new Headers(seen[0].init?.headers).get("Authorization")).toBe(
      "Bearer dev-token",
    );
    expect(status.mode).toBe("shadow");
    expect(status.event_counts.emitted).toBe(7);
    expect(status.event_counts.resolved).toBe(2);
  });

  test("an unreadable quant store surfaces as ApiError UNAVAILABLE", async () => {
    vi.stubGlobal(
      "fetch",
      async () =>
        jsonResponse(503, {
          ok: false,
          data: null,
          error: { code: "UNAVAILABLE", message: "quant database unavailable" },
        }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await expect(client.quantStatus()).rejects.toMatchObject({
      code: "UNAVAILABLE",
    });
  });
});

describe("Research view", () => {
  test("populated status renders SHADOW counts, suppression and promotions", async () => {
    stubQuantFetch(() =>
      jsonResponse(200, { ok: true, data: populatedStatus(), error: null }),
    );
    render(<Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />);

    // SHADOW banner with the not-actionable framing.
    expect(await screen.findByText(/SHADOW/)).toBeInTheDocument();
    expect(screen.getByText(/not actionable/)).toBeInTheDocument();
    // Event counts.
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    // Suppression reason with its count.
    expect(
      screen.getByText(/holdout CI lower bound -0\.0123 not above zero/),
    ).toBeInTheDocument();
    expect(screen.getByText("4")).toBeInTheDocument();
    // Promotion record with the measured range and its static caption.
    expect(
      screen.getByText("breakout_continuation_long"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("measured range (0.0123–0.0456)"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("range of measured outcomes, not a prediction"),
    ).toBeInTheDocument();
  });

  test("unreadable store renders insufficient evidence, never blank", async () => {
    stubQuantFetch(() =>
      jsonResponse(503, {
        ok: false,
        data: null,
        error: { code: "UNAVAILABLE", message: "quant database unavailable" },
      }),
    );
    render(<Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />);

    expect(await screen.findByText(/insufficient evidence/)).toBeInTheDocument();
    expect(researchText().trim().length).toBeGreaterThan(0);
  });

  test("empty store renders insufficient evidence, never blank", async () => {
    stubQuantFetch(() =>
      jsonResponse(200, { ok: true, data: emptyStatus(), error: null }),
    );
    render(<Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />);

    expect(await screen.findByText(/insufficient evidence/)).toBeInTheDocument();
    expect(researchText().trim().length).toBeGreaterThan(0);
  });

  test("research copy carries none of the banned words", async () => {
    stubQuantFetch(() =>
      jsonResponse(200, { ok: true, data: populatedStatus(), error: null }),
    );
    render(<Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />);
    await screen.findByText(/SHADOW/);

    const text = researchText();
    for (const pattern of BANNED_PATTERNS) {
      expect(text).not.toMatch(pattern);
    }
  });

  test("research output is never rendered as setups", async () => {
    stubQuantFetch(() =>
      jsonResponse(200, { ok: true, data: populatedStatus(), error: null }),
    );
    const { container } = render(
      <Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />,
    );
    await screen.findByText(/SHADOW/);

    // No setup-table components render research rows: the view uses no
    // <table> at all, and never borrows the Dashboard's Top Signals frame.
    expect(container.querySelector("table")).toBeNull();
    expect(screen.queryByText(/Top Signals/)).toBeNull();
  });

  test("passed promotion never flips live labels away from SHADOW", async () => {
    // populatedStatus() already carries a passed promotion; add legacy
    // ACTIONABLE rows on top. The shadow-only contract: live labels stay
    // SHADOW, and no ACTIONABLE badge or section may appear.
    stubQuantFetch(() =>
      jsonResponse(200, {
        ok: true,
        data: { ...populatedStatus(), actionable_events: 5 },
        error: null,
      }),
    );
    render(<Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />);
    await screen.findByText(/SHADOW/);
    expect(screen.getByText(/not actionable/)).toBeInTheDocument();
    expect(screen.queryByText("ACTIONABLE")).toBeNull();
  });
});
