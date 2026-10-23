// Status — the single landing page for the collection-only service.
//
// House rules:
//   * The quant engine is explicitly DEVELOPING — it never shows "ready".
//     /readyz reflects only collector/service readiness, and is labelled as
//     such; it never gates the quant engine state, which is a fixed
//     "In development / not ready" label.
//   * No candidates, scores, or signal surfaces are shown.
//   * Unavailable values render as "unavailable" — never zero, never a guess.
import { useEffect, useState, type ReactNode } from "react";
import { StatCard } from "../components/StatCard";

export interface StatusPageProps {
  client: {
    readyz(): Promise<boolean>;
    collectorHealth(): Promise<unknown>;
  };
  connection: "starting" | "connected" | "error";
}

interface CollectorHealth {
  status: "starting" | "healthy" | "degraded" | "failed";
  started_ts: number;
  first_success_ts: number | null;
  last_cycle_ts: number | null;
  cycle_count: number;
  bars: { coins: number; new_bars: number; errors: unknown } | null;
  funding: unknown;
  settlements: unknown;
  prices: unknown;
  data_dir: string;
  every_s: number;
}

function readyLabel(connection: StatusPageProps["connection"]): string {
  switch (connection) {
    case "starting":
      return "Starting…";
    case "connected":
      return "Connected";
    case "error":
      return "Connection error";
  }
}

function collectorLabel(ready: boolean | null): string {
  if (ready === true) return "ready";
  if (ready === false) return "initializing";
  return "starting";
}

export function StatusPage(props: StatusPageProps): ReactNode {
  const [ready, setReady] = useState<boolean | null>(null);
  const [health, setHealth] = useState<CollectorHealth | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (props.connection !== "connected") return;
      try {
        const [readyRes, healthRes] = await Promise.all([
          props.client.readyz(),
          props.client.collectorHealth(),
        ]);
        if (cancelled) return;
        setReady(readyRes);
        setHealth(healthRes as CollectorHealth);
      } catch (err) {
        if (cancelled) return;
        setLoadError(
          err instanceof Error ? err.message : "failed to load status",
        );
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [props]);

  const bars = health?.bars ?? null;
  const cycle = (health && bars)
    ? `${String(bars.coins)} coins, ${String(bars.new_bars)} new bars`
    : "unavailable";

  return (
    <section className="px-6 py-6" aria-label="Quant engine status">
      <h1 className="text-lg font-semibold text-fg">ALT RADAR</h1>

      {/* Quant engine: explicitly developing, NEVER ready-for-trading. */}
      <div className="mt-4 rounded-xl border border-border bg-card px-4 py-4">
        <div className="flex items-baseline justify-between">
          <span className="text-xs font-medium uppercase tracking-wide text-muted">
            Quant engine
          </span>
          <span className="text-sm font-semibold text-short">
            In development / not ready
          </span>
        </div>
        <p className="mt-2 text-sm text-fg">
          In development. No candidates are generated or displayed in this
          phase — the legacy scanner and rule-based signal surfaces have been
          removed from the application and its service. The quant engine is
          not ready; only bar, funding, settlement, and price archives are
          collected, and real collector status is shown below.
        </p>
        {loadError && (
          <p className="mt-2 text-sm text-short">
            Status unavailable: {loadError}
          </p>
        )}
      </div>

      {/* Collector/service readiness (decoupled from the quant engine). */}
      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard label="API status" value={readyLabel(props.connection)} />
        <StatCard label="Service readiness" value={collectorLabel(ready)} />
        <StatCard
          label="Collector"
          value={health ? health.status : "unavailable"}
        />
        <StatCard
          label="Cycles completed"
          value={health ? String(health.cycle_count) : "unavailable"}
        />
        <StatCard label="This cycle" value={health ? cycle : "unavailable"} />
        <StatCard
          label="Archive path"
          value={health ? health.data_dir : "unavailable"}
        />
      </div>
    </section>
  );
}
