// TopBar — wordmark, venue subtitle, SCANNING pill, live UTC clock, and
// two labelled placeholder icons.
//
// House rules:
//   * The clock is UTC HH:MM:SS, ticking once per second via setInterval;
//     the interval is cleared on unmount.
//   * Bell and gear are non-functional placeholders: aria-labelled buttons
//     with no navigation side effect.
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
          MEXC Perpetual Futures Scanner
        </span>
      </div>
      <div className="flex items-center gap-4">
        <span className="rounded-full border border-accent px-2 py-0.5 text-xs font-semibold text-accent">
          SCANNING
        </span>
        <time
          dateTime={now.toISOString()}
          aria-label="Current UTC time"
          className="text-sm tabular-nums text-muted"
        >
          {formatUtcClock(now)}
        </time>
        <div className="flex items-center gap-2">
          <button
            type="button"
            aria-label="Notifications"
            className="rounded-lg border border-border px-2 py-1 text-xs text-muted hover:bg-card-hover"
          >
            {/* bell placeholder */}
            <svg
              aria-hidden="true"
              width="16"
              height="16"
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            >
              <path d="M4 7a4 4 0 0 1 8 0c0 3 1 4 1 4H3s1-1 1-4Z" />
              <path d="M6.5 13.5a1.8 1.8 0 0 0 3 0" />
            </svg>
          </button>
          <button
            type="button"
            aria-label="Preferences"
            className="rounded-lg border border-border px-2 py-1 text-xs text-muted hover:bg-card-hover"
          >
            {/* gear placeholder */}
            <svg
              aria-hidden="true"
              width="16"
              height="16"
              viewBox="0 0 16 16"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            >
              <circle cx="8" cy="8" r="2.5" />
              <path d="M8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M12.6 3.4l-1.4 1.4M4.8 11.2l-1.4 1.4" />
            </svg>
          </button>
        </div>
      </div>
    </header>
  );
}
