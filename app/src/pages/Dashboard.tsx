// Dashboard — the functional page: stat cards, filter bar, Top Signals
// table, Market Overview + coin detail (right column), and the bottom
// row (Market Movers / Recent Analysis / Activity Log).
//
// House rules:
//   * One read on mount: client.snapshot(). Filtering and sorting happen
//     client-side over the snapshot's setups — a filter change never issues
//     a new request. Closed 5m bars are read once per displayed coin for
//     the trend sparklines.
//   * READ-ONLY: every call this page makes is a GET. There is no POST path.
//   * Row click = selection only (selectCoin) — never backend state.
//   * "data as of <ts>" header line; timestamps are UTC-seconds ints.
//   * Colors come from theme tokens only — never raw hexes.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createClient } from "../api/client";
import type { ApiClient } from "../api/client";
import { useEvents } from "../api/sse";
import type { Snapshot, Setup } from "../api/types";
import { ActivityLog } from "../components/ActivityLog";
import { FilterBar } from "../components/FilterBar";
import { MarketMovers } from "../components/MarketMovers";
import { MarketOverview } from "../components/MarketOverview";
import { CoinDetail } from "../components/CoinDetail";
import { RecentAnalysis } from "../components/RecentAnalysis";
import { StatCards } from "../components/StatCards";
import { TopSignalsTable } from "../components/TopSignalsTable";
import { useDashboardStore } from "../store/dashboard";
import type { ConnectionState, MarketFilter, SortKey } from "../store/dashboard";

/** Formats a UTC-seconds int as "YYYY-MM-DD HH:MM:SS UTC". */
function formatUtc(ts: number): string {
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`
  );
}

/**
 * Reads the dashboard snapshot. `load()` is the single fetch path: the
 * mount effect calls it once, and live SSE transitions call `refetch`.
 *
 * Coalescing: while a read is in flight a further call does not start a
 * second request — it arms `pending`, and exactly one extra read runs when
 * the current one lands. A burst of transition events therefore costs one
 * extra GET, not one per event, and the mount read is never duplicated.
 */
function useSnapshot(client: ApiClient): {
  snapshot: Snapshot | null;
  error: string | null;
  refetch: () => void;
} {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inFlightRef = useRef(false);
  const pendingRef = useRef(false);
  const mountedRef = useRef(true);

  const load = useCallback((): void => {
    if (inFlightRef.current) {
      pendingRef.current = true;
      return;
    }
    inFlightRef.current = true;
    client
      .snapshot()
      .then((data) => {
        if (!mountedRef.current) return;
        setSnapshot(data);
        setError(null);
        useDashboardStore.getState().setDataAsOf(data.data_as_of);
      })
      .catch((err: unknown) => {
        if (!mountedRef.current) return;
        setError(err instanceof Error ? err.message : "snapshot read failed");
      })
      .finally(() => {
        inFlightRef.current = false;
        if (pendingRef.current) {
          pendingRef.current = false;
          load();
        }
      });
  }, [client]);

  useEffect(() => {
    mountedRef.current = true;
    load();
    return () => {
      mountedRef.current = false;
    };
  }, [load]);

  return { snapshot, error, refetch: load };
}

/** How often the liveness probe re-checks the local API (ms). */
const CONNECTION_PROBE_INTERVAL_MS = 5_000;

/**
 * Connection means the local API is reachable and its event stream opened.
 * Scanner readiness is separate: the service can serve persisted data while
 * a new scan runs, so /readyz's first-sweep state must not look disconnected.
 *
 *   * not alive → "error" (contained fallback, retrying)
 *   * alive, stream up → "connected"
 *   * alive, stream down → hold an established connection during brief
 *     reconnects; show "starting" only before the first stream connects.
 *
 * Probed on mount, on stream transitions, and every five seconds.
 */
function useConnectionStatus(
  client: ApiClient,
  streamOpen: boolean,
): void {
  const streamOpenRef = useRef(streamOpen);
  streamOpenRef.current = streamOpen;

  useEffect(() => {
    let cancelled = false;

    async function probe(): Promise<void> {
      const alive = await client.healthz();
      if (cancelled) return;
      if (!alive) {
        useDashboardStore.getState().setConnection("error");
        return;
      }
      // Write only on a real transition: zustand notifies subscribers on
      // every set(), so re-setting the same value would re-render the
      // shell for nothing (and, in tests, for an act() warning per
      // probe).
      const current = useDashboardStore.getState().connection;
      let next: ConnectionState;
      if (streamOpenRef.current) {
        next = "connected";
      } else {
        // Ready but the stream is down: a reconnect blip is not a first
        // sweep, so it must not print "starting" — hold the current state
        // and let the next successful probe re-confirm.
        next = current === "connected" ? "connected" : "starting";
      }
      if (next !== current) {
        useDashboardStore.getState().setConnection(next);
      }
    }

    void probe();
    const timer = setInterval(() => {
      void probe();
    }, CONNECTION_PROBE_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [client, streamOpen]);
}

/**
 * Reads closed 5m closes for each coin not yet cached. The snapshot itself
 * carries no bars, so the trend sparkline is drawn only from real closed
 * bars; a coin whose bars never arrive keeps the empty frame (never
 * fabricated), and a null volume reading never counts as 0.
 *
 * Dedup happens in the resolution, never before the await: a coin is marked
 * fetched only once its bars have actually landed. An earlier run whose
 * cleanup fires first (React StrictMode's double-invoke in dev, or a
 * filter/sort change producing a fresh `coins` array mid-flight) simply
 * loses its promise — the next run sees the coin as unfetched and retries
 * it, so no coin is ever stranded with an empty sparkline. Duplicate GETs
 * for the same coin are idempotent and bounded (one extra read per re-run).
 */
function useClosedCloses(client: ApiClient, coins: string[]): Record<string, number[]> {
  const [closes, setCloses] = useState<Record<string, number[]>>({});
  const fetchedRef = useRef<Set<string>>();
  if (fetchedRef.current === undefined) fetchedRef.current = new Set<string>();
  const fetched = fetchedRef.current;

  useEffect(() => {
    let cancelled = false;
    const pending = coins.filter((coin) => !fetched.has(coin));
    // Safe to skip: this run started no fetch, so there is nothing for a
    // cleanup to cancel. Coins already marked fetched have landed data.
    if (pending.length === 0) return;

    Promise.all(
      pending.map(async (coin) => {
        try {
          const bars = await client.bars(coin, "5m");
          const values = Array.isArray(bars.bars)
            ? bars.bars.filter((bar) => bar.closed).map((bar) => bar.close)
            : [];
          return [coin, values] as const;
        } catch {
          return [coin, [] as number[]] as const;
        }
      }),
    ).then((entries) => {
      if (cancelled) return;
      for (const [coin] of entries) fetched.add(coin);
      setCloses((prev) => ({ ...prev, ...Object.fromEntries(entries) }));
    });

    return () => {
      cancelled = true;
    };
  }, [client, coins, fetched]);

  return closes;
}

/** Top tier size for the market segment filter. */
const TOP_N = 20;

/**
 * Market segment over the setups. The /snapshot contract carries no 24h
 * quote-volume ranking, so the segment uses each setup's own volume
 * reading (volume vs the 20-bar median — the only volume measure the
 * contract exposes): the top tier is the TOP_N highest readings, the tail
 * is everything else. Setups whose volume reading did not arrive are never
 * treated as 0 — they always land in the tail.
 */
function applyMarketSegment(setups: Setup[], segment: MarketFilter): Setup[] {
  if (segment === "all") return setups;
  const withVolume = setups.filter((s) => s.inputs.volume_pct !== null);
  const withoutVolume = setups.filter((s) => s.inputs.volume_pct === null);
  const ranked = [...withVolume].sort(
    (a, b) => (b.inputs.volume_pct ?? 0) - (a.inputs.volume_pct ?? 0),
  );
  if (segment === "top20") return ranked.slice(0, TOP_N);
  return [...ranked.slice(TOP_N), ...withoutVolume];
}

/** Sort order of the setups table. */
function sortSetups(setups: Setup[], sort: SortKey): Setup[] {
  const rows = [...setups];
  switch (sort) {
    case "newest":
      return rows.sort((a, b) => b.emitted_ts - a.emitted_ts);
    case "expiry":
      return rows.sort((a, b) => a.expires_ts - b.expires_ts);
    case "change24h":
      return rows.sort(
        (a, b) => Math.abs(b.change_24h_pct ?? -1) - Math.abs(a.change_24h_pct ?? -1),
      );
  }
}

export interface DashboardProps {
  apiBaseUrl?: string;
  serviceToken?: string;
}

export function Dashboard({
  apiBaseUrl = "/api",
  serviceToken = import.meta.env.DEV ? "dev-token" : "",
}: DashboardProps = {}) {
  const client = useMemo(
    () => createClient({ baseUrl: apiBaseUrl, token: serviceToken }),
    [apiBaseUrl, serviceToken],
  );

  const marketFilter = useDashboardStore((s) => s.marketFilter);
  const horizonFilter = useDashboardStore((s) => s.horizonFilter);
  const directionFilter = useDashboardStore((s) => s.directionFilter);
  const sort = useDashboardStore((s) => s.sort);
  const moversTab = useDashboardStore((s) => s.moversTab);
  const selectedCoin = useDashboardStore((s) => s.selectedCoin);

  const { snapshot, error, refetch } = useSnapshot(client);

  // Live SSE stream: subscribe FIRST, then the snapshot lands. The hook's
  // reconcile gate drops events already covered by snapshot_event_id and
  // applies newer ones; a transition that lands in the subscribe-before-
  // copy window is applied exactly once. Every applied transition (and
  // every server resync) re-snapshots, so the board reflects the
  // transition instead of waiting for the next full reload. The snapshot
  // read coalesces, so a burst costs one extra GET.
  const { connected: streamOpen } = useEvents({
    url: `${apiBaseUrl.replace(/\/+$/, "")}/events`,
    token: serviceToken,
    snapshotEventId: snapshot?.snapshot_event_id ?? null,
    onEvent: () => {
      refetch();
    },
    onResync: () => {
      refetch();
    },
  });

  // Connection banner state: driven by /healthz + /readyz, gated on the
  // stream being open — see useConnectionStatus.
  useConnectionStatus(client, streamOpen);

  const activeSetups = useMemo(
    () => (snapshot ? snapshot.setups.filter((s) => s.state === "active") : []),
    [snapshot],
  );

  const filtered = useMemo(() => {
    const segmented = applyMarketSegment(activeSetups, marketFilter);
    return segmented.filter((s) => s.horizon === horizonFilter);
  }, [activeSetups, marketFilter, horizonFilter]);

  const counts = useMemo(
    () => ({
      all: filtered.length,
      long: filtered.filter((s) => s.direction === "LONG").length,
      short: filtered.filter((s) => s.direction === "SHORT").length,
    }),
    [filtered],
  );

  const rows = useMemo(() => {
    const byDirection =
      directionFilter === "all"
        ? filtered
        : filtered.filter(
            (s) => s.direction.toLowerCase() === directionFilter,
          );
    return sortSetups(byDirection, sort);
  }, [filtered, directionFilter, sort]);

  const coins = useMemo(() => {
    // The Top Signals table's coins AND the Market Overview's own coins
    // (BTC + ETH by contract): the overview sparklines are drawn from the
    // same closed 5m closes cache, so a coin missing from the filtered
    // table (e.g. under a 24h horizon filter) still gets its real closes
    // and the overview row never keeps a dead empty frame.
    const overviewCoins = snapshot?.market_overview.map((r) => r.coin) ?? [];
    return Array.from(new Set([...rows.map((s) => s.coin), ...overviewCoins]));
  }, [rows, snapshot]);
  const closes = useClosedCloses(client, coins);

  // Blocking reason for the selected coin, from the snapshot's
  // coins_without_setup (never invented — null when not listed).
  const blockingReason = useMemo(() => {
    if (!snapshot || selectedCoin === null) return null;
    return (
      snapshot.coins_without_setup.find((c) => c.coin === selectedCoin)
        ?.blocking_reason ?? null
    );
  }, [snapshot, selectedCoin]);

  return (
    <section className="space-y-4 px-6 py-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold text-fg">Dashboard</h1>
        {snapshot ? (
          <span className="text-xs text-muted">
            data as of {snapshot.data_as_of === null
              ? "unavailable"
              : formatUtc(snapshot.data_as_of)}
          </span>
        ) : null}
      </div>

      {snapshot ? (
        <>
          <StatCards snapshot={snapshot} />

          {snapshot.unavailable ? (
            <p role="status" className="rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted">
              {snapshot.unavailable}
            </p>
          ) : null}

          <FilterBar counts={counts} />

          <div>
            <h2 className="mb-2 text-sm font-semibold text-fg">Top Signals</h2>
            <TopSignalsTable
              setups={rows}
              barsByCoin={closes}
              dataAsOf={snapshot.data_as_of}
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <MarketOverview
              overview={snapshot.market_overview}
              closesByCoin={closes}
            />
            <CoinDetail client={client} blockingReason={blockingReason} />
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <MarketMovers movers={snapshot.movers} tab={moversTab} />
            <RecentAnalysis entries={snapshot.recent_analysis} />
            <ActivityLog entries={snapshot.activity} />
          </div>
        </>
      ) : error ? (
        <div className="rounded-xl border border-border bg-card px-4 py-3 text-sm text-muted">
          snapshot unavailable — {error}
        </div>
      ) : (
        <div className="rounded-xl border border-border bg-card px-4 py-3 text-sm text-muted">
          loading snapshot…
        </div>
      )}
    </section>
  );
}
