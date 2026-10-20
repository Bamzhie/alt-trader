// Research — the Analysis page: a read-only view over the quant shadow
// watcher (GET /quant-status + GET /quant-research + quant.shadow SSE
// frames).
//
// House rules:
//   * Two reads on mount: client.quantStatus() (counts, suppression,
//     promotions) and client.research() (the research surface: sample,
//     intervals, status, provenance). Every quant.shadow frame and every
//     server resync re-reads them — research rows never come from the
//     snapshot and never render through setup-table components (no <table>
//     here at all), so shadow findings can never mix into Dashboard tables.
//   * SHADOW labeling only. The view's own labels are `SHADOW`, `not
//     actionable` (in the badge at the top), `insufficient evidence` and
//     `measured range (lower–upper)` with the static caption `range of
//     measured outcomes, not a prediction`. A status never borrows another
//     status' label: a rule that passed its gate says `gate passed —
//     research only`, it does not say "not actionable".
//   * Uncertainty is interval endpoints only — never a point reading, and
//     never a point proportion: the outcome shares stay in the payload,
//     they are not rendered.
//   * Empty (zero events) and unreadable-store (503) states render
//     insufficient evidence — never blank.
//   * An unavailable value renders the literal "unavailable" — never a
//     zero, never a guess.
//   * No frontend math: every number comes verbatim from the API — even the
//     counts of rules and buckets.
//   * Colors come from theme tokens only — never raw hexes.
import { useCallback, useEffect, useMemo, useState } from "react";
import { createClient } from "../api/client";
import type { ApiClient } from "../api/client";
import { useEvents } from "../api/sse";
import type {
  QuantPromotion,
  QuantResearch,
  QuantResearchRule,
  QuantResearchSample,
  QuantStatus,
} from "../api/types";

/** Renders interval endpoints with the exact allowed label. */
function formatRange(lo: number, hi: number): string {
  return `measured range (${formatEndpoint(lo)}–${formatEndpoint(hi)})`;
}

/**
 * One interval endpoint: four decimals, but a non-zero endpoint is NEVER
 * shown as a zero — a value that would round away falls back to its own
 * digits instead of pretending to be 0.0000.
 */
function formatEndpoint(value: number): string {
  const fixed = value.toFixed(4);
  if (value !== 0 && Number(fixed) === 0) {
    return String(value);
  }
  return fixed;
}

function hasEndpoints(promotion: QuantPromotion): boolean {
  return (
    typeof promotion.holdout?.ci_lo === "number" &&
    typeof promotion.holdout?.ci_hi === "number"
  );
}

/** Formats a UTC-seconds int as "YYYY-MM-DD HH:MM:SS UTC". */
function formatUtc(ts: number): string {
  const d = new Date(ts * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`
  );
}

/** An unavailable number is the literal "unavailable" — never a zero. */
function orUnavailable(value: number | null): string {
  return value === null ? "unavailable" : String(value);
}

// SampleCounts — the sample the surface describes, with its denominators.
//
// House rules:
//   * "X of Y" with the denominator named in the footnote — a bare number
//     never stands in for a denominator.
//   * The sample's days and coins count EVERY shadow event in the sample;
//     a rule's own days and coins count its resolved rows (the measured
//     range's denominators). The footnote says which is which.
function SampleCounts({ sample }: { sample: QuantResearchSample }) {
  return (
    <div className="rounded-xl border border-border bg-card px-4 py-3">
      <h2 className="mb-2 text-sm font-semibold text-fg">Sample</h2>
      <ul className="space-y-1 text-sm">
        <li className="flex items-baseline justify-between gap-2">
          <span className="text-muted">Resolved</span>
          <span className="tabular-nums text-fg">
            {sample.resolved} of {sample.events}
            <span className="ml-2 text-xs text-muted">
              resolved of all shadow events
            </span>
          </span>
        </li>
        <li className="flex items-baseline justify-between gap-2">
          <span className="text-muted">Open</span>
          <span className="tabular-nums text-fg">
            {sample.open} of {sample.events}
            <span className="ml-2 text-xs text-muted">
              open of all shadow events
            </span>
          </span>
        </li>
        <li className="flex items-baseline justify-between gap-2">
          <span className="text-muted">Days observed</span>
          <span className="tabular-nums text-fg">{sample.days}</span>
        </li>
        <li className="flex items-baseline justify-between gap-2">
          <span className="text-muted">Coins observed</span>
          <span className="tabular-nums text-fg">{sample.coins}</span>
        </li>
        <li className="flex items-baseline justify-between gap-2">
          <span className="text-muted">Rules observed</span>
          <span className="tabular-nums text-fg">{sample.rules}</span>
        </li>
      </ul>
      <p className="mt-2 text-xs text-muted">
        sample days and coins count every shadow event in the sample; a rule's
        days and coins count its resolved rows — the measured range's
        denominators
      </p>
    </div>
  );
}

// StatusRows — calibration, coverage and validation status.
//
// House rules:
//   * A value the API could not compute renders the literal "unavailable",
//     with the API's reason next to it — never a zero, never a guess.
//   * Every count here comes from the API: the view never counts rules or
//     buckets itself.
function StatusRows({ research }: { research: QuantResearch }) {
  const coverage =
    research.coverage.ratio === null
      ? "unavailable"
      : `${research.coverage.attributed} of ${research.coverage.eligible}`;

  return (
    <div className="rounded-xl border border-border bg-card px-4 py-3">
      <h2 className="mb-2 text-sm font-semibold text-fg">Status</h2>
      <ul className="space-y-2 text-sm">
        <li>
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-muted">Calibration</span>
            <span className="text-fg">
              {research.calibration.available
                ? `${research.calibration.count} buckets`
                : "unavailable"}
            </span>
          </div>
          {research.calibration.reason ? (
            <p className="mt-1 text-xs text-muted">
              {research.calibration.reason}
            </p>
          ) : null}
          {research.calibration.buckets.map((bucket) => (
            <p
              key={bucket.label}
              className="mt-1 text-xs text-muted"
            >
              {bucket.label} — {bucket.n} rows — measured{" "}
              {orUnavailable(bucket.measured)} · expected{" "}
              {orUnavailable(bucket.expected)}
            </p>
          ))}
        </li>
        <li>
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-muted">Coverage</span>
            <span className="tabular-nums text-fg">{coverage}</span>
          </div>
          <p className="mt-1 text-xs text-muted">
            {research.coverage.reason ?? "attributed of eligible opportunities"}
          </p>
        </li>
        <li>
          <div className="flex items-baseline justify-between gap-2">
            <span className="text-muted">Validation</span>
            <span className="text-fg">
              {research.validation.validated} of {research.validation.rules}{" "}
              rules with a passed gate record
            </span>
          </div>
        </li>
      </ul>
    </div>
  );
}

// RuleRows — one block per rule: counts, the measured range and its status.
//
// House rules:
//   * Uncertainty is interval endpoints only: `measured range (lo–hi)` with
//     the static caption, or `measured range unavailable`. No point reading,
//     no mean, and no point proportion: the outcome shares are not
//     rendered.
//   * A missing value renders the literal "unavailable" — never a zero.
//   * The validation status keeps its own vocabulary: a rule that passed
//     its gate says `gate passed — research only`; one that did not says
//     `gate not passed — research only`.
function RuleRows({ rules }: { rules: QuantResearchRule[] }) {
  return (
    <div className="rounded-xl border border-border bg-card px-4 py-3">
      <h2 className="mb-2 text-sm font-semibold text-fg">Rule measurements</h2>
      <ul className="space-y-3 text-sm">
        {rules.map((rule) => {
          const lo = rule.interval?.lo;
          const hi = rule.interval?.hi;
          const range =
            typeof lo === "number" && typeof hi === "number"
              ? formatRange(lo, hi)
              : "measured range unavailable";
          return (
            <li key={rule.rule_id}>
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-semibold text-fg">{rule.rule_id}</span>
                <span className="text-xs text-muted">{rule.role}</span>
              </div>
              <p className="mt-1 text-muted">
                {rule.resolved} of {rule.events} resolved · days{" "}
                {orUnavailable(rule.days)} · coins {orUnavailable(rule.coins)}
              </p>
              <p className="mt-1 text-fg">{range}</p>
              <p className="mt-1 text-xs text-muted">
                range of measured outcomes, not a prediction
              </p>
              <p className="mt-1 text-xs text-muted">{rule.cost_note}</p>
              <p className="mt-1 text-xs text-muted">
                validation:{" "}
                {rule.validation.status === "validated"
                  ? "gate passed — research only"
                  : "gate not passed — research only"}
              </p>
              {rule.validation.reasons.map((reason) => (
                <p key={reason} className="mt-1 text-xs text-muted">
                  {reason}
                </p>
              ))}
              {rule.suppression.map((row) => (
                <p
                  key={`${rule.rule_id}:${row.reason}`}
                  className="mt-1 text-xs text-muted"
                >
                  suppression — {row.reason} × {row.n}
                </p>
              ))}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ProvenanceRows — where the numbers came from.
//
// House rules:
//   * Provenance is plain muted text; a missing hash or revision renders
//     the literal "unavailable" — never a guess.
//   * Timestamps are UTC-seconds integers rendered "YYYY-MM-DD HH:MM:SS UTC".
function ProvenanceRows({ research }: { research: QuantResearch }) {
  const provenance = research.provenance;
  return (
    <div className="rounded-xl border border-border bg-card px-4 py-3">
      <h2 className="mb-2 text-sm font-semibold text-fg">Provenance</h2>
      <ul className="space-y-1 text-sm">
        <li className="flex items-baseline justify-between gap-2">
          <span className="text-muted">Config hash</span>
          <span className="text-fg">
            {provenance.config_hash ?? "unavailable"}
          </span>
        </li>
        <li className="flex items-baseline justify-between gap-2">
          <span className="text-muted">Code revision</span>
          <span className="text-fg">{provenance.code_rev ?? "unavailable"}</span>
        </li>
        <li className="flex items-baseline justify-between gap-2">
          <span className="text-muted">Generated</span>
          <span className="text-fg">{formatUtc(research.generated_ts)}</span>
        </li>
      </ul>
      <p className="mt-2 text-xs text-muted">registered rules</p>
      {provenance.rule_ids.length > 0 ? (
        <ul className="space-y-1 text-xs text-muted">
          {provenance.rule_ids.map((ruleId) => (
            <li key={ruleId}>{ruleId}</li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted">unavailable</p>
      )}
      <p className="mt-2 text-xs text-muted">feature versions</p>
      {provenance.feature_versions.length > 0 ? (
        <ul className="space-y-1 text-xs text-muted">
          {provenance.feature_versions.map((feature) => (
            <li key={feature.name}>
              {feature.name} {feature.version}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-muted">unavailable</p>
      )}
    </div>
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
  const [statusError, setStatusError] = useState<string | null>(null);
  const [research, setResearch] = useState<QuantResearch | null>(null);
  const [researchError, setResearchError] = useState<string | null>(null);

  const load = useCallback((): void => {
    client
      .quantStatus()
      .then((data) => {
        setStatus(data);
        setStatusError(null);
      })
      .catch((err: unknown) => {
        setStatusError(
          err instanceof Error ? err.message : "quant status read failed",
        );
      });
    client
      .research()
      .then((data) => {
        setResearch(data);
        setResearchError(null);
      })
      .catch((err: unknown) => {
        setResearchError(
          err instanceof Error ? err.message : "research surface read failed",
        );
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

  // The reasons the API gives for an insufficient surface. The empty state
  // keeps the API's reason when there is one, and falls back to the
  // established wording when the research read did not answer.
  const insufficientReasons =
    research !== null && research.status === "insufficient"
      ? research.reasons
      : [];
  const emptyReason = insufficientReasons[0] ?? "no shadow events recorded yet";

  return (
    <section className="space-y-4 px-6 py-6">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-lg font-semibold text-fg">Research</h1>
        <p className="rounded-md border border-border bg-card px-2 py-1 text-xs font-semibold text-muted">
          SHADOW — findings here are not actionable
        </p>
      </div>

      {status === null ? (
        statusError !== null ? (
          <p
            role="status"
            className="rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted"
          >
            insufficient evidence — {statusError}
          </p>
        ) : (
          <p
            role="status"
            className="rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted"
          >
            loading research…
          </p>
        )
      ) : status.event_counts.total === 0 ? (
        <>
          <p
            role="status"
            className="rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted"
          >
            insufficient evidence — {emptyReason}
          </p>
          {insufficientReasons.slice(1).map((reason) => (
            <p key={reason} className="text-xs text-muted">
              {reason}
            </p>
          ))}
        </>
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
            <p className="mt-2 text-xs text-muted">
              emitted counts the shadow events that are still open; resolved
              counts the ones that finished
            </p>
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

          {researchError !== null ? (
            <p
              role="status"
              className="rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted"
            >
              insufficient evidence — {researchError}
            </p>
          ) : research !== null && research.status === "insufficient" ? (
            <>
              <p
                role="status"
                className="rounded-lg border border-border bg-card px-4 py-3 text-sm text-muted"
              >
                insufficient evidence — {emptyReason}
              </p>
              {insufficientReasons.slice(1).map((reason) => (
                <p key={reason} className="text-xs text-muted">
                  {reason}
                </p>
              ))}
            </>
          ) : research !== null ? (
            <>
              <SampleCounts sample={research.sample} />
              <StatusRows research={research} />
              <RuleRows rules={research.rules} />
              <ProvenanceRows research={research} />
            </>
          ) : null}
        </>
      )}
    </section>
  );
}
