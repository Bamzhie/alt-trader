// Typed read-only client for the collection-only service.
//
// The service is in active development; it only exposes bar/funding/
// settlement/price archives plus liveness, readiness, and collector health.
// There are no candidates, no snapshot, no SSE event bus, and no commands.

export interface ApiErrorOptions {
  code: string;
  message: string;
}

export class ApiError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.code = code;
  }
}

export interface CreateClientOptions {
  baseUrl?: string;
  token: string;
}

/** Collector cycle summary returned by /collector-health. */
export interface CollectorHealth {
  status: "starting" | "healthy" | "degraded" | "failed";
  started_ts: number;
  first_success_ts: number | null;
  last_cycle_ts: number | null;
  last_duration_s: number | null;
  cycle_count: number;
  next_due_ts: number | null;
  last_error: string | null;
  bars: BarsSummary | null;
  funding: HookSummary | null;
  settlements: HookSummary | null;
  prices: HookSummary | null;
  data_dir: string;
  every_s: number;
}

export interface BarsSummary {
  coins: number;
  new_bars: number;
  failed_coins: number;
  stopped: boolean;
  errors: Array<{ coin: string; error: string }>;
}

export interface HookSummary {
  ok: boolean;
  seconds: number;
  error: string | null;
  result: Record<string, unknown> | null;
}

export type BarTimeframe = "5m" | "15m" | "1H" | "4H" | "1D";

export interface Bar {
  ts: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  closed: boolean;
}

export interface Bars {
  coin: string;
  tf: string;
  closed_only: boolean;
  count: number;
  data_as_of: number | null;
  bars: Bar[];
  unavailable: string | null;
}

function isEnvelope(value: unknown): value is {
  ok: boolean;
  data: unknown;
  error: { code: string; message: string } | null;
} {
  return (
    typeof value === "object" &&
    value !== null &&
    "ok" in value &&
    typeof((value as { ok: unknown }).ok) === "boolean"
  );
}

function unwrap(body: unknown): unknown {
  if (!isEnvelope(body)) {
    throw new ApiError("NETWORK", "response was not a valid envelope");
  }
  if (body.ok) return body.data;
  const code = body.error?.code ?? "NETWORK";
  const message = body.error?.message ?? "request failed";
  throw new ApiError(code, message);
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new ApiError("NETWORK", "response body was not valid JSON");
  }
}

export type ApiClient = {
  healthz(): Promise<boolean>;
  readyz(): Promise<boolean>;
  collectorHealth(): Promise<CollectorHealth>;
  bars(coin: string, tf: BarTimeframe): Promise<Bars>;
};

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

  return {
    async healthz(): Promise<boolean> {
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
      try {
        const data = (await get("/readyz")) as { ready?: unknown };
        return data?.ready === true;
      } catch (err) {
        if (err instanceof ApiError && err.code === "UNAVAILABLE") return false;
        throw err;
      }
    },

    async collectorHealth(): Promise<CollectorHealth> {
      return (await get("/collector-health")) as CollectorHealth;
    },

    async bars(coin: string, tf: BarTimeframe): Promise<Bars> {
      const params = new URLSearchParams({ coin, tf });
      return (await get(`/bars?${params.toString()}`)) as Bars;
    },
  };
}
