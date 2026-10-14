// App — the shell: sidebar, top bar, connection banner, and state routing.
//
// House rules:
//   * Routing is a tiny `usePage` hook holding the current page key — no
//     router dependency, no URL.
//   * Connection state is read from the Zustand store (never fetched here);
//     the shell issues no requests of its own on mount.
//   * Colors come from theme tokens only — never raw hexes.
import { useState } from "react";
import { ConnectionBanner } from "./components/ConnectionBanner";
import { Sidebar } from "./components/Sidebar";
import type { Page as PageKey } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { Dashboard } from "./pages/Dashboard";
import { StubPage } from "./pages/StubPage";
import { Watchlist } from "./pages/Watchlist";
import { useDashboardStore } from "./store/dashboard";

export type Page = PageKey;

/** Current shell page plus its setter — the whole "router". */
function usePage(initial: Page): { page: Page; setPage(p: Page): void } {
  const [page, setPage] = useState<Page>(initial);
  return { page, setPage };
}

export interface AppProps {
  apiBaseUrl?: string;
  serviceToken?: string;
}

export default function App({
  apiBaseUrl = "/api",
  serviceToken = import.meta.env.DEV ? "dev-token" : "",
}: AppProps = {}) {
  const { page, setPage } = usePage("dashboard");
  const connection = useDashboardStore((s) => s.connection);

  return (
    <div className="flex min-h-screen bg-bg text-fg">
      <Sidebar page={page} onNavigate={setPage} connection={connection} />
      <div className="flex min-w-0 flex-1 flex-col">
        <TopBar />
        <ConnectionBanner connection={connection} />
        <main className="min-w-0 flex-1">
          {page === "dashboard" ? (
            <Dashboard apiBaseUrl={apiBaseUrl} serviceToken={serviceToken} />
          ) : page === "watchlist" ? (
            <Watchlist apiBaseUrl={apiBaseUrl} serviceToken={serviceToken} />
          ) : (
            <StubPage
              name={
                {
                  watchlist: "Watchlist",
                  signals: "Signals",
                  analysis: "Analysis",
                  trades: "Trades",
                  settings: "Settings",
                }[page]
              }
            />
          )}
        </main>
      </div>
    </div>
  );
}
