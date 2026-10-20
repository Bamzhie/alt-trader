// Manual-review candidate tests — mocked fetch only.
//
// HARD RULES under test:
//   * A candidate is a CANDIDATE for manual review: the view says so and
//     says the app does not execute it. Nothing here is a recommendation,
//     a prediction, or an order.
//   * The evidence status of the rule (exploratory / insufficient /
//     validated) is shown as data, with its denominators and its reason.
//   * Uncertainty is interval endpoints only — no point reading.
//   * Banned copy: confidence / probability / tier / TOTAL / 1m / leverage /
//     liquidation / scanned must not appear in the rendered text.
//   * No <table> in the Research view, and no setup-table components.
import { afterEach, describe, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { Research } from "../src/pages/Research";
import { sseStreamResponse } from "./sse_stub";

const BASE_URL = "http://127.0.0.1:18741";

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

function statusBody(): Record<string, unknown> {
  return {
    mode: "shadow",
    status: "healthy",
    paused: false,
    pause_reason: null,
    config_hash: "abc123",
    event_counts: { total: 9, emitted: 7, resolved: 2 },
    suppression_top: [],
    promotions: [],
    reason: null,
  };
}

function researchBody(): Record<string, unknown> {
  return {
    version: "research-surface-v1",
    status: "ok",
    reasons: [],
    generated_ts: 1_700_000_000,
    provenance: {
      config_hash: "abc123",
      code_rev: "deadbee",
      rule_ids: ["quant-v1:breakout_start:rvol4:fade:T288_ks1.5_R1"],
      feature_versions: [{ name: "pivots", version: "pivots-v1" }],
    },
    sample: { events: 9, resolved: 2, open: 7, days: 5, coins: 3, rules: 1 },
    rules: [],
    calibration: { available: false, buckets: [], reason: "no predictions" },
    coverage: { attributed: 2, eligible: 9, ratio: 0.22, reason: null },
    validation: { status: "not_validated", reasons: ["no promotion record"] },
  };
}

function signalBody(): Record<string, unknown> {
  return {
    version: "signals-v1",
    generated_ts: 1_700_000_000,
    count: 1,
    counts: { long: 1, short: 0 },
    note: "Rule-generated candidates for MANUAL REVIEW. The app does not execute them.",
    signals: [
      {
        id: 7,
        coin: "AAA",
        direction: "long",
        side: 1,
        rule_id: "quant-v1:breakout_start:rvol4:fade:T288_ks1.5_R1",
        rule_label: "breakout_start rvol>=4 fade",
        rule_version: "quant-v1",
        role: "candidate",
        signal_ts: 1_699_999_000,
        signal_ts_utc: "2026-11-14 22:50:00 UTC",
        data_freshness: {
          signal_age_s: 1000,
          signal_age_min: 16.7,
          fresh: true,
          basis: "the age of the signal bar's close",
        },
        entry: {
          price: null,
          basis: "the open of the bar after the signal (not yet observed)",
          reference_price: 100.0,
        },
        risk: {
          stop: 99.0,
          target: 102.0,
          stop_frac: 0.01,
          horizon_bars: 288,
          assumptions: {
            stop: "k_stop * sigma * sqrt(T) bars from the entry",
            target: "r_tp * the stop distance",
            time_exit: "the close of the last bar of the horizon",
            tie_break: "a bar that touches both the stop and the target resolves as the STOP",
            side_rule: "the rule's policy sets the trade side",
            k_stop: 1.5,
            r_tp: 1.5,
            horizon_bars: 288,
            sigma: 0.002,
            leverage: "none applied: the levels are R (risk units) and prices",
          },
        },
        outcome: { state: "open", kind: null, exit_ts: null, gross_log: null, net_r: null },
        costs: {
          friction_frac: 0.0009,
          fee_per_side: 0.0005,
          basis: "fee is an assumption and the slippage is tiered by volume",
          funding_coverage: null,
        },
        evidence: {
          status: "exploratory",
          reason: "previously examined",
          n_events: 300,
          n_coins: 20,
          n_days: 30,
          ci_lo_day: -0.01,
          ci_hi_day: 0.02,
          family_p: 0.4,
          funding_coverage: 0.99,
          source: "HYD-0003",
        },
        execution: {
          mode: "SHADOW",
          actionable: false,
          note: "MANUAL REVIEW ONLY - the app does not execute this signal",
        },
      },
    ],
  };
}

function stubFetch(signalResponder: (url: string) => Response): void {
  vi.stubGlobal("fetch", (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/events")) {
      return Promise.resolve(sseStreamResponse(null).response);
    }
    if (url.endsWith("/quant-status")) {
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true, data: statusBody(), error: null }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }
    if (url.endsWith("/quant-research")) {
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true, data: researchBody(), error: null }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    }
    return Promise.resolve(signalResponder(url));
  });
}

function okResponse(data: unknown): Response {
  return new Response(JSON.stringify({ ok: true, data, error: null }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("the manual-review candidate block", () => {
  test("a candidate is shown with everything a reviewer needs", async () => {
    stubFetch(() => okResponse(signalBody()));
    render(<Research apiBaseUrl={BASE_URL} serviceToken="t" />);
    const text = (await screen.findByText(/AAA/))?.textContent ?? "";
    expect(text.length).toBeGreaterThan(0);
    const body = document.querySelector("section")?.textContent ?? "";
    // identity, direction, rule, timestamp, freshness
    expect(body).toContain("AAA");
    expect(body).toContain("LONG");
    expect(body).toContain("breakout_start rvol>=4 fade");
    expect(body).toContain("2026-11-14 22:50:00 UTC");
    expect(body).toContain("16.7 min");
    // entry, stop, target, horizon and the assumptions behind them
    expect(body).toContain("reference price 100");
    expect(body).toContain("stop 99");
    expect(body).toContain("target 102");
    expect(body).toContain("288 bars");
    expect(body).toContain("resolves as the STOP");
    // the evidence status with its denominators, and the range endpoints
    expect(body).toContain("exploratory");
    expect(body).toContain("300 events");
    expect(body).toContain("20 coins");
    expect(body).toContain("30 days");
    expect(body).toContain("measured range (-0.0100–0.0200)");
    expect(body).toContain("range of measured outcomes, not a prediction");
    // manual review, and never an order
    expect(body).toContain("manual review only");
    expect(body).toContain("does not execute this signal");
  });

  test("the rendered candidate carries no banned copy", async () => {
    stubFetch(() => okResponse(signalBody()));
    render(<Research apiBaseUrl={BASE_URL} serviceToken="t" />);
    await screen.findByText(/AAA/);
    const text = document.querySelector("section")?.textContent ?? "";
    for (const pattern of BANNED_PATTERNS) {
      expect(text).not.toMatch(pattern);
    }
  });

  test("no candidate is rendered as a table", async () => {
    stubFetch(() => okResponse(signalBody()));
    render(<Research apiBaseUrl={BASE_URL} serviceToken="t" />);
    await screen.findByText(/AAA/);
    expect(document.querySelector("table")).toBeNull();
  });

  test("an empty candidate list renders insufficient evidence", async () => {
    stubFetch(() =>
      okResponse({ ...signalBody(), count: 0, signals: [], counts: { long: 0, short: 0 } }),
    );
    render(<Research apiBaseUrl={BASE_URL} serviceToken="t" />);
    const body = (await screen.findByText(/insufficient evidence/))?.textContent ?? "";
    expect(body).toContain("insufficient evidence");
  });

  test("a failed candidate read renders insufficient evidence, not a blank", async () => {
    stubFetch(() =>
      new Response(
        JSON.stringify({ ok: false, data: null, error: { code: "UNAVAILABLE", message: "store gone" } }),
        { status: 503, headers: { "Content-Type": "application/json" } },
      ),
    );
    render(<Research apiBaseUrl={BASE_URL} serviceToken="t" />);
    const body = (await screen.findByText(/insufficient evidence/))?.textContent ?? "";
    expect(body).toContain("insufficient evidence");
  });
});
