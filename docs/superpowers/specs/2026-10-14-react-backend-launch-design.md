# React Frontend and Python Backend Launch Design

**Status:** Draft for review  
**Date:** 2026-10-14

## Goal

Replace the Tkinter/Qt application launch path with one local launcher for the
Python scanner/API backend and the React dashboard. Python remains the backend;
React is the only supported application UI.

## Current state

- `python3 -m gui` opens the Tkinter scanner UI.
- `python3 -m qtgui` opens the optional PySide6 scanner UI.
- `proto.service` owns the HTTP API and embeds the scanner loop.
- `app/` contains the React dashboard. Vite serves it on loopback port 5173 and
  proxies `/api` to the service at loopback port 18741.
- The frontend uses the development bearer token `dev-token`; production token
  injection is not implemented because this repository has no Tauri shell yet.

## Proposed behavior

Add a root `run-app.sh` launcher that:

1. Checks that Python, Node/npm, installed frontend dependencies, and the
   configured loopback ports are available. It fails with a direct message and
   does not install dependencies or silently choose different ports.
2. Starts `python3 -m proto.service` with the existing scanner options and the
   development token expected by the local React frontend.
3. Starts Vite on `127.0.0.1:5173` in strict-port mode. The existing Vite proxy
   sends `/api` requests to the service at `127.0.0.1:18741`.
4. Waits for both the backend health endpoint and Vite to answer, then opens the
   dashboard in the default browser. If browser opening is unavailable, it
   prints the dashboard URL and keeps both processes running.
5. On exit or Ctrl-C, stops Vite and the backend, allowing the backend's
   existing shutdown logic to stop its scanner loop and release its scanner
   lock. A startup failure also cleans up any process already started.

The Python GUI modules are no longer the default or documented app launch
path. The existing `gui` and `qtgui` implementation modules remain untouched in
this change so their scanner/model code and current tests are not removed as
part of launcher replacement.

## CLI and configuration

Arguments passed to `run-app.sh` are forwarded to `proto.service`, preserving
service options such as `--db`, `--coins`, `--stake`, and `--interval`. The
launcher does not expose Vite arguments; the frontend port and proxy target
remain a single fixed local configuration.

The development token is explicitly limited to this loopback development
launcher. The backend remains bound to loopback, uses its existing authentication
and origin checks, and retains its scanner lock. This design does not claim to
provide a production desktop package or production-grade token injection.

## Documentation changes

Update the README requirements and launch instructions to describe the React
frontend, Python service, Node/npm, and installed frontend dependencies. Make
`./run-app.sh` the application launch command. Keep headless service/daemon and
terminal CLI instructions separate from UI startup. Remove Tkinter and Qt GUI
launch commands from the normal usage section.

## Out of scope

- Deleting the Tkinter/Qt implementation modules or changing their internal
  behavior.
- Changing scanner rules, watcher calculations, API response contracts, or
  persistence.
- Installing Node modules automatically.
- Building a Tauri/native package, injecting a per-launch production token, or
  changing frontend design.

## Acceptance criteria

- One documented command launches backend and React dashboard together on the
  configured loopback ports.
- The page opens only after both processes answer; the user can still use the
  printed URL when no browser is available.
- Ctrl-C and startup failures cleanly stop child processes and do not leave a
  second scanner running.
- Existing scanner CLI options are passed to the backend unchanged.
- The README no longer presents Python GUI commands as the app launch path.
- The existing Python and React code remains otherwise unchanged.
