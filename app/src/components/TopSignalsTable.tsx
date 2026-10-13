// TopSignalsTable — one row per active setup: coin, price, 24h %, trend
// sparkline (closed 5m bars), 2-line analysis, evidence chips, direction
// pill, and expiry countdown.
//
// House rules:
//   * Row click = selection only (selectCoin) — never backend state, never
//     a POST. Keyboard Enter/Space does the same for the focused row.
//   * The sparkline is drawn ONLY from closed 5m bars handed in by the
//     caller; no bars → the empty Sparkline frame, never fabricated data.
//   * A null indicator input renders "unavailable" through EvidenceBar —
//     never 0.
//   * Deltas and the 24h % carry explicit signs, so a neutral value never
//     renders as a bare green number.
//   * Colors come from theme tokens only — never raw hexes.
import { EvidenceBar } from "./EvidenceBar";
import { LetterBadge } from "./LetterBadge";
import { SignalPill } from "./SignalPill";
import { Sparkline } from "./Sparkline";
import type { Setup } from "../api/types";
import { useDashboardStore } from "../store/dashboard";

export interface TopSignalsTableProps {
  /** Already filtered + sorted active setups. */
  setups: Setup[];
  /** Closed 5m closes per coin, for the trend sparkline. */
  barsByCoin: Record<string, number[]>;
  /** UTC-seconds timestamp the countdowns are measured against. */
  dataAsOf: number;
}

/** Display labels for setup inputs, matching the trade-plan indicator names. */
const INPUT_LABELS: Record<string, string> = {
  rsi: "RSI 14",
  macd: "MACD",
  ema50: "EMA 50",
  ema200: "EMA 200",
  volume_pct: "Volume +%",
};

function labelFor(key: string): string {
  return INPUT_LABELS[key] ?? key;
}

/** Thousands separators, precision by magnitude — never invented digits. */
function formatPrice(p: number): string {
  if (p >= 1000) return Math.round(p).toLocaleString("en-US");
  if (p >= 1) return p.toFixed(2);
  return p.toFixed(4);
}

/** 24h change with an explicit sign; a true 0 stays neutral (muted). */
function formatChange(v: number): { text: string; cls: string } {
  if (v === 0) return { text: "0.00%", cls: "text-muted" };
  const sign = v > 0 ? "+" : "";
  return {
    text: `${sign}${v.toFixed(2)}%`,
    cls: v > 0 ? "text-long" : "text-short",
  };
}

/** Expiry countdown measured against the snapshot's data_as_of timestamp. */
function formatCountdown(expiresTs: number, nowTs: number): string {
  const secs = expiresTs - nowTs;
  if (secs <= 0) return "expired";
  const days = Math.floor(secs / 86400);
  const hours = Math.floor((secs % 86400) / 3600);
  const minutes = Math.floor((secs % 3600) / 60);
  if (days > 0) return `in ${days}d ${hours}h`;
  if (hours > 0) return `in ${hours}h ${minutes}m`;
  return `in ${minutes}m`;
}

export function TopSignalsTable({
  setups,
  barsByCoin,
  dataAsOf,
}: TopSignalsTableProps) {
  const selectedCoin = useDashboardStore((s) => s.selectedCoin);
  const selectCoin = useDashboardStore((s) => s.selectCoin);

  return (
    <div className="overflow-x-auto rounded-xl border border-border bg-card">
      <table className="w-full min-w-[900px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-border text-left text-xs font-medium uppercase tracking-wide text-muted">
            <th className="px-4 py-2">Coin</th>
            <th className="px-4 py-2">Price</th>
            <th className="px-4 py-2">24h %</th>
            <th className="px-4 py-2">Trend</th>
            <th className="px-4 py-2">Analysis</th>
            <th className="px-4 py-2">Evidence</th>
            <th className="px-4 py-2">Signal</th>
            <th className="px-4 py-2">Expires</th>
          </tr>
        </thead>
        <tbody>
          {setups.length === 0 ? (
            <tr>
              <td colSpan={8} className="px-4 py-10 text-center text-sm text-muted">
                silence is valid — no active setups on this filter
              </td>
            </tr>
          ) : (
            setups.map((setup) => {
              const change = formatChange(setup.change_24h_pct);
              const values = barsByCoin[setup.coin] ?? [];
              const up = values.length >= 2
                ? values[values.length - 1] >= values[0]
                : true;
              const analysisLines = setup.analysis.slice(0, 2);
              const inputs: Record<string, number | null> = {};
              for (const [key, value] of Object.entries(setup.inputs)) {
                inputs[labelFor(key)] = value;
              }
              const selected = selectedCoin === setup.coin;
              return (
                <tr
                  key={setup.id}
                  tabIndex={0}
                  aria-selected={selected}
                  onClick={() => selectCoin(setup.coin)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      selectCoin(setup.coin);
                    }
                  }}
                  className={
                    selected
                      ? "cursor-pointer border-b border-border bg-bg-elevated"
                      : "cursor-pointer border-b border-border hover:bg-card-hover"
                  }
                >
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <LetterBadge symbol={setup.coin} size={28} />
                      <div className="leading-tight">
                        <div className="font-semibold text-fg">{setup.coin}</div>
                        <div className="text-xs text-muted">
                          {setup.name ?? setup.coin}
                        </div>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-3 tabular-nums text-fg">
                    {formatPrice(setup.price)}
                  </td>
                  <td className={`px-4 py-3 tabular-nums ${change.cls}`}>
                    {change.text}
                  </td>
                  <td className="px-4 py-3">
                    <Sparkline values={values} up={up} />
                  </td>
                  <td className="max-w-[280px] px-4 py-3 text-xs text-muted">
                    {analysisLines.length === 0 ? (
                      <span>no analysis lines</span>
                    ) : (
                      analysisLines.map((line) => (
                        <p key={line} className="line-clamp-2">
                          {line}
                        </p>
                      ))
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <EvidenceBar criteria={setup.criteria} inputs={inputs} />
                  </td>
                  <td className="px-4 py-3">
                    <SignalPill direction={setup.direction} />
                  </td>
                  <td className="px-4 py-3 text-xs text-muted">
                    {formatCountdown(setup.expires_ts, dataAsOf)}
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
}
