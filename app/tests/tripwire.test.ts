// The socket tripwire installed by tests/setup.ts must block every egress
// path a component could reach underneath a mocked fetch: net.connect,
// net.createConnection (the property node:http actually drives), and the
// node:http/https request/get entry points.
//
// 192.0.2.1 is RFC 5737 TEST-NET-1 — guaranteed non-loopback and
// non-routable — so a missed tripwire can never actually leave this machine.
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
const net = require("node:net") as typeof import("node:net");
const http = require("node:http") as typeof import("node:http");
const https = require("node:https") as typeof import("node:https");

const NON_LOOPBACK_HOST = "192.0.2.1";

// Runs `fn` expecting the tripwire to throw synchronously. If it does not
// throw, any handle the unguarded call created is neutralised (error
// handler + destroy) so the failing run fails cleanly without leaking a
// socket or reaching the network.
function expectTripwire(fn: () => unknown): void {
  let error: unknown;
  let handle:
    | { on?: (event: string, cb: () => void) => void; destroy?: () => void }
    | undefined;
  try {
    handle = fn() as typeof handle;
  } catch (err) {
    error = err;
  }
  if (handle && typeof handle.destroy === "function") {
    if (typeof handle.on === "function") handle.on("error", () => {});
    handle.destroy();
  }
  expect(error).toBeInstanceOf(Error);
  expect((error as Error).message).toContain("socket tripwire");
}

test("net.connect to a non-loopback host throws", () => {
  expectTripwire(() => net.connect({ host: NON_LOOPBACK_HOST, port: 443 }));
});

test("net.connect(port, host) to a non-loopback host throws", () => {
  expectTripwire(() => net.connect(443, NON_LOOPBACK_HOST));
});

test("net.createConnection to a non-loopback host throws", () => {
  expectTripwire(() =>
    net.createConnection({ host: NON_LOOPBACK_HOST, port: 443 }),
  );
});

test("http.request to a non-loopback host throws", () => {
  expectTripwire(() => http.request({ host: NON_LOOPBACK_HOST, port: 80 }));
});

test("http.get to a non-loopback host throws", () => {
  expectTripwire(() => http.get({ host: NON_LOOPBACK_HOST, port: 80 }));
});

test("http.request with a non-loopback URL string throws", () => {
  expectTripwire(() => http.request(`http://${NON_LOOPBACK_HOST}/`));
});

test("https.request to a non-loopback host throws", () => {
  expectTripwire(() => https.request({ host: NON_LOOPBACK_HOST, port: 443 }));
});

test("https.get to a non-loopback host throws", () => {
  expectTripwire(() => https.get({ host: NON_LOOPBACK_HOST, port: 443 }));
});

test("https.request with a non-loopback URL string throws", () => {
  expectTripwire(() => https.request(`https://${NON_LOOPBACK_HOST}/`));
});

test("loopback hosts are still allowed through the tripwire", () => {
  const socket = net.connect({ host: "127.0.0.1", port: 1 });
  expect(socket).toBeInstanceOf(net.Socket);
  socket.on("error", () => {});
  socket.destroy();
});
