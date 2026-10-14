# ALT RADAR systemd service and React dashboard design

**Status:** Revised for review  
**Date:** 2026-10-14

## Goal

Run ALT RADAR as a user-level systemd service that starts with the machine, owns the scanner and API, and serves the React dashboard from the same loopback origin. The browser UI reads the service's live data; it does not start a second backend process. Python GUI launchers are no longer part of the supported normal workflow.

## Current context

- `proto.service` already embeds the scanner and resolver, but the example unit passes `--no-scan`, so it currently runs without the full scan loop.
- The service API binds to loopback on port `18741` and authenticates routes with a persistent bearer token stored at `~/.config/altradar/service.token`.
- The React app in `app/` is currently served by Vite during development. Its production build is `app/dist`.
- The current dashboard contains a development token constant. Production must not ship that token or bake the service token into the static bundle.
- The existing Python GUI packages and their modules are outside this change; they become legacy launch paths rather than being deleted.

## Architecture

```text
machine boot / user systemd
          │
          ▼
proto.service (one long-lived process)
  ├── scanner + resolver loop
  ├── authenticated API and SSE at 127.0.0.1:18741
  └── React static files from app/dist at the same origin
          │
          ▼
browser: http://127.0.0.1:18741/
```

The backend is the only runtime service. systemd owns its lifecycle and restarts it after failure. The dashboard launcher only checks service health and opens the browser; it never starts, stops, or supervises the backend.

## Service and boot lifecycle

- Update `altradar.service.example` to run the scanner-enabled service. Remove `--no-scan`; retain the existing collection and resolution intervals unless implementation review finds they conflict with the current service defaults.
- Keep the existing user-level unit model (`WantedBy=default.target`). Document installation, `systemctl --user enable --now altradar.service`, and `loginctl enable-linger <user>` so the user manager can start at boot and remain active after logout.
- Keep loopback binding and the existing service token file and permissions. The dashboard and API are intended for the same machine, not LAN exposure.
- The backend serves the built UI when `app/dist` exists. Make the path configurable, with the repository's `app/dist` as the development checkout default. Missing UI assets must produce a clear service log/error and leave health/API diagnostics available.
- For systemd installations outside the checkout, document building the React app before enabling the service and verify the configured static asset path is readable by the service user.

## Same-origin routing and authentication

- Serve `/`, frontend assets, and client-side routes from the React build. Serve API requests under `/api/` and map them to the service's existing route handlers without changing their response envelopes. Preserve query strings and streaming behavior for SSE.
- Keep `GET /healthz` unauthenticated for service health checks. All other API routes remain authenticated. The backend serves a same-origin runtime bootstrap endpoint that returns the existing service token; the browser stores it in memory and uses it as a bearer token for API and SSE requests. Do not put the token in the static bundle, URL, persistent browser storage, or logs.
- Restrict the bootstrap endpoint to loopback requests and the expected same-origin host/origin policy. The token remains protected by the local OS account boundary and existing file permissions; this design does not claim protection from other processes running as the same user.
- Remove the hardcoded development token from the production dashboard path. Development may retain an explicit Vite-only mock/dev credential path, clearly separated from production runtime config.
- Because the UI and API share one origin, production browser traffic does not require CORS. Keep the existing development CORS policy for Vite if the current developer workflow needs it.

## React build and launcher behavior

- `npm run build` creates `app/dist`; Node/Vite are build-time and development tools only, not production runtime dependencies.
- Add or update a lightweight launcher (`run-app.sh`) that checks `http://127.0.0.1:18741/healthz`, opens `http://127.0.0.1:18741/` in the default browser, and reports a concise actionable error if the service is unavailable. It must not invoke Python GUI modules or start a server.
- On an unavailable service, direct the user to `systemctl --user status altradar.service` and the service journal. Ctrl-C or closing the launcher must not stop the service.
- Keep Vite available for frontend development, with its existing proxy targeting the local backend. Production use is through the systemd-served static bundle.

## Documentation and scope

- Update README quick-start and service setup to make systemd backend plus React browser UI the normal launch flow. Clearly label Tkinter/Qt launch commands as legacy/deprecated, and remove them from the primary quick-start instructions.
- Do not delete Python GUI code, remove GUI dependencies, rewrite scanner behavior, alter API payload contracts, or change trading/strategy logic as part of this launcher work.
- Do not add a second frontend daemon, Docker, a reverse proxy, remote binding, or LAN access.

## Verification criteria

1. The example user unit starts one scanner-enabled `proto.service` process and systemd restarts it after a forced process exit.
2. With a production React build present, `/` and static assets load from `127.0.0.1:18741`; direct navigation to client-side routes works.
3. `/api/healthz` or the agreed health route is handled consistently, and authenticated API reads and SSE work through `/api/` on the same origin. The health check remains unauthenticated; protected routes reject missing/invalid tokens.
4. The browser receives the token only through the runtime bootstrap, keeps it in memory, and sends it as a bearer token. No production artifact contains the development token or service token.
5. Starting the launcher with the service active opens the dashboard. Starting it with the service down reports systemd diagnostics and does not spawn a backend process.
6. Enabling lingering and the user unit makes the service start at boot and continue after logout; document that this requires the host's user-systemd environment.
7. Vite development continues to work against the same backend, and existing API response contracts remain unchanged.

## Open implementation details

- Choose the concrete runtime-bootstrap route and exact static-file fallback/error behavior during implementation, preserving the security properties above.
- Confirm systemd's working directory and static path resolution for both checkout-based development and the documented installed service setup.
- Confirm the service's existing CORS/Origin gate accepts the production same-origin browser origin and does not weaken protections for other origins.

## Review gate

This document is the revised design for review. After approval, write the implementation plan; do not begin implementation from this design alone.
