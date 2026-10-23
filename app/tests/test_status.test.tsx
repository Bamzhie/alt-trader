// Component tests for the neutral Status shell.
//
// Covers the two review findings:
//  1. The quant engine is NEVER "ready": it is a fixed "In development /
//     not ready" label even when /readyz reports the collector is ready.
//  2. An unavailable service at launch transitions from "Starting…" to a
//     connection error.
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";
import App from "../src/App";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

interface HealthzKind { alive: boolean }
interface ReadyzKind { ready: boolean }

function stubFetch(opts: {
  healthz?: HealthzKind;
  readyz?: ReadyzKind;
} = {}): void {
  const healthz = opts.healthz ?? { alive: true };
  const readyz = opts.readyz ?? { ready: true };
  vi.stubGlobal("fetch", (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/healthz")) {
      return Promise.resolve(
        new Response(JSON.stringify({ ok: true, alive: healthz.alive }),
          { status: healthz.alive ? 200 : 503,
            headers: { "Content-Type": "application/json" } }),
      );
    }
    if (url.endsWith("/readyz")) {
      return Promise.resolve(
        new Response(
          readyz.ready
            ? JSON.stringify({ ok: true, data: { ready: true }, error: null })
            : JSON.stringify({
                ok: false,
                data: {
                  status: "starting", bars: null, started_ts: 100,
                  first_success_ts: null, cycle_count: 0,
                  data_dir: "/tmp/bars", every_s: 300,
                },
                error: { code: "UNAVAILABLE",
                         message: "no successful cycle yet" },
              }),
          { status: readyz.ready ? 200 : 503,
            headers: { "Content-Type": "application/json" } },
        ),
      );
    }
    if (url.endsWith("/collector-health")) {
      return Promise.resolve(
        new Response(
          JSON.stringify({
            ok: true,
            data: {
              status: "healthy", started_ts: 100, first_success_ts: 1000,
              last_cycle_ts: 1200, cycle_count: 7,
              bars: { coins: 42, new_bars: 128, failed_coins: 0,
                      stopped: false, errors: [] },
              funding: { ok: true, result: { coins: 1, new_rows: 1 } },
              settlements: { ok: true, result: { coins: 1, new_rows: 1 } },
              prices: { ok: true, result: { series: 2, new_rows: 2 } },
              data_dir: "/tmp/bars", every_s: 300,
            },
            error: null,
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    }
    return Promise.resolve(
      new Response(JSON.stringify({ ok: false }), { status: 404 }));
  });
}

test("quant engine stays 'In development / not ready' even when ready=true", async () => {
  stubFetch({ healthz: { alive: true }, readyz: { ready: true } });
  render(<App />);
  expect(
    await screen.findByText("In development / not ready"),
  ).toBeInTheDocument();
  await waitFor(() => {
    expect(screen.getAllByText("ready").length).toBeGreaterThanOrEqual(1);
  });
});

test("status copy does not claim the live system is already collection-only", async () => {
  stubFetch({ healthz: { alive: true }, readyz: { ready: true } });
  render(<App />);
  await screen.findByText("In development / not ready");
  expect(
    screen.queryByText(/may still use the old configuration until its owner stops it/i),
  ).toBeNull();
  expect(
    screen.getByText(/No candidates are generated or displayed in this/i),
  ).toBeInTheDocument();
});

test("engine readiness is decoupled: ready does not mark the engine ready", async () => {
  stubFetch({ healthz: { alive: true }, readyz: { ready: true } });
  render(<App />);
  const engineLabel = await screen.findByText("Quant engine");
  const row = engineLabel.closest(".flex")!;
  await waitFor(() => {
    expect(row).toHaveTextContent(/In development \/ not ready/);
  });
});

test("status page surfaces real collector metrics only", async () => {
  stubFetch({ healthz: { alive: true }, readyz: { ready: true } });
  render(<App />);
  expect(await screen.findByText("42 coins, 128 new bars")).toBeInTheDocument();
  expect(screen.getByText("healthy")).toBeInTheDocument();
  expect(screen.getByText("/tmp/bars")).toBeInTheDocument();
  expect(screen.getByText("7")).toBeInTheDocument();
});

test("unavailable service at launch transitions to a connection error", async () => {
  stubFetch({ healthz: { alive: false }, readyz: { ready: false } });
  render(<App />);
  expect(screen.getByText(/connecting to ALT RADAR service/i))
    .toBeInTheDocument();
  // The first liveness probe rejects health -> the shell flips from
  // "Starting…" to "Connection error" and renders the contained banner.
  await waitFor(() => {
    expect(screen.getByText("Connection error")).toBeInTheDocument();
  });
  expect(screen.getByText(/service unavailable/i)).toBeInTheDocument();
});
