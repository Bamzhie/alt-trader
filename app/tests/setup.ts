import "@testing-library/jest-dom/vitest";
import { createRequire } from "node:module";

// Socket tripwire: tests must never touch the network. Any non-loopback
// connect (net or dgram) throws — this catches accidental fetch/HTTP/SSE
// attempts that bypass a mocked `fetch`.
const require = createRequire(import.meta.url);
const net = require("node:net") as typeof import("node:net");
const dgram = require("node:dgram") as typeof import("node:dgram");
const http = require("node:http") as typeof import("node:http");
const https = require("node:https") as typeof import("node:https");

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"]);

function isLoopback(host: unknown): boolean {
  // Unspecified host defaults to localhost on both TCP and UDP.
  if (host === undefined || host === null || host === "") return true;
  return typeof host === "string" && LOOPBACK_HOSTS.has(host.toLowerCase());
}

function assertLoopback(where: string, host: unknown): void {
  if (!isLoopback(host)) {
    throw new Error(
      `socket tripwire: ${where} to non-loopback host "${String(host)}" — ` +
        "tests must mock fetch instead of touching the network",
    );
  }
}

const originalConnect = net.connect.bind(net);
net.connect = ((...args: Parameters<typeof net.connect>) => {
  const [first, second] = args;
  if (first !== null && typeof first === "object") {
    const opts = first as { host?: string; hostname?: string };
    assertLoopback("net.connect", opts.host ?? opts.hostname);
  } else if (typeof second === "string") {
    // net.connect(port, host)
    assertLoopback("net.connect", second);
  }
  return originalConnect(...args);
}) as typeof net.connect;

// node:http and node:https internally call `net.createConnection`, which is a
// SEPARATE export of the same underlying function — patching only net.connect
// leaves those paths open. Patch both entries (they are distinct properties,
// even though net.connect === net.createConnection at load time).
const originalCreateConnection = net.createConnection.bind(net);
net.createConnection = ((
  ...args: Parameters<typeof net.createConnection>
) => {
  const [first, second] = args;
  if (first !== null && typeof first === "object") {
    const opts = first as { host?: string; hostname?: string };
    assertLoopback("net.createConnection", opts.host ?? opts.hostname);
  } else if (typeof second === "string") {
    assertLoopback("net.createConnection", second);
  }
  return originalCreateConnection(...args);
}) as typeof net.createConnection;

// http.request / https.request (and the .get shorthand) accept either an
// options object or a URL string as first argument; the host can also arrive
// via the `hostname` key or as a parsed URL. Check every shape.
function assertHttpLoopback(where: string, ...args: unknown[]): void {
  const first = args[0];
  if (typeof first === "string") {
    let hostname: string | undefined;
    try {
      hostname = new URL(first).hostname;
    } catch {
      // not a parseable URL — leave the host to the net-level checks
    }
    if (hostname !== undefined) assertLoopback(where, hostname);
    return;
  }
  if (first !== null && typeof first === "object") {
    const opts = first as { host?: string; hostname?: string };
    const host = opts.host ?? opts.hostname;
    if (host !== undefined) assertLoopback(where, host);
  }
}

for (const [label, mod] of [
  ["http", http],
  ["https", https],
] as const) {
  for (const fn of ["request", "get"] as const) {
    const original = (mod[fn] as (...a: unknown[]) => unknown).bind(mod);
    (mod as unknown as Record<string, (...a: unknown[]) => unknown>)[fn] = (
      ...args: unknown[]
    ) => assertHttpLoopback(`${label}.${fn}`, ...args) ?? original(...args);
  }
}

const originalCreateSocket = dgram.createSocket.bind(dgram);
dgram.createSocket = ((...args: Parameters<typeof dgram.createSocket>) => {
  const first = args[0];
  if (first !== null && typeof first === "object") {
    const opts = first as { host?: string; hostname?: string };
    assertLoopback("dgram.createSocket", opts.host ?? opts.hostname);
  }
  const socket = originalCreateSocket(...args);
  const originalSend = socket.send.bind(socket);
  socket.send = ((
    ...sendArgs: Parameters<typeof socket.send>
  ): ReturnType<typeof socket.send> => {
    // dgram send(buf, offset, len, port, address[, cb]) — the address overload
    // has no tuple element at index 4, so read it off the runtime args.
    const address = (sendArgs as unknown[])[4];
    if (typeof address === "string") assertLoopback("dgram.send", address);
    return originalSend(...sendArgs);
  }) as typeof socket.send;
  return socket;
}) as typeof dgram.createSocket;
