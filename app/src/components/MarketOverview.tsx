// MarketOverview — BTC + ETH rows only. There is NO TOTAL row: the
// contract exposes no portfolio total, so none is computed or rendered.
//
// House rules:
//   * Row click = selection only (store.selectCoin) — never backend
//     state and never a POST.
//   * A null price renders "unavailable" — never 0 or a carry-over.
//   * The 24h % is rendered signed with two decimals; a null reading is
//     never fabricated.
//   * Colors come from theme tokens only — never raw hexes.
//   * Pure render over the snapshot's `market_overview` rows: no fetch
//     inside this component — the Dashboard passes the rows in.
import type { MarketOverviewRow } from "../api/types";
import { LetterBadge } from "./LetterBadge";
import { Sparkline } from "./Sparkline";
import { useDashboardStore } from "../store/dashboard";

export interface MarketOverviewProps {
  /** Snapshot market_overview rows (BTC + ETH only, by contract). */
  overview: MarketOverviewRow[];
  /** Closed-bar closes per coin for the mini sparkline; missing = empty frame. */
  closesByCoin?: Record<string, number[]>;
}

/** Signed 24h change with two decimals — never a bare unsigned number. */
function signedPct(pct: number): string {
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

export function MarketOverview({ overview, closesByCoin }: MarketOverviewProps) {
  const selectCoin = useDashboardStore((s) => s.selectCoin);

  return (
    <section className="rounded-xl border border-border bg-card p-4">
      <h2 className="text-sm font-semibold text-fg">Market Overview</h2>
      <ul className="mt-3 space-y-2">
        {overview.map((row) => {
          const closes = closesByCoin?.[row.coin] ?? [];
          const up = row.change_24h_pct >= 0;
          return (
            <li key={row.coin}>
              <button
                type="button"
                onClick={() => selectCoin(row.coin)}
                className="flex w-full items-center gap-3 rounded-lg border border-border px-3 py-2 text-left hover:bg-card-hover"
              >
                <LetterBadge symbol={row.coin} size={28} />
                <div className="min-w-0 flex-1">
                  <div className="flex items-baseline gap-2">
                    <span className="text-sm font-semibold text-fg">{row.coin}</span>
                    <span className="truncate text-xs text-muted">{row.name}</span>
                  </div>
                  <div className="mt-0.5 flex items-center gap-2">
                    <span className="text-xs text-fg">
                      {row.price === null
                        ? "unavailable"
                        : row.price.toLocaleString("en-US", {
                            maximumFractionDigits: 2,
                          })}
                    </span>
                    <span className={up ? "text-xs font-medium text-long" : "text-xs font-medium text-short"}>
                      {signedPct(row.change_24h_pct)}
                    </span>
                  </div>
                </div>
                <Sparkline values={closes} up={up} />
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
