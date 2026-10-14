// EvidenceBar — criteria chips + indicator input chips. This component
// REPLACES the old confidence bar: there is no numeric confidence,
// probability or tier anywhere in it or in its output.
//
// House rules:
//   * passed criteria use the long token (green); failed criteria are
//     muted;
//   * a null input renders the literal word "unavailable" — never 0,
//     never an inferred or carry-over value.
import type { SetupCriterion } from "../api/types";

export interface EvidenceBarProps {
  criteria: SetupCriterion[];
  inputs: Record<string, number | null>;
}

/** Display-only number formatting; never invents precision. */
function formatInput(v: number): string {
  return String(Math.round(v * 1e4) / 1e4);
}

export function EvidenceBar({ criteria, inputs }: EvidenceBarProps) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      {criteria.map((c) => (
        <span
          key={c.name}
          className={
            c.passed
              ? "rounded-full border border-long px-2 py-0.5 text-xs font-medium text-long"
              : "rounded-full border border-border px-2 py-0.5 text-xs font-medium text-muted"
          }
        >
          {c.name}
        </span>
      ))}
      {Object.entries(inputs).map(([name, value]) => (
        <span
          key={name}
          className={
            value === null
              ? "inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-xs text-muted"
              : "inline-flex items-center gap-1 rounded-full border border-border px-2 py-0.5 text-xs text-fg"
          }
        >
          <span className="font-medium">{name}</span>
          <span>{value === null ? "unavailable" : formatInput(value)}</span>
        </span>
      ))}
    </div>
  );
}
