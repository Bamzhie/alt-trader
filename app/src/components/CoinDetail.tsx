// CoinDetail — the selected coin's panel: header (badge, "COIN - Name",
// price, 24h %, direction pill), timeframe tabs (5m/15m/1H/4h/1D — the
// options array literally omits 1m), the CandleChart, and the
// Analysis / Trade Plan / Metrics tabs.
//
// House rules:
//   * All network goes through the Task 3 client (createClient) — GETs
//     only; a failure surfaces "bars unavailable" per tab or an error
//     message, never fabricated data.
//   * A coin without a setup renders "no setup yet" plus its blocking
//     reason in the SAME panel (the panel stays mounted).
//   * `rsi: null` renders "unavailable", never 0 — every indicator is
//     null-checked before it is formatted.
//   * Trade Plan levels carry the literal line "100 USDT reference
//     notional — reference size, not a position" and there is no
//     leverage key anywhere.
//   * Timestamps are UTC-seconds ints; bars are closed only (the chart
//     filters them).
//   * Colors come from theme tokens only — never raw hexes.
import { useEffect, useMemo, useState } from "react";
import { createClient, ApiError } from "../api/client";
import type { ApiClient } from "../api/client";
import type {
  Bars,
  Metrics,
  Setup,
  TradePlan,
} from "../api/types";
import { CandleChart } from "./CandleChart";
import { LetterBadge } from "./LetterBadge";
import { SignalPill } from "./SignalPill";
import type { BarTimeframe } from "../api/types";
import { useDashboardStore } from "../store/dashboard";
import type { Tab } from "../store/dashboard";

// The options array literally omits "1m" — 1m is not a supported
// timeframe anywhere in the contract.
const TIMEFRAME_OPTIONS: readonly BarTimeframe[] = ["5m", "15m", "1H", "4H", "1D"];

const TAB_OPTIONS: readonly Tab[] = ["analysis", "tradeplan", "metrics"];

const TAB_LABEL: Record<Tab, string> = {
  analysis: "Analysis",
  tradeplan: "Trade Plan",
  metrics: "Metrics",
};

export interface CoinDetailProps {
  /** Injectable client; defaults to the /api dev-proxy client. */
  client?: ApiClient;
  /**
   * Blocking reason for the selected coin, looked up from the snapshot's
   * `coins_without_setup` by the caller. null = not in that list, in
   * which case the panel renders the generic no-setup copy — never an
   * invented reason.
   */
  blockingReason?: string | null;
}

interface Async<T> {
  data: T | null;
  error: string | null;
}

/** Formats a UTC-seconds int as "YYYY-MM-DD HH:MM:SS UTC". */
function formatUtc(ts: number): string {
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`
  );
}

/** Formats a level: grouped integer part, up to 2 decimals. */
function formatLevel(v: number): string {
  return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

/**
 * Formats an indicator value: grouped integer part, up to 4 decimals,
 * and always at least enough decimals to keep the value significant
 * (MACD 0.0211 must never collapse to "0.02").
 */
function formatNumber(v: number): string {
  if (!Number.isFinite(v)) return String(v);
  const abs = Math.abs(v);
  let decimals = 2;
  if (abs > 0 && abs < 1) {
    decimals = Math.max(2, Math.ceil(-Math.log10(abs)) + 2);
  }
  return v.toLocaleString("en-US", {
    minimumFractionDigits: Math.min(decimals, 4),
    maximumFractionDigits: Math.max(decimals, 4),
  });
}

/** Indicator value with the "unavailable" rule: null never renders as 0. */
function formatIndicator(v: number | null): string {
  return v === null ? "unavailable" : formatNumber(v);
}

/** Signed 24h change, two decimals. */
function signedPct(pct: number): string {
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  if (err instanceof Error) return err.message;
  return "request failed";
}

function emptyBars(coin: string, tf: BarTimeframe): Bars {
  return {
    coin,
    tf,
    bars: [],
    closed_only: true,
    count: 0,
    data_as_of: 0,
    unavailable: null,
  };
}

export function CoinDetail({ client: clientProp, blockingReason }: CoinDetailProps) {
  const client = useMemo(
    () => clientProp ?? createClient({ baseUrl: "/api", token: "dev-token" }),
    [clientProp],
  );

  const selectedCoin = useDashboardStore((s) => s.selectedCoin);
  const tab = useDashboardStore((s) => s.tab);
  const barTimeframe = useDashboardStore((s) => s.barTimeframe);
  const setTab = useDashboardStore((s) => s.setTab);
  const setBarTimeframe = useDashboardStore((s) => s.setBarTimeframe);

  const [setup, setSetup] = useState<Async<Setup>>({ data: null, error: null });
  const [plan, setPlan] = useState<Async<TradePlan>>({ data: null, error: null });
  const [metrics, setMetrics] = useState<Async<Metrics>>({ data: null, error: null });
  const [bars, setBars] = useState<Async<Bars>>({ data: null, error: null });

  // Reset + fetch on every coin switch. A coin without a setup 404s —
  // the panel then renders "no setup yet" with its blocking reason.
  //
  // The four states are cleared at the TOP of the effect (and for a null
  // selection too): a coin switch BTC -> ETH must not keep the previous
  // coin's setup/plan/metrics/bars on screen while ETH's reads are in
  // flight — otherwise the header renders "ETH - Bitcoin" with BTC's
  // price and the chart shows BTC candles until ETH's data lands.
  useEffect(() => {
    setSetup({ data: null, error: null });
    setPlan({ data: null, error: null });
    setMetrics({ data: null, error: null });
    setBars({ data: null, error: null });
    if (selectedCoin === null) return;
    let cancelled = false;
    const coin = selectedCoin;

    client
      .setup(coin)
      .then((data) => {
        if (!cancelled) setSetup({ data, error: null });
      })
      .catch((err: unknown) => {
        if (!cancelled) setSetup({ data: null, error: errorMessage(err) });
      });
    client
      .tradeplan(coin)
      .then((data) => {
        if (!cancelled) setPlan({ data, error: null });
      })
      .catch((err: unknown) => {
        if (!cancelled) setPlan({ data: null, error: errorMessage(err) });
      });
    client
      .metrics(coin)
      .then((data) => {
        if (!cancelled) setMetrics({ data, error: null });
      })
      .catch((err: unknown) => {
        if (!cancelled) setMetrics({ data: null, error: errorMessage(err) });
      });
    client
      .bars(coin, barTimeframe)
      .then((data) => {
        if (!cancelled) setBars({ data, error: null });
      })
      .catch((err: unknown) => {
        if (!cancelled) setBars({ data: null, error: errorMessage(err) });
      });

    return () => {
      cancelled = true;
    };
  }, [client, selectedCoin, barTimeframe]);

  // The empty-bars object is memoized on coin + timeframe: an inline
  // `emptyBars(...)` literal would be a NEW object identity every render,
  // and CandleChart's effect depends on `bars`, so each in-flight fetch
  // would tear down and recreate the chart. Same key as the effect above.
  const placeholderBars = useMemo(
    () => emptyBars(selectedCoin ?? "", barTimeframe),
    [selectedCoin, barTimeframe],
  );

  if (selectedCoin === null) {
    return (
      <section className="rounded-xl border border-border bg-card p-4">
        <h2 className="text-sm font-semibold text-fg">Coin Detail</h2>
        <p className="mt-2 text-xs text-muted">select a coin from the board</p>
      </section>
    );
  }

  const name = setup.data?.name ?? plan.data?.name ?? selectedCoin;
  const price = setup.data?.price ?? null;
  const change24h = setup.data?.change_24h_pct ?? null;

  const noSetup = setup.data === null && setup.error !== null;

  return (
    <section className="rounded-xl border border-border bg-card p-4">
      {/* header */}
      <div className="flex flex-wrap items-center gap-3">
        <LetterBadge symbol={selectedCoin} size={32} />
        <h2 className="text-sm font-semibold text-fg">
          {selectedCoin} - {name}
        </h2>
        <span className="text-sm font-medium text-fg">
          {price === null ? "price unavailable" : formatLevel(price)}
        </span>
        {change24h === null ? (
          <span className="text-xs text-muted">24h unavailable</span>
        ) : (
          <span
            className={
              change24h >= 0
                ? "text-xs font-medium text-long"
                : "text-xs font-medium text-short"
            }
          >
            {signedPct(change24h)}
          </span>
        )}
        {setup.data ? <SignalPill direction={setup.data.direction} /> : null}
        {metrics.data !== null ? (
          <span className="text-xs text-muted">
            RSI 14 {formatIndicator(metrics.data.rsi)}
          </span>
        ) : null}
        {plan.data !== null ? (
          <span className="text-xs text-muted">
            {plan.data.reference_size_note}
          </span>
        ) : null}
      </div>

      {/* timeframe tabs — 1m deliberately absent */}
      <div className="mt-3 flex flex-wrap gap-1" role="tablist" aria-label="bar timeframe">
        {TIMEFRAME_OPTIONS.map((tf) => (
          <button
            key={tf}
            type="button"
            role="tab"
            aria-selected={tf === barTimeframe}
            onClick={() => setBarTimeframe(tf)}
            className={
              tf === barTimeframe
                ? "rounded-lg border border-border-strong bg-bg-elevated px-2 py-0.5 text-xs font-semibold text-fg"
                : "rounded-lg border border-border px-2 py-0.5 text-xs text-muted"
            }
          >
            {tf}
          </button>
        ))}
      </div>

      {/* chart or per-tab bars failure */}
      {bars.error !== null ? (
        <div className="mt-3 rounded-xl border border-border bg-bg-elevated px-4 py-3 text-xs text-muted">
          bars unavailable — {bars.error}
        </div>
      ) : (
        <div className="mt-3">
          <CandleChart bars={bars.data ?? placeholderBars} />
        </div>
      )}

      {/* content tabs */}
      <div className="mt-3 flex gap-1" role="tablist" aria-label="detail section">
        {TAB_OPTIONS.map((t) => (
          <button
            key={t}
            type="button"
            role="tab"
            aria-selected={t === tab}
            onClick={() => setTab(t)}
            className={
              t === tab
                ? "rounded-lg border border-border-strong bg-bg-elevated px-2 py-0.5 text-xs font-semibold text-fg"
                : "rounded-lg border border-border px-2 py-0.5 text-xs text-muted"
            }
          >
            {TAB_LABEL[t]}
          </button>
        ))}
      </div>

      <div className="mt-3">
        {noSetup ? (
          <div className="rounded-lg border border-border bg-bg-elevated px-3 py-2 text-xs text-muted">
            <p className="font-medium text-fg">no setup yet</p>
            <p className="mt-1">
              {blockingReason ?? "no rule criteria met on the last sweep"}
            </p>
          </div>
        ) : tab === "analysis" ? (
          <AnalysisPane setup={setup.data} error={setup.error} />
        ) : tab === "tradeplan" ? (
          <TradePlanPane plan={plan.data} error={plan.error} />
        ) : (
          <MetricsPane metrics={metrics.data} error={metrics.error} />
        )}
      </div>
    </section>
  );
}

function AnalysisPane({ setup, error }: { setup: Setup | null; error: string | null }) {
  if (setup === null) {
    return (
      <p className="text-xs text-muted">
        {error === null ? "loading…" : `analysis unavailable — ${error}`}
      </p>
    );
  }
  return (
    <div>
      <p className="text-xs font-medium uppercase tracking-wide text-muted">
        {setup.rule}
      </p>
      <ul className="mt-2 space-y-1">
        {setup.analysis.map((line) => (
          <li key={line} className="text-xs text-fg">
            {line}
          </li>
        ))}
      </ul>
      <ul className="mt-2 space-y-0.5">
        {setup.criteria.map((c) => (
          <li
            key={c.name}
            className={
              c.passed ? "text-xs text-long" : "text-xs text-muted"
            }
          >
            {c.passed ? "✓" : "✗"} {c.name}
          </li>
        ))}
      </ul>
    </div>
  );
}

function TradePlanPane({ plan, error }: { plan: TradePlan | null; error: string | null }) {
  if (plan === null) {
    return (
      <p className="text-xs text-muted">
        {error === null ? "loading…" : `trade plan unavailable — ${error}`}
      </p>
    );
  }
  return (
    <div data-testid="tradeplan-pane">
      <ul className="space-y-1">
        {plan.analysis.map((line) => (
          <li key={line} className="text-xs text-fg">
            {line}
          </li>
        ))}
      </ul>
      <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
        <span className="text-muted">Entry Zone</span>
        <span className="text-fg">
          {formatLevel(plan.entry_zone.low)} – {formatLevel(plan.entry_zone.high)}
        </span>
        <span className="text-muted">Stop</span>
        <span className="text-fg">{formatLevel(plan.stop)}</span>
        <span className="text-muted">TP1</span>
        <span className="text-fg">{formatLevel(plan.tp1)}</span>
        <span className="text-muted">TP2</span>
        <span className="text-fg">{formatLevel(plan.tp2)}</span>
      </div>
      <div className="mt-3">
        <p className="text-xs font-medium uppercase tracking-wide text-muted">
          Key Indicators
        </p>
        <ul className="mt-1 space-y-0.5">
          {plan.indicators.map((ind) => (
            <li key={ind.name} className="flex justify-between text-xs">
              <span className="text-muted">{ind.name}</span>
              <span className="text-fg">{formatIndicator(ind.value)}</span>
            </li>
          ))}
        </ul>
      </div>
      <p className="mt-3 text-xs text-muted">
        {plan.reference_size_note}
      </p>
      <div className="mt-2 flex flex-wrap gap-4 text-xs text-muted">
        <span>Signal valid for {formatUtc(plan.valid_until_ts)}</span>
        <span>Next update in {formatUtc(plan.next_update_ts)}</span>
      </div>
    </div>
  );
}

function MetricsPane({ metrics, error }: { metrics: Metrics | null; error: string | null }) {
  if (metrics === null) {
    return (
      <p className="text-xs text-muted">
        {error === null ? "loading…" : `metrics unavailable — ${error}`}
      </p>
    );
  }
  const rows: Array<[string, number | null]> = [
    ["RSI 14", metrics.rsi],
    ["MACD", metrics.macd],
    ["EMA 50", metrics.ema50],
    ["EMA 200", metrics.ema200],
    ["Volume +%", metrics.volume_pct],
  ];
  return (
    <ul className="space-y-0.5">
      {rows.map(([label, value]) => (
        <li key={label} className="flex justify-between text-xs">
          <span className="text-muted">{label}</span>
          <span className="text-fg">{formatIndicator(value)}</span>
        </li>
      ))}
    </ul>
  );
}
