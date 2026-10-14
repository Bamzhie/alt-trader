# ALT RADAR native desktop app and always-on backend design

**Status:** Revised for review  
**Date:** 2026-10-14

## Goal

Run the scanner and API as a user-level systemd service that starts with the machine. Provide a system-native desktop app, built with Tauri and the existing React frontend, that opens in its own desktop window and reads from that already-running backend. Normal use must not open a browser or start another backend process.

## Current context

- `proto.service` embeds the scanner and resolver, but `altradar.service.example` currently passes `--no-scan`; the unit must be scanner-enabled.
- The service API binds to loopback on port `18741` and uses a persistent token at `~/.config/altradar/service.token`.
- `app/` is a React/Vite frontend. `app/src-tauri/` is the intended native desktop shell.
- The current Tauri design already describes the React shell and token access for a persistent service, but it also includes spawn-fallback behavior. That behavior conflicts with the required systemd-owned backend lifecycle and is removed here.
- Python GUI modules remain in the repository as legacy code; this work changes the supported launch path rather than deleting those modules.

## Architecture

```text
machine boot / user systemd
          │
          ▼
proto.service (one long-lived process)
  ├── scanner + resolver loop
  └── authenticated API + SSE at 127.0.0.1:18741
          ▲
          │ loopback HTTP/SSE; existing service token
          │
Tauri desktop app (React UI in native window)
          ▲
          │ desktop launcher / application menu
          └── user opens ALT RADAR
```

The backend service owns scanning, data collection, API, and restart behavior. The native app owns only the desktop window and presentation. It attaches to the service and never starts, stops, or supervises it. Closing the desktop window leaves the backend running.

## Backend service and boot lifecycle

- Update `altradar.service.example` to run the scanner-enabled service by removing `--no-scan`. Retain collection and resolution intervals unless implementation review finds they conflict with current service defaults.
- Keep the user-level systemd unit (`WantedBy=default.target`). Document installation, `systemctl --user enable --now altradar.service`, and `loginctl enable-linger <user>` so it starts at boot and remains active after logout.
- Keep loopback binding, service token file location, and existing file permissions. Do not expose the API on LAN interfaces.
- The native app performs a readiness check against the existing service. If unavailable, it shows a clear in-app message with `systemctl --user status altradar.service` and journal commands. It does not start a fallback backend.
- The systemd service remains available if the native UI is closed, crashes, or is not installed.

## Native desktop app

- Use Tauri 2 with the existing React/TypeScript/Vite app. Launching ALT RADAR from the desktop creates a native app window; do not launch the system browser or a web-server tab.
- Provide an installable desktop entry/application menu item and Linux packaging (at minimum `.deb` and `.AppImage`, subject to build environment support). The desktop entry starts the packaged Tauri app, not Python GUI modules or the backend.
- The app connects only to `http://127.0.0.1:18741`, checks liveness/readiness, and reads the existing snapshot, setups, metrics, and SSE APIs. UI state reflects service availability honestly; no mock production data is shown as live data.
- Closing the window terminates the UI process only. It must not stop, signal, or restart the systemd service.
- Vite remains available for development, with its proxy to the local service. Production UI assets are bundled into the Tauri app; the systemd backend does not serve the React bundle.

## Authentication and origin security

- The desktop app obtains the persistent service token through a narrowly scoped Tauri Rust command that reads `~/.config/altradar/service.token` for the current OS user. Keep the token in frontend memory only and send it as a bearer token for protected API and SSE calls. Never embed it in frontend assets, command-line arguments, URLs, local storage, or logs.
- Restrict the Rust command to the expected app context and return only the token needed by the frontend. Keep the file mode `0600` and loopback-only backend binding. Document that same-user processes are within the local trust boundary.
- The API remains authenticated except `GET /healthz`; readiness and data endpoints require the bearer token. Preserve existing origin/host checks and permit only the Tauri asset origin plus the localhost Vite development origins needed for development.
- Production desktop requests do not require CORS changes beyond the existing Tauri origin allowlist. Do not weaken checks to allow arbitrary origins.
- Remove the hardcoded development token from the production path. Any development credential must be explicit and isolated to Vite/mock development.

## Desktop launcher and setup

- Add or update a desktop launcher script only if needed for development. The supported user path is the installed ALT RADAR desktop entry, which opens the Tauri window.
- Setup documentation installs/enables the user systemd service and installs the desktop app as separate components. It clearly explains that the app expects the service to be running and does not manage its lifecycle.
- README quick-start makes the systemd service plus native React/Tauri app the normal workflow. Tkinter/Qt commands are labeled legacy and removed from the primary quick-start.
- Do not add a browser launcher, a React static server, a second frontend daemon, Docker, reverse proxy, remote binding, or LAN access.

## Scope boundaries

- Do not delete Python GUI code or remove its dependencies in this task.
- Do not rewrite scanner behavior, alter API payload contracts, or change trading/strategy logic.
- Do not create a second scanner process or a UI-spawned backend fallback.
- Do not migrate the existing database or change its schema under this launcher work.

## Verification criteria

1. The example user unit starts one scanner-enabled `proto.service`, and systemd restarts it after an unexpected exit.
2. The user unit starts at boot and survives logout when linger is enabled.
3. Launching ALT RADAR from the desktop opens a native Tauri window without opening a browser.
4. With the service available, the UI obtains the service token through the Rust bridge and reads authenticated data and SSE from loopback.
5. With the service stopped, the UI shows an actionable unavailable state and does not spawn a backend.
6. Closing or crashing the UI does not stop or restart the backend; stopping the service leaves the UI safely disconnected.
7. Production assets contain neither the service token nor the development token; API authentication and origin checks remain enforced.
8. Vite development continues to work against the local service, and existing API response contracts remain unchanged.

## Open implementation details

- Confirm the current Tauri capability/permission model for the token-reading Rust command and restrict it to the app's main window.
- Confirm packaging dependencies and supported Linux formats on the target development machine.
- Confirm service readiness semantics and the exact UI status message for a running service before its first completed scan.

## Review gate

This revised document is for review. After approval, write an implementation plan; do not begin implementation from this design alone.
