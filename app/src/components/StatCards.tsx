// StatCards — the four dashboard stat cards.
//
// House rules:
//   * A 24h delta is rendered ONLY from comparison_24h, and only when it is
//     available — never inferred from the nearest history row. When no
//     covering snapshot exists the New Setups 24h card renders the literal
//     word "unavailable"; the Active Setups card renders no delta row at
//     all rather than a second "unavailable" label or an inferred number.
//   * Deltas carry an explicit sign: a neutral 0 renders "+0" so the StatCard
//     (which colors by a leading "-") never paints a bare "0" green.
//   * Universe wording is "eligible" / "evaluated" — never "scanned".
//   * Pure render of the snapshot: no fetch, no network, no timers.
//   * Colors come from theme tokens only — never raw hexes.
import { StatCard } from "./StatCard";
import type { StatCardDelta } from "./StatCard";
import type { Snapshot } from "../api/types";

export interface StatCardsProps {
  snapshot: Snapshot;
}

/** Signed delta string with an explicit sign — never a bare number. */
function signedDelta(n: number): string {
  // A neutral 0 carries an explicit "+" so StatCard (which colors by a
  // leading "-") never paints a bare "0" green — the Task 6 hand-off.
  return n >= 0 ? `+${n}` : `${n}`;
}

export function StatCards({ snapshot }: StatCardsProps) {
  const cmp = snapshot.comparison_24h;

  // Active Setups: the delta rides along only when a covering snapshot
  // exists; otherwise no delta row is rendered at all (never inferred).
  const activeDelta: StatCardDelta | null =
    cmp.available && cmp.delta_active_setups !== null
      ? { value: signedDelta(cmp.delta_active_setups), available: true }
      : null;

  // New Setups 24h: the whole point of the card is the 24h window, so a
  // missing covering snapshot renders "unavailable" loudly.
  const newSetupsDelta: StatCardDelta = {
    value:
      cmp.available && cmp.delta_new_setups_24h !== null
        ? signedDelta(cmp.delta_new_setups_24h)
        : null,
    available: cmp.available && cmp.delta_new_setups_24h !== null,
  };

  const coverage =
    snapshot.monitoring_coverage === null
      ? "unavailable"
      : `${(snapshot.monitoring_coverage * 100).toFixed(1)}%`;

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <StatCard
        label="Active Setups"
        value={String(snapshot.active_setups)}
        delta={activeDelta}
      />
      <StatCard
        label="Eligible Perps"
        value={`${snapshot.evaluated_count} of ${snapshot.eligible_count}`}
        footnote="evaluated of eligible"
      />
      <StatCard
        label="Monitoring Coverage"
        value={coverage}
        footnote={snapshot.monitoring_coverage === null ? undefined : "of the last sweep"}
      />
      <StatCard
        label="New Setups 24h"
        value={String(snapshot.new_setups_24h)}
        delta={newSetupsDelta}
      />
    </div>
  );
}
