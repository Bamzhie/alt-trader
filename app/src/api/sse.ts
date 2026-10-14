// SSE hook: opens the /events stream, applies the snapshot-event-id
// reconcile gate, and reconnects with exponential backoff carrying
// Last-Event-ID.
//
// Why fetch and not EventSource: EventSource can't send an Authorization
// header (the contract requires the bearer token on every route), and a
// fetch + ReadableStream reader keeps the stream testable without a real
// socket (the vitest tripwire stays armed).
//
// Reconcile contract (mock_service.py _events_route):
//   * ids are zero-padded, monotonic, width-6 — plain string comparison is
//     the documented lexicographic compare.
//   * The caller's snapshot already covers every event with
//     id <= snapshot_event_id, so those are dropped on arrival (idempotent
//     replay). While the snapshot is still in flight (snapshotEventId is
//     null) the gate is OPEN: a transition that arrives in the
//     subscribe-before-copy window is applied exactly once — the store
//     applies it, and any later replay is swallowed by the watermark.
//   * A "resync" frame means the server lost our cursor (ring-buffer floor):
//     the store must re-snapshot. We surface onResync() and keep the stream
//     open — the new snapshot's id tightens the gate.
import { useEffect, useRef, useState } from "react";
import type { Event } from "./types";

/** A resync frame carries a human-readable reason in its data payload. */
interface ResyncFrame extends Event {
  reason?: string;
}

export interface UseEventsOptions {
  /**
   * Base delay for exponential reconnect backoff (ms). The first reconnect
   * waits `backoffMs`, doubling each failed attempt up to 30s. Tests pin it
   * small; production uses the default.
   */
  backoffMs?: number;
}

export interface UseEventsInput {
  /** Full URL of the SSE stream (e.g. "http://127.0.0.1:18741/api/events"). */
  url: string;
  /** Bearer token sent as the Authorization header on every connect. */
  token: string;
  /**
   * snapshot_event_id of the snapshot currently applied by the store.
   * null = the snapshot is still in flight (gate open). String compare:
   * ids are zero-padded monotonic width-6.
   */
  snapshotEventId: string | null;
  /** Called for each transition event with id > snapshotEventId. */
  onEvent: (event: Event) => void;
  /** Called when the server emits a resync frame — the store re-snapshots. */
  onResync: () => void;
  options?: UseEventsOptions;
}

export interface UseEventsResult {
  connected: boolean;
  lastEventId: string | null;
}

const DEFAULT_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30_000;

/** Decodes one SSE frame into an Event, or null for comments/keepalives. */
function parseFrame(frame: string): ResyncFrame | null {
  let id: string | null = null;
  let kind = "message";
  const dataLines: string[] = [];
  for (const rawLine of frame.split("\n")) {
    const line = rawLine.replace(/\r$/, "");
    if (line === "" || line.startsWith(":")) continue; // comment / keepalive
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    switch (field) {
      case "id":
        id = value;
        break;
      case "event":
        kind = value;
        break;
      case "data":
        dataLines.push(value);
        break;
      default:
        break; // unknown field — SSE spec says ignore
    }
  }
  if (id === null) return null; // no id: nothing we can reconcile against
  let parsed: Record<string, unknown> = {};
  const data = dataLines.join("\n");
  if (data !== "") {
    try {
      parsed = JSON.parse(data) as Record<string, unknown>;
    } catch {
      parsed = {};
    }
  }
  return {
    id,
    kind,
    ts: typeof parsed.ts === "number" ? parsed.ts : 0,
    coin: typeof parsed.coin === "string" ? parsed.coin : null,
    reason: typeof parsed.reason === "string" ? parsed.reason : undefined,
  };
}

export function useEvents(input: UseEventsInput): UseEventsResult {
  const {
    url,
    token,
    snapshotEventId,
    onEvent,
    onResync,
    options,
  } = input;
  const backoffMs = options?.backoffMs ?? DEFAULT_BACKOFF_MS;

  const [connected, setConnected] = useState(false);
  const [lastEventId, setLastEventId] = useState<string | null>(null);

  // Latest-wins refs so the streaming loop never reads stale closures and
  // the connect effect does not restart when identity-only props change.
  const snapshotIdRef = useRef(snapshotEventId);
  snapshotIdRef.current = snapshotEventId;
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const onResyncRef = useRef(onResync);
  onResyncRef.current = onResync;
  // The reconnect header must carry the freshest cursor even though
  // setLastEventId is batched for the caller.
  const lastEventIdRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let controller: AbortController | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    async function connect(): Promise<void> {
      while (!cancelled) {
        controller = new AbortController();
        setConnected(false);
        try {
          const response = await fetch(url, {
            headers: {
              Authorization: `Bearer ${token}`,
              Accept: "text/event-stream",
              ...(lastEventIdRef.current !== null
                ? { "Last-Event-ID": lastEventIdRef.current }
                : {}),
            },
            signal: controller.signal,
          });
          if (cancelled) return;
          if (!response.ok || !response.body) {
            throw new Error(`SSE connect failed: HTTP ${response.status}`);
          }
          attempt = 0; // a healthy connect resets the backoff
          setConnected(true);

          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          try {
            for (;;) {
              const { done, value } = await reader.read();
              if (done) break;
              buffer += decoder.decode(value, { stream: true });
              let idx: number;
              while ((idx = buffer.indexOf("\n\n")) !== -1) {
                const raw = buffer.slice(0, idx);
                buffer = buffer.slice(idx + 2);
                const event = parseFrame(raw);
                if (event === null) continue; // keepalive / comment
                lastEventIdRef.current = event.id;
                setLastEventId(event.id);
                if (event.kind === "resync") {
                  onResyncRef.current();
                  continue;
                }
                // Reconcile gate: the applied snapshot already covers
                // ids <= snapshot_event_id (string compare — zero-padded
                // monotonic ids are lexicographically ordered).
                const gate = snapshotIdRef.current;
                if (gate !== null && event.id <= gate) continue;
                onEventRef.current(event);
              }
            }
          } finally {
            reader.releaseLock();
          }
        } catch {
          if (cancelled) return;
        }
        if (cancelled) return;
        setConnected(false);
        const delay = Math.min(backoffMs * 2 ** attempt, MAX_BACKOFF_MS);
        attempt += 1;
        await new Promise<void>((resolve) => {
          timer = setTimeout(resolve, delay);
        });
      }
    }

    void connect();

    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
      controller?.abort();
    };
    // Refs hold the latest callbacks/watermark, so only the connection
    // identity (url/token/backoff) may restart the loop.
  }, [url, token, backoffMs]);

  return { connected, lastEventId };
}
