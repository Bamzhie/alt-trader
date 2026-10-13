// ActivityLog — the structured journal of the bottom row.
//
// House rules:
//   * Newest first, and a hard cap on what is displayed: the feed can
//     carry far more entries than the panel has room for, so the oldest
//     are dropped and the cap is DISCLOSED ("showing 8 of 10") — a silent
//     truncation would read as "nothing else happened".
//   * Each row carries its journal kind (emission / invalidation /
//     expiry / sweep-complete) as a label, so the reader can tell a
//     sweep summary from a per-coin transition at a glance.
//   * "Time ago" is measured against the snapshot's data_as_of (the
//     store's dataAsOf), NEVER the wall clock; with no reference stamp
//     yet the row renders "unavailable" — never "0m ago".
//   * Empty journal → "no activity yet", the literal wording the brief
//     pins. A coin-bearing row is clickable (selection only, never a
//     POST); a sweep-complete row has no coin and is plain text.
//   * Colors come from theme tokens only — never raw hexes.
//   * Pure render over the snapshot's `activity` rows: no fetch inside.
import type { ActivityEntry } from "../api/types";
import { useDashboardStore } from "../store/dashboard";

export interface ActivityLogProps {
  /** Snapshot activity rows (the structured journal). */
  entries: ActivityEntry[];
}

/** How many journal rows the panel displays at most. */
const SHOWN = 8;

/** Compact relative stamp, e.g. "10m ago", "2h ago", "3d ago". */
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

export function ActivityLog({ entries }: ActivityLogProps) {
  const selectCoin = useDashboardStore((s) => s.selectCoin);
  const dataAsOf = useDashboardStore((s) => s.dataAsOf);

  // Newest first regardless of the feed's own order, then capped. The
  // total is the feed's own length, so the disclosure line reports what
  // the snapshot actually carried.
  const ordered = [...entries].sort((a, b) => b.ts - a.ts);
  const shown = ordered.slice(0, SHOWN);
  const hidden = ordered.length - shown.length;

  return (
    <section className="rounded-xl border border-border bg-card p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold text-fg">Activity Log</h2>
        {hidden > 0 ? (
          <span className="text-xs text-muted">
            showing {shown.length} of {ordered.length}
          </span>
        ) : null}
      </div>
      {shown.length === 0 ? (
        <p className="mt-3 text-xs text-muted">no activity yet</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {shown.map((entry, index) => {
            const coin = entry.coin;
            return (
              <li
                key={`${entry.ts}-${entry.kind}-${coin ?? index}`}
                className="rounded-lg px-1 py-1"
              >
                <div className="flex items-baseline gap-2">
                  <span className="shrink-0 rounded-full border border-border bg-bg-elevated px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted">
                    {entry.kind}
                  </span>
                  <span className="shrink-0 text-xs tabular-nums text-muted">
                    {timeAgo(entry.ts, dataAsOf)}
                  </span>
                  {coin === null ? null : (
                    <button
                      type="button"
                      onClick={() => selectCoin(coin)}
                      className="text-sm font-semibold text-fg hover:text-accent"
                    >
                      {coin}
                    </button>
                  )}
                </div>
                <p className="mt-0.5 line-clamp-2 text-xs text-muted">
                  {entry.text}
                </p>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
