// CandleChart — closed-bar candlesticks and a volume histogram, mounted
// on a div ref via Lightweight Charts (the ONLY charting library in
// this app). It renders candles + volume ONLY: the frontend never
// computes indicators, so no EMA (or any other) overlay is drawn here —
// the chart values must come from the service, not from bar closes.
//
// House rules:
//   * CLOSED bars only — every bar is filtered on `closed` before it is
//     drawn; an open bar is never rendered (close-time honesty).
//   * Empty bars → an empty frame. No fabricated candles, no retry
//     spinner, no placeholder series.
//   * Colors come from the CSS custom properties resolved by the caller
//     (getComputedStyle at mount) — never raw hexes in this module.
//   * jsdom has no canvas: mount is wrapped so a chart-less environment
//     renders the empty frame instead of throwing.
import { useEffect, useRef } from "react";
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Bars } from "../api/types";

export interface CandleChartProps {
  /** Closed bars in chronological order. */
  bars: Bars;
}

/** Resolves a CSS custom property (theme token) to a literal value. */
function tokenValue(name: string, fallback: string): string {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return fallback;
  }
  const probe = document.createElement("div");
  probe.style.color = `var(${name})`;
  document.body.appendChild(probe);
  const value = getComputedStyle(probe).color || fallback;
  probe.remove();
  // jsdom returns the raw var() expression instead of resolving it — a
  // var() string is not a parseable color for the chart lib, so fall back.
  return value.startsWith("var(") ? fallback : value;
}

export function CandleChart({ bars }: CandleChartProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick">[]>([]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    // jsdom has no canvas (getContext returns null, and matchMedia is
    // absent) — in that environment the chart cannot paint anything, so
    // the empty frame stays and no chart DOM is created at all.
    const canvasProbe = document.createElement("canvas");
    const canvasOk = canvasProbe.getContext("2d") !== null;
    const matchMediaOk =
      typeof window.matchMedia === "function";
    if (!canvasOk || !matchMediaOk) return;

    let chart: IChartApi;
    try {
      chart = createChart(container, {
        layout: {
          background: { color: "transparent" },
          textColor: tokenValue("--muted", "#8a94ab"),
        },
        grid: {
          vertLines: { color: tokenValue("--chart-grid", "#1e2637") },
          horzLines: { color: tokenValue("--chart-grid", "#1e2637") },
        },
        rightPriceScale: { borderColor: tokenValue("--border", "#232c42") },
        timeScale: { borderColor: tokenValue("--border", "#232c42") },
        autoSize: true,
      });
    } catch {
      // No canvas (jsdom) — the empty frame stays; nothing is fabricated.
      return;
    }
    chartRef.current = chart;

    const closed = bars.bars.filter((b) => b.closed);

    if (closed.length === 0) {
      // Empty bars → empty frame: no series are created at all.
      return () => {
        chart.remove();
        chartRef.current = null;
        seriesRef.current = [];
      };
    }

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: tokenValue("--long", "#2fbf71"),
      downColor: tokenValue("--short", "#e5484d"),
      borderUpColor: tokenValue("--long", "#2fbf71"),
      borderDownColor: tokenValue("--short", "#e5484d"),
      wickUpColor: tokenValue("--long", "#2fbf71"),
      wickDownColor: tokenValue("--short", "#e5484d"),
    });
    candleSeries.setData(
      closed.map((b) => ({
        time: b.ts as UTCTimestamp,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      })),
    );

    // Volume histogram on its own overlay scale.
    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceScaleId: "volume",
      priceFormat: { type: "volume" },
      priceLineVisible: false,
      lastValueVisible: false,
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.75, bottom: 0 },
    });
    volumeSeries.setData(
      closed.map((b) => ({
        time: b.ts as UTCTimestamp,
        value: b.volume,
        color:
          b.close >= b.open
            ? tokenValue("--long", "#2fbf71")
            : tokenValue("--short", "#e5484d"),
      })),
    );

    seriesRef.current = [candleSeries];

    return () => {
      chart.remove();
      chartRef.current = null;
      seriesRef.current = [];
    };
  }, [bars]);

  return (
    <div
      ref={containerRef}
      data-testid="candle-chart"
      aria-label={bars.bars.length > 0 ? "price chart" : "no chart data"}
      className="h-64 w-full rounded-xl border border-border bg-bg-elevated"
    />
  );
}
