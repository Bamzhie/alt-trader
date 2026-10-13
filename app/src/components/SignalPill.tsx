// SignalPill — LONG/SHORT direction pill. long = long token (green),
// short = short token (red). Pure render; colors from theme tokens only.
import type { Direction } from "../api/types";

export interface SignalPillProps {
  direction: Direction;
}

export function SignalPill({ direction }: SignalPillProps) {
  const isLong = direction === "LONG";
  return (
    <span
      className={
        isLong
          ? "inline-flex items-center rounded-full border border-long px-2 py-0.5 text-xs font-semibold text-long"
          : "inline-flex items-center rounded-full border border-short px-2 py-0.5 text-xs font-semibold text-short"
      }
    >
      {direction}
    </span>
  );
}
