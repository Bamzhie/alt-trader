// Research surface tests (Workstream D) — mocked fetch only.
//
// The socket tripwire in tests/setup.ts blocks all real network egress, so
// every test stubs global fetch (vi.stubGlobal). The stub is URL-aware:
// /quant-status and /quant-research answer their own contract shapes and
// /events answers with an open SSE stream (a JSON body there would end the
// stream at once and spin the hook's reconnect loop).
//
// HARD RULES under test:
//   * Sample counts carry their denominators ("X of Y" with the
//     denominator named).
//   * Uncertainty is interval endpoints only: `measured range (lower–upper)`
//     with the static caption `range of measured outcomes, not a prediction`,
//     or `measured range unavailable`. No point reading, no mean — and no
//     point proportion: the outcome shares are not rendered at all.
//   * An unavailable value renders the literal "unavailable" — never a zero.
//   * An insufficient surface renders the insufficient-evidence block WITH
//     the reason the API gave.
//   * Provenance (config hash, code revision, rule ids, feature versions)
//     is rendered as plain muted text.
//   * Every count in the view comes from the API — the view never counts
//     rules or buckets itself.
//   * Banned copy: the strings confidence / probability / tier / TOTAL / 1m
//     / leverage / liquidation / scanned must not appear in research copy
//     (a plain substring scan, the way the backend scans the payload), and
//     a passed gate record still renders as research, never as an action.
import { afterEach, describe, expect, test, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { createClient } from "../src/api/client";
import { Research } from "../src/pages/Research";
import { sseStreamResponse } from "./sse_stub";

const BASE_URL = "http://127.0.0.1:18741";

const RULE_ID = "quant-v1:breakout_start:rvol4:fade:T288_ks1.5_R1";
const CONTROL_ID = "quant-v1:breakout_start:rvol4:cont:T288_ks1.5_R1";

// A /quant-status body shaped like proto/service.py _handle_quant_status.
// `emitted` counts the rows that are STILL OPEN, so emitted + resolved ==
// total.
function populatedStatus(): Record<string, unknown> {
  return {
    mode: "shadow",
    status: "healthy",
    paused: false,
    pause_reason: null,
    config_hash: "abc123",
    event_counts: { total: 9, emitted: 5, resolved: 4 },
    actionable_events: 0,
    suppression_top: [
      {
        rule_id: RULE_ID,
        reason: "cooldown",
        n: 4,
      },
    ],
    promotions: [],
    reason: null,
  };
}

// A /quant-research body shaped like proto/quant/research_surface.py.
function researchSurface(): Record<string, unknown> {
  return {
    version: "research-surface-v1",
    status: "ok",
    reasons: [],
    generated_ts: 1760000000,
    provenance: {
      config_hash: "abc123",
      code_rev: "0f1e2d3",
      rule_ids: [RULE_ID, CONTROL_ID],
      feature_versions: [{ name: "hurst", version: "hurst-v1" }],
    },
    sample: {
      events: 11,
      resolved: 4,
      open: 7,
      days: 5,
      coins: 4,
      rules: 2,
    },
    rules: [
      {
        rule_id: RULE_ID,
        role: "candidate",
        events: 9,
        resolved: 4,
        open: 5,
        days: 3,
        coins: 2,
        interval: { lo: 0.0123, hi: 0.0456 },
        outcome_shares: { tp: 0.5, sl: 0.25, time: 0.25 },
        cost_note:
          "costs are assumed (fee + slippage); funding is not modeled; " +
          "no fill model beyond next bar open",
        suppression: [{ reason: "cooldown", n: 4 }],
        validation: {
          status: "not_validated",
          reasons: ["holdout events 12 < 200"],
        },
      },
      {
        rule_id: CONTROL_ID,
        role: "control",
        events: 2,
        resolved: 0,
        open: 2,
        days: null,
        coins: null,
        interval: null,
        outcome_shares: { tp: null, sl: null, time: null },
        cost_note:
          "costs are assumed (fee + slippage); funding is not modeled; " +
          "no fill model beyond next bar open",
        suppression: [],
        validation: {
          status: "validated",
          reasons: ["holdout CI lower bound above zero"],
        },
      },
    ],
    calibration: {
      available: false,
      buckets: [],
      count: 0,
      reason:
        "the shadow store records outcomes, not predicted outcomes - " +
        "calibration is not computed",
    },
    coverage: {
      attributed: 11,
      eligible: 15,
      ratio: 11 / 15,
      reason:
        "attributed events over recorded opportunities (emitted + recorded " +
        "suppressions)",
    },
    validation: { validated: 1, rules: 2 },
  };
}

function jsonResponse(status: number, payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface StubOptions {
  status?: Record<string, unknown>;
  research?: Record<string, unknown>;
  /** When set, /quant-research answers this HTTP status instead. */
  researchStatus?: number;
}

/** Answers the two research routes; /events answers with an open stream. */
function stubResearchFetch(opts: StubOptions = {}): string[] {
  const calls: string[] = [];
  vi.stubGlobal(
    "fetch",
    async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(url);
      if (url.endsWith("/events")) {
        return sseStreamResponse(init?.signal ?? null).response;
      }
      if (url.endsWith("/quant-research")) {
        if (opts.researchStatus !== undefined) {
          return jsonResponse(opts.researchStatus, {
            ok: false,
            data: null,
            error: { code: "UNAVAILABLE", message: "quant database unavailable" },
          });
        }
        return jsonResponse(200, {
          ok: true,
          data: opts.research ?? researchSurface(),
          error: null,
        });
      }
      return jsonResponse(200, {
        ok: true,
        data: opts.status ?? populatedStatus(),
        error: null,
      });
    },
  );
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

/**
 * Every banned wording from the research copy discipline — a PLAIN
 * SUBSTRING scan, the way the backend scans the payload, so a word inside
 * another word ("tiered") cannot slip through.
 */
const BANNED = [
  "confidence",
  "probability",
  "tier",
  "total",
  "1m",
  "leverage",
  "liquidation",
  "scanned",
];

function researchText(): string {
  const section = document.querySelector("section");
  return section?.textContent ?? "";
}

describe("createClient.research", () => {
  test("hits /quant-research with the bearer token and returns the surface", async () => {
    const seen: Array<{ url: string; init: RequestInit | undefined }> = [];
    vi.stubGlobal(
      "fetch",
      async (input: RequestInfo | URL, init?: RequestInit) => {
        seen.push({ url: String(input), init });
        return jsonResponse(200, {
          ok: true,
          data: researchSurface(),
          error: null,
        });
      },
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    const surface = await client.research();

    expect(seen).toHaveLength(1);
    expect(seen[0].url).toBe(`${BASE_URL}/quant-research`);
    expect(new Headers(seen[0].init?.headers).get("Authorization")).toBe(
      "Bearer dev-token",
    );
    expect(surface.version).toBe("research-surface-v1");
    expect(surface.sample.events).toBe(11);
  });

  test("a body that is not a research surface is a contract error", async () => {
    vi.stubGlobal(
      "fetch",
      async () =>
        jsonResponse(200, {
          ok: true,
          data: { mode: "shadow", status: "healthy" },
          error: null,
        }),
    );
    const client = createClient({ baseUrl: BASE_URL, token: "dev-token" });

    await expect(client.research()).rejects.toMatchObject({ code: "NETWORK" });
  });
});

describe("Research surface", () => {
  test("the sample counts carry their denominators", async () => {
    stubResearchFetch();
    render(<Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />);
    await screen.findByText(/SHADOW/);

    // "X of Y" with the denominator named in the footnote.
    expect(screen.getByText("4 of 11")).toBeInTheDocument();
    expect(screen.getByText("7 of 11")).toBeInTheDocument();
    expect(
      screen.getByText("resolved of all shadow events"),
    ).toBeInTheDocument();
    expect(screen.getByText("open of all shadow events")).toBeInTheDocument();

    // The plain counts name their scope.
    const days = screen.getByText("Days observed").closest("li");
    expect(days?.textContent).toContain("5");
    const coins = screen.getByText("Coins observed").closest("li");
    expect(coins?.textContent).toContain("4");
    const rules = screen.getByText("Rules observed").closest("li");
    expect(rules?.textContent).toContain("2");
  });

  test("the uncertainty renders as interval endpoints only", async () => {
    stubResearchFetch();
    render(<Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />);
    await screen.findByText(/SHADOW/);

    expect(
      screen.getByText("measured range (0.0123–0.0456)"),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText("range of measured outcomes, not a prediction").length,
    ).toBeGreaterThan(0);
    // A rule with no resolved outcome has no range at all.
    expect(screen.getByText("measured range unavailable")).toBeInTheDocument();
  });

  test("the outcome shares are never rendered as a point proportion", async () => {
    stubResearchFetch();
    render(<Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />);
    await screen.findByText(/SHADOW/);

    expect(screen.queryByText(/outcome shares/)).toBeNull();
    expect(screen.queryByText(/tp 0\.50/)).toBeNull();
    expect(researchText()).not.toMatch(/tp 0\./);
  });

  test("an unavailable value renders the literal unavailable", async () => {
    stubResearchFetch();
    render(<Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />);
    await screen.findByText(/SHADOW/);

    // The control rule has no resolved outcome: no range, no denominators.
    expect(screen.getByText("measured range unavailable")).toBeInTheDocument();
    const control = screen.getAllByText(CONTROL_ID)[0].closest("li");
    expect(control?.textContent).toContain("days unavailable");
    expect(control?.textContent).toContain("coins unavailable");
    // Calibration is not computable from the shadow store.
    const calibration = screen.getByText("Calibration").closest("li");
    expect(calibration?.textContent).toContain("unavailable");
  });

  test("a null provenance value renders unavailable, never a guess", async () => {
    const surface = researchSurface();
    (surface.provenance as Record<string, unknown>).code_rev = null;
    stubResearchFetch({ research: surface });
    render(<Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />);
    await screen.findByText(/SHADOW/);

    const revision = screen.getByText("Code revision").closest("li");
    expect(revision?.textContent).toContain("unavailable");
  });

  test("an insufficient surface renders the reason the API gave", async () => {
    const surface = researchSurface();
    surface.status = "insufficient";
    surface.reasons = ["no shadow events recorded yet"];
    surface.sample = { events: 0, resolved: 0, open: 0, days: 0, coins: 0, rules: 0 };
    surface.rules = [];
    stubResearchFetch({ research: surface });
    render(<Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />);

    expect(await screen.findByText(/insufficient evidence/)).toBeInTheDocument();
    expect(
      screen.getByText("insufficient evidence — no shadow events recorded yet"),
    ).toBeInTheDocument();
  });

  test("an empty store renders the insufficient-evidence block once", async () => {
    const surface = researchSurface();
    surface.status = "insufficient";
    surface.reasons = ["no shadow events recorded yet"];
    surface.sample = { events: 0, resolved: 0, open: 0, days: 0, coins: 0, rules: 0 };
    surface.rules = [];
    stubResearchFetch({
      status: {
        ...populatedStatus(),
        event_counts: { total: 0, emitted: 0, resolved: 0 },
      },
      research: surface,
    });
    render(<Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />);

    expect(await screen.findByText(/insufficient evidence/)).toBeInTheDocument();
    // Exactly one insufficient-evidence block: never a blank, never two.
    expect(screen.getAllByText(/insufficient evidence/)).toHaveLength(1);
  });

  test("the provenance rows are rendered as plain muted text", async () => {
    stubResearchFetch();
    render(<Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />);
    await screen.findByText(/SHADOW/);

    const configHash = screen.getByText("Config hash").closest("li");
    expect(configHash?.textContent).toContain("abc123");
    const revision = screen.getByText("Code revision").closest("li");
    expect(revision?.textContent).toContain("0f1e2d3");
    expect(screen.getAllByText(RULE_ID).length).toBeGreaterThan(0);
    expect(screen.getByText("hurst hurst-v1")).toBeInTheDocument();
    // Timestamps are UTC-seconds integers rendered as YYYY-MM-DD HH:MM:SS UTC.
    expect(
      screen.getByText(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC$/),
    ).toBeInTheDocument();
  });

  test("the coverage and validation status rows come from the API", async () => {
    stubResearchFetch();
    render(<Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />);
    await screen.findByText(/SHADOW/);

    const coverage = screen.getByText("Coverage").closest("li");
    expect(coverage?.textContent).toContain("11 of 15");
    // The counts are the API's: validation {validated: 1, rules: 2}.
    const validation = screen.getByText("Validation").closest("li");
    expect(validation?.textContent).toContain("1 of 2 rules");
  });

  test("a passed gate record still renders as research, never as an action", async () => {
    stubResearchFetch();
    render(<Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />);
    await screen.findByText(/SHADOW/);

    // The validated rule keeps its own vocabulary: the gate passed, the
    // rule stays research.
    expect(
      screen.getByText(/gate passed — research only/),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/gate not passed — research only/),
    ).toBeInTheDocument();
    expect(screen.queryByText("ACTIONABLE")).toBeNull();
    expect(researchText()).not.toMatch(/ACTIONABLE/);
    // The standing "not actionable" statement stays in the SHADOW badge.
    expect(screen.getByText(/not actionable/)).toBeInTheDocument();
    // The unvalidated rule says why, with the gate's own reason.
    expect(screen.getByText("holdout events 12 < 200")).toBeInTheDocument();
  });

  test("the research surface carries none of the banned words", async () => {
    stubResearchFetch();
    render(<Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />);
    await screen.findByText(/SHADOW/);

    const text = researchText().toLowerCase();
    for (const word of BANNED) {
      expect(text).not.toContain(word);
    }
  });

  test("the research surface renders no table", async () => {
    stubResearchFetch();
    const { container } = render(
      <Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />,
    );
    await screen.findByText(/SHADOW/);

    expect(container.querySelector("table")).toBeNull();
  });

  test("an unreadable research store renders insufficient evidence", async () => {
    stubResearchFetch({ researchStatus: 503 });
    render(<Research apiBaseUrl={BASE_URL} serviceToken="dev-token" />);

    expect(await screen.findByText(/insufficient evidence/)).toBeInTheDocument();
    expect(researchText().trim().length).toBeGreaterThan(0);
  });
});
