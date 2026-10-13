// SSE hook tests — RED/GREEN with a fake fetch-based stream.
//
// The socket tripwire in tests/setup.ts blocks all real network egress, so
// global fetch is stubbed and the "stream" is a ReadableStream the test
// drives frame by frame. Frames are byte-for-byte what mock_service.py
// writes (id:/event:/data: lines, \n\n terminated, keepalive as ":"
// comment lines).
import { afterEach, describe, expect, test, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { useEvents } from "../src/api/sse";
import type { Event, Snapshot } from "../src/api/types";

const STREAM_URL = "http://127.0.0.1:18741/api/events";
const TOKEN = "test-token";

/** One SSE frame, exactly as mock_service.py's _frame() writes it. */
function frame(id: string, kind: string): Uint8Array {
  const data = JSON.stringify({ id, kind, ts: 1791936000, coin: null });
  return new TextEncoder().encode(
    `id: ${id}\nevent: ${kind}\ndata: ${data}\n\n`,
  );
}

/** A keepalive comment frame (mock_service.py writes `: keepalive <ts>`). */
const KEEPALIVE = new TextEncoder().encode(": keepalive 1791936000\n\n");

/** The envelope the /snapshot route would answer. */
function snapshotEnvelope(snapshotEventId: string): Response {
  const data: Snapshot = {
    snapshot_event_id: snapshotEventId,
    ts: 1791936000,
    data_as_of: 1791936000,
    eligible_count: 150,
    evaluated_count: 132,
    active_setups: 6,
    monitoring_coverage: 0.984,
    new_setups_24h: 6,
    setups: [],
    comparison_24h: {
      available: true,
      reason: null,
      prior_ts: 1791849600,
      delta_active_setups: 1,
      delta_new_setups_24h: 1,
    },
    snapshot_history: [],
    coins_without_setup: [],
    market_overview: [],
    movers: [],
    recent_analysis: [],
    activity: [],
    supported_timeframes: ["5m", "15m", "1H", "4H", "1D"],
    event_kinds: [
      "setup.emitted",
      "invalidated",
      "expired",
      "resolved",
      "monitoring.unavailable",
      "monitoring.ok",
      "sweep.completed",
    ],
    unavailable: null,
  };
  return new Response(JSON.stringify({ ok: true, data, error: null }), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

interface FakeStream {
  push(bytes: Uint8Array): void;
  close(): void;
}

interface FetchCall {
  url: string;
  init: RequestInit | undefined;
}

/**
 * Fetch stub. SSE GETs get a fresh test-driven stream (recorded in
 * `streams`, in connect order); /snapshot GETs answer with the pending or
 * resolved response the test installs via `setSnapshot`.
 */
function installFetchStub(): {
  streams: FakeStream[];
  calls: FetchCall[];
  setSnapshot(response: Response | Promise<Response>): void;
} {
  const streams: FakeStream[] = [];
  const calls: FetchCall[] = [];
  let snapshot: Response | Promise<Response> = snapshotEnvelope("");

  vi.stubGlobal("fetch", (input: RequestInfo | URL, init?: RequestInit) => {
    const url =
      typeof input === "string"
        ? input
        : input instanceof URL
          ? input.href
          : input.url;
    calls.push({ url, init });
    if (url.endsWith("/snapshot")) {
      return Promise.resolve(snapshot);
    }
    // The signal comes straight off init: constructing a Request from the
    // hook's init re-wraps the AbortSignal and can drop it.
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
    streams,
    calls,
    setSnapshot: (response) => {
      snapshot = response;
    },
  };
}

/** Renders useEvents with the four brief-pinned options (+ backoffMs). */
function mountHook(opts: {
  snapshotEventId: string | null;
  onEvent: (e: Event) => void;
  onResync: () => void;
  backoffMs?: number;
}) {
  return renderHook(
    (props: { snapshotEventId: string | null }) =>
      useEvents({
        url: STREAM_URL,
        token: TOKEN,
        snapshotEventId: props.snapshotEventId,
        onEvent: opts.onEvent,
        onResync: opts.onResync,
        options: { backoffMs: opts.backoffMs },
      }),
    { initialProps: { snapshotEventId: opts.snapshotEventId } },
  );
}

/** Waits until the hook has opened its first stream. */
async function waitConnected(result: {
  current: { connected: boolean };
}): Promise<void> {
  await waitFor(() => expect(result.current.connected).toBe(true));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useEvents — snapshot reconcile", () => {
  test("events <= snapshot_event_id are dropped", async () => {
    const stub = installFetchStub();
    const onEvent = vi.fn();
    const { result, unmount } = mountHook({
      snapshotEventId: "0002",
      onEvent,
      onResync: vi.fn(),
    });

    await waitConnected(result);

    // Stream emits e1 (id "0001"), e2 (id "0002"); snapshot id "0002".
    await act(async () => {
      stub.streams[0].push(frame("0001", "sweep.completed"));
      stub.streams[0].push(frame("0002", "sweep.completed"));
      stub.streams[0].push(frame("0003", "setup.emitted"));
    });

    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent.mock.calls[0][0].kind).toBe("setup.emitted");
    expect(result.current.lastEventId).toBe("0003");

    unmount();
  });

  test("resync event triggers onResync", async () => {
    const stub = installFetchStub();
    const onEvent = vi.fn();
    const onResync = vi.fn();
    const { result, unmount } = mountHook({
      snapshotEventId: "0002",
      onEvent,
      onResync,
    });

    await waitConnected(result);

    await act(async () => {
      stub.streams[0].push(frame("0003", "sweep.completed"));
      // The server emits a resync frame (kind "resync").
      stub.streams[0].push(frame("0004", "resync"));
    });

    expect(onResync).toHaveBeenCalledTimes(1);
    // The resync frame itself is not a transition event.
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent.mock.calls[0][0].id).toBe("0003");

    unmount();
  });

  test("transition between subscribe and snapshot is applied once", async () => {
    const stub = installFetchStub();
    const onEvent = vi.fn();
    const onResync = vi.fn();

    // Snapshot fetch starts but has not resolved yet.
    let releaseSnapshot!: () => void;
    const snapshotPending = new Promise<Response>((resolve) => {
      releaseSnapshot = () => resolve(snapshotEnvelope("0004"));
    });
    stub.setSnapshot(snapshotPending);

    // The hook subscribes (gate open, snapshot id null) BEFORE the snapshot
    // fetch resolves — the exact window mock_service.py protects by
    // subscribing before copying the ring.
    const { result, rerender, unmount } = mountHook({
      snapshotEventId: null,
      onEvent,
      onResync,
    });

    await waitConnected(result);

    // Transition id "0005" arrives BEFORE the snapshot fetch resolves
    // (snapshot id will be "0004").
    await act(async () => {
      stub.streams[0].push(frame("0005", "setup.emitted"));
    });

    // The snapshot resolves with snapshot_event_id "0004".
    await act(async () => {
      releaseSnapshot();
      rerender({ snapshotEventId: "0004" });
      await snapshotPending;
    });

    // The transition was applied exactly once — not twice (the gate must
    // not replay it) and not zero times (the gate must not swallow it).
    expect(onEvent).toHaveBeenCalledTimes(1);
    expect(onEvent.mock.calls[0][0].id).toBe("0005");
    expect(onEvent.mock.calls[0][0].kind).toBe("setup.emitted");

    unmount();
  });

  test("refresh traffic produces no events", async () => {
    const stub = installFetchStub();
    const onEvent = vi.fn();
    const { result, unmount } = mountHook({
      snapshotEventId: "0002",
      onEvent,
      onResync: vi.fn(),
    });

    await waitConnected(result);

    // A plain /snapshot refresh (new response, same event ids already
    // applied) followed by heartbeat traffic: the server emits nothing new.
    await act(async () => {
      stub.setSnapshot(snapshotEnvelope("0002"));
      stub.streams[0].push(KEEPALIVE);
      stub.streams[0].push(KEEPALIVE);
    });

    expect(onEvent).not.toHaveBeenCalled();

    unmount();
  });
});

describe("useEvents — reconnect", () => {
  test("reconnect after close sends Last-Event-ID, delayed by backoff", async () => {
    const stub = installFetchStub();
    const onEvent = vi.fn();
    const { result, unmount } = mountHook({
      snapshotEventId: "0002",
      onEvent,
      onResync: vi.fn(),
      backoffMs: 20,
    });

    await waitConnected(result);
    expect(stub.calls.length).toBe(1);

    await act(async () => {
      stub.streams[0].push(frame("0009", "sweep.completed"));
    });
    expect(result.current.lastEventId).toBe("0009");

    // Server closes the stream.
    await act(async () => {
      stub.streams[0].close();
    });
    await waitFor(() => expect(result.current.connected).toBe(false));

    // Backoff: no reconnect attempt before the delay elapses.
    expect(stub.calls.length).toBe(1);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
    expect(stub.calls.length).toBe(1);

    // After the backoff window, reconnect — carrying Last-Event-ID.
    await waitFor(() => expect(stub.calls.length).toBe(2), { timeout: 2000 });
    const headers = new Headers(stub.calls[1].init?.headers);
    expect(headers.get("Last-Event-ID")).toBe("0009");
    expect(headers.get("Authorization")).toBe(`Bearer ${TOKEN}`);

    await waitConnected(result);

    unmount();
  });
});
