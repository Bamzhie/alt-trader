// Sidebar — six-page navigation plus the footer status block.
//
// House rules:
//   * Nav is plain buttons driven by state routing in App.tsx — no router
//     dependency, no <a href>.
//   * The active item is marked with aria-current="page" and painted with
//     the accent token; inactive items use muted. Colors are token classes
//     only — never raw hexes.
//   * Footer API status text mirrors the store's `connection` state:
//     "Starting…" / "Connected" / "Connection error".
import type { ConnectionState } from "../store/dashboard";

/** Page keys of the shell's state routing (defined by App.tsx). */
export type Page =
  | "dashboard"
  | "watchlist"
  | "signals"
  | "analysis"
  | "trades"
  | "settings";

/** Nav entries in display order. */
const NAV_ITEMS: { page: Page; label: string }[] = [
  { page: "dashboard", label: "Dashboard" },
  { page: "watchlist", label: "Watchlist" },
  { page: "signals", label: "Signals" },
  { page: "analysis", label: "Analysis" },
  { page: "trades", label: "Trades" },
  { page: "settings", label: "Settings" },
];

/** API status text per connection state. */
function apiStatusText(connection: ConnectionState): string {
  switch (connection) {
    case "starting":
      return "Starting…";
    case "connected":
      return "Connected";
    case "error":
      return "Connection error";
  }
}

export interface SidebarProps {
  /** Currently routed page — drives the active item. */
  page: Page;
  /** Called with the clicked page key. */
  onNavigate(page: Page): void;
  /** Connection lifecycle from the store. */
  connection: ConnectionState;
}

export function Sidebar({ page, onNavigate, connection }: SidebarProps) {
  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-bg-elevated">
      <nav aria-label="Main" className="flex-1 px-3 py-4">
        <ul className="space-y-1">
          {NAV_ITEMS.map((item) => {
            const active = item.page === page;
            return (
              <li key={item.page}>
                <button
                  type="button"
                  aria-current={active ? "page" : undefined}
                  onClick={() => onNavigate(item.page)}
                  className={
                    active
                      ? "flex w-full items-center rounded-lg bg-card px-3 py-2 text-left text-sm font-semibold text-accent"
                      : "flex w-full items-center rounded-lg px-3 py-2 text-left text-sm text-muted hover:bg-card-hover"
                  }
                >
                  {item.label}
                </button>
              </li>
            );
          })}
        </ul>
      </nav>
      <footer className="border-t border-border px-4 py-3 text-xs text-muted">
        <div>API status: {apiStatusText(connection)}</div>
        <div className="mt-1">Exchange: MEXC</div>
        <div className="mt-1">Account: Futures</div>
      </footer>
    </aside>
  );
}
