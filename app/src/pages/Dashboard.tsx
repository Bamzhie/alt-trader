// Dashboard — the functional page: stat cards, filter bar, Top Signals
// table, and the placeholder regions Tasks 9–11 fill (Market Overview,
// coin detail, bottom row).
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
import { useEffect, useMemo, useRef, useState } from "react";
import { createClient } from "../api/client";
import type { ApiClient } from "../api/client";
import type { Snapshot, Setup } from "../api/types";
import { FilterBar } from "../components/FilterBar";
import { StatCards } from "../components/StatCards";
import { TopSignalsTable } from "../components/TopSignalsTable";
import { useDashboardStore } from "../store/dashboard";
import type { MarketFilter, SortKey } from "../store/dashboard";

// Dev bearer for the stdlib mock service (its default token, printed once
// to stdout by dev/mock_service.py). The production service injects a
// per-launch token the shell captures — never bundled — so this constant
// stays dev-scoped and is replaced when the Tauri shell lands.
const DEV_TOKEN = "dev-token";

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
 * Fetches the dashboard snapshot once on mount. A failure surfaces the
 * ApiError message — the page then renders a contained unavailable region
 * instead of stale or invented numbers.
 */
function useSnapshot(client: ApiClient): {
  snapshot: Snapshot | null;
  error: string | null;
} {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    client
      .snapshot()
      .then((data) => {
        if (cancelled) return;
        setSnapshot(data);
        useDashboardStore.getState().setDataAsOf(data.data_as_of);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : "snapshot read failed");
      });
    return () => {
      cancelled = true;
    };
  }, [client]);

  return { snapshot, error };
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
        (a, b) => Math.abs(b.change_24h_pct) - Math.abs(a.change_24h_pct),
      );
  }
}

/** Structural placeholder for a region a later task fills. */
function RegionPlaceholder({ title, note }: { title: string; note: string }) {
  return (
    <section className="rounded-xl border border-border bg-card p-4">
      <h2 className="text-sm font-semibold text-fg">{title}</h2>
      <p className="mt-2 text-xs text-muted">{note}</p>
    </section>
  );
}

export function Dashboard() {
  const client = useMemo(() => createClient({ baseUrl: "/api", token: DEV_TOKEN }), []);

  const marketFilter = useDashboardStore((s) => s.marketFilter);
  const horizonFilter = useDashboardStore((s) => s.horizonFilter);
  const directionFilter = useDashboardStore((s) => s.directionFilter);
  const sort = useDashboardStore((s) => s.sort);

  const { snapshot, error } = useSnapshot(client);

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

  const coins = useMemo(() => rows.map((s) => s.coin), [rows]);
  const closes = useClosedCloses(client, coins);

  return (
    <section className="space-y-4 px-6 py-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold text-fg">Dashboard</h1>
        {snapshot ? (
          <span className="text-xs text-muted">
            data as of {formatUtc(snapshot.data_as_of)}
          </span>
        ) : null}
      </div>

      {snapshot ? (
        <>
          <StatCards snapshot={snapshot} />

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
            <RegionPlaceholder
              title="Market Overview"
              note="BTC and ETH overview rows build here in a later task."
            />
            <RegionPlaceholder
              title="Coin Detail"
              note="Chart, tabs, and trade plan build here in a later task."
            />
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <RegionPlaceholder
              title="Market Movers"
              note="Gainers, losers, and volume tabs build here in a later task."
            />
            <RegionPlaceholder
              title="Recent Analysis"
              note="The last five transitions build here in a later task."
            />
            <RegionPlaceholder
              title="Activity Log"
              note="The structured journal builds here in a later task."
            />
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
