// FilterBar — direction counts, market segment, horizon, and sort controls
// for the Top Signals table.
//
// House rules:
//   * All state lives in the Zustand store (Task 5) — this component reads
//     and writes it, never fetching.
//   * Counts come from the market+horizon-filtered set, so the Long/Short
//     numbers describe exactly what a direction click would show.
//   * Horizon default is 4h (store default); 4h and 24h only.
//   * Colors come from theme tokens only — never raw hexes.
import { useDashboardStore } from "../store/dashboard";
import type {
  DirectionFilter,
  HorizonFilter,
  MarketFilter,
  SortKey,
} from "../store/dashboard";

export interface FilterBarProps {
  /** Counts of the market+horizon-filtered set, before direction narrowing. */
  counts: { all: number; long: number; short: number };
}

/** Segmented control — one button per option; active uses the accent token. */
function Segment<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: { key: T; text: string }[];
  value: T;
  onChange(next: T): void;
}) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-xs font-medium uppercase tracking-wide text-muted">
        {label}
      </span>
      <div className="flex rounded-lg border border-border bg-bg-elevated p-0.5">
        {options.map((opt) => {
          const active = opt.key === value;
          return (
            <button
              key={opt.key}
              type="button"
              aria-pressed={active}
              onClick={() => onChange(opt.key)}
              className={
                active
                  ? "rounded-md bg-card px-2.5 py-1 text-xs font-semibold text-accent"
                  : "rounded-md px-2.5 py-1 text-xs text-muted hover:text-fg"
              }
            >
              {opt.text}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function FilterBar({ counts }: FilterBarProps) {
  const directionFilter = useDashboardStore((s) => s.directionFilter);
  const setDirectionFilter = useDashboardStore((s) => s.setDirectionFilter);
  const marketFilter = useDashboardStore((s) => s.marketFilter);
  const setMarketFilter = useDashboardStore((s) => s.setMarketFilter);
  const horizonFilter = useDashboardStore((s) => s.horizonFilter);
  const setHorizonFilter = useDashboardStore((s) => s.setHorizonFilter);
  const sort = useDashboardStore((s) => s.sort);
  const setSort = useDashboardStore((s) => s.setSort);

  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-3 rounded-xl border border-border bg-card px-4 py-3">
      <Segment<DirectionFilter>
        label="Direction"
        value={directionFilter}
        onChange={setDirectionFilter}
        options={[
          { key: "all", text: `All (${counts.all})` },
          { key: "long", text: `Long (${counts.long})` },
          { key: "short", text: `Short (${counts.short})` },
        ]}
      />
      <Segment<MarketFilter>
        label="Market"
        value={marketFilter}
        onChange={setMarketFilter}
        options={[
          { key: "all", text: "All" },
          { key: "top20", text: "Top 20" },
          { key: "tail", text: "Tail" },
        ]}
      />
      <Segment<HorizonFilter>
        label="Horizon"
        value={horizonFilter}
        onChange={setHorizonFilter}
        options={[
          { key: "4h", text: "4h" },
          { key: "24h", text: "24h" },
        ]}
      />
      <Segment<SortKey>
        label="Sort"
        value={sort}
        onChange={setSort}
        options={[
          { key: "newest", text: "Newest" },
          { key: "expiry", text: "Expiry" },
          { key: "change24h", text: "24h %" },
        ]}
      />
    </div>
  );
}
