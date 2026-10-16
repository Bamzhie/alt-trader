// Research — the Analysis page: a read-only view over the quant shadow
// watcher (GET /quant-status + quant.shadow SSE frames).
//
// House rules:
//   * One read on mount: client.quantStatus(). Every quant.shadow frame and
//     every server resync re-reads it — research rows never come from the
//     snapshot and never render through setup-table components (no <table>
//     here at all), so shadow findings can never mix into Dashboard tables.
//   * SHADOW labeling only. Allowed labels: `SHADOW`, `not actionable`,
//     `insufficient evidence`, `measured range (lower–upper)` with the
//     static caption `range of measured outcomes, not a prediction`.
//     Uncertainty is interval endpoints only — never a point reading.
//   * Empty (zero events) and unreadable-store (503) states render
//     insufficient evidence — never blank.
//   * Colors come from theme tokens only — never raw hexes.
import { useCallback, useEffect, useMemo, useState } from "react";
import { createClient } from "../api/client";
import type { ApiClient } from "../api/client";
import { useEvents } from "../api/sse";
import type { QuantPromotion, QuantStatus } from "../api/types";

/** Renders interval endpoints with the exact allowed label. */
function formatRange(lo: number, hi: number): string {
  return `measured range (${lo.toFixed(4)}–${hi.toFixed(4)})`;
}

function hasEndpoints(promotion: QuantPromotion): boolean {
  return (
    typeof promotion.holdout?.ci_lo === "number" &&
    typeof promotion.holdout?.ci_hi === "number"
  );
}

export interface ResearchProps {
  apiBaseUrl?: string;
  serviceToken?: string;
}

export function Research({
  apiBaseUrl = "/api",
  serviceToken = import.meta.env.DEV ? "dev-token" : "",
}: ResearchProps = {}) {
  const client: ApiClient = useMemo(
    () => createClient({ baseUrl: apiBaseUrl, token: serviceToken }),
    [apiBaseUrl, serviceToken],
  );
  const [status, setStatus] = useState<QuantStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((): void => {
    client
      .quantStatus()
      .then((data) => {
        setStatus(data);
        setError(null);
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : "quant status read failed");
      });
  }, [client]);

  useEffect(() => {
    load();
  }, [load]);

  // Live shadow frames carry counts only; each new one re-reads the status.
  // The hook has no snapshot watermark here (gate open), so only
  // quant.shadow frames trigger a re-read — other kinds are ignored.
  useEvents({
    url: `${apiBaseUrl.replace(/\/+$/, "")}/events`,
    token: serviceToken,
    snapshotEventId: null,
    onEvent: (event) => {
      if (event.kind === "quant.shadow") load();
    },
    onResync: () => {
      load();
    },
  });

  return (
    <section className="space-y-4 px-6 py-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold text-fg">Research</h1>
        <p className="rounded-md border border-border bg-card px-2 py-1 text-xs font-semibold text-muted">
          SHADOW — findings here are not actionable
        </p>
      </div>

      {status ? (
        status.event_counts.total === 0 ? (
          <p
            role="status"
            className="rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted"
          >
            insufficient evidence — no shadow events recorded yet
          </p>
        ) : (
          <>
            <p className="text-xs text-muted">watcher: {status.status}</p>
            {status.paused ? (
              <p className="rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted">
                watcher paused{status.pause_reason ? ` — ${status.pause_reason}` : ""}
              </p>
            ) : null}
            <div className="rounded-xl border border-border bg-card px-4 py-3">
              <h2 className="mb-2 text-sm font-semibold text-fg">
                Shadow events
              </h2>
              <ul className="space-y-1 text-sm">
                <li className="flex items-baseline justify-between gap-2">
                  <span className="text-muted">Emitted</span>
                  <span className="tabular-nums text-fg">
                    {status.event_counts.emitted}
                  </span>
                </li>
                <li className="flex items-baseline justify-between gap-2">
                  <span className="text-muted">Resolved</span>
                  <span className="tabular-nums text-fg">
                    {status.event_counts.resolved}
                  </span>
                </li>
                <li className="flex items-baseline justify-between gap-2">
                  <span className="text-muted">Shadow events</span>
                  <span className="tabular-nums text-fg">
                    {status.event_counts.total}
                  </span>
                </li>
              </ul>
            </div>

            {status.suppression_top.length > 0 ? (
              <div className="rounded-xl border border-border bg-card px-4 py-3">
                <h2 className="mb-2 text-sm font-semibold text-fg">
                  Suppression
                </h2>
                <ul className="space-y-2 text-sm">
                  {status.suppression_top.map((row) => (
                    <li
                      key={`${row.rule_id}:${row.reason}`}
                      className="flex items-baseline justify-between gap-2"
                    >
                      <span className="text-fg">
                        <span className="font-semibold">{row.rule_id}</span>
                        <span className="text-muted"> — {row.reason}</span>
                      </span>
                      <span className="tabular-nums text-fg">{row.n}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}

            {status.promotions.length > 0 ? (
              <div className="rounded-xl border border-border bg-card px-4 py-3">
                <h2 className="mb-2 text-sm font-semibold text-fg">
                  Promotions
                </h2>
                <ul className="space-y-2 text-sm">
                  {status.promotions.map((promotion) => (
                    <li key={`${promotion.rule_id}:${promotion.config_hash}`}>
                      <span className="font-semibold text-fg">
                        {promotion.rule_id}
                      </span>
                      <span className="mt-1 block text-muted">
                        {hasEndpoints(promotion)
                          ? formatRange(
                              promotion.holdout.ci_lo,
                              promotion.holdout.ci_hi,
                            )
                          : "measured range unavailable"}
                      </span>
                      <span className="mt-1 block text-xs text-muted">
                        range of measured outcomes, not a prediction
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </>
        )
      ) : error ? (
        <p
          role="status"
          className="rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted"
        >
          insufficient evidence — {error}
        </p>
      ) : (
        <p className="rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted">
          loading research…
        </p>
      )}
    </section>
  );
}
