// StatCard — label / value / optional delta / optional footnote.
//
// House rules:
//   * a delta with available === false renders the literal word
//     "unavailable" — never an inferred or carry-over number;
//   * available === true with a null value string also renders
//     "unavailable" (the flag is authoritative, the string is not).
export interface StatCardDelta {
  /** Pre-formatted delta string, e.g. "+3" — null when unavailable. */
  value: string | null;
  /** true only when a covering prior snapshot exists. */
  available: boolean;
}

export interface StatCardProps {
  label: string;
  value: string;
  delta?: StatCardDelta | null;
  footnote?: string;
}

export function StatCard({ label, value, delta, footnote }: StatCardProps) {
  // available === false always shows "unavailable", even if a stale value
  // string rode along.
  const deltaText = delta?.available === true ? delta.value : null;
  const deltaClass =
    deltaText !== null && deltaText.startsWith("-")
      ? "text-short"
      : "text-long";

  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="text-xs font-medium uppercase tracking-wide text-muted">
        {label}
      </div>
      <div className="mt-1 text-2xl font-semibold text-fg">{value}</div>
      {delta ? (
        <div className="mt-1 text-xs font-medium">
          {deltaText !== null ? (
            <span className={deltaClass}>{deltaText}</span>
          ) : (
            <span className="text-muted">unavailable</span>
          )}
        </div>
      ) : null}
      {footnote ? (
        <div className="mt-1 text-xs text-muted">{footnote}</div>
      ) : null}
    </div>
  );
}
