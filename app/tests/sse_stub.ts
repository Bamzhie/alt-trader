// Shared SSE stub for the wired Dashboard (Task 11 fix round 1).
//
// Now that Dashboard.tsx opens /api/events and probes /healthz + /readyz,
// every test that renders <App /> must answer those routes. A stub that
// falls through to the snapshot-envelope answer would hand the hook a JSON
// body where a stream was expected: the hook reads it, the stream ends
// immediately, and its 1s-backoff reconnect loop spins for the rest of the
// test — act() noise and a 2.5x slower suite.
//
// This helper answers the contract shapes instead: an SSE body that stays
// open until the test closes it or the hook aborts, plus the two probes.

/** A test-driven SSE body: open on creation, closed by `close()` or abort. */
export interface FakeStream {
  push(bytes: Uint8Array): void;
  close(): void;
}

/**
 * Builds a 200 text/event-stream Response whose body never ends on its
 * own. Enqueued bytes flow to the hook's reader; `cancel`/abort closes it.
 */
export function sseStreamResponse(
  signal?: AbortSignal | null,
): { response: Response; stream: FakeStream } {
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
  const stream: FakeStream = {
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
  };
  return {
    response: new Response(body, {
      status: 200,
      headers: { "Content-Type": "text/event-stream" },
    }),
    stream,
  };
}

/** The non-envelope liveness answer: `{"ok":true,"alive":true}`. */
export function healthzOk(): Response {
  return new Response(JSON.stringify({ ok: true, alive: true }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

/** The readiness answer while the first sweep has finished. */
export function readyzOk(): Response {
  return new Response(
    JSON.stringify({ ok: true, data: { ready: true }, error: null }),
    { status: 200, headers: { "Content-Type": "application/json" } },
  );
}

/** The readiness answer while the first sweep is still running. */
export function readyzUnavailable(): Response {
  return new Response(
    JSON.stringify({
      ok: false,
      data: null,
      error: { code: "UNAVAILABLE", message: "first sweep in progress" },
    }),
    { status: 503, headers: { "Content-Type": "application/json" } },
  );
}

/**
 * Installs a minimal read-only fetch stub answering every route the wired
 * Dashboard opens on mount (/healthz, /readyz, /snapshot, /events) plus
 * the detail panel's /bars and /metrics. Any POST throws.
 *
 * Tests that render <App /> without stubbing fetch at all would otherwise
 * leave the mount-time probes resolving after the test body — a state
 * update outside act() per probe, and a real (failing) fetch attempt per
 * render. Chrome-level tests that don't care about the data path install
 * this in beforeEach and stay deterministic.
 */
export function stubReadOnlyFetch(): void {
  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if ((init?.method ?? "GET").toUpperCase() === "POST") {
      throw new Error(`POST attempted against read-only dashboard: ${url}`);
    }
    if (url.endsWith("/healthz")) return Promise.resolve(healthzOk());
    if (url.endsWith("/readyz")) return Promise.resolve(readyzOk());
    if (url.endsWith("/events")) {
      return Promise.resolve(sseStreamResponse(init?.signal ?? null).response);
    }
    if (url.includes("/bars") || url.includes("/metrics")) {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            ok: false,
            data: null,
            error: { code: "NOT_FOUND", message: "stub" },
          }),
          { status: 404, headers: { "Content-Type": "application/json" } },
        ),
      );
    }
    return Promise.resolve(
      new Response(
        JSON.stringify({
          ok: true,
          data: {
            snapshot_event_id: "000012",
            ts: 1791936000,
            data_as_of: 1791936000,
            eligible_count: 0,
            evaluated_count: 0,
            active_setups: 0,
            monitoring_coverage: null,
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
          },
          error: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );
  });
}
