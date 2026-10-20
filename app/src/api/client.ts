// Typed API client for the ALT RADAR read-only contract.
//
// Every JSON route answers with the envelope {ok, data, error:{code,message}}.
// This client unwraps it: ok → data; otherwise it throws ApiError with the
// server's code; a fetch that throws (or a body that is not the envelope) is
// mapped to NETWORK. Commands are POST-only, idempotent by contract, and
// there is no order path — this client can pause, resume, and toggle trigger
// rules, nothing else.

import type {
  BarTimeframe,
  Bars,
  Envelope,
  ErrorCode,
  Metrics,
  QuantResearch,
  QuantStatus,
  SetupsPage,
  SetupsQuery,
  Setup,
  Snapshot,
  TradePlan,
  QuantSignals,
} from "./types";

/** Thrown for every failure path; `code` is one of ErrorCode. */
export class ApiError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.code = code;
  }
}

export interface CreateClientOptions {
  /** Base URL of the service. Defaults to "/api" (the Vite dev proxy). */
  baseUrl?: string;
  /** Bearer token. The Dashboard never POSTs, but probes/reads still send it. */
  token: string;
}

export interface ApiClient {
  /** Liveness probe — auth-free, non-envelope response. Never throws. */
  healthz(): Promise<boolean>;
  /** Readiness probe — false when the first sweep is still in progress. Never throws. */
  readyz(): Promise<boolean>;
  snapshot(): Promise<Snapshot>;
  quantStatus(): Promise<QuantStatus>;
  /** The read-only research surface (sample, intervals, provenance). */
  research(): Promise<QuantResearch>;
  /**
   * Rule-generated candidates for MANUAL REVIEW. The app never executes
   * them; a promotion or validation result never authorises an order.
   */
  signals(): Promise<QuantSignals>;
  setups(query: SetupsQuery): Promise<SetupsPage>;
  setup(id: string): Promise<Setup>;
  bars(coin: string, tf: BarTimeframe): Promise<Bars>;
  metrics(coin: string): Promise<Metrics>;
  tradeplan(id: string): Promise<TradePlan>;
  pause(): Promise<void>;
  resume(): Promise<void>;
  disableTrigger(rule: string): Promise<void>;
  enableTrigger(rule: string): Promise<void>;
}

function isEnvelope(value: unknown): value is Envelope<unknown> {
  return (
    typeof value === "object" &&
    value !== null &&
    "ok" in value &&
    typeof (value as { ok: unknown }).ok === "boolean"
  );
}

function unwrap(body: unknown): unknown {
  if (!isEnvelope(body)) {
    throw new ApiError("NETWORK", "response was not a valid envelope");
  }
  if (body.ok) {
    return body.data;
  }
  const code = body.error?.code;
  const message = body.error?.message ?? "request failed";
  if (typeof code !== "string" || code === "") {
    throw new ApiError("NETWORK", message);
  }
  throw new ApiError(code, message);
}

/** Reads the JSON body, mapping parse failures to NETWORK. */
async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new ApiError("NETWORK", "response body was not valid JSON");
  }
}

/** Builds the query string from a SetupsQuery, dropping undefined entries. */
function buildQuery(query: SetupsQuery): string {
  const params = new URLSearchParams();
  if (query.state !== undefined) params.set("state", query.state);
  if (query.limit !== undefined) params.set("limit", String(query.limit));
  if (query.cursor !== undefined) params.set("cursor", query.cursor);
  const qs = params.toString();
  return qs ? `?${qs}` : "";
}

/**
 * Shape guard for the research surface. A body that is not a research
 * surface is a contract violation — it is reported as such, never rendered
 * as a partial surface.
 */
function isQuantResearch(value: unknown): value is QuantResearch {
  if (typeof value !== "object" || value === null) return false;
  const v = value as Partial<QuantResearch>;
  return (
    typeof v.version === "string" &&
    (v.status === "ok" || v.status === "insufficient") &&
    Array.isArray(v.reasons) &&
    typeof v.generated_ts === "number" &&
    typeof v.provenance === "object" &&
    v.provenance !== null &&
    typeof v.sample === "object" &&
    v.sample !== null &&
    Array.isArray(v.rules) &&
    typeof v.calibration === "object" &&
    v.calibration !== null &&
    typeof v.coverage === "object" &&
    v.coverage !== null &&
    typeof v.validation === "object" &&
    v.validation !== null
  );
}

export function createClient(opts: CreateClientOptions): ApiClient {
  const baseUrl = (opts.baseUrl ?? "/api").replace(/\/+$/, "");
  const token = opts.token;

  async function get(path: string): Promise<unknown> {
    let response: Response;
    try {
      response = await fetch(`${baseUrl}${path}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
    } catch {
      throw new ApiError("NETWORK", "network request failed");
    }
    return unwrap(await readJson(response));
  }

  async function post(path: string, body: Record<string, unknown>): Promise<unknown> {
    let response: Response;
    try {
      response = await fetch(`${baseUrl}${path}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
    } catch {
      throw new ApiError("NETWORK", "network request failed");
    }
    return unwrap(await readJson(response));
  }

  return {
    async healthz(): Promise<boolean> {
      // Liveness only: auth-free, and deliberately NOT the envelope.
      // Any failure (transport or probe) means "not alive".
      try {
        const response = await fetch(`${baseUrl}/healthz`);
        if (!response.ok) return false;
        const body = (await readJson(response)) as { alive?: unknown };
        return body?.alive === true;
      } catch {
        return false;
      }
    },

    async readyz(): Promise<boolean> {
      // Alive-but-not-ready is a normal first-sweep state: it answers
      // UNAVAILABLE, which we surface as `false` rather than throwing.
      try {
        const data = (await get("/readyz")) as { ready?: unknown };
        return data?.ready === true;
      } catch (err) {
        if (err instanceof ApiError && err.code === "UNAVAILABLE") {
          return false;
        }
        throw err;
      }
    },

    async snapshot(): Promise<Snapshot> {
      return (await get("/snapshot")) as Snapshot;
    },

    async quantStatus(): Promise<QuantStatus> {
      return (await get("/quant-status")) as QuantStatus;
    },

    async research(): Promise<QuantResearch> {
      const data = await get("/quant-research");
      if (!isQuantResearch(data)) {
        throw new ApiError("NETWORK", "response was not a research surface");
      }
      return data;
    },

    async signals(): Promise<QuantSignals> {
      return (await get("/quant-signals")) as QuantSignals;
    },

    async setups(query: SetupsQuery): Promise<SetupsPage> {
      const data = (await get(`/setups${buildQuery(query)}`)) as {
        setups: Setup[];
        next_cursor: string | null;
      };
      return { setups: data.setups, nextCursor: data.next_cursor };
    },

    async setup(id: string): Promise<Setup> {
      return (await get(`/setup/${id}`)) as Setup;
    },

    async bars(coin: string, tf: BarTimeframe): Promise<Bars> {
      const params = new URLSearchParams({ coin, tf });
      return (await get(`/bars?${params.toString()}`)) as Bars;
    },

    async metrics(coin: string): Promise<Metrics> {
      const params = new URLSearchParams({ coin });
      return (await get(`/metrics?${params.toString()}`)) as Metrics;
    },

    async tradeplan(id: string): Promise<TradePlan> {
      return (await get(`/tradeplan/${id}`)) as TradePlan;
    },

    async pause(): Promise<void> {
      await post("/commands/pause", {});
    },

    async resume(): Promise<void> {
      await post("/commands/resume", {});
    },

    async disableTrigger(rule: string): Promise<void> {
      await post("/commands/disable-trigger", { rule });
    },

    async enableTrigger(rule: string): Promise<void> {
      await post("/commands/enable-trigger", { rule });
    },
  };
}

export type { ErrorCode };
