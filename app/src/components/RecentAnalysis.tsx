// RecentAnalysis — the last five setup transitions of the bottom row.
//
// House rules:
//   * Exactly five entries are shown, newest first. A sixth entry in the
//     feed is dropped by the cap, never silently squashed into the list.
//   * "Time ago" is measured against the snapshot's data_as_of (the
//     store's dataAsOf), NEVER the wall clock: a relative stamp that
//     drifts with the render time would misdate the data. With no
//     reference stamp yet, the row renders "unavailable" — never "0m ago".
//   * The direction rides along as the same LONG/SHORT pill the signals
//     table uses; no score, no tier, no probability anywhere.
//   * Row click = selection only (selectCoin) — never backend state and
//     never a POST.
//   * Empty feed → an honest empty state; a coin with no entries is
//     simply absent, never a placeholder row.
//   * Colors come from theme tokens only — never raw hexes.
//   * Pure render over the snapshot's `recent_analysis` rows: no fetch
//     inside this component.
import type { RecentAnalysisEntry } from "../api/types";
import { SignalPill } from "./SignalPill";
import { useDashboardStore } from "../store/dashboard";

export interface RecentAnalysisProps {
  /** Snapshot recent_analysis rows, already newest-first from the feed. */
  entries: RecentAnalysisEntry[];
}

/** How many transitions the panel shows. */
const SHOWN = 5;

/** Compact relative stamp, e.g. "30m ago", "2h ago", "3d ago". */
function timeAgo(ts: number, nowTs: number | null): string {
  if (nowTs === null) return "unavailable";
  const secs = nowTs - ts;
  if (secs < 0) return "unavailable";
  const minutes = Math.floor(secs / 60);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export function RecentAnalysis({ entries }: RecentAnalysisProps) {
  const selectCoin = useDashboardStore((s) => s.selectCoin);
  const dataAsOf = useDashboardStore((s) => s.dataAsOf);

  // Newest first regardless of the feed's own order, then capped: the
  // snapshot's rows are already newest-first, but the sort makes the
  // panel's contract independent of the service's ordering choice.
  const shown = [...entries]
    .sort((a, b) => b.ts - a.ts)
    .slice(0, SHOWN);

  return (
    <section className="rounded-xl border border-border bg-card p-4">
      <h2 className="text-sm font-semibold text-fg">Recent Analysis</h2>
      {shown.length === 0 ? (
        <p className="mt-3 text-xs text-muted">no transitions yet</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {shown.map((entry) => (
            <li key={`${entry.ts}-${entry.coin}`}>
              <button
                type="button"
                onClick={() => selectCoin(entry.coin)}
                className="w-full rounded-lg px-1 py-1 text-left hover:bg-card-hover"
              >
                <div className="flex items-baseline gap-2">
                  <span className="shrink-0 text-xs tabular-nums text-muted">
                    {timeAgo(entry.ts, dataAsOf)}
                  </span>
                  <span className="text-sm font-semibold text-fg">
                    {entry.coin}
                  </span>
                  <span className="ml-auto shrink-0">
                    <SignalPill direction={entry.direction} />
                  </span>
                </div>
                <p className="mt-0.5 line-clamp-2 text-xs text-muted">
                  {entry.text}
                </p>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
