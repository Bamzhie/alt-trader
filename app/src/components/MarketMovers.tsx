// MarketMovers — the Gainers / Losers / High Volume panel of the bottom row.
//
// House rules:
//   * The active sub-tab lives in the Zustand store (moversTab) and is
//     written only through setMoversTab — this component fetches nothing.
//   * Gainers are the positive change_24h_pct readings, worst-first
//     excluded; losers are the negative ones, biggest loss first. A 0.00%
//     reading is neither a gainer nor a loser, so it appears on neither
//     tab rather than being rounded into one.
//   * The HIGH VOLUME tab is the honest exception: the /snapshot contract
//     carries no quote-volume measure on `movers` (Mover is
//     coin/name/change_24h_pct only), so the tab says the ranking is
//     unavailable instead of inventing an order from the 24h % — a
//     fabricated ranking would be worse than no ranking.
//   * Row click = selection only (selectCoin) — never backend state and
//     never a POST.
//   * The 24h % renders signed with two decimals; a true 0 stays muted.
//   * Colors come from theme tokens only — never raw hexes.
//   * Pure render over the snapshot's `movers` rows: no fetch inside.
import type { Mover } from "../api/types";
import { LetterBadge } from "./LetterBadge";
import { useDashboardStore } from "../store/dashboard";
import type { MoversTab } from "../store/dashboard";

export interface MarketMoversProps {
  /** Snapshot movers rows (the contract's own gainers/losers feed). */
  movers: Mover[];
  /** Active sub-tab, as held by the store. */
  tab: MoversTab;
}

/** Sub-tab labels, in display order. */
const TABS: { key: MoversTab; text: string }[] = [
  { key: "gainers", text: "Gainers" },
  { key: "losers", text: "Losers" },
  { key: "volume", text: "High Volume" },
];

/** Signed 24h change with two decimals — never a bare unsigned number. */
function signedPct(pct: number): string {
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

/** Rows for the active tab, already ordered. Never mutates the input. */
function rowsFor(movers: Mover[], tab: MoversTab): Mover[] {
  if (tab === "gainers") {
    return movers
      .filter((m) => m.change_24h_pct > 0)
      .sort((a, b) => b.change_24h_pct - a.change_24h_pct);
  }
  if (tab === "losers") {
    return movers
      .filter((m) => m.change_24h_pct < 0)
      .sort((a, b) => a.change_24h_pct - b.change_24h_pct);
  }
  // "volume" is handled by the caller: there is no volume measure to
  // rank by, so no rows are produced for it.
  return [];
}

export function MarketMovers({ movers, tab }: MarketMoversProps) {
  const setMoversTab = useDashboardStore((s) => s.setMoversTab);
  const selectCoin = useDashboardStore((s) => s.selectCoin);

  // The active tab is the prop the caller renders from the store's
  // moversTab; a click writes the store, which re-renders the page with
  // the new value. Nothing here reads or writes anything else.
  const active = tab;

  return (
    <section className="rounded-xl border border-border bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-fg">Market Movers</h2>
        <div className="flex rounded-lg border border-border bg-bg-elevated p-0.5">
          {TABS.map((t) => {
            const isActive = t.key === active;
            return (
              <button
                key={t.key}
                type="button"
                aria-pressed={isActive}
                onClick={() => setMoversTab(t.key)}
                className={
                  isActive
                    ? "rounded-md bg-card px-2 py-0.5 text-xs font-semibold text-accent"
                    : "rounded-md px-2 py-0.5 text-xs text-muted hover:text-fg"
                }
              >
                {t.text}
              </button>
            );
          })}
        </div>
      </div>

      {active === "volume" ? (
        // Honest unavailability: the contract exposes no volume ranking,
        // so the tab says so rather than ranking by the 24h %.
        <p className="mt-3 text-xs text-muted">
          volume ranking unavailable — the snapshot carries no quote-volume measure
        </p>
      ) : (
        <ul className="mt-3 space-y-1">
          {rowsFor(movers, active).length === 0 ? (
            <li className="px-1 py-2 text-xs text-muted">
              no {active === "gainers" ? "gainers" : "losers"} on this sweep
            </li>
          ) : (
            rowsFor(movers, active).map((mover) => {
              const up = mover.change_24h_pct >= 0;
              return (
                <li key={mover.coin}>
                  <button
                    type="button"
                    onClick={() => selectCoin(mover.coin)}
                    className="flex w-full items-center gap-3 rounded-lg px-1 py-1.5 text-left hover:bg-card-hover"
                  >
                    <LetterBadge symbol={mover.coin} size={24} />
                    <span className="text-sm font-semibold text-fg">
                      {mover.coin}
                    </span>
                    <span className="truncate text-xs text-muted">{mover.name}</span>
                    <span
                      className={
                        mover.change_24h_pct === 0
                          ? "ml-auto text-xs font-medium tabular-nums text-muted"
                          : up
                            ? "ml-auto text-xs font-medium tabular-nums text-long"
                            : "ml-auto text-xs font-medium tabular-nums text-short"
                      }
                    >
                      {signedPct(mover.change_24h_pct)}
                    </span>
                  </button>
                </li>
              );
            })
          )}
        </ul>
      )}
    </section>
  );
}
