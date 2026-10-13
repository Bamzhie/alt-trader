// SSE wiring integration tests (Task 11 fix round 1).
//
// The hook (src/api/sse.ts useEvents) and the store's setConnection were
// unit-tested but imported by NOTHING in src/ — so in the running app the
// ConnectionBanner was permanently stuck on "starting — first sweep in
// progress" and live SSE transitions never reached the dashboard.
//
// These tests pin the wired path end to end: the banner must be driven by
// /healthz (alive) + /readyz (ready) — NOT by the hook's stream-open flag,
// because the mock's /events route only checks auth, so a stream can open
// while the service is still not ready — and an applied transition must
// re-snapshot the dashboard.
//
// HARD RULES under test (see app/src/api/types.ts header):
//   * fetch is stubbed; the socket tripwire in tests/setup.ts guards every
//     egress path. No real sockets are opened.
//   * READ-ONLY — the POST stub throws, so any write attempt fails loudly.
//   * Timestamps are UTC-seconds ints.
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import App from "../src/App";
import { useDashboardStore } from "../src/store/dashboard";
import type { Snapshot } from "../src/api/types";

const DATA_AS_OF = 1791936000;

/** One SSE frame, byte-for-byte what mock_service.py's _frame() writes. */
function frame(id: string, kind: string): Uint8Array {
  const data = JSON.stringify({ id, kind, ts: DATA_AS_OF, coin: null });
  return new TextEncoder().encode(
    `id: ${id}\nevent: ${kind}\ndata: ${data}\n\n`,
  );
}

/** A valid but empty snapshot — every region renders off it. */
function makeSnapshot(): Snapshot {
  return {
    snapshot_event_id: "000012",
    ts: DATA_AS_OF,
    data_as_of: DATA_AS_OF,
    eligible_count: 150,
    evaluated_count: 132,
    active_setups: 0,
    monitoring_coverage: 0.984,
    new_setups_24h: 0,
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
  };
}

function jsonResponse(status: number, payload: unknown): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

interface FakeStream {
  push(bytes: Uint8Array): void;
  close(): void;
}

interface Stub {
  calls: string[];
  snapshotCalls: string[];
  readyzCalls: number;
  streams: FakeStream[];
}

/**
 * Fetch stub for the whole wired path. GETs answer the contract routes the
 * Dashboard now touches: /healthz (non-envelope liveness), /readyz
 * (readiness; 503 UNAVAILABLE while the first sweep runs), /snapshot, and
 * /events as a test-driven ReadableStream (recorded in `streams`, in
 * connect order). Any POST throws — the read-only rule.
 */
function installStub(opts: { ready: boolean; alive?: boolean }): Stub {
  const calls: string[] = [];
  const snapshotCalls: string[] = [];
  const streams: FakeStream[] = [];
  let readyzCalls = 0;
  const alive = opts.alive ?? true;

  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push(url);
    if ((init?.method ?? "GET").toUpperCase() === "POST") {
      throw new Error(`POST attempted against read-only dashboard: ${url}`);
    }
    if (url.endsWith("/healthz")) {
      return Promise.resolve(
        alive
          ? jsonResponse(200, { ok: true, alive: true })
          : jsonResponse(500, { ok: false, alive: false }),
      );
    }
    if (url.endsWith("/readyz")) {
      readyzCalls += 1;
      return Promise.resolve(
        opts.ready
          ? jsonResponse(200, { ok: true, data: { ready: true }, error: null })
          : jsonResponse(503, {
              ok: false,
              data: null,
              error: { code: "UNAVAILABLE", message: "first sweep in progress" },
            }),
      );
    }
    if (url.endsWith("/snapshot")) {
      snapshotCalls.push(url);
      return Promise.resolve(
        jsonResponse(200, { ok: true, data: makeSnapshot(), error: null }),
      );
    }
    if (url.includes("/bars")) {
      const q = new URL(url, "http://localhost").searchParams;
      return Promise.resolve(
        jsonResponse(200, {
          ok: true,
          data: {
            coin: q.get("coin") ?? "",
            tf: q.get("tf") ?? "15m",
            bars: [],
            closed_only: true,
            count: 0,
            data_as_of: DATA_AS_OF,
            unavailable: null,
          },
          error: null,
        }),
      );
    }
    if (url.includes("/metrics")) {
      const q = new URL(url, "http://localhost").searchParams;
      return Promise.resolve(
        jsonResponse(200, {
          ok: true,
          data: {
            coin: q.get("coin") ?? "",
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
        }),
      );
    }
    if (url.includes("/tradeplan/") || url.includes("/setup/")) {
      return Promise.resolve(
        jsonResponse(404, {
          ok: false,
          data: null,
          error: { code: "NOT_FOUND", message: "no setup for coin" },
        }),
      );
    }
    // Everything else is the SSE stream: a fresh test-driven body.
    const signal = init?.signal ?? null;
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        controller = c;
      },
      cancel() {
        // aborted by the hook — nothing to clean up in a fake stream
      },
    });
    if (signal) {
      signal.addEventListener("abort", () => {
        try {
          controller.close();
        } catch {
          // already closed
        }
      });
    }
    streams.push({
      push: (bytes) => {
        try {
          controller.enqueue(bytes);
        } catch {
          // stream already closed
        }
      },
      close: () => {
        try {
          controller.close();
        } catch {
          // already closed
        }
      },
    });
    return Promise.resolve(
      new Response(body, {
        status: 200,
        headers: { "Content-Type": "text/event-stream" },
      }),
    );
  });

  return {
    calls,
    snapshotCalls,
    get readyzCalls() {
      return readyzCalls;
    },
    streams,
  };
}

beforeEach(() => {
  useDashboardStore.setState({
    selectedCoin: null,
    marketFilter: "all",
    horizonFilter: "4h",
    directionFilter: "all",
    sort: "newest",
    dataAsOf: null,
    connection: "starting",
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("connection state is driven by /healthz + /readyz", () => {
  test("banner reaches connected only when readyz is true and the stream is open", async () => {
    const stub = installStub({ ready: true });
    render(<App />);

    // The stream opens…
    await waitFor(() => expect(stub.streams.length).toBe(1));
    // …and the readiness probe has actually run.
    await waitFor(() => expect(stub.readyzCalls).toBeGreaterThan(0));

    await waitFor(() =>
      expect(useDashboardStore.getState().connection).toBe("connected"),
    );
    expect(screen.queryByText(/first sweep in progress/i)).toBeNull();
    expect(stub.calls.some((u) => u.endsWith("/healthz"))).toBe(true);
  });

  test("banner stays starting while readyz is false, even with the stream open and delivering events", async () => {
    const stub = installStub({ ready: false });
    render(<App />);

    await waitFor(() => expect(stub.streams.length).toBe(1));
    await waitFor(() => expect(stub.readyzCalls).toBeGreaterThan(0));

    // The stream is genuinely live — an applied transition re-snapshots.
    await act(async () => {
      stub.streams[0].push(frame("000013", "sweep.completed"));
    });
    await waitFor(() => expect(stub.snapshotCalls.length).toBeGreaterThan(1));

    // …yet the banner must NOT claim connected: the mock's /events route
    // only checks auth, so stream-open alone says nothing about readiness.
    expect(useDashboardStore.getState().connection).toBe("starting");
    expect(
      screen.getByText(/first sweep in progress/i),
    ).toBeInTheDocument();
  });

  test("healthz failure flips the banner to error", async () => {
    installStub({ ready: true, alive: false });
    render(<App />);

    await waitFor(() =>
      expect(useDashboardStore.getState().connection).toBe("error"),
    );
    const banner = screen.getByRole("alert");
    expect(banner.textContent).toMatch(/connection error/i);
  });
});

describe("live SSE transitions reach the dashboard", () => {
  test("an applied sweep.completed event re-snapshots", async () => {
    const stub = installStub({ ready: true });
    render(<App />);

    await waitFor(() => expect(stub.streams.length).toBe(1));
    await waitFor(() => expect(stub.snapshotCalls.length).toBe(1));
    // The watermark is only applied once the mount snapshot has landed.
    await screen.findByText("Active Setups");
    const before = stub.snapshotCalls.length;

    await act(async () => {
      stub.streams[0].push(frame("000013", "sweep.completed"));
    });

    await waitFor(() =>
      expect(stub.snapshotCalls.length).toBeGreaterThan(before),
    );
  });

  test("a resync frame re-snapshots", async () => {
    const stub = installStub({ ready: true });
    render(<App />);

    await waitFor(() => expect(stub.streams.length).toBe(1));
    await screen.findByText("Active Setups");
    const before = stub.snapshotCalls.length;

    await act(async () => {
      stub.streams[0].push(frame("000014", "resync"));
    });

    await waitFor(() =>
      expect(stub.snapshotCalls.length).toBeGreaterThan(before),
    );
  });

  test("an event at or below the snapshot watermark does not re-snapshot", async () => {
    const stub = installStub({ ready: true });
    render(<App />);

    await waitFor(() => expect(stub.streams.length).toBe(1));
    await screen.findByText("Active Setups");
    const before = stub.snapshotCalls.length;

    // The mount snapshot carries snapshot_event_id "000012"; "000005" is
    // already covered by it and must be dropped, not re-fetched.
    await act(async () => {
      stub.streams[0].push(frame("000005", "setup.emitted"));
      await new Promise((resolve) => setTimeout(resolve, 50));
    });

    expect(stub.snapshotCalls.length).toBe(before);
  });
});
