// TopBar — wordmark and venue subtitle.
//
// House rules:
//   * The legacy "SCANNING" pill and live UTC clock were part of the
//     scanner UI and have been removed; the bar collector runs headlessly
//     in the systemd service.
//   * Colors come from theme tokens only — never raw hexes.
import { useEffect, useState } from "react";

/** Formats a Date as UTC HH:MM:SS. */
function formatUtcClock(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}:${pad(
    date.getUTCSeconds(),
  )} UTC`;
}

export function TopBar() {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  return (
    <header className="flex items-center justify-between border-b border-border bg-bg-elevated px-6 py-3">
      <div className="flex items-baseline gap-3">
        <h1 className="text-lg font-semibold tracking-wide text-fg">
          ALT RADAR
        </h1>
        <span className="text-xs text-muted">
          MEXC Perpetual Futures data collection
        </span>
      </div>
      <time
        dateTime={now.toISOString()}
        aria-label="Current UTC time"
        className="text-sm tabular-nums text-muted"
      >
        {formatUtcClock(now)}
      </time>
    </header>
  );
}
