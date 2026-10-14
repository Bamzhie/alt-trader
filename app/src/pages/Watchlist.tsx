import { useEffect, useMemo, useState } from "react";
import { createClient } from "../api/client";
import type { CoinWithoutSetup, Snapshot } from "../api/types";

interface WatchlistProps {
  apiBaseUrl: string;
  serviceToken: string;
}

function price(value: number | null | undefined): string {
  if (value == null) return "unavailable";
  return value.toLocaleString("en-US", { maximumSignificantDigits: 8 });
}

function volume(value: number | null | undefined): string {
  if (value == null) return "unavailable";
  return `$${value.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

export function Watchlist({ apiBaseUrl, serviceToken }: WatchlistProps) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const client = useMemo(
    () => createClient({ baseUrl: apiBaseUrl, token: serviceToken }),
    [apiBaseUrl, serviceToken],
  );

  useEffect(() => {
    let active = true;
    const load = () => {
      void client.snapshot().then((data) => {
        if (!active) return;
        setSnapshot(data);
        setError(null);
      }).catch((err: unknown) => {
        if (!active) return;
        setError(err instanceof Error ? err.message : "snapshot read failed");
      });
    };
    load();
    const timer = window.setInterval(load, 30_000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [client]);

  const coins = useMemo(() => {
    const rows = snapshot?.coins_without_setup ?? [];
    const normalized = query.trim().toUpperCase();
    return normalized
      ? rows.filter((row) => row.coin.toUpperCase().includes(normalized))
      : rows;
  }, [query, snapshot]);

  return (
    <section className="space-y-4 px-6 py-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-fg">Watchlist</h1>
          <p className="mt-1 text-sm text-muted">
            Current MEXC perpetual universe. Rows are market data, not quant setups.
          </p>
        </div>
        <label className="text-xs text-muted">
          Search coins
          <input
            aria-label="Search coins"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="BTC, SOL…"
            className="ml-2 rounded-md border border-border bg-bg-elevated px-3 py-2 text-sm text-fg outline-none focus:border-accent"
          />
        </label>
      </header>

      {error ? (
        <p role="alert" className="rounded-lg border border-short px-4 py-3 text-sm text-short">
          Market data unavailable — {error}
        </p>
      ) : null}
      {snapshot?.unavailable ? (
        <p role="status" className="rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted">
          {snapshot.unavailable}
        </p>
      ) : null}

      <div className="overflow-hidden rounded-xl border border-border bg-card">
        <div className="flex items-center justify-between border-b border-border px-4 py-3 text-sm">
          <span className="font-semibold text-fg">Scanned perpetuals</span>
          <span className="text-muted">{coins.length} coins</span>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[680px] text-left text-sm">
            <thead className="bg-bg-elevated text-xs uppercase tracking-wide text-muted">
              <tr>
                <th className="px-4 py-3">Coin</th>
                <th className="px-4 py-3">Price</th>
                <th className="px-4 py-3">24h</th>
                <th className="px-4 py-3">Quote volume</th>
                <th className="px-4 py-3">Watcher status</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {coins.map((coin: CoinWithoutSetup) => (
                <tr key={coin.coin}>
                  <td className="px-4 py-3 font-semibold text-fg">{coin.coin}</td>
                  <td className="px-4 py-3 tabular-nums text-fg">{price(coin.price)}</td>
                  <td className={`px-4 py-3 tabular-nums ${(coin.change_24h_pct ?? 0) >= 0 ? "text-long" : "text-short"}`}>
                    {coin.change_24h_pct == null
                      ? "unavailable"
                      : `${coin.change_24h_pct >= 0 ? "+" : ""}${coin.change_24h_pct.toFixed(2)}%`}
                  </td>
                  <td className="px-4 py-3 tabular-nums text-muted">{volume(coin.quote_vol_24h)}</td>
                  <td className="px-4 py-3 text-xs text-muted">{coin.blocking_reason}</td>
                </tr>
              ))}
              {coins.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-4 py-10 text-center text-muted">
                    {snapshot ? "No current coins match this search." : "Loading market data…"}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
